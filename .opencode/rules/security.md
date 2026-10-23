# Security rules

These are the things the blocking security review checks. Getting them right up front saves a round trip.

- Every query that reads or writes customer data is scoped to the caller's tenant or owner.
  Primary-key lookups alone are not scoped.
- Authorization checks happen before the action, not after.
- Use parameterized queries. Never build SQL, shell commands, or file paths from request input.
- No secrets in code, config, fixtures, or logs. Read them from the environment or the secret store.
- Do not log emails, user IDs, tokens, or full request/response bodies.
- Network calls have timeouts. Retries only wrap idempotent operations.
- Do not disable TLS verification, even in tests that talk to real hosts.
