# Gate 2.4 / C-6 immutable parsed review history contract

**Authority.** [ENGINE_ROADMAP.md](../../ENGINE_ROADMAP.md) remains authoritative. This document contains the bounded contract, adversarial matrix, and durable evidence for its tracker entry.

**Tracker.** See [EXECUTION_PLAN.md](../../EXECUTION_PLAN.md) for gate status, sequencing, and links to every contract.

## Tracker evidence

- Planning evidence (2026-08-02): the Gate 2.4 contract and adversarial
    matrix below were derived from authoritative C-6, the completed Gate 2.3
    schema-8 boundary, and every current review re-parse path
    (`_implementation_review_history`, `_report_review_history`,
    `_current_finding_streak`, `_sol_prompt`, `_review`, and exact-stage
    recovery). The inspected baseline is clean `main` aligned with
    `origin/main` at `51f71b169b0684a43ce1a31a65cb4f7ae4c16bff`. This planning
    pass changes only this tracker. The draft contract awaits repository-owner
    review; no runtime model, migration, provider, fixture, test, private run,
    target checkout, Git operation, commit, push, or later Gate 2 item changed.
  - Approval evidence (2026-08-02): the repository owner approved the Gate 2.4 /
    C-6 contract below for implementation, including the schema-9 shape, exact
    backfill mapping, separate immutable review-migration audit, migrated-run
    execution refusal, unreadable-history visibility, read/write paths,
    crash/retry behavior, CLI presentation, and later-item exclusions.
  - Implementation evidence (2026-08-02): the uncommitted Gate 2.4 diff adds the
    immutable `ReviewRecord`/`UnreadableReviewRecord`/`ReviewMigrationAudit`
    models, schema 9, atomic parsed-record writes in `_review` and exact-stage
    recovery (consume-or-reparse-once, unreadable markers on failure), parsed-only
    history/streak/Sol/report control with an explicit unreadable report section,
    the exact historical V8 model, and the adjacent `8_to_9` transform that is
    the sole interpreter of legacy review stdout. Raw stdout remains full-
    fidelity audit data and is never an identity or result oracle; provider
    commands, deadlines, retry policy, capability ceilings, target ownership,
    Git gates, prompts beyond the review-history source, and compatibility
    identifiers are unchanged.
  - Validation evidence (2026-08-02): all 174 deterministic tests pass using
    temporary repositories, synthetic versioned records, recorded fixtures,
    fake providers, and local child processes. Coverage includes all 40 Gate 2.4
    matrix rows across static model/source inspection, raw-stdout independence,
    operation filtering, fail-closed links and current-field contradictions,
    atomic write snapshots, transport/content retry linkage, crash-before/after-
    raw-save recovery for both review stages, persisted-record consumption,
    re-parse failure markers, streak/escalation sequences, unreadable visibility
    in report and status, prompt rendering, V1--V8 migration through schema 9,
    prior-audit preservation, bounded backfill markers, and later-gate
    boundaries. Python compilation, root and installed CLI help,
    compatibility import, documentation links/matrix checks, and
    `git diff --check` pass with `UV_NO_EDITABLE=1` for installed-package
    validation. No Jobs checkout, private run, live provider, network service,
    external target, commit, push, or later Gate 2 item was used.
  - Review decision (2026-08-02): the repository owner reviewed the complete
    uncommitted implementation diff and approved it for publication. The owner
    explicitly authorized commit, direct push to `origin/main`, and this tracker
    update.
  - Publication evidence (2026-08-02): implementation commit
    `828c710` (`Implement Gate 2.4 immutable parsed review history`) was pushed
    directly to `origin/main` with all 174 deterministic tests passing.



**Status and boundary.** This note specifies the immediately following unchecked
Gate 2 item: persist an immutable parsed review record for every successfully
validated Sonnet review, linked to its exact raw physical attempt, and make
unreadable legacy review history visible and non-silent instead of silently
dropping it from control calculations. The repository owner approved this
contract and its adversarial matrix for implementation on 2026-08-02. The
bounded runtime implementation is complete and published on `origin/main` as
commit `828c710`.

This item is the direct successor of Gate 2.3. It builds on the approved
`adversarial_review` role and the `specification_review`/`implementation_review`
operation IDs instead of introducing a new purpose string. It does not implement
the generic provider-adapter or configuration work, and it does not change
provider commands, deadlines, retry policy, capability ceilings, target
ownership, Git gates, prompts beyond the review-history source, or the
compatibility identifiers.

