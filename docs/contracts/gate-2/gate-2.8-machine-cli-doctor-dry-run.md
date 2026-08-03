# Gate 2.8 — Versioned machine CLI output, doctor, and read-only dry-run

**Status:** implemented; awaiting review.

## Purpose and bounded scope

Milestone 1 requires versioned read contracts for human and machine clients.
This item adds that boundary to the existing Jobs-compatible CLI without
changing provider routing, provider retry/authority rules, Git gates, storage
semantics, or compatibility identifiers.

In scope:

- a versioned JSON response envelope for every existing public CLI command;
- a `doctor` command that reports deterministic environment readiness without
  revealing secrets or invoking provider work; and
- `run <task-ref> --dry-run`, which performs deterministic planning and leaves
  no persistent or target-repository change.

Out of scope:

- generic configuration, route selection, model discovery, provider fallback,
  live provider validation, event sourcing, telemetry, or any Gate 2.9 work;
- changing `jobs-orchestrator`, `JOBS_REPO`, `src/jobs_orchestrator`, current
  provider commands, provider authority/retry behavior, Git gates, or storage
  behavior outside an explicitly requested dry run; and
- automatic repair of a target repository, a run directory, credentials, or
  provider configuration.

## Invariants

1. Existing human-readable invocations retain their command names, arguments,
   exit semantics, prompts, and output behavior unless `--json` is supplied.
2. `--json` emits exactly one UTF-8 JSON object on standard output. Its stable
   top-level shape is `{"contract_version":"continuo.cli.v1","command":
   string,"ok":boolean,"result":object|null,"error":object|null}`. Keys
   remain present with `null` where inapplicable. A successful response has a
   non-null `result` and null `error`; an expected command failure has null
   `result` and an `error` object containing stable `code` and human-readable
   `message` fields.
3. JSON result objects use only documented public fields. Persisted
   `WorkflowRun` JSON, exception text, Rich markup, table layout, provider
   stdout/stderr, prompts, command lines, and secrets are not a substitute for
   this contract and must not be embedded wholesale in the envelope.
4. `--json` emits the current contract version. A client may require it
   explicitly with `--json-version continuo.cli.v1`; the implementation must
   reject any version it cannot produce rather than silently changing
   `continuo.cli.v1`. The persisted run schema version and CLI contract version
   are independent values.
5. `doctor` is diagnostic only. Its checks cover the configured target and run
   storage, Git availability and target state, configured provider binary
   discoverability, locally available non-secret authentication status, and
   the currently compiled route/capability declarations. It reports each
   check as `pass`, `warn`, `fail`, or `unknown`, plus a stable reason code.
6. `doctor` must never print credentials, tokens, cookie values, environment
   variable values, full provider commands, provider prompts, or raw provider
   diagnostics. Authentication that cannot be established from a safe local
   adapter probe is `unknown`, not guessed or exposed.
7. `doctor` and dry-run never call `execute_sonnet_review`,
   `execute_terra_resolution`, `execute_sol_escalation`,
   `execute_luna_implementation`, their retry/supervision helpers, or any
   live-provider/network operation. Provider checks are adapter-owned,
   deterministic local probes with fakes in tests.
8. `run <task-ref> --dry-run` validates the same deterministic inputs needed
   before creating a new run: task-ref syntax and unique local task
   resolution, target identity and Git state, compiled route/capability
   compatibility, and applicable pre-provider gates. It returns a plan of the
   stages and authority classes that *would* be requested; it does not claim a
   provider result, approval, commit, push, or merge.
9. A successful dry-run result is deterministic for unchanged inputs. It has
   no generated run ID, timestamp, random value, or provider-derived field.
   It identifies the resolved task path and content SHA-256, repository state,
   and planned stage/authority sequence using a separately versioned
   `plan_version` field.
10. Dry-run is genuinely read-only: it must not create, delete, rename, chmod,
    chown, open-for-write, lock, migrate, harden, or otherwise modify the run
    directory, `.target-locks`, target worktree, target `.git` directory,
    task files, configuration, credentials, or environment. Read-only Git
    queries are permitted; no Git command may mutate refs, index, worktree,
    hooks, or configuration.
