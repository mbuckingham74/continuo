# Claude result-envelope fixtures

These deterministic fixtures exercise Continuo's Claude CLI result boundary.
Tests load the saved `returncode`, `stdout`, and `stderr`; they never invoke a
provider.

## Recorded fixtures

- `success.json` is sanitized from a successful Claude review attempt persisted
  by Continuo on 2026-08-02. The historical run did not record its CLI version;
  Claude Code 2.1.220 was observed when the fixture was reviewed.
- `provider_error.json` was captured on 2026-08-02 with Claude Code 2.1.220 from
  the approved synthetic, read-only M0.3 capture. It reached the explicit
  `$0.10` budget guard and returned `error_max_budget_usd`. The CLI reported
  `$0.104028`, demonstrating that an in-flight call can finish slightly above a
  between-call ceiling.
- `max_turns.json` was captured on 2026-08-02 with Claude Code 2.1.220 from the
  approved synthetic, read-only M0.3 capture. It returned `error_max_turns` and
  reported `$0.0514985`.

Recorded envelopes retain every top-level field from the source capture.
Session/UUID values, model-authored text, durations, token/cost detail, and model
usage detail were replaced with bounded synthetic values. `usage` and
`modelUsage` remain present but are emptied because their nested telemetry is
not part of the result/error discriminator contract. No envelope discriminator,
error flag, terminal reason, error subtype, structured-output field, or process
return code was added, removed, or renamed.

## Derived fixtures

- `malformed_envelope.json` truncates the sanitized success stdout.
- `missing_structured_output.json` removes only `structured_output` from the
  sanitized success envelope.
- `schema_invalid.json` replaces only `structured_output` with a review object
  that contains an additional field forbidden by Continuo's closed schema.

Derived fixtures are controller-protocol adversaries, not claims that Claude
emitted those exact payloads. Their provenance names the source fixture and the
single transformation. Every fixture records the SHA-256 of its exact saved
stdout string; tests verify the checksums before using it.