**Invariant and current evidence.** A raw stdout change, an envelope upgrade, a
schema change, or truncated output must not change which review results feed
correction streaks, Sol escalation, Sol prompts, or report defect counts; a run
whose review history is partially unreadable must say so and must never silently
lose history from policy calculations. At baseline
`51f71b169b0684a43ce1a31a65cb4f7ae4c16bff`, those decisions still re-parse raw
stdout on every call and swallow every failure:

- `_implementation_review_history()` (orchestrator.py:1657) rebuilds finding
  history by re-parsing each `adversarial_review`/`implementation_review`
  provider record inside `except Exception: continue`; unparseable records
  silently disappear. It feeds `_current_finding_streak()` (orchestrator.py:1691,
  which decides ordinary correction versus Sol escalation) and `_sol_prompt()`
  (orchestrator.py:1701, recent-finding history);
- `_report_review_history()` (orchestrator.py:1324) uses the same re-parse for
  the report's `distinct_defects` count; unparseable records are silently
  absent;
- `_review()` (orchestrator.py:2385) parses at validation time and stores only
  the single current fields `spec_review`/`implementation_review`
  (models.py:361-362) — no immutable history is persisted; and
- exact-stage recovery for `spec_reviewing`/`reviewing` (orchestrator.py:3214-
  3245) re-parses the last raw record to reconstruct state after a crash; a
  changed envelope at that point is a silent, untested failure surface.

The existing `ReviewResult` (models.py:99) is the closed parsed vocabulary. The
problem is that parsed results are never durably linked to the raw attempts that
produced them and are reconstructed by re-parsing instead.

**Persisted model and schema version.** This is a structural persisted-model
change and therefore increments `CURRENT_RUN_SCHEMA_VERSION` from 8 to 9.
New version-9 records use two immutable, extra-forbid, strict closed models plus
a separate immutable migration audit:

- `ReviewRecord` contains `recorded_at`, an `operation_id` limited to the
  approved `specification_review`/`implementation_review` vocabulary, the
  parsed `result: ReviewResult`, and `provider_record_index` linking to the
  exact raw `ProviderRecord`. The intake's free-form `purpose` string is not
  used: the roadmap forbids replacing one overloaded string with another, and
  Gate 2.3 already owns that vocabulary;
- `UnreadableReviewRecord` contains `recorded_at`, the same bounded
  `operation_id`, `provider_record_index`, and a bounded reason code from
  `invalid_review_envelope`, `invalid_review_schema`,
  `invalid_review_semantics`, or `unreadable_legacy_review`; and
- `ReviewMigrationAudit` is immutable and records `migration_id`, `migrated_at`,
  `source_schema_version`, `target_schema_version: 9`, `source_structural_class`,
  `source_sha256`, `applied_steps` (ending `8_to_9`), bounded `reason_codes`,
  `parsed_count`, `unreadable_count`, and the inherited
  `disposition`. `RunStructuralClass` gains `V8`; `LegacyRunStructuralClass`
  stays unchanged.

`WorkflowRun` gains `review_records`, `unreadable_review_records`, and
`review_migration_audit`. The existing `spec_review`/`implementation_review`
fields remain the workflow's current-review state; the new lists are the
immutable history. Raw commands, stdout/stderr, failure provenance,
fingerprints, and prompts remain full-fidelity sensitive audit data in
`provider_runs`.

Version-9 cross-field validation requires, with no display or raw-text
comparison anywhere in the rules:

- every `ReviewRecord` and `UnreadableReviewRecord` index is in range and the
  referenced record has role `adversarial_review`, the matching operation ID,
  `returncode == 0`, and no `failure_kind` (only successful attempts are review
  history; failed transport attempts are already visible as provider failure
  evidence);
- each provider record index appears at most once across both review lists
  (parsed and unreadable are mutually exclusive);
- for ordinary version-9 runs, each review operation's current field equals the
  last parsed record's result for that operation, and is `None` when no parsed
  record exists; and
- for migrated runs, a preserved current field may disagree with the backfilled
  history only when the review audit records a bounded reason
  (`current_review_unreadable` or `resume_review_field_absent`), and the run
  remains execution-blocked.

**Write contract.** New runs are version 9 with empty review lists and no audit.

- `_review()` (both purposes) appends one immutable `ReviewRecord` at the
  moment `parse_sonnet_review` succeeds — on the initial parse or on the
  content-retry parse — in the same atomic run snapshot that sets the current
  field and the stage transition. The record links the exact final physical
  attempt that produced the validated stdout (retry attempts persist their own
  raw audit records and yield no parsed record);
- the parsed record is never derived from command text, stdout/stderr prose,
  model names, or display names;
