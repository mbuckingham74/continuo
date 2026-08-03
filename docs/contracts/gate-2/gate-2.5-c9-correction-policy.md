# Gate 2.5 / C-9 persisted correction/escalation policy contract (approved for implementation)

**Authority.** [ENGINE_ROADMAP.md](../../ENGINE_ROADMAP.md) remains authoritative. This document contains the bounded contract, adversarial matrix, and durable evidence for its tracker entry.

**Tracker.** See [EXECUTION_PLAN.md](../../EXECUTION_PLAN.md) for gate status, sequencing, and links to every contract.



**Status and boundary.** This note specifies only the next unchecked Gate 2
item. It is derived from authoritative [C-9 in the engine
roadmap](../../ENGINE_ROADMAP.md#c-9--correction-bound-and-schema-migration),
Milestone 1 item 4 and its exit criteria, the current correction policy in the
[README](../../../README.md#correction-and-escalation-policy), and the schema-9
model/controller/migration/report/test paths. It is a documentation-only
approval request. The repository owner approved implementation after reviewing
this contract. Implementation remains paused while the documentation
reorganization is reviewed; no runtime model, migration, fixture, test,
private run, target checkout, provider, commit, or push was changed as part of
that reorganization.

**Invariant and reproduced problem.** A run must make every correction,
escalation, budget, block, resume, and recovery decision under the immutable
policy it resolved when the run was created. At schema 9,
`WorkflowRun.correction_cycles` has a persistence-level `le=12` constraint,
while `MAX_TOTAL_CORRECTIONS = 12` and
`MAX_SOL_ESCALATIONS_PER_FINDING = 2` are module constants consulted by
`_review_and_correct()`, `_run_from()`, and `_resume_owned()`. The first,
second, third, and fourth consecutive occurrences are also encoded in those
branches rather than in saved state. A later code change can therefore alter
an unfinished schema-9 run's correction authority on ordinary resume; the
field bound also rejects a valid future policy before the saved policy can be
consulted. This violates the Milestone 1 requirement that a run resumes under
the policy it started with.

**Schema-10 and immutable-policy decision.** Gate 2.5 increments
`CURRENT_RUN_SCHEMA_VERSION` from 9 to 10 and adds a required
`resolved_correction_policy` field to every ordinary schema-10 `WorkflowRun`.
The field is an extra-forbid, strict, frozen `ResolvedCorrectionPolicy` model,
not a free-form configuration object, provider result, or human approval
record. Its closed version-1 shape is:

- `policy_id = "builtin.correction_escalation.v1"`;
- `maximum_total_corrections = 12`;
- `maximum_sol_escalations_per_persistent_finding = 2`; and
- `persistent_finding_actions = ("ordinary_correction",
  "sol_guided_correction", "sol_guided_correction", "block")`.

The model validates the four-action sequence and its consistency with the two
Sol rounds. It is the resolved, exact snapshot of the README policy: occurrence
one schedules an ordinary Luna correction; occurrences two and three obtain
Sol guidance then schedule a Sol-guided Luna correction; occurrence four blocks
for human attention. A future policy change must introduce a new closed policy
version and an explicitly approved resolver/migration contract; it must not
edit version 1, add a picker, or treat arbitrary integers or action lists as
configuration.

`correction_cycles` remains a strict non-negative persisted counter, but Gate
2.5 removes its `le=12` field constraint. For an ordinary schema-10 run,
cross-field validation instead requires
`correction_cycles <= resolved_correction_policy.maximum_total_corrections`.
The counter remains the number of correction slots durably reserved before a
Luna correction, including a writer attempt later requiring explicit recovery;
it is not redefined as a provider-call, successful-write, Sol-call, or metric
counter. This replaces the hard-coded persistence ceiling with the saved
policy's ceiling without redesigning metrics.

To preserve the fact that older records have no resolved policy, schema 10 also
has an extra-forbid, strict, frozen `PolicyMigrationAudit`. It contains
`migration_id`, `migrated_at`, `source_schema_version`,
`target_schema_version: 10`, `source_structural_class`, `source_sha256`,
`applied_steps` ending in `9_to_10`, bounded `reason_codes`, and the inherited
migration `disposition`. A migrated schema-10 record has
`resolved_correction_policy = null` and exactly this audit with the reason
`missing_resolved_correction_policy`; an ordinary schema-10 record has a
non-null policy and no policy audit. The model rejects every other combination,
including a policy audit on a new run, a missing policy without the immutable
audit, an edited audit lineage, or a policy/counter mismatch. `WorkflowRun`
remains mutable only for its existing stage/audit evolution; the nested policy
value itself is frozen, and no controller path replaces it.

**New-run resolution and control contract.** `Controller.new_run()` resolves
the sole built-in version-1 policy before the first durable save, target claim,
provider arm, or provider invocation, and persists it in the initial run
snapshot. Construction outside that path must supply the same validated
resolved policy; there is no implicit model default during load or resume.
Resolution does not read a configuration file, environment variable, CLI
option, model/display name, task prose, provider output, approval record, or
repository content.

All correction decisions use the saved policy through one deterministic
policy-evaluation helper. The helper receives the saved policy, the persisted
correction count, and the parsed implementation-review finding streak; it
returns exactly `ordinary_correction`, `sol_guided_correction`, or `block`.
It checks the global budget immediately before reserving any correction and
uses the saved finding-action sequence for the per-finding result. No control
branch, error text, prompt selection, or resume condition may consult
`MAX_TOTAL_CORRECTIONS`, `MAX_SOL_ESCALATIONS_PER_FINDING`, a literal `12`, or
the current README wording. The obsolete constants are removed rather than
retained as a second authority.

For an ordinary action, the controller clears stale Sol guidance, increments
the counter once, saves `correction_pending`, and then follows the existing
writer-attempt protocol. For a Sol-guided action, it persists the existing
`sol_escalating` arm and guidance flow first; after successful guidance,
`sol_guidance_ready` re-evaluates the same saved policy and reserves exactly
one correction before `correction_pending`. A policy block uses the existing
bounded blocked stages/messages, but derives the capacity and action from the
saved snapshot. `POLICY_AMBIGUITY`, read-only retry/content-retry behavior,
writer capability recovery, target ownership, verification, and human Git
gates remain unchanged.

**Resume, recovery, and crash behavior.** Every continuation path reads the
loaded run's policy, never a resolver result: `_review_and_correct()`,
`_run_from()` at `sol_guidance_ready`, `_resume_owned()` for the historical
blocked-after-correction/escalation compatibility stages, and exact-stage
recovery after a saved review, Sol result, or writer result. Those paths must
not reserve another correction merely because a process crashed after the
reservation save. A crash before initial run persistence creates no resumable
run; a crash after it leaves a run with its policy already fixed. A crash after
Sol guidance but before the correction reservation resumes from
`sol_guidance_ready` and applies the saved budget once. A crash after the
reservation but before/during Luna retains the existing `correction_pending` /
writer-recovery behavior and preserves both the policy and already reserved
count. A malformed current schema-10 policy, missing policy, policy/audit
contradiction, or counter exceeding the saved maximum fails validation before
target coordination, provider work, correction, status-as-current, or Git.

**Exact historical migration treatment.** Add an exact `_RunV9` historical
validator retaining schema 9's `correction_cycles <= 12` contract and its
review/identity/audit shape, one pure adjacent `9_to_10` transform, and the
single `(9, 10)` registry entry. `1_to_2` through `8_to_9` retain their literal
historical version constants and behavior. The transform copies every supplied
field value and every earlier audit value byte-for-value in its decoded form;
it sets no policy values, does not translate the former constants into a
claimed snapshot, does not reinterpret raw review/provider text, and does not
change stage, correction count, guidance, policy decisions, route identities,
or disposition. It appends only the null policy field and immutable policy
migration audit described above.

All V1--V9 records therefore migrate through the existing explicit,
default-no-confirmation, private atomic compare-and-swap command into
schema-10 records that remain execution-refused under their inherited
disposition. A V9 source takes only `9_to_10`; earlier sources retain their
legacy, identity, and review audits and gain a policy audit whose lineage has
the same migration ID/time/source/hash/disposition and whose `applied_steps`
are the complete chain ending `9_to_10`. V10 passed to `migrate-run` is already
current and is never rewritten. Failure before replacement, source change,
concurrent migration, or final validation failure preserves the original;
there is no downgrade, policy backfill, eligibility override, bulk rewrite,
backup/archive/delete path, or automatic historical resume.

**Reporting, status, compatibility, and rollback.** Complete sensitive
`status <run-id>` JSON exposes the saved policy or the bounded policy-audit
absence reason under the existing privacy warning. `report` continues to
present the existing correction, Sol escalation, and policy-decision metrics;
it adds a concise policy line identifying the saved policy ID, total-correction
usage/capacity, per-finding Sol capacity, and four-action sequence. A migrated
record continues to receive the existing bounded classification/refusal rather
than a fabricated policy report. This is presentation of an existing run fact,
not versioned JSON, a new metric definition, or a doctor/dry-run interface.

Root and installed CLI names/options, `jobs-orchestrator`, `JOBS_REPO`,
`src/jobs_orchestrator`, Jobs task resolution, provider commands, route
identities, fake-provider injection, retry/deadline behavior, ownership,
storage permissions, and Git gates remain compatible. The existing migration
command supplies the only rollback guarantee: it is atomic before replacement
and leaves an original record intact on every pre-replace failure. Gate 2.5
adds no reverse migration or mutable policy edit/reset command.

**Adversarial test matrix.** Tests use only committed synthetic V1--V9
records, in-memory derivatives, temporary private run directories/repositories,
injected clocks/IDs/failures, fake providers, and local child processes. They
do not inspect private runs, access Jobs, invoke a live provider, use an
external target, commit, or push. Every identifier below is unique to Gate 2.5.

| ID | Fixture / event | Required assertions |
|---|---|---|
| G25-I1 | Static model/source inspection | Schema is exactly 10; `ResolvedCorrectionPolicy` and `PolicyMigrationAudit` are strict, frozen, extra-forbid models; ordinary runs require the sole built-in v1 snapshot; `correction_cycles` has no `le=12`; no obsolete correction constants or literal-policy control branch remains. |
| G25-I2 | Attempt to mutate the nested policy, add an unknown field, alter its action sequence, or persist an arbitrary capacity | Validation/freezing fails; no generic config, environment, CLI, provider, task, label, or approval text can select policy behavior. |
| G25-I3 | New run is created, including a dirty-repository block before provider work | The initial durable record is schema 10 with the exact policy and no policy migration audit before target/provider work; the same snapshot survives the dirty block. |
| G25-I4 | Construct/load ordinary V10 with missing/null policy, a policy audit, a counter above capacity, or mismatched policy/audit lineage | It fails closed before current classification, coordination, provider work, correction, reporting-as-current, or Git. |
| G25-P1 | First occurrence of one parsed implementation finding | Saved policy evaluates to ordinary correction; stale Sol guidance clears; count reserves once; Luna receives no Sol guidance. |
| G25-P2 | Second consecutive occurrence of the same key | Saved policy starts Sol round 1, then exactly one Sol-guided correction; count reserves once only after saved guidance. |
| G25-P3 | Third consecutive occurrence of the same key | Saved policy starts Sol round 2, then exactly one final Sol-guided correction; no third Sol escalation occurs. |
| G25-P4 | Fourth consecutive occurrence of the same key | Run blocks for human attention without Sol or Luna invocation and without incrementing the counter. |
| G25-P5 | Different finding follows one or more prior persistent findings | The parsed-history streak resets; the new finding receives its ordinary action while the global saved budget remains shared. |
| G25-P6 | Twelve distinct findings with successful corrections, then a thirteenth | Exactly twelve correction reservations occur; the thirteenth blocks on the saved global capacity before Sol/Luna; no field-level validation exception substitutes for the policy block. |
| G25-P7 | Saved counter is already at capacity at an implementation review and at `sol_guidance_ready` | Both paths block from the saved policy before a new correction reservation, Sol repeat, or Luna call. |
| G25-P8 | PASS, POLICY_AMBIGUITY, specification review, unreadable marker, and transport/content retry | Existing non-correction behavior remains; only parsed implementation findings affect the policy streak; retries do not reserve corrections. |
| G25-R1 | Resume `implementation_reviewed` after an ordinary finding | It evaluates the loaded policy, not current module values/resolution, and schedules the same next action. |
| G25-R2 | Resume `sol_guidance_ready` after the saved Sol result | It uses the loaded capacity/action once, reserves one correction once, and preserves the saved guidance. |
| G25-R3 | Resume legacy compatibility stages `blocked_after_correction` and `blocked_after_escalation` with a new versus persistent parsed finding | Re-entry uses the saved policy for both global and per-finding decisions; no current hard-coded limit changes the outcome. |
| G25-R4 | Crash after initial V10 persistence, after Sol result, after correction reservation, during a writer attempt, and after writer-result persistence | Policy and count round-trip unchanged; recovery neither resolves a replacement policy nor double-reserves/repeats Luna; existing writer recovery blocks remain authoritative. |
| G25-R5 | Crash before initial persistence or corrupt/missing current policy bytes | No executable run is created/continued; bounded validation/classification failure exposes no source prose. |
| G25-M1 | Registry/validator inspection | Exact V9 historical model, exactly one `9_to_10` step, and a single registry entry exist; `1_to_2` through `8_to_9` and their literal historical constants are unchanged; all intermediate validators run. |
| G25-M2 | Ordinary V9 source with count 0, 1, and 12; V9 source at correction/escalation/block stages | Migration preserves each value/stage/guidance/review/provider/policy-decision field; it writes null policy plus the bounded missing-policy audit; migrated execution remains refused. |
| G25-M3 | V1--V8 synthetic fixtures migrate through V10 | Existing audits and all prior values are preserved; policy audit shares exact lineage and full step prefix ending `9_to_10`; no policy is invented and final disposition is inherited. |
| G25-M4 | V9 source with malformed/unknown policy-like extra data, out-of-bound count, invalid earlier audit, or incoherent history | Strict V9 classification archives/refuses it before transform; migration never drops/normalizes the bytes or grants policy authority. |
| G25-M5 | Already-current ordinary V10 and migrated V10 passed to `migrate-run` | Both report already current with no rewrite, new timestamp, new audit, policy substitution, or eligibility change. |
| G25-M6 | Transform/final-validation/temp-write/CAS/source-change/concurrent/crash failure cases | Existing atomic rollback behavior holds: original bytes/inode protections remain until replace; one complete V10 survives post-replace; rerun is idempotent. |
| G25-L1 | `report` and `status <run-id>` on an ordinary V10 run | Status exposes the saved policy under the existing warning; report renders policy ID, use/capacity, Sol capacity, and schedule while preserving all existing metric meanings. |
| G25-L2 | `status`, `report`, `resume`, and `migrate-run` on V1--V9, migrated V10, archive-only, unsupported, and malformed V10 records | Existing bounded classification/refusal behavior remains; absent historical policy is visible only as its bounded audit reason and never defaults to today's policy. |
| G25-C1 | Current route/display/model names are renamed or duplicated while a saved V10 policy run resumes | Correction/escalation action is unchanged because only the saved policy, parsed finding keys, and counter control it. |
| G25-C2 | Current source changes the built-in resolver after a run was saved | Resume/recovery still uses the serialized v1 policy and takes the original action; resolver is called only for new runs. |
| G25-C3 | Sol emits POLICY_AMBIGUITY or provider failure at either Sol round | Existing policy-authority/provider block behavior remains; no correction slot is reserved after an unsuccessful/ambiguous Sol result. |
| G25-C4 | Writer recovery is restored/adopted after a saved correction reservation | Counter, saved policy, finding history, guidance, provider retry rules, and ownership evidence remain unchanged except for the existing recovery audit. |
| G25-B1 | Scope inspection | No approval request/decision redesign, metric redesign, versioned JSON, doctor, dry-run, event ADR, generic adapter/config, route picker, policy edit API, or later Milestone implementation is added. |
| G25-B2 | Authority/compatibility inspection | Provider commands/capabilities, retries, deadlines, ownership, verification, storage, human Git authority, root CLI, `jobs-orchestrator`, `JOBS_REPO`, and `src/jobs_orchestrator` remain unchanged; no Jobs or live-provider access occurs. |
| G25-B3 | Full deterministic suite and installed-package smoke checks | Existing and new tests pass using fakes/fixtures only; Python compilation, root/installed help/import, link/matrix checks, and macOS installed validation with `UV_NO_EDITABLE=1` pass. |

**Explicit exclusions and later-gate boundaries.** Gate 2.5 does not add or
redesign approval records, metrics, versioned JSON, `doctor`, dry-run, an
event/state ADR, generic configuration or adapters, policy files, policy
overrides, model/route selection, provider discovery, verification plugins,
isolated workspaces, queues, telemetry, UI, or a policy-management command. It
does not change parsed review history, approval/Git authority, provider
commands, retries, deadlines, capabilities, writer recovery, target ownership,
storage behavior, compatibility identifiers, or historical execution refusal.
Later configuration work may add a separately approved versioned policy
resolver; it must preserve this run-snapshot contract rather than reinterpret
existing records.

**Exit criteria for approval and later implementation:** the repository owner
approves the schema-10 policy/audit shape, exact no-inference historical
treatment, policy evaluator, new-run resolution point, resume/recovery/crash
rules, reporting line, compatibility/rollback boundary, exclusions, and every
unique matrix row. Implementation may begin only after that approval; this
documentation diff itself must first pass link, matrix-uniqueness, and
`git diff --check` validation.

**Owner decision.** No decision is intentionally deferred: the approved
contract fixes the current policy exactly as published and makes historical
policy absence non-executable, including the schema-10 null-policy-plus-audit
treatment rather than inferred backfill.

- [ ] Add immutable approval request and decision records with identity,
  timestamps, decision text, and repository fingerprints.
- [ ] Correct logical-call/physical-attempt and verification metric semantics.
- [ ] Add versioned machine-readable CLI output, `doctor`, and a genuinely
  read-only dry-run contract.
- [ ] Write the event/state architecture ADR without implicitly beginning an
  event-sourced rewrite.

**Exit criteria:** route display changes cannot alter policy; resume uses the
saved policy and schema; migrations have success and failure-path tests; and all
control-relevant provider results have durable parsed representations.
