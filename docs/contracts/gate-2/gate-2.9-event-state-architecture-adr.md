# Gate 2.9 ADR — Transactional state with append-only audit events

**Status:** accepted, owner-approved, and published; documentation-only ADR.

## Decision summary

Continuo will retain its versioned atomic `WorkflowRun` JSON snapshots as the
authoritative run state until durable-operation work begins. The selected
target architecture for that later work is **SQLite transactional current
state plus append-only audit events**. A state revision and its corresponding
audit event must commit in one database transaction. Events will not be the
sole source of truth, and Continuo will not reconstruct executable state by
folding an event history.

Gate 2.9 is an architecture decision only. It adds no database, event writer,
schema version, migration, replay path, queue, service, UI, or dual-write
behavior.

## Context and decision drivers

The current controller has two deliberately different persistence boundaries:

- one private, versioned JSON snapshot per run is the source of truth for
  inspection and resume; updates use a private temporary file and atomic
  replacement; and
- per-target SQLite coordinates durable ownership and a crash-releasing
  execution mutex. It is not a second store for workflow state.

The controller persists armed provider and writer state before invoking the
corresponding provider, records typed outcomes before choosing the next
transition, and validates saved repository evidence on resume. Historical run
schemas have explicit classification and migration rules. These properties
must survive any later storage change.

Future asynchronous approvals and a single-writer command boundary need an
atomic way to change current state and record an audit fact. Adding an event
file beside each snapshot would create an unavoidable cross-file dual-write
window. Making events the sole source of truth would instead require fold
versioning, replay compatibility, snapshots/compaction, event-payload
migrations, and substantially broader recovery semantics without a current
need for those capabilities.

## Options considered

| Option | Strengths | Failure and migration cost | Decision |
|---|---|---|---|
| Atomic JSON snapshots plus append-only event files | Smallest change; preserves current files directly | State/event writes cannot be atomic across files; crash gaps require reconciliation policy; multi-process command handling remains awkward | Rejected as the durable target; current snapshots remain authoritative only until an explicit cutover |
| SQLite transactional current state plus events | State revision and audit event commit together; fits a single-writer service and asynchronous commands; SQL constraints can enforce revision ordering | Requires an explicit import/cutover, database backup and migration policy, and materialized compatibility reads | **Selected target architecture** |
| Events as sole truth with materialized JSON views | Maximum replay and temporal-query flexibility | Requires deterministic folds forever, fold-version migration, compaction, replay validation, and event correction semantics; magnifies sensitive-data retention | Rejected; no full event-sourcing rewrite is approved |

## Authoritative-state contract

### Before cutover

1. The current-schema `WorkflowRun` JSON file remains the only authoritative
   workflow state. No event journal is inferred, generated, or required.
2. `TargetCoordinator` SQLite remains authoritative only for target ownership
   and execution exclusion. It does not authorize a workflow transition and
   cannot repair or replace a run snapshot.
3. Existing load, classification, migration, writer recovery, provider
   retry/authority, Git gates, storage permissions, CLI identifiers, and JSON
   read contracts remain unchanged by this ADR.

### After a separately approved cutover

1. One private SQLite control store owns a versioned current-state row and an
   ordered event stream for each imported or newly created run. The exact file
   location and physical schema belong to the later implementation contract.
2. Current state is a complete, strictly validated, versioned run payload, not
   a partially normalized collection whose joins can create an invalid
   `WorkflowRun`.
3. Each successful transition increments a per-run `state_revision` exactly
   once and inserts exactly one event for that revision in the same SQLite
   transaction. A uniqueness constraint on `(run_id, state_revision)` prevents
   duplicate transition commits.
4. The current-state row is authoritative for inspection, resume, and control.
   Events are immutable audit facts and query inputs, but never an independent
   authorization source and never a replay program.
5. JSON files or JSON CLI responses produced from the database are versioned
   materialized read/export views. They must never be accepted as a second
   writable state store.
6. All mutations enter through one controller-owned writer boundary. A CLI,
   UI, approval endpoint, queue consumer, provider, or project adapter may
   submit a command but may not update state or event tables directly.

## Transaction and external-side-effect ordering

A database transaction cannot include a provider process, target-worktree
edit, Git commit, or Git push. The later implementation must therefore retain
the current armed-operation protocol:

