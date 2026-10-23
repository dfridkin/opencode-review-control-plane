# `make verify` is what the idle plugin and CI run. It must fail until you wire it up:
# a verification step that passes vacuously teaches everyone to ignore it.

.PHONY: verify lint typecheck test control-plane-test codeowners evals-replay metrics

verify: lint typecheck test

lint:
	@echo "TODO: wire your linter here (e.g. ruff check . / eslint .)" >&2; exit 1

typecheck:
	@echo "TODO: wire your type checker here (e.g. mypy . / tsc --noEmit)" >&2; exit 1

test:
	@echo "TODO: wire your test suite here, with a coverage floor (e.g. pytest --cov --cov-fail-under=80)" >&2; exit 1

# --- review control plane --------------------------------------------------

control-plane-test:
	python3 -m unittest discover -s scripts/tests -v

codeowners:
	python3 scripts/sync_codeowners.py --write

evals-replay:
	python3 evals/run_evals.py --replay evals/fixtures/replay

metrics:
	python3 scripts/review_metrics.py --since $$(date -u -d '28 days ago' +%F 2>/dev/null || date -u -v-28d +%F) --markdown metrics.md
