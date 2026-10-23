// Verify before "done" (Layer 1).
//
// When a session goes idle (the agent thinks it is done), run scripts/hooks/verify-on-stop.sh.
// If verification fails, send the failure back into the session as a new message so the
// agent keeps working.
//
// The script is a no-op in CI and on a clean tree, caches a tree that already passed,
// and gives up after AGENT_VERIFY_MAX_BLOCKS attempts.
//
// Event payload fields differ across opencode versions; the session ID lookup below
// is defensive. If the hook never fires, run `opencode` with --print-logs and check
// the shape of the `session.idle` event your version emits.

import type { Plugin } from "@opencode-ai/plugin"
import { spawnSync } from "node:child_process"
import { join } from "node:path"

export const VerifyOnIdle: Plugin = async ({ client, directory }) => {
  const running = new Set<string>()
  const script = join(directory, "scripts", "hooks", "verify-on-stop.sh")

  return {
    event: async ({ event }: { event: any }) => {
      if (event?.type !== "session.idle") return
      if (process.env.CI || process.env.GITHUB_ACTIONS) return

      const props = event.properties ?? {}
      const sessionID: string | undefined = props.sessionID ?? props.sessionId ?? props.info?.id
      if (!sessionID || running.has(sessionID)) return

      // Subagent sessions report to their parent; only verify the top-level session.
      try {
        const session: any = await client.session.get({ path: { id: sessionID } })
        if (session?.data?.parentID ?? session?.parentID) return
      } catch {
        // If the lookup fails, verify anyway.
      }

      running.add(sessionID)
      try {
        const res = spawnSync("bash", [script], {
          cwd: directory,
          input: JSON.stringify({ session_id: sessionID, hook_event_name: "session.idle" }),
          env: { ...process.env, AGENT_PROJECT_DIR: directory },
          encoding: "utf8",
          timeout: 15 * 60 * 1000,
        })
        const out = (res.stdout ?? "").trim()
        if (!out) return
        const decision = JSON.parse(out)
        if (decision.decision === "block" && decision.reason) {
          await client.session.promptAsync({
            path: { id: sessionID },
            body: { parts: [{ type: "text", text: decision.reason }] },
          })
        } else if (decision.systemMessage) {
          console.warn(`[verify-on-idle] ${decision.systemMessage}`)
        }
      } catch (err) {
        console.warn(`[verify-on-idle] skipped: ${err}`)
      } finally {
        running.delete(sessionID)
      }
    },
  }
}