1. transactionally persist the intended operation, its authority, stable
   identity, pre-operation repository evidence, and an `armed` audit event;
2. commit before starting the external operation;
3. perform at most the already-authorized external operation;
4. transactionally persist the typed observed result and matching audit event;
   and
5. if step 3 completes but step 4 is interrupted, resume from the armed state
   and saved repository/provider evidence under the existing read-only versus
   workspace-write recovery rules.

An event does not grant retry, fallback, Git, approval, or provider authority.
No recovery path may repeat a write-capable provider merely because its
completion event is absent.

## Event contract boundary

The later event envelope must be separately versioned and contain at least:

- stable event type and event-schema version;
- event ID, run ID, and committed state revision;
- recorded timestamp and controller/command actor identity where applicable;
- previous-event hash plus pre- and post-state payload hashes; and
- a typed, redacted payload containing references to durable run records rather
  than copied raw provider material.

Event types use stable control identifiers, never model/display labels. Event
payloads must not contain secrets, environment values, raw prompts, full task
specifications, raw provider stdout/stderr, full commands, or unbounded diffs.
Existing typed record IDs or indexes are referenced where possible.

A hash chain is only an integrity and gap-detection aid. Unless a root is
signed or anchored outside the same mutable store, documentation and CLI output
must not describe the journal as tamper-proof or independently tamper-evident.

## Failure, crash, and concurrency semantics

- SQLite transactions use explicit schema versions, foreign keys, uniqueness
  constraints, and a bounded busy policy. Lock contention is a visible blocked
  command, not an implicit retry loop.
- A transaction failure commits neither the new current state nor its event.
  The caller may retry only an idempotent controller command whose external
  side-effect rules permit it.
- Database corruption, an unsupported database schema, an invalid current
  payload, a revision gap, a hash mismatch, or an event/current-state mismatch
  blocks execution while preserving bounded read/diagnostic access.
- Read-only commands open the control store in SQLite read-only mode when they
  promise no mutation. `doctor` and dry-run may not create a database, journal,
  WAL, lock, migration, or repaired permission as a side effect.
- Target ownership remains enforced independently. A state transaction does
  not weaken canonical-target identity, one-active-run ownership, or the
  crash-releasing execution mutex.
- WAL/rollback journals, backups, exports, and temporary artifacts inherit the
  existing private-storage and symlink/hard-link rejection requirements.

## Migration, compatibility, and rollback

The future implementation requires its own approved contract and migration
matrix. At minimum it must:

1. import an exact current JSON snapshot with its source SHA-256 and schema
   classification; never import an unsupported or execution-blocked record as
   executable state;
2. make import idempotent and reject a same-run ID whose imported source hash
   differs;
3. keep pre-cutover JSON records readable under their existing classification
   and explicit migration rules;
4. establish one unambiguous per-run authority marker before the first
   database-backed transition, so JSON and SQLite can never both be writable;
5. validate a versioned materialized JSON view against the authoritative row
   before claiming compatibility;
6. define backup, restore, integrity-check, and rollback procedures before
   enabling any consequential database-backed run; and
7. refuse rollback to a pre-cutover JSON snapshot after a database-backed
   transition unless a separately tested reverse materialization preserves the
   exact current revision and all control-relevant facts.

The cutover does not rename or remove `jobs-orchestrator`, `JOBS_REPO`,
`src/jobs_orchestrator`, command names, provider commands, route/role IDs, or
existing run-schema identifiers.

## Explicit non-goals and deferred work

- No code or persisted-schema change in Gate 2.9.
- No event sourcing, event replay, temporal controller, compaction, snapshot
  folding, queue, asynchronous gate, notification, telemetry, or UI.
- No consolidation of target-ownership SQLite with the future state store
  without its own failure/migration analysis.
- No live-provider or Jobs-repository validation.
- No claim that SQLite alone supplies remote durability, disaster recovery,
  multi-host coordination, or strong tamper evidence.
- No Gate 3 generic-configuration or adapter work.

## Consequences

The decision preserves the proven snapshot controller while fixing the future
dual-write problem at the architecture boundary. It provides a coherent path
to asynchronous commands and complete local audit queries without taking on
event-fold compatibility as part of every future state change.