11. Dry-run must not construct or enter `TargetCoordinator`, call
    `persist`, `migrate_run_record`, `_prepare_private_storage`, or any
    operation whose normal behavior can create a file or repair permissions.
    Missing or unsafe storage is reported as a non-mutating diagnostic; it is
    never created or hardened by dry-run or doctor.
12. Normal runs remain the only path that creates run records, takes target
    ownership, invokes providers, or progresses controller state. A dry-run
    does not reserve a target and has no resume or recovery effect.

## CLI contract

`--json` and `--json-version` are common options accepted by `run`, `resume`, `recover-writer`,
`release-target`, `approve-policy`, `report`, `migrate-run`, `status`, and the
new `doctor`. It is intentionally opt-in so existing text clients remain
compatible. Commands that require human confirmation retain that confirmation
in text mode; JSON mode must fail closed with a stable
`interactive_confirmation_required` error rather than prompt or perform a
write-capable transition.

The JSON `result` for mutating commands reports only the public outcome
(including run ID, stage, and explicitly requested action). It must not make a
mutating command safe merely because its output is JSON. `status --json` and
`report --json` receive purpose-built read representations that distinguish a
current record from an inspectable non-current classification. The current
schema's model dump remains an implementation detail.

The JSON `error.code` set is documented and finite for Gate 2.8: `invalid_input`,
`not_found`, `precondition_failed`, `record_not_current`, `storage_unsafe`,
`target_invalid`, `git_unavailable`, `interactive_confirmation_required`, and
`internal_error`. A future version may add codes only without changing the
meaning of an existing code; clients must treat an unknown code as a failure.

## Doctor contract

`doctor [--repo PATH] [--json]` reads the same `--repo`/`JOBS_REPO` target
selection as existing commands. It does not require a task reference and does
not inspect a task body. Its result contains `doctor_version`, canonical target
path (when safely determined), and an ordered list of checks with `id`,
`status`, `code`, and a redacted human-readable summary. The required check IDs
are `git_binary`, `target_repository`, `target_state`, `run_storage`,
`provider_binaries`, `provider_auth`, and `route_capabilities`.

A failed or unknown `doctor` check does not change its result into a write,
does not repair the condition, and does not authorize a later run. `doctor`
returns a nonzero exit when any required check fails; an `unknown` authentication
status is a warning unless a route explicitly requires a capability that cannot
be established locally. This preserves a clear distinction between local
readiness evidence and live-provider availability.

## Dry-run contract

`run <task-ref> --dry-run [--repo PATH] [--json]` uses the normal target
selection but always returns a plan rather than a `WorkflowRun`. It is valid
only when all deterministic pre-provider gates pass. Its `result` includes:

- `plan_version` (`continuo.run-plan.v1`);
- `task` with the task reference, relative resolved path, and content SHA-256;
- `repository` with canonical path, branch, HEAD, origin, and clean state; and
- `planned_stages`, an ordered list of stable stage IDs with their declared
  authority (`controller`, `provider_read_only`, `provider_workspace_write`,
  or `human_approval`).

The plan is deliberately not a preview of provider output or file changes.
When a check fails, it returns the JSON error envelope (or the existing text
failure path) and still makes no mutation. The dry-run option is rejected for
all commands other than `run`; it cannot be combined with a future action to
reuse or adopt a plan.

## Persistence, migration, recovery, and audit effects

The CLI/doctor/plan envelopes are derived interfaces, not persisted run
records. Gate 2.8 changes neither `CURRENT_RUN_SCHEMA_VERSION` nor historical
record classification/migration. Normal command audit and recovery semantics
remain unchanged. `doctor` and dry-run write no run, migration, ownership, or
provider-attempt audit record; their absence is intentional evidence of their
read-only authority.

## Implementation boundaries and deterministic tests

Implementation must centralize envelope rendering and error mapping so a JSON
caller never receives mixed Rich text and JSON on standard output. Tests use
Typer's CLI runner and deterministic repositories, run directories, and
provider/doctor adapter fakes. They must prove no provider invocation and take
pre/post snapshots of target and storage contents, modes, inodes, mtimes, and
lock artifacts. Tests for installed-package compatibility run with
`UV_NO_EDITABLE=1`.