- a transport-failed attempt persists a raw record only and never produces a
  review record or an unreadable marker in current runs;
- a crash after the raw save but before the parsed save leaves the run armed at
  `spec_reviewing`/`reviewing`; recovery consumes the matching raw record once
  (see crash behavior) and never re-appends; and
- `_retry_structured_once()` keeps its single same-route content retry; the
  parsed record links the retry attempt's index.

**Read and control paths.** All history-derived control moves to parsed records:

- `_implementation_review_history()` and `_report_review_history()` derive from
  `run.review_records` filtered by `implementation_review`; no raw re-parse
  remains in any history, streak, escalation, prompt, or report path;
- `_current_finding_streak()` and `_sol_prompt()` consume parsed records only;
- the report's `distinct_defects` uses parsed records, and the report gains an
  explicit non-silent unreadable section: count plus bounded indices and reason
  codes rendered as `N unreadable review record(s)`, with no effect on defect,
  streak, or escalation values;
- `status <run-id>` prints the complete sensitive JSON for an ordinary current
  record, now including review records, unreadable markers, and the review
  audit; historical records receive bounded classification until explicitly
  migrated; and
- no stable `--json` report/status schema, `doctor`, picker, or dry-run behavior
  is introduced.

**Crash, retry, and recovery behavior.** Exact-stage recovery for
`spec_reviewing`/`reviewing` must consume the persisted parsed record for the
matching armed identity and last raw record when it exists, without re-parsing
or provider invocation; when it does not exist, recovery re-parses the matching
raw record exactly once, appends the parsed record plus current field and stage
in one atomic snapshot, and clears the armed identity. A re-parse failure at
that boundary appends an `UnreadableReviewRecord` and blocks
(`blocked_provider_output`) with the raw audit retained; it never silently
continues, re-queues, or treats the record as PASS. Transport retries, the one
content retry, timeout/interruption, provider-error, and writer-state blocks
keep their existing semantics.

**Stepwise migration and historical treatment.** Add an exact historical
version-8 model and one adjacent `8_to_9` transform. The `1_to_2` through
`7_to_8` functions remain unchanged; `_step_6_to_7` and `_step_7_to_8` keep
their literal historical version constants. The `8_to_9` step is the sole place
where legacy review stdout may be interpreted. Backfill policy, applied to each
`adversarial_review` record with a matching operation ID:

- `returncode != 0` or a `failure_kind` → not review history; no record, no
  marker (already visible as provider failure evidence);
- otherwise the current parser validates the raw stdout once: success produces
  exactly one `ReviewRecord`; failure produces exactly one bounded
  `UnreadableReviewRecord`; nothing is defaulted, invented, or dropped; raw
  bytes are preserved unchanged;
- preserved `spec_review`/`implementation_review` fields are kept value-for-
  value; a field with no parseable counterpart records
  `current_review_unreadable`; a parsed record with no preserved field records
  `resume_review_field_absent`;
- the audit's `parsed_count`/`unreadable_count` must equal the produced list
  lengths and its disposition must equal the inherited classification
  disposition; and
- `migrate_classification()` verifies the review audit's lineage exactly like
  the identity audit (matching `migration_id`, `migrated_at`, source version,
  structural class, SHA-256, and `applied_steps` ending `8_to_9`, with
  `identity_migration_audit.applied_steps == review steps[:-1]` when the source
  is version 7 or older) and verifies prior audits are preserved value-for-value
  for version-8 sources.

Migrated records remain `resume_eligibility_deferred`, `resume_blocked`, or
`inspection_only`, and every controller mutation rejects them before target
coordination or side effects. The migration adds no eligibility-clear command,
no parsed-history repair command, and no automatic backfill: a direct
V1--V8 chain carries the unchanged Gate 2.2 audit (through 7), the unchanged
Gate 2.3 identity audit (through 8), and the new review audit (through 9);
a version-8 ordinary record has only the review audit; new version-9 runs have
none. Atomic compare-and-swap, default-no confirmation, private temporary
replacement, rollback-before-replace, concurrency, crash, idempotency, bounded
diagnostics, and no-private-record validation retain the Gate 2.3 contract.

**Backward compatibility and read paths.** Root and installed CLI names,
`jobs-orchestrator`, `JOBS_REPO`, `src/jobs_orchestrator`, command names/options,
Jobs task resolution, and injectable fake-provider callables remain available.
Historical JSON compatibility is provided only through strict classification and
explicit migration; version 9 does not accept legacy label fields as ordinary
current-model aliases and does not re-parse raw stdout for control state.

