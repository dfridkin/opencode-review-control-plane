#!/usr/bin/env python3
"""Run one review agent headless with opencode and return validated JSON.

Every CI review step (security review, bug hunters, verifiers, evals) goes through
this adapter. It runs `opencode run --format json` with any provider opencode supports,
including open-weight models behind an OpenAI-compatible endpoint (vLLM, llama.cpp,
Ollama, LM Studio).

The JSON schema goes in the prompt. The reply is extracted and validated here, with one
corrective retry, because open-weight models often wrap JSON in prose or get a field wrong.

The agent runs read-only: no edits, no web, shell limited to git reads. Its CI config
comes from ci/opencode.ci.json through OPENCODE_CONFIG_CONTENT, which outranks the
repository's own opencode.json.

  python3 scripts/agent_run.py --model openweight/qwen3-coder \
      --agent .opencode/agents/bug-hunter.md --task "Focus area: logic. Base: abc123" \
      --schema .github/review-prompts/candidates.schema.json --out out.json

Env: REVIEW_MODEL (default for --model),
     LLM_BASE_URL, LLM_API_KEY, LLM_CONTEXT, LLM_MAX_OUTPUT (the `openweight` provider)
Exit: 0 valid output written, 2 no valid output after retries, 1 usage or opencode error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CI_OPENCODE_CONFIG = ROOT / "ci" / "opencode.ci.json"
OPENWEIGHT_PROVIDER = "openweight"

class EngineError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# JSON extraction and validation (pure)
# ---------------------------------------------------------------------------

FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.S)


def _balanced_objects(text: str) -> list[str]:
    """Every top-level {...} span, string-aware."""
    spans, depth, start, in_str, esc = [], 0, None, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                spans.append(text[start : i + 1])
                start = None
    return spans


def extract_json(text: str) -> dict | None:
    """Best effort: whole text, then fenced blocks, then the LAST balanced object.
    Open-weight models often wrap JSON in prose or fences; the last object is
    usually the answer after any reasoning."""
    text = (text or "").strip()
    candidates = [text] + [m.group(1).strip() for m in FENCE.finditer(text)][::-1] \
        + _balanced_objects(text)[::-1]
    for c in candidates:
        try:
            v = json.loads(c)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(v, dict):
            return v
    return None


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}


def validate(value, schema: dict, path: str = "$") -> list[str]:
    """Minimal JSON Schema subset: type, properties, required, items, enum, minimum, maximum."""
    errs: list[str] = []
    t = schema.get("type")
    if t == "integer":
        if not (isinstance(value, int) and not isinstance(value, bool)):
            return [f"{path}: expected integer"]
    elif t == "number":
        if not (isinstance(value, (int, float)) and not isinstance(value, bool)):
            return [f"{path}: expected number"]
    elif t in _TYPES and not isinstance(value, _TYPES[t]):
        return [f"{path}: expected {t}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: above maximum {schema['maximum']}")
    if isinstance(value, dict):
        for r in schema.get("required", []):
            if r not in value:
                errs.append(f"{path}: missing required '{r}'")
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                errs += validate(value[k], sub, f"{path}.{k}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errs += validate(item, schema["items"], f"{path}[{i}]")
    return errs


def strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        parts = text.split("---\n", 2)
        if len(parts) == 3:
            return parts[2].lstrip("\n")
    return text


# ---------------------------------------------------------------------------
# opencode
# ---------------------------------------------------------------------------

@dataclass
class Request:
    instructions: str
    task: str
    schema: dict
    input_path: Path | None = None
    model: str | None = None
    timeout: int = 900


@dataclass
class Result:
    data: dict | None
    raw: str
    errors: list[str] = field(default_factory=list)
    attempts: int = 0


def opencode_parse_events(stdout: str) -> str:
    """`opencode run --format json` emits one JSON event per line. Collect the
    assistant text parts; raise on an error event."""
    texts: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "error":
            err = ev.get("error") or {}
            msg = (err.get("data") or {}).get("message") or err.get("name") or "unknown error"
            raise EngineError(f"opencode error: {msg}")
        part = ev.get("part") or {}
        if ev.get("type") == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
    return "\n\n".join(texts)


def opencode_config(model: str, env: dict[str, str]) -> str:
    cfg = json.loads(CI_OPENCODE_CONFIG.read_text())
    provider, _, model_id = model.partition("/")
    if provider == OPENWEIGHT_PROVIDER and model_id:
        models = cfg.setdefault("provider", {}).setdefault(OPENWEIGHT_PROVIDER, {}).setdefault("models", {})
        models.setdefault(model_id, {
            "name": model_id,
            "limit": {"context": int(env.get("LLM_CONTEXT") or 131072),
                      "output": int(env.get("LLM_MAX_OUTPUT") or 16384)},
        })
    cfg["model"] = model
    return json.dumps(cfg)


def compose_prompt(req: Request, feedback: str | None) -> str:
    # `opencode run` has no system-prompt flag; instructions lead the message.
    parts = ["<instructions>\n" + req.instructions.strip() + "\n</instructions>", req.task.strip()]
    if req.input_path:
        parts.append(f"The input is attached as the file {req.input_path.name}.")
    parts.append("Respond with ONE JSON object and nothing else. It must validate against "
                 "this JSON Schema:\n" + json.dumps(req.schema))
    if feedback:
        parts.append("Your previous reply was rejected: " + feedback
                     + "\nReturn only the corrected JSON object.")
    return "\n\n".join(parts)


def run_opencode(req: Request, feedback: str | None, env: dict[str, str]) -> str:
    if not req.model or "/" not in req.model:
        raise EngineError("opencode needs --model in provider/model form, e.g. openweight/qwen3-coder")
    env = dict(env)
    env["OPENCODE_CONFIG_CONTENT"] = opencode_config(req.model, env)
    env["OPENCODE_DISABLE_CLAUDE_CODE"] = "1"  # never pull a stray CLAUDE.md or .claude/ into CI reviews
    cmd = ["opencode", "run", "--format", "json", "--agent", "ci-reviewer", "--model", req.model]
    if req.input_path:
        cmd += ["--file", str(req.input_path)]
    cmd.append(compose_prompt(req, feedback))
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=req.timeout, env=env,
                         stdin=subprocess.DEVNULL)
    text = opencode_parse_events(res.stdout)
    if res.returncode != 0 and not text:
        raise EngineError(f"opencode exited {res.returncode}: {(res.stderr or res.stdout)[-500:]}")
    return text


def run(req: Request, retries: int = 1, env: dict[str, str] | None = None, runner=None) -> Result:
    env = dict(os.environ if env is None else env)
    runner = runner or run_opencode
    feedback, raw, errors = None, "", []
    for attempt in range(1, retries + 2):
        raw = runner(req, feedback, env)
        data = extract_json(raw)
        if data is None:
            errors = ["reply contained no JSON object"]
        else:
            errors = validate(data, req.schema)
            if not errors:
                return Result(data, raw, [], attempt)
        feedback = "; ".join(errors[:5])
    return Result(None, raw, errors, retries + 1)


# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=os.environ.get("REVIEW_MODEL") or None)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--agent", help=".opencode/agents/<name>.md; its body becomes the instructions")
    src.add_argument("--instructions", help="plain instructions file")
    ap.add_argument("--task", required=True)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--input", help="file attached to the message, e.g. the PR diff")
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    instr_path = Path(args.agent or args.instructions)
    instructions = strip_frontmatter(instr_path.read_text())
    req = Request(instructions=instructions, task=args.task,
                  schema=json.loads(Path(args.schema).read_text()),
                  input_path=Path(args.input) if args.input else None,
                  model=args.model)
    try:
        res = run(req, retries=args.retries)
    except (EngineError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"agent_run: {e}", file=sys.stderr)
        return 1
    if res.data is None:
        print(f"agent_run: no valid output after {res.attempts} attempt(s): {'; '.join(res.errors[:5])}",
              file=sys.stderr)
        Path(args.out + ".raw.txt").write_text(res.raw)
        return 2
    Path(args.out).write_text(json.dumps(res.data, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