## Adversarial matrix

| ID | Scenario | Expected deterministic result |
|---|---|---|
| G28-01 | Each public command succeeds with `--json`. | Exactly one parseable `continuo.cli.v1` envelope; `ok=true`, no Rich/table text on stdout, and only documented public fields. |
| G28-02 | A malformed argument or unknown run ID is requested with `--json`. | Exactly one failure envelope with `invalid_input` or `not_found`, nonzero exit, and no traceback or mixed output. |
| G28-03 | A current run and an inspectable non-current record are requested through `status --json`. | Both are represented by stable purpose-built read objects; raw persisted model dumps and migration internals are not leaked as the CLI schema. |
| G28-04 | A future/unsupported JSON contract version is requested. | The command fails closed with a stable error and never falls back to a different version. |
| G28-05 | JSON mode reaches `approve-policy` without noninteractive authority. | It returns `interactive_confirmation_required`; no decision, resume, provider action, or run mutation occurs. |
| G28-06 | `doctor` sees missing Git, invalid/non-root target, dirty target, or unavailable configured provider binary. | Ordered checks identify the failing stable codes; nonzero exit; no repair, provider execution, Git mutation, or secret output. |
| G28-07 | Local adapter authentication probe is unavailable, throws, or contains a token-like string in its diagnostic. | `provider_auth` is `unknown` or `fail` with a redacted summary; the token-like string never appears in text or JSON. |
| G28-08 | Route declarations reference a missing binary or capability incompatible with the route's authority. | `doctor` reports `route_capabilities` failure without changing route selection, fallback behavior, retries, or provider authority. |
| G28-09 | `run` dry-run succeeds against a clean deterministic fixture. | Plan has stable `continuo.run-plan.v1` data; all provider fakes have zero calls; no run file, lock, database, migration, or audit record exists afterward. |
| G28-10 | Dry-run uses an absent run directory, an existing permissive run directory, or an unsafe storage artifact. | It reports a non-mutating diagnostic/error; directory existence, modes, inodes, mtimes, and artifacts are unchanged. |
| G28-11 | Dry-run encounters invalid/ambiguous task, missing task, detached HEAD, dirty repository, or mismatched repository root. | It fails before planning; target worktree and `.git` snapshots are byte/mode/mtime unchanged and no provider fake is called. |
| G28-12 | Dry-run is run twice against identical inputs. | Byte-identical JSON output and exit status; no generated run ID, timestamp, random value, or provider-derived data. |
| G28-13 | A dry-run attempt is made to traverse persistence/ownership helpers or any provider execution helper. | Instrumented helpers fail the test if called; the dry-run completes/fails exclusively through read-only planning paths. |
| G28-14 | Existing text commands and the `jobs-orchestrator` installed-package entry point are exercised. | Text compatibility remains intact; `UV_NO_EDITABLE=1` validates the compatibility entry point, and no compatibility identifier is renamed or removed. |
| G28-15 | Full deterministic regression suite runs after the feature tests. | All tests pass with fixtures/fakes only; no live provider, Jobs checkout, Git publication, commit, or push occurs. |

## Evidence

Implemented with deterministic local Git fixtures and provider/doctor fakes
only. No live provider or Jobs checkout was invoked.

- `uv run python -m py_compile orchestrator.py test_orchestrator.py` — passed.
- `uv run python -m unittest test_orchestrator.Gate28CliContractTests` — 9
  Gate 2.8 tests passed, including JSON envelope/version rejection, doctor
  redaction and non-repair, dry-run determinism, and target/storage snapshots.
- `uv run python -m unittest -q` — 200 deterministic tests passed.
- `UV_NO_EDITABLE=1 uv run jobs-orchestrator --help` — passed; the installed
  `jobs-orchestrator` compatibility entry point exposes all existing commands
  plus `doctor`.
- Markdown links, unique matrix IDs, and `git diff --check` were revalidated
  after implementation.