**Adversarial test matrix.** Tests use synthetic versioned records, committed
historical fixtures, temporary private run directories/repositories, fake
providers, injected clocks/IDs/confirmation, and existing local child-process
coverage. No private run, Jobs path, live provider, network service, external
target, commit, or push is used.

| ID | Fixture / event | Required assertions |
|---|---|---|
| I1 | Static model and source inspection | `ReviewRecord`/`UnreadableReviewRecord`/`ReviewMigrationAudit` are immutable closed models using the approved operation subset; schema is exactly 9; no legacy control fields exist; no raw re-parse remains in history, streak, escalation, or report paths. |
| I2 | One parsed record's raw stdout is edited, truncated, or replaced | History, streaks, escalation, and report defect keys are unchanged; raw bytes remain audit-only and are never an identity or result oracle. |
| I3 | Review records mix specification and implementation operations | Only `implementation_review` feeds finding streaks, Sol history, and defect counts; specification records are visible but excluded without any label or text comparison. |
| I4 | Review record links a non-review or mismatched-operation record, duplicates an index, or references a failed attempt | Version-9 validation fails closed before any control use, prompt, or reporting. |
| I5 | Ordinary run's current field contradicts its last parsed record, or is set with no parsed record | Validation rejects the run before any mutation; migrated equivalents require an audit-recorded bounded reason and stay blocked. |
| P1 | New run is constructed | Schema is exactly 9; both review lists are empty; no migration, identity, or review audit exists; no legacy control field exists. |
| P2 | Each successful spec and implementation review returns one physical result | Exactly one parsed `ReviewRecord` is persisted in the same atomic snapshot as the current field and stage; the link is the exact final attempt index; command/model prose is never inspected. |
| P3 | Read-only transport retry creates three physical attempts | Only the successful attempt yields a parsed record; failed attempts persist raw audit only; no unreadable markers are invented for current runs. |
| P4 | Invalid structured output receives one content retry then succeeds | The parsed record links the retry attempt exactly once; the first attempt remains raw-only; no duplicate record exists. |
| P5 | Crash after raw save but before parsed save | Resume consumes or re-parses the matching record once and persists the parsed record, current field, and stage in one atomic snapshot; no provider re-invocation and no duplicate. |
| P6 | Persist/load full current identity and review state | All review records, unreadable markers, audits, raw attempts, pending identity, and unrelated fields round-trip exactly. |
| M1 | Migration registry and historical validators are inspected | `1_to_2` through `7_to_8` remain unchanged; `6_to_7`/`7_to_8` keep literal constants; the exact V8 historical model and exactly one adjacent `8_to_9` step exist; every intermediate validates. |
| M2 | V8 ordinary record is backfilled | Each successful review-role attempt maps to exactly one parsed record or one bounded unreadable marker; raw bytes are unchanged; `parsed_count`/`unreadable_count` match; execution stays refused. |
| M3 | V1--V8 fixtures migrate through version 9 | Ordered prior transforms and values remain intact; legacy and identity audits are preserved value-for-value; the review audit is appended with coherent lineage and disposition. |
| M4 | V8 record already carrying an identity audit | Identity audit is byte-for-byte unchanged; the review audit is appended separately; no double audit; execution remains blocked. |
| M5 | Current V9 source is passed to `migrate-run` | Command reports already current without rewrite, timestamp change, or second review audit. |
| M6 | Unreadable legacy review (truncated stdout, changed envelope, invalid result semantics) | Bounded unreadable marker with a closed reason code; no result is invented, defaulted, or dropped; status exposes the marker. |
| M7 | Legacy retry pairs and failed attempts | Transport-failed attempts produce neither records nor markers; only successful attempts backfill; the final content-retry success maps once. |
| M8 | Preserved current field disagrees with backfilled history | Mismatch is recorded as `current_review_unreadable` or `resume_review_field_absent` in the review audit; no field is overwritten; the run remains blocked. |
| M9 | Backfill index/operation incoherence or audit-count mismatch | Migration fails closed before any write; no partial, invented, or duplicated review history. |
| C1 | Review display/model is renamed or duplicated on current records | Streaks, escalation, and report keys are unchanged; no label or text comparison exists in control flow. |
| C2 | Spec and implementation reviews interleave across cycles | Finding streak counts `implementation_review` records only, in physical-record order. |
| C3 | Crash recovery's last record has the right identity but the parsed record is missing and re-parse fails | Recovery blocks with a visible unreadable marker and the raw audit retained; no silent continue. |
| C4 | Crash recovery where the parsed record already exists | The persisted record is consumed once without re-parse or provider invocation. |
| C5 | Same finding across correction cycles, then a new finding | Streak increments on parsed history only, resets on a new key, and triggers the two-Sol escalation sequence exactly; unreadable markers never affect counts. |
| C6 | Unreadable marker present in a current run's history | Report shows count, indices, and reasons; defect, streak, and escalation values are unaffected; status JSON exposes the markers. |
| C7 | Sol and review prompts render parsed summaries only | No raw stdout appears in prompt text; unreadable markers render as explicit unreadable entries, never as findings. |
| R1 | Read-only `unavailable` attempt retries | Same role/adapter/route/model/operation is retained for the 5/15-second sequence; failed attempts persist raw audit only. |
| R2 | Invalid Sonnet structured output receives the one content retry | Retry uses the same stable route identity and operation; the parsed record links the retry attempt; content and transport retry remain mutually exclusive. |
| R3 | Crash before raw save | Armed identity only; no parsed record; recovery blocks without invocation. |
| R4 | Crash after raw save before parsed save, for both spec and implementation stages | Exact-stage recovery re-parses the matching record once and persists parsed record, current field, and stage atomically. |
| R5 | Crash after parsed save | Recovery consumes the persisted record without re-parse or provider invocation; no duplicate record. |
| R6 | Re-parse failure during recovery | `blocked_provider_output` with an unreadable marker; never silently continues or re-queues. |
| L1 | Human `report` and `status <run-id>` inspect an ordinary V9 run | Report aggregates parsed reviews and renders unreadable counts separately; status exposes complete sensitive identity/review JSON under the existing privacy warning. |
| L2 | `status`, `report`, `resume`, and `migrate-run` inspect V8, migrated V9, archive, and unsupported records | Existing bounded classification/refusal behavior remains; only explicit approved migration writes and no migrated record executes. |
| L3 | Root and installed help/import smoke checks run with `UV_NO_EDITABLE=1` on macOS | Commands/options remain aligned; `jobs-orchestrator`, `JOBS_REPO`, and `src/jobs_orchestrator` are unchanged. |
| B1 | Source/diff boundary inspection | No configuration file, provider adapter interface, model catalog discovery, picker, route override/migration, capability discovery, or automatic fallback is added. |
| B2 | Persisted-control boundary inspection | Resolved correction/escalation policy, approval records, logical-call metrics, versioned JSON, doctor/dry-run, and event/state ADR remain untouched. |
| B3 | Authority and scope inspection | Deadlines, retries, writer recovery, target ownership, verification, commit/push/merge gates, storage privacy, and Jobs compatibility behavior do not change. |
| B4 | Complete deterministic validation | No private record migration, Jobs access, live provider, network service, external target, commit, push, or later-gate implementation occurs. |

