# Gate 2.7 / Q-7 and Q-8 provider-metric semantics contract (published)

**Authority.** [ENGINE_ROADMAP.md](../../ENGINE_ROADMAP.md) remains authoritative. This document contains the bounded contract and adversarial matrix for its Q-7/Q-8 tracker entry.

**Tracker.** See [EXECUTION_PLAN.md](../../EXECUTION_PLAN.md) for gate status and sequencing.

## Planning evidence

- Planning evidence (2026-08-02): this draft is derived only from the roadmap [quick-hit decisions](../../ENGINE_ROADMAP.md#5-quick-hit-triage), Milestone 1 item 6 and exit criteria, the next unchecked tracker item, and the current `ProviderAttempt`, `ProviderExecution`, `ProviderRecord`, `_run()`, `_record_provider()`, resume, structured-output retry, report, migration, and deterministic-test paths. Baseline: clean `main` aligned with `origin/main` at `6b29413dae558b4464408e4477adc161c377574a`.
- Documentation-only validation evidence is recorded after the matrix. No runtime source, test, fixture, run record, provider, target checkout, Git side effect, commit, push, or later Gate 2 item is changed by this planning diff.

## Status and boundary

This is the next unchecked Gate 2 item after published Gate 2.6. It gives persisted provider activity an unambiguous logical-invocation identity, keeps every subprocess start as a physical attempt, reports both units separately, and renames the existing successful-writer proxy so it cannot be mistaken for verification execution.

This gate does not add real verification execution records. It does not add versioned `--json`, `doctor`, dry-run, an event/state ADR, generic configuration, adapters, route selection, verification plugins, new provider commands, or a public metrics API. Those remain later roadmap work.

## Invariant and current evidence

At `6b29413`, `_run()` may perform as many as three bounded read-only subprocess attempts for one provider invocation. `_record_provider()` appends one `ProviderRecord` per attempt, but `_run_report()` labels `len(provider_runs)` and per-role physical-attempt counts as provider “calls.” It also labels successful implementation/correction provider attempts as `verification_runs`, although those records prove only that a writer provider returned successfully; they do not prove that deterministic verification executed.

Every current provider record must belong to exactly one durable logical invocation and have an exact physical-attempt ordinal within it. Reports must never count a retrying subprocess as an additional logical invocation, collapse multiple physical attempts into one attempt, infer a historical grouping, or call a writer-provider success a verification run.

## Counting vocabulary and grouping contract

The following terms are closed for this gate:

| Term | Exact meaning |
|---|---|
| logical provider invocation | One controller call to a configured provider function, recorded by one `_record_provider()` call. |
| physical provider attempt | One actual or fake provider execution attempt represented by one `ProviderRecord`. For `_run()`, this is one subprocess start. |
| retry transition | A physical attempt whose persisted `retry_scheduled` value is true, meaning `_run()` schedules the next attempt in the same logical invocation. |
| successful writer invocation | A logical `implementation_write` or `correction_write` invocation whose final physical attempt has return code zero and no failure kind. It is a proxy for successful writer-provider completion, not verification. |

All attempts returned together in one `ProviderExecution.attempts` tuple form one logical invocation. An execution without an attempts tuple still produces one logical invocation containing one physical attempt. The following are new logical invocations because each makes another controller-level provider call: the one allowed malformed-structured-output retry, a call made after resuming a blocked/interrupted provider stage, a writer call made after explicit recovery, and any later correction, review, policy, or escalation call. Equal prompts, identities, operations, or commands do not merge invocations.

Same-provider unavailability retries remain limited to the current read-only `_run()` policy and remain inside the original invocation. Workspace-write providers still receive no automatic physical retry. This vocabulary changes observation only; it does not change provider authority, command construction, retry eligibility, retry delays, failure classification, timeouts, ownership, or resume behavior.

## Schema-12 persistence and validation contract

This persisted structural change increments `CURRENT_RUN_SCHEMA_VERSION` from 11 to 12. Each `ProviderRecord` adds:

- `logical_invocation_id`: a bounded, opaque, controller-generated identifier; and
- `physical_attempt_ordinal`: a strict positive integer starting at one within that invocation.

Ordinary schema-12 runs require both fields on every provider record. `_record_provider()` creates one invocation ID per call, assigns it to every normalized attempt, and appends ordinals `1..N` in tuple order. Provider output, command text, route/display metadata, environment variables, and operator text cannot supply or influence the identifier. Tests inject deterministic IDs.

Provider records for one invocation are contiguous. Within a group, identity control key, operation, capability, command authority, and repository fingerprints remain coherent; invocation IDs never recur after another group starts; ordinals are unique and gap-free; every non-final attempt has `retry_scheduled=true`; and the final attempt has `retry_scheduled=false`. A single-attempt invocation therefore has ordinal one and no scheduled retry. Existing route/operation/capability and writer/review/policy source-link validators continue to bind their physical record indices, normally the successful final attempt.

Validation rejects missing or malformed grouping fields on ordinary records, duplicate or reused IDs, non-contiguous groups, ordinal gaps/reordering, incompatible records in one group, a retry flag without its next attempt, a non-final attempt without a retry flag, or any new field on a historical source model. Models remain strict and extra-forbid. The controller appends grouping facts and never edits historic provider records.

## Report and display contract

The internal report and human-readable report use names that expose their unit:

| Report value | Required semantics |
|---|---|
| `provider_logical_invocations_total` | Number of distinct persisted logical invocation groups. |
| `provider_physical_attempts_total` | Number of persisted `ProviderRecord` attempts. |
| `role_logical_invocation_counts` | Logical invocations grouped by stable role ID. |
| `role_physical_attempt_counts` | Physical attempts grouped by stable role ID. |
| `provider_physical_attempt_seconds_total` and role timing | Sum of recorded physical-attempt durations; untimed counts are explicitly physical attempts. |
| `provider_physical_attempt_failure_counts` | Failure kinds counted per physical attempt. |
| `provider_retry_transitions_total` | Count of physical attempts with `retry_scheduled=true`. |
| `sol_escalations` | Logical invocations of the stable escalation-executive role. |
| `successful_writer_invocations` | Logical successful writer invocations as defined above. |

The ambiguous keys and labels `provider_calls_total`, `role_counts`, `provider_failure_counts`, `provider_retry_attempts`, and `verification_runs` are removed from the current internal report. Human output says “logical invocations,” “physical attempts,” “physical-attempt time/failures,” “retry transitions,” and “successful writer invocations (not verification executions).” It prints no `Provider calls` or `Verification runs` label.

Final logical outcome is the final physical attempt in a validated group. Timing and infrastructure-failure totals stay physical because they measure work actually attempted. Correction cycles, distinct defects, approval counts, policy decisions, ownership, final review, commit, and push facts remain unchanged. This internal dictionary is still not a versioned machine-readable contract; Gate 2.8 owns that interface.

The existing `run.verification` result payload and deterministic `_verify()` behavior are unchanged. This gate neither converts that payload into an execution ledger nor claims a durable verification count. A later contract may add real verification execution records and metrics without reusing the writer-success name.

## Exact V11 migration, failures, and recovery

Add an exact historical `_ProviderRecordV11` and `_RunV11` validator plus one pure `11_to_12` transform. V1--V10 validators and `1_to_2` through `10_to_11` remain literal and unchanged. The transform preserves every supplied V11 value and prior audit in decoded form, adds null grouping fields to every historical provider record, and appends a frozen `ProviderInvocationMigrationAudit` with target schema 12, reason `missing_provider_invocation_identity`, inherited disposition, and `unattributed_physical_attempt_count` equal to the number of historical provider records.

Migration never groups records using adjacency, route, operation, prompt, command, timestamp, failure, or `retry_scheduled`; those facts are insufficient proof of the original `_record_provider()` boundary. It never invents an invocation ID, ordinal, logical count, retry relationship, writer-success count, or verification execution. Migrated V1--V11 records retain the existing execution-refused disposition and cannot be reported as ordinary current metrics. Ordinary schema-12 records have no provider-invocation migration audit; migrated schema-12 records require null grouping fields on every historical provider record and an exact coherent audit count.

V1--V11 records migrate only through the existing explicit, default-no-confirmation, private atomic compare-and-swap command. Direct V11 uses only `11_to_12`; earlier records retain all prior audit lineages and gain the full step sequence ending `11_to_12`. Current V12 records are never rewritten. Malformed source/final records, grouping-like V11 extras, audit conflict, transform failure, temp-write failure, source change, concurrency, or crash before replacement preserves original bytes under the existing atomicity contract. There is no reverse/bulk migration, grouping repair, audit override, or execution-eligibility override.

## Compatibility and authority

Root and installed CLI names, provider commands and models, stable role/provider/route/operation identifiers, capability checks, prompts, return-code/failure normalization, timeout/interruption behavior, same-provider retry limits, writer side-effect rules, target ownership, repository fingerprints, parsed review links, correction/escalation policy, approval gates, Git gates, storage privacy, and Jobs compatibility identifiers remain unchanged.

All validation uses deterministic fixtures/fakes and temporary repositories. No live provider is invoked, no external target is used, and `/Users/michaelbuckingham/Documents/my-apps/jobs`, `JOBS_REPO`, `jobs-orchestrator`, and `src/jobs_orchestrator` are not accessed or modified.

## Adversarial test matrix

| ID | Fixture / event | Required assertions |
|---|---|---|
| G27-I1 | Static schema/report inspection | Schema 12; exact terms and fields; ambiguous metric names absent. |
| G27-I2 | One execution with no attempts tuple | One logical invocation, one physical attempt, ordinal one, no retry transition. |
| G27-I3 | One read-only execution with three outage attempts | One logical invocation, three physical attempts, ordinals `1..3`, two retry transitions. |
| G27-I4 | Two equal provider calls with identical prompt/command/output | Two distinct logical invocation IDs; no value-based merging. |
| G27-I5 | Provider/fake supplies grouping-like text | Controller-owned identity wins; provider data gains no metric authority. |
| G27-V1 | Missing/bad ID or ordinal on ordinary V12 | Strict validation fails before report, resume, provider, or Git. |
| G27-V2 | Duplicate/reused ID, interleaved group, or ordinal gap/reorder | Validation fails closed. |
| G27-V3 | Mixed role/route/operation/capability/fingerprint in one group | Validation fails closed. |
| G27-V4 | Retry flag false before another grouped attempt, true on final, or true without successor | Validation fails closed. |
| G27-V5 | Single workspace-write invocation requests automatic retry | Existing no-retry authority holds; invalid grouped evidence is rejected. |
| G27-R1 | One-, two-, and three-attempt invocations across roles | Logical and physical totals and per-role counts are exact. |
| G27-R2 | Timed and untimed physical attempts | Duration sums and untimed labels are explicitly physical. |
| G27-R3 | Failed attempts followed by success | Physical failure count includes failed attempts; logical invocation count stays one. |
| G27-R4 | Terminal failed invocation | Physical failure and retry-transition counts are exact; no fabricated success. |
| G27-R5 | Successful implementation and correction invocations | `successful_writer_invocations` counts each logical final success once; workspace-write invocations remain single-attempt. |
| G27-R6 | Writer succeeds but deterministic verification is absent/fails | Writer proxy remains named writer success; no verification-run count appears. |
| G27-R7 | Deterministic verification succeeds without a new writer invocation | Writer count does not change; no inferred verification execution record. |
| G27-R8 | Escalation invocation with physical retries | `sol_escalations` counts one logical invocation, not attempts. |
| G27-R9 | Human-readable report | Both units and exact proxy caveat display; old calls/verification labels absent. |
| G27-W1 | Malformed structured output then allowed same-provider call | Two logical invocations; each invocation's physical attempts remain separate. |
| G27-W2 | Resume a blocked/interrupted provider stage | Resumed provider call receives a new logical invocation ID. |
| G27-W3 | Explicit writer recovery followed by another writer call | Separate logical invocations; no automatic workspace-write retry. |
| G27-W4 | Review, unreadable-review, policy, and writer source links | Existing physical final-attempt indices remain coherent. |
| G27-W5 | Save/reload after multi-attempt execution | IDs, ordinals, counts, and final outcomes round-trip exactly. |
| G27-M1 | Registry and historical-model inspection | Exact V11/provider shape; one `11_to_12`; older literal steps unchanged. |
| G27-M2 | Ordinary V11 with zero, one, and several provider records | Values preserve; null grouping plus exact missing-identity audit; execution refused. |
| G27-M3 | V1--V10 migration chain | Prior audits preserve; lineage ends `11_to_12`; no grouping or metrics invented. |
| G27-M4 | V11 adjacency/retry flags appear groupable | Migration still leaves every record unattributed. |
| G27-M5 | Malformed V11 or grouping-like unknown fields | Archive/refuse before transform; no normalization or inference. |
| G27-M6 | Current/migrated V12 and transform/CAS/concurrency/crash failures | No current rewrite; audit coherence, atomic rollback, and idempotency hold. |
| G27-A1 | Current V12 status/report versus migrated V12 | Current metrics are exact; migrated history remains visibly execution/report refused. |
| G27-C1 | Route/model/display rename | Stable grouping and role counts do not change control identity or behavior. |
| G27-C2 | Provider timeout/interruption/quota/auth/rate-limit/configuration/unavailable paths | Existing classifiers, retry eligibility, and blocking behavior stay unchanged. |
| G27-C3 | Approval, ownership, storage, policy, review, writer recovery, and Git paths | Existing authority and persisted links remain unchanged. |
| G27-C4 | Root/installed CLI and compatibility identifiers | Existing commands/imports/options remain; `UV_NO_EDITABLE=1` installed validation passes. |
| G27-B1 | Scope inspection | No real verification ledger/plugin, JSON, doctor, dry-run, ADR, generic core, Q-4, or later Gate 2 work. |
| G27-B2 | Full deterministic validation | Fixtures/fakes/temp repos only; full tests, compilation, help/import, links/IDs, and diff check pass. |

## Documentation validation evidence (2026-08-02)

The approved planning diff was documentation-only. Final implementation validation confirms that all referenced local Markdown targets resolve, all 37 `G27-*` matrix IDs are unique, and tracked plus new-file `git diff --check` validations pass. The implementation diff contains only the approved Gate 2.7 source, tests, tracker/index reconciliation, and contract evidence; it contains no fixture, run record, provider command, target-checkout, or later-gate change.

## Implementation evidence (2026-08-02, published)

Schema 12 adds controller-owned `logical_invocation_id` and `physical_attempt_ordinal` facts to each ordinary `ProviderRecord`. `_record_provider()` assigns one ID per controller-level provider call and gap-free ordinals to its normalized physical attempts. Strict run validation rejects absent, reused, interleaved, reordered, route/operation/capability/command/fingerprint-incoherent, or retry-incoherent groupings. Structured-output retries, resumed calls, and writer recovery calls remain separate logical invocations; bounded read-only unavailability retries remain physical attempts inside one invocation; workspace-write automatic retries remain forbidden.

The exact V11 provider/run models and pure `11_to_12` migration preserve historical values and earlier audit lineages, add null grouping fields plus `ProviderInvocationMigrationAudit`, record the exact unattributed physical-attempt count, and retain execution/report refusal without adjacency or retry-flag inference. Current classification, migration output, and controller execution guards recognize the new audit.

The internal and human reports now expose logical invocation totals, physical-attempt totals, both per-role units, physical timing/failure counts, and retry transitions. Sol escalations count logical invocations. The former `verification_runs` proxy is now `successful_writer_invocations`, and the human label explicitly says it is not verification execution; no real verification ledger or metric was added.

All 191 deterministic tests pass with fakes, fixtures, and temporary repositories only. Focused grouping, validation, migration, malformed-output retry, and resume tests pass; bytecode compilation passes; root CLI help passes; and a rebuilt macOS installed package passes supported `jobs_orchestrator` import and `UV_NO_EDITABLE=1 jobs-orchestrator --help` checks from outside the checkout. No Jobs access, live provider, external target, commit, push, or later Gate 2 implementation occurred.

## Explicit exclusions and owner decision requested

No verification execution record, public JSON schema, `doctor`, dry-run, event/state ADR, generic engine work, Q-4 Git-record change, Jobs access, live provider, external-target access, commit, push, or later Gate 2 work is authorized by this gate.

The repository owner approved this exact Gate 2.7 contract and adversarial matrix on 2026-08-02, then separately approved the bounded implementation for commit and push. The tracker is complete, and the containing `main` commit is the publication record.