The cost is an explicit later storage migration and a database operational
contract. JSON becomes a materialized compatibility/read surface after
cutover, so tooling that currently treats files as writable state must be
identified and prohibited. Historical events begin at cutover unless a
separate importer emits clearly marked migration facts; synthetic claims about
pre-cutover event history are forbidden.

## Adversarial decision matrix

These cases constrain the later implementation contract; Gate 2.9 itself makes
no runtime change.

| ID | Scenario | Required outcome |
|---|---|---|
| G29-01 | Gate 2.9 is merged without later durable-operation approval. | No runtime file, database, event, schema, CLI, migration, provider, Git, or storage behavior changes. |
| G29-02 | A process crashes between proposed current-state and event writes. | The single SQLite transaction exposes either both records or neither; a half transition is impossible. |
| G29-03 | A provider or writer is armed and the controller crashes during the external attempt. | The committed armed state remains authoritative; recovery follows existing capability-aware evidence and never implicitly repeats an uncertain writer. |
| G29-04 | A provider completes but result persistence is interrupted. | Resume classifies the armed operation from durable evidence; event absence grants no retry or success authority. |
| G29-05 | Two writers submit commands for the same run revision. | Exactly one `(run_id, state_revision)` transition commits; the other receives visible stale-revision/lock failure. |
| G29-06 | A UI, approval API, or provider tries to update tables directly. | Access is denied or rejected; only the controller-owned single-writer boundary can commit state/events. |
| G29-07 | An event type/display/model label is renamed. | Stable event, role, route, provider-adapter, and operation IDs preserve control meaning; display changes do not alter policy. |
| G29-08 | Event payload construction receives secrets, raw prompts, provider output, commands, or an unbounded diff. | Schema/redaction validation rejects or replaces the field with a bounded reference; sensitive raw data is not journaled. |
| G29-09 | An operator edits an event, deletes a row, or introduces a revision/hash gap. | Integrity diagnostics surface the exact bounded failure and execution blocks; no tamper-proof claim is made without an external anchor. |
| G29-10 | A current JSON run is imported twice, or imported after its source changes. | Same-hash import is idempotent; different-hash or identity collision fails closed without changing either authority. |
| G29-11 | JSON and SQLite copies exist during cutover and disagree. | The per-run authority marker selects at most one source; ambiguity or mismatch blocks execution and is never resolved by mtime. |
| G29-12 | A database-backed run is exported to legacy JSON and edited. | The export remains a read/materialized view; edits cannot mutate or override authoritative state. |
| G29-13 | Database schema, current payload schema, or event schema is newer than supported. | Inspection reports the version incompatibility; no implicit migration, downgrade, replay, or execution occurs. |
| G29-14 | `doctor` or dry-run targets absent or unsafe future database storage. | It reports readiness without creating, migrating, chmodding, locking, or opening a write-capable SQLite connection. |
| G29-15 | WAL, rollback, backup, temporary, or export artifacts have unsafe ownership, mode, links, or identity. | Private-storage validation rejects them before use; no fallback to a less protected path occurs. |
| G29-16 | A database transaction fails after a Git/provider side effect. | Saved armed state remains recoverable; no event is fabricated and existing approval, fingerprint, retry, and writer-recovery gates remain authoritative. |
| G29-17 | A proposal attempts to add replay, compaction, a queue, async gates, or UI as part of this ADR. | The work is rejected as out of scope and requires a separately approved contract in roadmap order. |
| G29-18 | A rollback is requested after the first database-backed transition. | Rollback is refused unless a tested reverse materialization preserves the exact revision and every control-relevant record. |

## Gate 2.9 evidence

This accepted ADR is a documentation-only decision based on clean synchronized
`main` at `2b63ac8370d902528c03d183487861ab7e6336ac`. No implementation or test
count is claimed. The architecture was approved on 2026-08-03 and published on
`origin/main` as commit `0790c425aa554d3dccadd86f2c054f5ea7094ea4`
(`Record event state architecture decision`).

- Relative Markdown links validated.
- All 18 `G29-*` matrix IDs are unique.
- Worktree scope is documentation-only.
- Tracked and new-file whitespace checks passed, including `git diff --check`.