**Explicit exclusions and later-gate boundaries.** Gate 2.4 does not persist the
resolved correction or escalation policy; that remains the following Gate 2 item
and the migrated-run execution refusal stays in force. It does not add approval
request/decision identity, correct logical-call/physical-attempt or verification
metric semantics, add versioned machine output, implement `doctor` or dry-run,
or decide the event/state architecture. It also does not create generic
configuration, provider adapters, model catalogs, a picker, route precedence,
resolved routing-table hashes, audited route migration, capability discovery,
startup authentication checks, project or task adapters, generic package/CLI
aliases, verification plugins, isolated workspaces, event sourcing,
asynchronous gates, telemetry, or UI. It does not change provider commands, live
models, permission ceilings, retry delays, deadlines, prompts beyond the
review-history source wording, human policy or Git authority, target ownership,
storage modes, retention/export behavior, or the compatibility identifiers
`jobs-orchestrator`, `JOBS_REPO`, and `src/jobs_orchestrator`. It does not add
parsed records for Sol guidance or Terra resolutions, which are not review
history. Raw stdout remains full-fidelity audit data and is never redacted,
exported, or reparsed for control state.

**Exit criteria:** the repository owner approves the parsed-record model,
schema-9 shape, exact backfill mapping, separate immutable review-migration
audit, migrated-run execution refusal, unreadable-history visibility, read/write
paths, crash/retry behavior, CLI presentation, and later-item exclusions; every
matrix row has deterministic coverage; all existing and new tests pass using
fixtures and fakes only; macOS installed-package validation uses
`UV_NO_EDITABLE=1`; documentation and compatibility checks plus
`git diff --check` pass; the complete implementation diff is reviewed; and no
subsequent Gate 2 or Gate 3 item begins. The owner approved the contract for
implementation on 2026-08-02 and authorized publication; the implementation
commit `828c710` is pushed to `origin/main` with all 174 deterministic tests
passing, and this tracker update records that evidence.