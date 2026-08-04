# Gate 4.3 contract — Extract current provider commands into provider adapters

**Status:** contract awaiting repository-owner approval; runtime implementation is
not started; no source-code or test changes occur until the owner approves this
contract.

## Decision

Gate 4.3 will refactor the provider-specific code in `providers.py` into
code-owned adapter modules behind the three-operation boundary approved in
[Gate 3.5](../gate-3/gate-3.5-provider-adapter-route-profile.md), while
preserving the exact observable behavior of the current `codex_cli` and
`claude_cli` paths. It will introduce a durable, persisted schema-14 lifecycle
for provider invocations and physical attempts that satisfies the pre-spawn arming
requirement of Gate 3.5, and it will bind the exact code-owned adapter descriptor
identity/version/hash into that authority.

The four existing compatibility routes keep their current route IDs, model IDs,
sandbox flags, command flags, output contracts, failure classification, retry
policy, and permission ceilings. No new provider, model, effort value, route,
account, authentication method, capability, permission, or fallback is introduced.
Gate 4.3 does **not** implement generalized machine-checkable capability-profile/
permission-ceiling enforcement; it only proves that the existing compatibility
commands preserve their current invariants and carries the saved
`capability_profile_id` as authority evidence.

Because the approved Gate 3.5/4.1 records require fields and structural evidence
that do not exist in current schema-13 runs, Gate 4.3 **requires a run-schema
migration to schema 14**. The migration must derive only facts that are provable
from the schema-13 `resolved_configuration` and historical records; it must not
invent the new durable invocation/attempt arming evidence that pre-Gate-4.3 runs
never possessed.

## Invariants protected

1. The three-operation adapter boundary from Gate 3.5 is respected:
   `probe_local`, `build_attempt`, `execute_attempt`.
2. Every provider invocation and every physical attempt is durably armed before
   any process starts, and the armed record survives controller crashes.
3. The completed provider result is durably separated from the parse/workflow
   decision: a crash after a completed attempt but before the parse/transition
   save never re-invokes the provider.
4. A saved run can prove the exact code-owned adapter descriptor
   (identity/version/hash) that was authority for each invocation and attempt.
5. The no-fallback rule is preserved: an adapter cannot substitute another
   provider, model, route, account, or capability. Configuration or adapter
   failure stops the run.
6. Permission ceilings remain controller-owned and adapter-independent; an
   adapter cannot widen tools, sandbox, network, Git, publication, or approval
   authority. Gate 4.3 proves only the existing compatibility command invariants.
7. Same-provider bounded retry remains the only automatic retry; workspace-writer
   attempts remain single-shot.
8. Failure classification precedence and vocabulary are unchanged.
9. The structured-output content retry is a new logical invocation, not a
   transport retry, never substitutes another route, and is granted only by a
   persisted invocation-origin/retry policy: it is decided only by the live
   workflow parse of an `ordinary_workflow` invocation of a
   content-retry-eligible operation. Recovery re-parse of an unconsumed
   outcome, content-retry invocations, and provider-block-resume invocations
   never issue it.
10. Persisted command audits are redacted and bounded.
11. Pre-Gate-4.3 schema-13 runs are not silently upgraded to claim arming evidence
    they never had; the migration matrix states exactly which states can resume
    provider work, which can recover a saved provider result, which are
    provider-next/no-attempt-started blocks, which are human-gated or
    controller-only transitions, and which remain execution-refused or require
    existing writer recovery.
12. Malformed structured-output evidence is era-matched and non-overlapping:
    failures of schema-14 armed attempts are recorded only in
    `provider_protocol_failure_records`; failures of historical schema-13
    attempts remain recorded only in `unreadable_review_records`. One failure
    is never persisted in both representations, and one attempt is never
    represented twice.
13. A provider-resumable blocked stage reached by a read-only invocation
    remains provider-resumable for schema-14 ordinary runs exactly as it is
    today; blocking does not silently convert a resumable block into a terminal
    one. Writer failures never enter the provider-resumable set; they preserve
    the existing repository-observation/writer-recovery path and the Gate 3.5
    writer conservatism.

## Reproduced / current architectural problem

`providers.py` currently mixes four concerns:

1. provider-specific CLI command construction for `claude` and `codex`;
2. provider-specific failure classification (Claude native envelope parsing,
   stderr anchored diagnostics, HTTP status regex, OS error mapping);
3. generic process supervision, timeout, TERM/KILL, and heartbeat; and
4. the bounded same-provider retry loop.

`orchestrator.py` imports `execute_sonnet_review`, `execute_terra_resolution`,
`execute_sol_escalation`, and `execute_luna_implementation` directly and binds
them to roles. This couples workflow routing to the exact CLI command shapes of
today's compatibility routes.

The current `WorkflowRun` and `ProviderRecord` also lack the durable pre-spawn
arming evidence required by Gate 3.5:

- there is no persisted `ProviderInvocationRequest` that is saved before the
  first attempt of a logical invocation;
- there is no persisted `ProviderAttemptArm` that records the exact command
  audit, final provider-facing prompt hash, adapter descriptor, and pre-spawn
  state before the process starts;
- there is no durable separation between a completed provider result and the
  controller's parse/transition decision, so a crash between a completed attempt
  and its interpretation could re-invoke the provider;
- `provider_resume_*` fields are separate, loosely linked, and do not reference
  an armed attempt; and
- `provider_runs` records only completed attempts, so a crash between arming and
  completion cannot be distinguished from a crash before arming.

## In-scope behavior

- Introduce a code-owned `ProviderAdapter` descriptor and interface with exactly the
  three Gate 3.5 operations:
  - `probe_local()` – non-network, non-mutating local readiness check.
  - `build_attempt(request: ProviderInvocationRequestV2) -> AttemptPlan` – return
    a plan containing the executable, exact argument vector, minimal child
    environment, supervision policy, redacted command audit, and the
    provider-facing prompt hash.
  - `execute_attempt(plan: AttemptPlan) -> ProviderAttemptResultV2` – perform
    exactly one physical attempt by delegating process supervision to the
    shared controller supervisor, apply the adapter's registered failure
    classifier, and return a normalized schema-v2 result.

- Create two concrete adapters: `claude_cli` (Sonnet review) and `codex_cli`
  (Terra, Sol, Luna). They own the exact flags and preamble used today.

- Move generic process supervision and the bounded retry loop into a
  controller-owned shared supervisor and runner.

- Implement a durable schema-14 provider lifecycle with these persisted records
  (see exact fields below):
  - `ProviderInvocationRequestV2` – one per logical invocation, created before the
    first attempt and persisted before any process starts.
  - `ProviderAttemptArmV2` – one per physical attempt, created and persisted before
    the process starts, updated when the attempt completes, and marking uncertain
    state when the outcome cannot be recorded.
  - `ProviderAttemptOutcome` – one per completed attempt, created atomically with
    the completed record and arm, holding the recoverable completed-unconsumed
    state and the transport-retry decision. It is updated after the controller
    parses the output or decides a content retry.
  - `ProviderProtocolFailureRecord` – one per malformed structured output of a
    schema-14 armed read-only attempt, created atomically with the consumed
    outcome and either the new logical invocation request for a content retry
    (first malformed result of a content-retry-eligible `ordinary_workflow`
    invocation) or the blocked transition (every other malformed result: a
    second malformed result, a recovery re-parse failure, or a malformed
    provider-block-resume result). It is generic across Sonnet review and Sol
    escalation output and never references a workspace-write attempt.
  - `ProviderRecordV2` – the completed attempt record, now linked to its invocation
    and attempt arm and carrying the full authority evidence.
  - `ProviderResumeStateV2` – a tagged object that replaces the four separate
    `provider_resume_*` fields for schema-14 runs. It has a schema-14 armed
    variant, a schema-14 resumable-block variant (valid only for read-only
    invocations), and a migrated schema-13 legacy variant.

- Preserve the exact current command vectors for the four compatibility routes.

- Preserve current failure classification and structured-output content retry.

- Implement a `13_to_14` migration that adds the new fields and audit, derives
  provable authority facts from the saved resolved configuration, leaves the
  new arming evidence and unprovable completeness/adapter-descriptor facts
  absent for pre-Gate-4.3 records, retains `review_records`,
  `unreadable_review_records`, and `review_migration_audit` unchanged as
  historical schema-13 evidence, and provides an exhaustive stage/state
  migration matrix.

## Explicitly out-of-scope behavior

- No new provider, model, effort value, route, account, authentication method, or
  credential handling.
- No provider fallback or substitution.
- No permission expansion, no new capability profile, no new permission ceiling.
- No generalized machine-checkable capability-profile/permission-ceiling
  enforcement (that is a later Gate 4 item).
- No changes to retry semantics, approval semantics, Git semantics, or
  writer-recovery semantics.
- No changes to failure semantics except the structural relocation of the
  transport retry loop.
- No changes to the resolved configuration schema, route-profile schema, or
  provider-account schema from Gate 4.2.
- No extraction of the repository adapter or task-spec adapter.
- No provider/model/effort catalog UI or picker.
- No generic package rename, no new CLI entry points, no Rust/TUI work.
- No live provider calls, network access, real credentials, Jobs checkout access,
  or external target mutation.

## Provider-adapter boundary

### Code-owned adapter descriptor

Each built-in adapter is identified by an immutable descriptor with a stable
`provider_adapter_descriptor_id`:

```yaml
provider_adapter_descriptor_id: codex_cli.v1     # or claude_cli.v1
provider_adapter_schema_version: 1
provider_adapter_id: codex_cli                   # links to route profile
adapter_contract_id: continuo.provider-adapter.v1
transport_kind: local_process
command_builder_ids:
  - codex-cli.compatibility-builder.v1
failure_classifier_id: codex-cli.failure-classifier.v1
local_probe_id: codex-cli.local-probe.v1
supports_process_group_supervision: true
supports_partial_output_capture: true
descriptor_sha256: <canonical payload hash>
```

The descriptor is code-owned, not configuration. The registry maps
`provider_adapter_id` to exactly one descriptor. The descriptor hash changes
whenever the adapter's command builders, classifier, probe, stable ID, or
contract identity change. Each descriptor's `command_builder_ids` contains
exactly the saved `command_builder_policy_id` of the compatibility routes that
bind that adapter (`codex-cli.compatibility-builder.v1` for `codex_cli`;
`claude-cli.compatibility-builder.v1` for `claude_cli`), so the membership
validation below is satisfiable by the baseline resolved configuration.

Before building an attempt, the controller validates the saved route against the
loaded descriptor:

1. `route_profile.provider_adapter_id` must equal the descriptor's
   `provider_adapter_id`.
2. `route_profile.command_builder_policy_id` must be in the descriptor's
   `command_builder_ids`.
3. The descriptor's `descriptor_sha256` must match the currently loaded bytes.

Only after this validation does the controller bind the descriptor's stable ID
and hash into the new request, arm, and result.

### Inline-only bounded storage (no external artifacts)

The run is persisted as one atomically replaced JSON file. Every durable byte
Gate 4.3 adds or preserves — controller prompts, protocol-failure evidence,
command audits, and hashes — lives **inside that single run JSON**. There is no
external artifact store, no sidecar file, and no out-of-JSON reference: an
atomic run save therefore covers every byte it claims to persist, and a crash
between two saves cannot leave an external byte inconsistent with the run
because no external byte exists. No adapter, controller path, or migration may
write prompt or evidence bytes to a separate file, directory, or store, and
nothing outside the run JSON is ever read back on resume.

Privacy and redaction are enforced at read time: controller prompts and
protocol-evidence text are private bytes inside the private run JSON — never
rendered in reports, prompts, diagnostics, or command audits; only their
hashes are referenced by derived views, exactly as the persisted command audit
replaces credential/prompt arguments with hashes today.

Two code-owned closed policies fix the absolute byte bounds:

```yaml
continuo.controller-prompt-bounds.v1:    # controller-facing prompts
  controller_prompt_maximum_bytes: <fixed constant>   # finite, positive, shared by all routes
continuo.protocol-failure-evidence.v1:   # malformed-output evidence
  inline_evidence_threshold_bytes: <fixed constant>
  maximum_evidence_bytes: <fixed constant>
  # constraint: 0 < inline_evidence_threshold_bytes <= maximum_evidence_bytes
```

The values are fixed code-owned catalog data; configuration, adapters,
prompts, and provider output cannot change them.

### Prompt retention

The exact controller-facing prompt is durably retained in one of three places:

- For schema-14 runs: inline in the `ProviderInvocationRequestV2` as the
  single field `controller_prompt_text` (exact UTF-8 text, at most
  `controller_prompt_maximum_bytes` from
  `continuo.controller-prompt-bounds.v1`), with `controller_prompt_sha256`
  covering exactly those bytes. Validation re-derives the hash from the stored
  text. A prompt above the bound is rejected and never truncated: the prompt
  is authority evidence, so lossy truncation is not permitted.
- For a schema-14 run in a provider-resumable block reached by a read-only
  invocation: the `schema_14_resumable_block` variant references the failed
  request by `failed_invocation_id` and cross-checks
  `controller_prompt_sha256` against that request's inline
  `controller_prompt_text`; the inline prompt is the authority for the child
  invocation created on resume.
- For schema-13 migrated runs: the original `provider_resume_prompt` string is
  preserved in the `MigratedProviderResumeState` legacy variant; it is the
  authority for the first schema-14 invocation created on resume. On resume
  the prompt is copied inline into the child request and must satisfy
  `controller_prompt_maximum_bytes`; a historical prompt above the bound
  cannot be stored faithfully, so the run blocks rather than persisting
  unbounded bytes (see the migrated resume rules).

Migration behavior: the `13_to_14` migration itself stores no prompt bytes —
migrated runs retain the historical `provider_resume_prompt` unchanged and
create no requests. Only resume copies a prompt into a schema-14 request, at
which point the absolute bound applies and the copied text must equal the
historical prompt byte-for-byte.

The adapter computes the final provider-facing prompt (including any fixed
preamble such as the Luna Git prohibition) during `build_attempt`. The hash of that
final prompt is stored in the pre-spawn `ProviderAttemptArmV2`.

### Schema-v2 ProviderInvocationRequest

Created and persisted **before** `build_attempt` is called:

```yaml
provider_invocation_schema_version: 2
invocation_id: provider-invocation-<uuid>
operation_id: specification_review
role_id: adversarial_review
route_id: builtin.adversarial_review.v1
route_profile_sha256: <saved route hash>
provider_adapter_id: claude_cli
provider_adapter_descriptor_id: claude_cli.v1
provider_adapter_descriptor_sha256: <exact descriptor hash at arm time>
provider_account_profile_id: builtin.claude-cli.local-session.v1
provider_account_profile_sha256: <saved account hash>
output_contract_id: claude-cli.compatibility-output.v1
capability_profile_id: continuo.read-only.v1
controller_prompt_text: <exact controller-facing UTF-8 prompt, bounded by continuo.controller-prompt-bounds.v1>
controller_prompt_sha256: <sha256 of exactly the controller_prompt_text bytes>
effort:
  mode: provider_default
  effort_id: null
  enforcement_policy_id: claude-cli.effort-omission.v1
requested_at: <UTC timestamp>
invocation_origin: ordinary_workflow  # ordinary_workflow | content_retry | provider_block_resume
content_retry_permitted: true         # true iff origin is ordinary_workflow and the operation is content-retry-eligible
# parent-child linkage (all null for ordinary_workflow):
parent_invocation_id: null  # content_retry: source invocation; provider_block_resume: failed invocation (null for migrated_legacy authority)
parent_attempt_id: null     # content_retry: source attempt; provider_block_resume: failed attempt (null for migrated_legacy authority)
parent_outcome_id: null     # content_retry: source outcome; provider_block_resume: source blocked outcome (null for migrated_legacy authority)
retry_kind: null            # non-null iff origin == content_retry; closed value: structured_output_content_retry
resume_authority_variant: null  # schema_14_resumable_block | migrated_legacy; non-null iff origin == provider_block_resume
source_blocked_stage: null  # the provider-resumable blocked stage at child creation; non-null iff origin == provider_block_resume
repository_snapshot_before_sha256: <writer only; otherwise absent>
```

Schema examples in this contract use the exact control IDs persisted by the
baseline resolved configuration (Gate 4.2): per-adapter compatibility
builder/output contract IDs, the coarse `continuo.read-only.v1` /
`continuo.workspace-write.v1` capability profiles, and the per-adapter effort
omission policy. Every example value shown here appears in the baseline saved
binding; no example invents an ID that the resolved configuration does not
persist.

The request does **not** contain the provider-facing prompt hash. The adapter
owns any fixed preamble and computes the final provider-facing prompt during
`build_attempt`; the resulting hash is stored in the pre-spawn
`ProviderAttemptArmV2`.

### Invocation origin and content-retry eligibility

Every `ProviderInvocationRequestV2` persists two closed fields:

- `invocation_origin`: a controller-owned closed enum with exactly three
  values:
  - `ordinary_workflow` – the first logical invocation of an operation started
    by the ordinary workflow path, including the first schema-14 invocation a
    migrated run creates while continuing from a non-blocked stage;
  - `content_retry` – the retry invocation created by the live parse decision
    after a first malformed structured result;
  - `provider_block_resume` – an invocation created on resume of a
    provider-resumable blocked stage (`schema_14_resumable_block` or
    `migrated_legacy` resume).
- `content_retry_permitted`: a boolean retry-eligibility carried by the
  invocation. Validation enforces the exact bijection:
  `content_retry_permitted == true` if and only if `invocation_origin ==
  "ordinary_workflow"` and the operation is in the fixed
  content-retry-eligible set (`specification_review`, `implementation_review`,
  `escalation_guidance`) whose saved route profile carries the compatibility
  `content_retry_policy_id`. Every other invocation — `content_retry`,
  `provider_block_resume`, and ordinary `policy_clarification`,
  `implementation_write`, or `correction_write` — persists
  `content_retry_permitted: false`.

The origin is provenance (why the invocation exists), not a guess from prior
records or IDs: both fields are persisted with the request before
`build_attempt` is called, and resume never infers them. The eligibility is the
retry-policy consequence and is validated to match the origin, so the pair can
never drift.

Retry-eligibility lifecycle:

1. **Ordinary workflow invocation** of a review or Sol operation:
   `content_retry_permitted: true`. Its first malformed structured result may
   take exactly one content retry through the live parse decision below.
2. **Content-retry invocation:** `content_retry_permitted: false`. A malformed
   result blocks as `blocked_provider_output`; there is no third attempt.
3. **Provider-block-resume invocation** (created from `schema_14_resumable_block`
   or `migrated_legacy` resume): `content_retry_permitted: false`. A malformed
   result persists its `ProviderProtocolFailureRecord` evidence and blocks as
   `blocked_provider_output`; no further invocation is spawned. This preserves
   the current behavior in which a blocked-provider resume performs exactly one
   provider call and consumes the result through recovery logic without calling
   the ordinary structured-output retry helper.

The content-retry decision itself is taken exactly once, in the live workflow
parse of an `ordinary_workflow` invocation. Recovery re-parse of an unconsumed
outcome never issues a content retry: it either parses, or persists the
protocol-failure record with the consumed blocked outcome and the
`blocked_provider_output` transition. The controller never reconstructs an
unsaved content-retry decision after a crash; a persisted unconsumed outcome is
always consumed through recovery logic.

### Parent-child invocation linkage

A `content_retry` or `provider_block_resume` request is a **child request**:
it persists explicit, resolvable parent authority in the request itself, so
resume never infers lineage from prior records, stage names, or IDs and never
re-creates a request that a crash already saved.

Closed field rules:

- A request with `invocation_origin: ordinary_workflow` has all six parent
  fields null (`parent_invocation_id`, `parent_attempt_id`,
  `parent_outcome_id`, `retry_kind`, `resume_authority_variant`,
  `source_blocked_stage`). Validation rejects an ordinary request with any
  parent field populated.
- A request with `invocation_origin: content_retry` has `retry_kind:
  structured_output_content_retry` (a closed single-value enum), non-null
  `parent_invocation_id`, `parent_attempt_id`, and `parent_outcome_id`
  resolving exactly as the content-retry source/target linkage below
  requires, and null `resume_authority_variant` and `source_blocked_stage`.
- A request with `invocation_origin: provider_block_resume` has
  `resume_authority_variant: schema_14_resumable_block` or `migrated_legacy`,
  `source_blocked_stage` set to the provider-resumable blocked stage at child
  creation, and null `retry_kind` (see the resumable-block authority linkage
  below).

#### Content-retry source/target linkage

For a `content_retry` request `T` created from an eligible `ordinary_workflow`
request `S` after the first malformed result, validation requires all of:

1. `S.invocation_origin == "ordinary_workflow"` and
   `S.content_retry_permitted == true`;
2. `S.operation_id` is in the fixed content-retry-eligible set
   (`specification_review`, `implementation_review`, `escalation_guidance`);
3. the source outcome `O` is `status: consumed` with
   `next_action: content_retry`, `O.content_retry_invocation_id ==
   T.invocation_id`, and `O.protocol_failure_record_index` set; `O` belongs to
   an attempt of `S` (`O.attempt_id` links the arm whose `invocation_id ==
   S.invocation_id`);
4. `T.parent_invocation_id == S.invocation_id`,
   `T.parent_attempt_id == O.attempt_id`, and
   `T.parent_outcome_id == O.outcome_id`;
5. `T.invocation_origin == "content_retry"` and
   `T.content_retry_permitted == false`;
6. `T.retry_kind == "structured_output_content_retry"`;
7. `T` is referenced by exactly one source outcome (no two outcomes reference
   the same target, and no outcome references two targets);
8. `S` and `T` are identical in every authority field: `operation_id`,
   `role_id`, `route_id`, `route_profile_sha256`, `provider_adapter_id`,
   `provider_adapter_descriptor_id`, `provider_adapter_descriptor_sha256`,
   `provider_account_profile_id`, `provider_account_profile_sha256`,
   `capability_profile_id`, `effort`, `output_contract_id`,
   `controller_prompt_text` bytes and `controller_prompt_sha256`, and
   repository snapshot authority (`repository_snapshot_before_sha256`; absent
   for read-only operations, so both requests carry `null`);
9. `T` differs from `S` only in permitted identity/time fields:
   `invocation_id`, `invocation_origin`, `content_retry_permitted`,
   `retry_kind`, the three parent IDs, and `requested_at`;
10. no content-retry request exists without its unique eligible source: every
    `content_retry` request must satisfy rules 1-9 against a saved `S` and
    `O`. Duplicate, cross-linked (parent fields resolving to different
    objects, or to another `content_retry` request), circular (an `S` or `O`
    that references `T` while `T` references it), mismatched, or orphaned
    (`S` or `O` absent) requests are rejected as incoherent; and
11. an `S` produces at most one live `content_retry` target, and a target
    whose attempt has completed is consumed normally and never re-issued.

#### Resumable-block authority linkage

For a `provider_block_resume` request `C` created on resume of a
provider-resumable block, validation requires all of:

1. `C.resume_authority_variant` matches the persisted resume state that
   authorized `C` (`schema_14_resumable_block` or `migrated_legacy`);
2. `C.source_blocked_stage` is in the explicit provider-resumable set and
   equals the resume state's blocked `stage`;
3. for `resume_authority_variant: schema_14_resumable_block`:
   `C.parent_invocation_id` equals the variant's `failed_invocation_id`,
   `C.parent_attempt_id` equals the failed attempt's `attempt_id`, and
   `C.parent_outcome_id` equals that attempt's consumed outcome with
   `next_action: block` whose transition created the resumable state. The
   parent invocation must be read-only and consistent with the variant
   (capability, identity role, and controller prompt hash);
4. for `resume_authority_variant: migrated_legacy`: `C.parent_invocation_id`,
   `C.parent_attempt_id`, and `C.parent_outcome_id` are all null (no
   historical schema-14 objects exist); the authority is the persisted
   `migrated_legacy` resume state and its preserved `provider_resume_*`
   fields, and `C.source_blocked_stage` equals the variant's `stage`;
5. one resumable-block authority — one `(resume_authority_variant,
   parent_invocation_id, source_blocked_stage)` key — may create **at most
   one live child request** (a child with no completed attempt); a second
   live child for the same authority is rejected as incoherent; and
6. `C.content_retry_permitted == false` and `C` is read-only by construction:
   a `provider_block_resume` request never carries workspace-write capability
   or an `implementation` identity.

### Schema-v1 ProviderAttemptArm

Created and persisted **before each physical attempt starts**:

```yaml
provider_attempt_arm_schema_version: 1
attempt_id: provider-attempt-<uuid>
invocation_id: <links to the request above>
physical_attempt_ordinal: 1
route_profile_sha256: <saved route hash>
provider_adapter_descriptor_id: claude_cli.v1
provider_adapter_descriptor_sha256: <exact descriptor hash at arm time>
provider_account_profile_id: builtin.claude-cli.local-session.v1
provider_account_profile_sha256: <saved account hash>
output_contract_id: claude-cli.compatibility-output.v1
capability_profile_id: continuo.read-only.v1
effort:
  mode: provider_default
  effort_id: null
  enforcement_policy_id: claude-cli.effort-omission.v1
command_audit_sha256: <hash of redacted command plan>
provider_facing_prompt_sha256: <hash of exact final UTF-8 prompt sent to the process>
repository_snapshot_before_sha256: <writer only; otherwise absent>
armed_at: <UTC timestamp>
status: armed              # armed | completed | uncertain
provider_record_index: null   # set to index in provider_runs when completed
terminal_status: null
failure_evidence: null
```

### Schema-v1 ProviderAttemptOutcome

Created and persisted **atomically with the completed record and arm**:

```yaml
provider_attempt_outcome_schema_version: 1
outcome_id: outcome-<uuid>
attempt_id: provider-attempt-<uuid>
provider_record_index: 0
recorded_at: <UTC timestamp>
# initial state after the attempt completes
status: unconsumed          # unconsumed | consumed
next_action: parse_output   # parse_output | retry_transport | block | content_retry
retry_transport_ordinal: null   # set if next_action == retry_transport
content_retry_invocation_id: null   # set if next_action == content_retry
# final state after the controller parses or decides a content retry
parsed_result_type: null     # review_result | sol_guidance | terra_resolution | writer_success | writer_failure
parsed_result_reference: null
protocol_failure_record_index: null   # set to index in provider_protocol_failure_records
workflow_transition_stage: null
```

The outcome is the controller's recoverable interpretation of the immutable
attempt. It starts as `unconsumed` and is updated to `consumed` after parsing.
If parsing fails, the outcome references an immutable
`ProviderProtocolFailureRecord` by `protocol_failure_record_index`.

### Schema-v1 ProviderProtocolFailureRecord

Created and persisted **atomically with the consumed outcome and either the new
logical invocation request (first malformed result of a
content-retry-eligible `ordinary_workflow` invocation) or the blocked
transition (every other malformed result: a second malformed result, a
recovery re-parse failure, or a malformed provider-block-resume result)** when
a provider attempt succeeds at the transport
layer but its output cannot be parsed according to the operation's output
contract. The record is immutable and carries closed bounded evidence of the
malformed output, so malformed Sonnet and Sol output can be durably
distinguished from transport failures without changing retry semantics. A
record is only ever created for a schema-14 armed read-only attempt; historical
schema-13 attempts never gain one (see the era-matching rules below).

```yaml
provider_protocol_failure_record_schema_version: 1
failure_record_id: provider-protocol-failure-<uuid>
recorded_at: <UTC timestamp>
provider_record_index: 0          # index in provider_runs of the source attempt
operation_id: escalation_guidance # the operation whose output contract failed
output_contract_id: codex-cli.compatibility-output.v1
provider_invocation_id: provider-invocation-<uuid>   # of the source attempt; never null
provider_attempt_id: provider-attempt-<uuid>         # of the source attempt; never null
raw_output_evidence:
  evidence_kind: inline           # inline | projected
  inline_text: <exact bounded evidence text>  # non-null iff evidence_kind == inline
  projected_text: null            # deterministic projection/truncation result; non-null iff evidence_kind == projected
raw_output_sha256: <sha256 of exactly the stored evidence text bytes>
raw_output_evidence_policy_id: continuo.protocol-failure-evidence.v1
parse_failure_reason: invalid_sol_escalation_result
```

`parse_failure_reason` is a closed, controller-owned reason registry with
exactly five fixed IDs — `invalid_review_envelope`, `invalid_review_schema`,
`invalid_review_semantics`, `invalid_sol_escalation_result`, and
`output_contract_violation` — defined by the code-owned registry
`continuo.protocol-failure-reasons.v1`:

```yaml
protocol_failure_reason_registry_id: continuo.protocol-failure-reasons.v1
registry_owner: controller
fixed_reason_ids:
  - invalid_review_envelope        # Claude native envelope of a review attempt fails to parse
  - invalid_review_schema          # review JSON payload fails the closed review schema
  - invalid_review_semantics       # review payload parses but fails semantic checks
  - invalid_sol_escalation_result  # Sol escalation guidance fails its output contract
  - output_contract_violation      # any other violation of the operation's output contract
```

Validation requires `parse_failure_reason` to be exactly one of these five
fixed IDs and consistent with the record's operation: review operations
(`specification_review`, `implementation_review`) may carry
`invalid_review_envelope`, `invalid_review_schema`, `invalid_review_semantics`,
or `output_contract_violation`; `escalation_guidance` may carry
`invalid_sol_escalation_result` or `output_contract_violation`. No adapter,
prompt, or configuration can invent, register, or remap a reason at runtime;
adding or redefining a reason requires a code change and a contract amendment,
exactly like the adapter descriptor registry. The registry is the only
authority for reason values. The historical schema-13 marker vocabulary
(`ReviewUnreadableReason`, including `unreadable_legacy_review`) belongs to
`unreadable_review_records` only and is never written into a
`ProviderProtocolFailureRecord`; the two vocabularies are separate closed
registries even where an ID string is shared.

#### Closed raw-output evidence representation

`raw_output_evidence` is a closed tagged union with exactly two variants,
**both stored inline inside the run JSON** (there is no external artifact):

- `evidence_kind: inline` requires a non-null `inline_text` holding the exact
  evidence bytes (UTF-8 text of length at most
  `inline_evidence_threshold_bytes`) and a null `projected_text`;
- `evidence_kind: projected` requires a non-null `projected_text` holding the
  deterministic bounded projection/truncation result (UTF-8 text of length at
  most `maximum_evidence_bytes`) and a null `inline_text`.

Exactly one variant is permitted. Validation rejects a record whose evidence is
missing, whose two variant fields are both null or both non-null, whose
`evidence_kind` disagrees with the populated field, whose inline text exceeds
`inline_evidence_threshold_bytes`, whose projected text exceeds
`maximum_evidence_bytes`, or whose evidence bytes do not re-derive from the
linked completed record.

The applicable bounds are fixed by the code-owned closed policy
`continuo.protocol-failure-evidence.v1`, which registers two constants and
fixes three rules:

```yaml
inline_evidence_threshold_bytes: <fixed constant>   # storage threshold
maximum_evidence_bytes: <fixed constant>            # absolute maximum
# policy constraint: 0 < inline_evidence_threshold_bytes <= maximum_evidence_bytes
```

1. **Projection:** the deterministic bounded projection of the source attempt's
   captured stdout/stderr that constitutes the exact evidence bytes. The
   projection rule is fixed by the policy and re-derivable for validation.
2. **Absolute maximum:** `maximum_evidence_bytes` is the absolute maximum size
   of any stored evidence text. Evidence larger than the maximum is first
   reduced by the policy's exact deterministic truncation rule (a code-owned
   rule over the projected bytes, re-derivable for validation) to a size no
   greater than `maximum_evidence_bytes`. The controller never persists
   unbounded evidence; a record carrying evidence above the maximum is
   rejected.
3. **Variant choice:** evidence bytes of size at most
   `inline_evidence_threshold_bytes` are stored verbatim in the `inline`
   variant; evidence larger than the threshold (whether or not it exceeds the
   maximum) is stored in the `projected` variant as the deterministic
   projection/truncation result, at most `maximum_evidence_bytes`. Both
   variants are inline UTF-8 text inside the run JSON; no evidence bytes ever
   leave the run.
4. **Redaction and privacy:** rules identical to the persisted command audit
   rules; evidence text is private (never rendered in reports, prompts, or
   diagnostics), and only `raw_output_sha256` is referenced by derived views.

`raw_output_sha256` covers exactly the stored evidence text bytes: for the
`inline` variant it equals the sha256 of the UTF-8 encoding of `inline_text`;
for the `projected` variant it equals the sha256 of the UTF-8 encoding of
`projected_text`. Because the projection and any truncation are deterministic
from the completed attempt's captured output, validation can re-derive the
exact evidence text from the linked `ProviderRecordV2` (projection,
truncation, and variant choice) and reject a record whose evidence text or
hash does not match it.

#### Linkage and atomic-state validation

Validation rejects a `ProviderProtocolFailureRecord` whose
`provider_record_index` is missing or points to a `ProviderRecordV2` with a
different `operation_id` or `output_contract_id`. Validation rejects a record
whose `provider_invocation_id` or `provider_attempt_id` is null, partially
populated, or does not resolve to the saved request and arm of the source
attempt, or whose arm does not itself link the same `provider_record_index`. A
protocol-failure record is schema-14 armed-attempt evidence by construction; a
record referencing a request/arm whose descriptor hash does not match the
loaded descriptor is rejected. Validation also rejects a record whose source
attempt is workspace-write (`capability_profile_id: continuo.workspace-write.v1`
or an `implementation` identity): writer output is never parsed against a
structured-output contract, and writer failures are handled by the existing
repository-observation/writer-recovery path.

The record is persisted in exactly one atomic run save together with the other
objects of its boundary, so only two crash states are realizable:

- **Crash before the atomic save:** the attempt outcome remains `status:
  unconsumed`, `next_action: parse_output`; no record and no retry request
  exist. Resume re-parses the saved completed output through the recovery
  parse, which never issues a content retry: a successful re-parse persists
  the consumed outcome and the workflow transition; a failed re-parse
  persists the `ProviderProtocolFailureRecord`, the consumed blocked outcome,
  and the `blocked_provider_output` transition with its resumable state. The
  completed attempt is not re-invoked, and an unsaved content-retry decision
  is never reconstructed after a crash.
- **Crash after the atomic save:** the failure record, the consumed outcome,
  and the retry invocation request (first malformed result of an eligible
  `ordinary_workflow` invocation), or the blocked transition with its
  resumable-block resume state (any other malformed result), all exist.

A record-only state (record without the consumed outcome), an outcome-only
state, or a retry-request-only state cannot be produced by the controller.
Validation rejects any persisted run exhibiting such a partial state as
incoherent; it is never repaired by inventing the missing objects. The
checkable linkage rules are: every `ProviderProtocolFailureRecord` is
referenced by exactly one consumed outcome through
`protocol_failure_record_index`; every outcome with
`next_action: content_retry` references an existing record and an existing
retry invocation request through `protocol_failure_record_index` and
`content_retry_invocation_id`; and every outcome with `next_action: block`
and a set `protocol_failure_record_index` references an existing record.

### Era-matched malformed-output evidence (`unreadable_review_records`)

The repository already persists `unreadable_review_records` (Gate 2.4): bounded
immutable markers carrying `recorded_at`, `operation_id`,
`provider_record_index`, and a closed `ReviewUnreadableReason`. Gate 4.3 keeps
that representation and freezes its era:

- `unreadable_review_records` is retained solely as evidence of historical
  schema-13 attempts: markers already persisted by schema-13 runs, and markers
  written during schema-14 recovery when re-parsing a migrated run's saved
  historical record fails (preserving the Gate 2.4 recovery contract of an
  unreadable marker plus `blocked_provider_output`).
- Schema-14 code paths never append an `UnreadableReviewRecord` for the output
  of a schema-14 armed attempt; those failures persist only as
  `ProviderProtocolFailureRecord` values.
- No failure is dual-written. The `13_to_14` migration preserves
  `unreadable_review_records` byte-for-byte, converts none of them into
  `ProviderProtocolFailureRecord` values, and creates no protocol-failure
  records; the historical markers carry no bounded raw output evidence, so no
  faithful protocol-failure record could be derived from them.

Validation makes the two representations unable to conflict:

1. a `ProviderProtocolFailureRecord` requires non-null schema-14 linkage, so it
   can never represent a historical record;
2. an `UnreadableReviewRecord` requires its `provider_record_index` to point to
   a `ProviderRecordV2` whose `provider_invocation_id` and
   `provider_attempt_id` are both null, so it can never represent a schema-14
   armed attempt; and
3. each provider attempt index appears at most once across `review_records`,
   `unreadable_review_records`, and `provider_protocol_failure_records`
   (extending the existing parsed/unreadable mutual exclusion).

Readers consume the era-appropriate representation:

- Streak, correction-policy, and escalation control continue to consume parsed
  `review_records` only; neither marker type affects counts (unchanged from
  Gate 2.4).
- The implementation-review and Sol prompt contexts render historical
  unreadable markers exactly as today (filtered to `implementation_review`,
  the only operation the current prompts consume), and additionally render
  schema-14 terminal protocol failures — those whose consuming outcome has
  `next_action: block` — with `operation_id: implementation_review` as
  explicit unreadable entries in the same bounded shape (index plus reason).
  First malformed attempts that led to a content retry are not prompt context,
  and no other operation's protocol failures enter prompts, preserving current
  prompt semantics.
- The report/status unreadable section continues to render
  `unreadable_review_records` unchanged (`N unreadable review record(s)` with
  bounded indices and reason codes), and the report gains an additive
  protocol-failure section rendering every `ProviderProtocolFailureRecord` as
  bounded index, operation, and reason. Neither section is silent: historical
  and schema-14 evidence are both visible, under their own representations.

### Tagged ProviderResumeStateV2

A single object that replaces the schema-13 `provider_resume_stage`,
`provider_resume_prompt`, `provider_resume_identity`, and
`provider_resume_operation_id` fields for schema-14 runs. It has exactly three
variants: `schema_14_armed`, `schema_14_resumable_block`, and
`migrated_legacy`.

#### Schema-14 armed variant

Used while a schema-14 run is in a provider-pending state and a real attempt arm
exists:

```yaml
provider_resume_state_schema_version: 1
variant: schema_14_armed
stage: spec_reviewing
operation_id: specification_review
identity:
  role_id: adversarial_review
  provider_adapter_id: claude_cli
  route_id: builtin.adversarial_review.v1
  model_id: sonnet
  display_name: Sonnet 5 High
invocation_id: <the armed invocation>
attempt_id: <the armed attempt>
expected_ordinal: 1
controller_prompt_sha256: <hash of the request's inline controller_prompt_text>
provider_facing_prompt_sha256: <the exact final prompt hash from the arm>
```

`invocation_id` and `attempt_id` are both required non-null in this variant.
The arm and the resume state referencing it are persisted atomically, so a
pending invocation without an arm is never referenced by a persisted
`schema_14_armed` state.

#### Schema-14 resumable-block variant

Used when a **read-only** schema-14 invocation ends in one of the
provider-resumable blocked stages, preserving the current resumability of those
blocks:

```yaml
provider_resume_state_schema_version: 1
variant: schema_14_resumable_block
stage: blocked_provider_output        # the provider-resumable blocked stage
operation_id: implementation_review
capability_profile_id: continuo.read-only.v1
identity:
  role_id: adversarial_review
  provider_adapter_id: claude_cli
  route_id: builtin.adversarial_review.v1
  model_id: sonnet
  display_name: Sonnet 5 High
failed_invocation_id: <the invocation whose attempt failed>
controller_prompt_sha256: <hash of the failed request's inline controller_prompt_text>
```

This variant is valid **only for read-only provider invocations**. It is never
created for a workspace-write (Luna) invocation: completed writer failures
enter the existing repository-observation/writer-recovery path
(`active_writer_attempt` plus `blocked_writer_retry_required`,
`blocked_writer_partial_changes`, or `blocked_writer_state_unknown`) exactly as
today, and ordinary resume never creates a fresh Luna invocation from this
variant. Validation rejects a variant whose `capability_profile_id` is not
`continuo.read-only.v1`, whose identity `role_id` is not in the read-only role
set (`adversarial_review`, `escalation_executive`, `policy_authority`), whose
`failed_invocation_id` resolves to a request carrying a workspace-write
capability profile or an `implementation` identity, or whose resume would
spawn a workspace-write attempt. This preserves the Gate 3.5 writer
conservatism: workspace-write attempts are single-shot, are never
automatically retried or ordinarily resumed, and retain the existing
writer-recovery authority.

This variant is persisted **atomically with the blocked stage transition**, and
only when the blocked stage is in the provider-resumable set:

```yaml
provider_resume_permitted_resumable_blocks:
  - blocked_provider_quota
  - blocked_provider_billing
  - blocked_provider_auth
  - blocked_provider_rate_limit
  - blocked_provider_unavailable
  - blocked_provider_configuration
  - blocked_provider_failure
  - blocked_provider_output
```

On resume, the controller validates the variant against the saved binding, the
loaded descriptor, and the failed request's inline `controller_prompt_text`
(`controller_prompt_sha256` must match the re-derived hash). It then fixes the
child request for the authority key `(schema_14_resumable_block,
failed_invocation_id, stage)` exactly as follows (see the resumable-block
authority linkage rules):

- If no child request with that authority key exists, the controller creates a
  **new** `ProviderInvocationRequestV2` (new `invocation_id`,
  `invocation_origin: provider_block_resume`, `content_retry_permitted:
  false`, `parent_invocation_id`, `parent_attempt_id`, and `parent_outcome_id`
  set from the variant and the failed attempt's consumed block outcome,
  `resume_authority_variant: schema_14_resumable_block`, `source_blocked_stage`
  equal to the variant's stage) from the failed request's inline prompt and
  the exact saved route, account, capability, effort, output contract, and
  adapter descriptor, and persists it **before** `build_attempt` is called.
- If a saved child request matching the authority key already exists with no
  attempt arm, the controller **reuses** it; it never creates a second child.
  A duplicate live child for the same authority, a child whose parent linkage
  does not resolve to this variant and the failed outcome, an orphaned child
  whose authority key no longer matches the persisted resume state, or an
  already-consumed child is incoherent and rejected.
- With the child request fixed (reused, or freshly created and persisted), the
  controller deterministically calls `build_attempt`, persists a new
  `ProviderAttemptArmV2` (ordinal 1) plus the `schema_14_armed` resume state
  atomically, and only then executes.
- A child whose arm exists but has no completed record follows the existing
  interrupted-attempt rules: resume blocks as `blocked_interrupted_provider`
  and does not automatically execute the already-armed attempt.

The failed invocation is retained as evidence; its ID is never reused and its
consumed result is never re-invoked. Because the child is
`provider_block_resume`, its result never gains a content retry: a malformed
child result persists its `ProviderProtocolFailureRecord` evidence and blocks
as `blocked_provider_output` (with this variant persisted atomically with the
blocked transition) without spawning another invocation — exactly as the
current blocked-provider resume performs one provider call and consumes the
result through recovery logic. Non-resumable blocks (`blocked_provider_timeout`,
`blocked_provider_interrupted`, `blocked_interrupted_provider`) never carry
this variant and remain non-provider-resumable.

#### Migrated schema-13 legacy variant

Used **only** for schema-13 provider-resumable blocked states. The migration is
permitted only when the historical stage is in the explicit provider-resumable
set:

```yaml
provider_resume_permitted_migrated_stages:
  - blocked_provider_quota
  - blocked_provider_billing
  - blocked_provider_auth
  - blocked_provider_rate_limit
  - blocked_provider_unavailable
  - blocked_provider_configuration
  - blocked_provider_failure
  - blocked_provider_output
```

These stages had no historical schema-14 invocation or attempt arm. The legacy
variant carries only provable schema-13 evidence. The variant's `stage` is the
blocked run stage; the preserved historical `provider_resume_stage` is the
in-progress provider stage that was armed before the block (schema-13 runs
retain the in-progress stage in `provider_resume_stage` while blocked):

```yaml
provider_resume_state_schema_version: 1
variant: migrated_legacy
source_schema_version: 13
stage: blocked_provider_unavailable
operation_id: implementation_review
identity:
  role_id: adversarial_review
  provider_adapter_id: claude_cli
  route_id: builtin.adversarial_review.v1
  model_id: sonnet
  display_name: Sonnet 5 High
provider_resume_stage: reviewing
provider_resume_prompt: <the exact controller-facing prompt from the schema-13 run>
provider_resume_identity:
  role_id: adversarial_review
  provider_adapter_id: claude_cli
  route_id: builtin.adversarial_review.v1
  model_id: sonnet
  display_name: Sonnet 5 High
provider_resume_operation_id: implementation_review
```

Validation rejects any `migrated_legacy` variant whose `stage` is not in the
permitted set above. A persisted `migrated_legacy` with an invalid stage is
incoherent and must block or refuse execution; it must not be silently discarded
or fall through to ordinary matrix behavior. Validation also rejects a
`migrated_legacy` variant whose `provider_resume_stage` is a writer stage
(`implementing`, `correcting`): writer failures preserve the existing
repository-observation/writer-recovery path and never become a resumable-block
resume. In-progress provider stages
(`spec_reviewing`, `reviewing`, `terra_resolving`, `sol_escalating`,
`implementing`, `correcting`) are never represented as `migrated_legacy`. Ordinary
schema-14 runs in those stages use the `schema_14_armed` variant; migrated
schema-13 runs in those stages are handled by the migration matrix only when
`provider_resume_state` is actually absent.

On resume, the controller consumes the legacy variant by creating the first
real `ProviderInvocationRequestV2` and `ProviderAttemptArmV2` from the saved
prompt and route/account information, with `invocation_origin:
provider_block_resume`, `content_retry_permitted: false`,
`resume_authority_variant: migrated_legacy`, `source_blocked_stage` equal to
the variant's blocked `stage`, and all three parent IDs null. No historical
invocation or attempt ID is invented. The prompt is copied inline from
`provider_resume_prompt` and must satisfy `controller_prompt_maximum_bytes`
byte-for-byte; a historical prompt above the bound cannot be stored
faithfully, so the run blocks as `blocked_interrupted_provider` rather than
persisting unbounded bytes.

The child request is persisted before `build_attempt`, so a crash after the
request save but before the arm save is recovered exactly like the schema-14
case: resume reuses the saved child request for the authority key
`(migrated_legacy, source_blocked_stage)` — never creating a second child —
deterministically calls `build_attempt`, persists the arm and
`schema_14_armed` resume state atomically, and only then executes. A malformed
result of that resume invocation persists its `ProviderProtocolFailureRecord`
evidence and blocks as `blocked_provider_output`
with the `schema_14_resumable_block` resume state (a schema-14 failed invocation
now exists); it never gains a content retry and no further invocation is
spawned.

### Schema-v2 ProviderRecord

A completed physical attempt record is linked to its request and arm:

```yaml
identity:
  role_id: adversarial_review
  provider_adapter_id: claude_cli
  route_id: builtin.adversarial_review.v1
  model_id: sonnet
  display_name: Sonnet 5 High
operation_id: specification_review
provider_invocation_id: <from request; null for historical schema-13 records>
provider_attempt_id: <from arm; null for historical schema-13 records>
logical_invocation_id: <null for schema-14 records; preserved from schema-13 records>
physical_attempt_ordinal: 1
command_audit:
  builder_id: claude-cli.compatibility-builder.v1
  adapter_id: claude_cli
  route_id: builtin.adversarial_review.v1
  model_id: sonnet
  operation_id: specification_review
  redacted_argv: ["claude", "-p", ..., "--", "<prompt-sha256>"]
command_audit_sha256: <hash of redacted audit>
provider_facing_prompt_sha256: <same as arm>
returncode: 0
stdout: ...
stderr: ...
duration_seconds: 12.3
terminal_status: succeeded
stdout_complete: true
stderr_complete: true
failure_kind: null
failure_source: null
failure_code: null
capability: read_only
capability_profile_id: continuo.read-only.v1
provider_account_profile_id: builtin.claude-cli.local-session.v1
provider_account_profile_sha256: <saved account hash>
route_profile_sha256: <saved route hash>
effort:
  mode: provider_default
  effort_id: null
  enforcement_policy_id: claude-cli.effort-omission.v1
output_contract_id: claude-cli.compatibility-output.v1
provider_adapter_descriptor_id: claude_cli.v1
provider_adapter_descriptor_sha256: <exact descriptor hash at arm time>
repository_fingerprint_before: null
repository_fingerprint_after: null
retry_scheduled: false
credential_generation: null
```

The full command vector (with the actual prompt) is used to spawn the process
but is not persisted in the run JSON; the command audit is redacted and bounded.

For historical schema-13 records the schema keeps the existing attempt linkage
exactly as recorded: `logical_invocation_id` and `physical_attempt_ordinal` are
preserved unchanged. The new schema-14 authority fields that were not durably
present remain `null`: `provider_invocation_id`, `provider_attempt_id`,
`provider_adapter_descriptor_id`, `provider_adapter_descriptor_sha256`,
`command_audit`, `command_audit_sha256`, `provider_facing_prompt_sha256`,
`stdout_complete`, and `stderr_complete`. The raw historical `command`, `stdout`,
and `stderr` are preserved unchanged; the raw historical `command` is never
converted into a schema-14 structured `command_audit` or used to derive a
`command_audit_sha256`. Historical `logical_invocation_id` values are legacy
attempt-grouping evidence only; they are never reinterpreted as a linkage to a
schema-14 `ProviderInvocationRequestV2` or `ProviderAttemptArmV2`.

Validation rejects a `ProviderRecordV2` whose `provider_invocation_id` or
`provider_attempt_id` is non-null while `provider_adapter_descriptor_id` or
`provider_adapter_descriptor_sha256` is null, because a schema-14 linkage requires
arming evidence. Validation also rejects a historical record whose saved
`logical_invocation_id` is used to derive or validate a schema-14 request or arm.

### Lifecycle and arming rules

1. **Before a logical invocation:**
   - The controller creates a `ProviderInvocationRequestV2` from the saved
     resolved configuration and the controller-facing prompt (inline
     `controller_prompt_text` plus `controller_prompt_sha256`).
   - The request is persisted before any `ProviderAttemptArmV2` is created.
   - A child request (`content_retry` or `provider_block_resume`) persists its
     parent linkage (`parent_invocation_id`, `parent_attempt_id`,
     `parent_outcome_id`, and `retry_kind` or `resume_authority_variant` /
     `source_blocked_stage`) in the same save, so a crash after the request
     save but before the arm save leaves a complete, resolvable child request
     that resume reuses instead of re-creating.

2. **Before each physical attempt:**
   - The controller loads the adapter descriptor by
     `route_profile.provider_adapter_id` and validates it against the route
     (builder membership and descriptor hash).
   - It calls `adapter.build_attempt(request)`. The adapter computes the final
     provider-facing prompt and returns an `AttemptPlan`.
   - The controller validates the plan against the request.
   - It creates a `ProviderAttemptArmV2` with `status: armed`, persists it, and
     updates `provider_resume_state` to the schema-14 armed variant. Both changes
     are persisted **atomically**.
   - Only then does it call `adapter.execute_attempt(plan)`.

3. **After a completed attempt:**
   - The controller appends a `ProviderRecordV2` to `provider_runs`.
   - It updates the matching `ProviderAttemptArmV2` to `status: completed` and sets
     `provider_record_index`, `terminal_status`, and `failure_evidence`.
   - It creates a `ProviderAttemptOutcome` recording the completed attempt and the
     transport decision:
     - `next_action: parse_output`, `status: unconsumed` if the attempt succeeded
       (return code 0, no transport failure). The output still needs parsing.
     - `next_action: retry_transport`, `status: consumed` if the attempt is a
       read-only `unavailable` and the ordinal is less than 3.
     - `next_action: block`, `status: consumed` if the attempt is a terminal
       transport failure, a writer failure, or the final `unavailable` attempt.
   - It **atomically persists** the completed record, the arm update, and the
     outcome before parsing, sleeping, or transitioning.

4. **After parsing the operation output:**
    - If the outcome is `status: unconsumed` with `next_action: parse_output`, the
      controller parses the saved output.
    - On successful parse, it **atomically persists** the parsed operation result
      (e.g., `ReviewRecord`, `sol_guidance`, or `terra_resolution`) plus the
      workflow transition and the updated outcome (`status: consumed`,
      `parsed_result_type`, `workflow_transition_stage`).
    - A structured-output content retry is permitted only when the failed
      attempt's invocation persists `invocation_origin: ordinary_workflow`
      with `content_retry_permitted: true`, and only from the live workflow
      parse. Recovery re-parse of an unconsumed outcome, content-retry
      invocations, and provider-block-resume invocations never issue a content
      retry. On a first malformed result of an eligible ordinary-workflow
      invocation, the controller performs the following durable sequence:

      1. **Atomically persist:**
           - the immutable `ProviderProtocolFailureRecord` containing the closed
             bounded raw output evidence, the operation/output-contract
             identity, and the full provider invocation/attempt linkage of the
             source attempt;
           - the consumed outcome with the content-retry decision
             (`status: consumed`, `next_action: content_retry`,
             `content_retry_invocation_id`,
             `protocol_failure_record_index` set to the new record); and
           - the new logical `ProviderInvocationRequestV2` for the retry
             (`invocation_origin: content_retry`,
             `content_retry_permitted: false`, `retry_kind:
             structured_output_content_retry`, and `parent_invocation_id`,
             `parent_attempt_id`, `parent_outcome_id` set from the source
             invocation, attempt, and outcome).

          At this boundary the malformed provider result is recorded as consumed;
          it cannot be re-invoked. The same generic record is used for malformed
          Sonnet review output and malformed Sol escalation guidance. Because the
          three objects are persisted in one atomic save, no record-only,
          outcome-only, or request-only intermediate state exists.

      2. Call `adapter.build_attempt(request)` for the new retry request. The
         adapter computes the final provider-facing prompt and returns an
         `AttemptPlan`. This step is deterministic given the saved request and
         the loaded descriptor; it does not invoke the provider.

      3. **Atomically persist:**
          - the new `ProviderAttemptArmV2` with `status: armed`; and
          - the schema-14 armed `provider_resume_state` referencing the new
            invocation and attempt.

         No process is spawned before this save completes.

      4. Only then call `adapter.execute_attempt(plan)` to spawn the retry.

    - On any other malformed result — a second malformed result (the failed
      attempt's invocation is itself a content retry), a malformed result of a
      provider-block-resume invocation, or a recovery re-parse failure — no
      content retry is permitted. The controller **atomically persists** the
      immutable `ProviderProtocolFailureRecord`, the consumed outcome
      (`status: consumed`, `next_action: block`,
      `protocol_failure_record_index` set to the new record), the transition
      to `blocked_provider_output`, and the `schema_14_resumable_block` resume
      state (read-only source invocation only). The provider is not re-invoked
      and no further invocation is spawned.

5. **Block transitions and resumable-block state:**
    - When a read-only invocation ends in a provider-resumable blocked stage —
      a terminal transport failure, an exhausted `unavailable` retry budget, or
      `blocked_provider_output` — the controller persists the
      `schema_14_resumable_block` resume state **atomically with the blocked
      stage transition**, preserving the current resumability of those stages.
      The variant is valid only for read-only invocations (see the
      resumable-block variant rules). The one exception is a blocked
      transition produced while consuming a migrated schema-13 record that has
      no schema-14 invocation; that case persists a `migrated_legacy` variant
      instead (see the migrated resume rules).
    - Writer failures never carry the resumable-block variant and never enter
      the provider-resumable set: a completed writer failure enters the
      existing repository-observation/writer-recovery path
      (`active_writer_attempt` with `blocked_writer_retry_required`,
      `blocked_writer_partial_changes`, or `blocked_writer_state_unknown`),
      and ordinary resume never invokes Luna (Gate 3.5 conservatism
      preserved).
    - Non-resumable provider blocks (`blocked_provider_timeout`,
      `blocked_provider_interrupted`, `blocked_interrupted_provider`) never
      carry the resumable-block variant and keep their existing semantics.
    - A `provider_block_resume` child request is created (or a saved one
      reused) **at resume**, before `build_attempt`, and carries the
      resumable-block authority linkage (`resume_authority_variant`,
      `source_blocked_stage`, and for schema-14 authorities the failed
      invocation, attempt, and block outcome). At most one live child request
      exists per authority key, and the child request save always precedes the
      arm save (see the resumable-block authority linkage rules and crash
      recovery below).

6. **Crash recovery for content retry and provider-block-resume children:**
    - Each numbered substep boundary below lies between two atomic saves. A
      crash therefore leaves either the complete state before the next save or
      the complete state after it; no partial combination of the objects in one
      atomic save is realizable. All child-request bytes are inside the run
      JSON, so no external byte can disagree with the saved state.
    - If the controller crashes after step 4.1 (request saved) but before step 4.2
      (`build_attempt`), resume sees the consumed outcome with
      `next_action: content_retry` and the saved retry invocation request. It
      revalidates the target's parent linkage against the source outcome (see
      the content-retry source/target linkage), calls `build_attempt`,
      persists the arm and resume state, and then spawns. The completed
      malformed result is not re-invoked, and no second target request is
      created.
    - If the controller crashes after step 4.2 (`build_attempt`) but before step 4.3
      (arm saved), resume repeats `build_attempt` from the saved request (the plan
      must be deterministic given the request and descriptor), then persists the
      arm and resume state, and then spawns. No unarmed process starts.
    - If the controller crashes after step 4.3 (arm saved) but before step 4.4
      (spawn), the retry attempt is armed but has no completed record, so the
      process may already have started. Resume blocks as
      `blocked_interrupted_provider`; it does not automatically execute the
      already-armed retry attempt. (Content retry exists only for read-only
      structured-output operations; no writer path applies.) The original
      malformed result remains consumed and is never re-invoked.
    - If the controller crashes after a `provider_block_resume` child request
      is saved (see the resumable-block authority linkage rules) but before
      its arm is saved, resume reuses the saved child request — never creating
      a second — deterministically
      repeats `build_attempt`, persists the arm plus the `schema_14_armed`
      resume state atomically, and only then executes. The old
      `schema_14_resumable_block` (or `migrated_legacy`) state remains the
      authority until the arm save completes; the child request is matched by
      its authority key, not by stage-name or ID heuristics.
    - If the controller crashes after the child arm is saved but before the
      child process spawns, the armed child has no completed record, so the
      process may already have started; resume blocks as
      `blocked_interrupted_provider` and does not automatically execute the
      already-armed child attempt.

7. **Uncertain outcome:**
    - If the controller crashes or is interrupted between the atomic pre-spawn
      save and the atomic post-attempt save, the arm remains `status: armed` or
      is explicitly set to `status: uncertain`.
    - Absence of a completed record is **not** treated as success or proof that
      the process did not start.
    - Resume uses the arm and outcome to decide whether to continue, block, or
      recover.

8. **Descriptor immutability:**
   - The descriptor ID and hash at arm time are part of the request, arm, and
     completed record.
   - On resume, the controller validates the loaded descriptor hash against the
     saved hash. A mismatch blocks the run as
     `provider_adapter_descriptor_changed`.

## WorkflowRun schema-14 fields

For schema-14 ordinary records:

```yaml
provider_invocations: [ProviderInvocationRequestV2, ...]
provider_attempt_arms: [ProviderAttemptArmV2, ...]
provider_attempt_outcomes: [ProviderAttemptOutcome, ...]
provider_protocol_failure_records: [ProviderProtocolFailureRecord, ...]
provider_runs: [ProviderRecordV2, ...]
provider_resume_state: ProviderResumeStateV2 | null
provider_adapter_migration_audit: ProviderAdapterMigrationAudit | null
# schema-13 resume fields are null for ordinary schema-14 records
provider_resume_stage: null
provider_resume_prompt: null
provider_resume_identity: null
provider_resume_operation_id: null
```

For schema-14 migrated records, the arming lists, the outcome list, and the
protocol-failure list are empty, and the migration audit records the absence.
Both ordinary and migrated schema-14 records retain the existing schema-9
review fields unchanged: `review_records`, `unreadable_review_records`, and
`review_migration_audit`. Schema-14 ordinary records never append an
`UnreadableReviewRecord` for a schema-14 armed attempt (see the era-matching
rules above).

## Migration and resume matrix

The `13_to_14` migration:

- Adds `provider_invocations`, `provider_attempt_arms`,
  `provider_attempt_outcomes`, `provider_protocol_failure_records`, and
  `provider_resume_state`.
- Preserves `review_records`, `unreadable_review_records`, and
  `review_migration_audit` unchanged. The migration converts no historical
  unreadable marker into a `ProviderProtocolFailureRecord` and creates no
  protocol-failure record: historical markers carry no bounded raw output
  evidence, and historical attempts have no schema-14 linkage, so no faithful
  protocol-failure record is derivable. The two representations remain
  era-disjoint after migration.
- Leaves each historical `ProviderRecord` in `provider_runs` unchanged except for
  adding the schema-14 fields that are provable from the saved
  `resolved_configuration` and for preserving the existing raw historical fields:
  - `route_profile_sha256`, `provider_account_profile_id`,
    `provider_account_profile_sha256`, `effort`, `capability_profile_id`, and
    `output_contract_id` are populated from the saved binding.
  - `provider_adapter_id` is preserved from the existing route identity.
  - `provider_adapter_descriptor_id` and `provider_adapter_descriptor_sha256`
    are set to `null` because the exact descriptor at the time of the historical
    attempt cannot be proven.
  - `command_audit` and `command_audit_sha256` are set to `null` (unknown).
    Historical schema-13 records did not durably persist a schema-14 redacted
    command audit. The raw historical `command` is preserved unchanged and is
    never converted into a schema-14 structured audit or used to derive a hash.
  - `provider_facing_prompt_sha256` is set to `null` (unknown). Historical
    schema-13 records did not durably persist the exact provider-facing prompt
    hash. The migration must not derive this value from the saved prompt.
  - `terminal_status` is derived from the existing return code and failure kind.
  - `stdout_complete` and `stderr_complete` are set to `null` (unknown)
    because historical records cannot prove whether output was truncated.
  - `provider_invocation_id` and `provider_attempt_id` remain `null` because the
    durable schema-14 arming evidence did not exist.
  - `logical_invocation_id` and `physical_attempt_ordinal` are **preserved
    unchanged** from the existing schema-13 record. They are legacy attempt-grouping
    evidence only and are never reinterpreted as a linkage to a schema-14
    `ProviderInvocationRequestV2` or `ProviderAttemptArmV2`.
- The schema-14 model permits these null values for historical records. A null
  descriptor hash means the record cannot prove which exact code-owned adapter
  descriptor was authority; a null `command_audit` and `command_audit_sha256`
  means the record cannot prove a schema-14 redacted command plan; a null
  `provider_facing_prompt_sha256` means the record cannot prove the final
  provider-facing prompt. The raw `command`, `stdout`, `stderr`,
  `logical_invocation_id`, `physical_attempt_ordinal`, and
  `provider_resume_prompt` fields are preserved unchanged as historical evidence
  only; they are not reinterpreted as schema-14 authority fields.
- Builds `provider_resume_state` as a `migrated_legacy` variant **only** when the
  stage is one of the provider-resumable blocked states listed below and the
  schema-13 `provider_resume_*` fields are complete and consistent. In-progress
  provider stages (`spec_reviewing`, `reviewing`, `terra_resolving`,
  `sol_escalating`, `implementing`, `correcting`) are **never** migrated to a
  `migrated_legacy` variant; they are handled by the matrix below.
- For all other stages, `provider_resume_state` is `null`.
- Records an immutable `ProviderAdapterMigrationAudit`:

  ```yaml
  provider_adapter_migration_audit:
    migration_id: migration-<uuid>
    migrated_at: <UTC timestamp>
    source_schema_version: 13
    target_schema_version: 14
    source_structural_class: V13
    source_sha256: <hash of schema-13 run bytes>
    applied_steps: ["13_to_14"]
    reason_codes: ["missing_provider_invocation_arm_evidence"]
    disposition: schema_upgraded
  ```

  The `RunMigrationDisposition` vocabulary is extended with `schema_upgraded`,
  and `RunStructuralClass` is extended with `V13` (plus an exact historical
  `_RunV13` model) so a schema-13 source can be classified and hashed as the
  migration source. The `13_to_14` step is registered in the migration registry
  with source version 13. This audit does **not** trigger execution refusal;
  only pre-existing historical migration-audit fields
  (`configuration_migration_audit`,
  `provider_invocation_migration_audit`, `approval_migration_audit`,
  `policy_migration_audit`, `identity_migration_audit`, `review_migration_audit`,
  and `migration_audit`) cause execution refusal.

### Exhaustive migrated schema-13 state resume rules

Every persisted schema-13 stage is covered exactly once below, except for the
two wildcard refusal rows that cover all stages carrying pre-existing
historical migration audits or a null `resolved_configuration`. The four
in-progress read-only provider stages (`spec_reviewing`, `reviewing`,
`terra_resolving`, `sol_escalating`) each appear in exactly two mutually
exclusive conditional rows — with and without a matching completed
`provider_runs` record — which together form the stage's single complete
handling; no other stage spans more than one row and no row conditions
overlap.

| Stage | Category | Schema-13 evidence | Resume behavior |
|---|---|---|---|
| `created`, `spec_review_passed`, `implementation_completed`, `implementation_verified`, `correction_pending`, `correction_completed`, `implementation_reviewed`, `sol_guidance_ready`, `blocked_after_correction`, `blocked_after_escalation` | Controller transitions that can lead to fresh provider work under schema-14 arming | No pending provider | Controller resumes the workflow and may invoke the next provider under schema-14 arming rules. The exact next stage is determined by the existing workflow logic. |
| `verifying` | Deterministic verification stage | No pending provider; `verification` dict may contain partial results | Ordinary resume leaves the run in `verifying` unchanged. No new provider authority is granted; the controller does not advance the run toward fresh provider work. Resumption of deterministic verification is out of scope for Gate 4.3. |
| `spec_reviewing`, `reviewing`, `terra_resolving`, `sol_escalating` with no matching `provider_runs` record | Provider-next / no-attempt-started | `provider_resume_*` fields present, no completed provider record for this stage | **Cannot re-invoke.** Block as `blocked_interrupted_provider`. This preserves current `_recover_provider_stage` behavior when no output was recorded. |
| `spec_reviewing`, `reviewing`, `terra_resolving`, `sol_escalating` with a matching `provider_runs` record | Recover saved provider result | Completed provider record exists for this stage | Consume the saved record and continue without re-invoking the provider. A re-parse failure blocks as `blocked_provider_output` with era-matched evidence (a historical unreadable marker for adversarial-review stages, no marker for Sol/Terra) and never issues a content retry. This preserves current `_recover_provider_stage` behavior. |
| `blocked_provider_quota`, `blocked_provider_billing`, `blocked_provider_auth`, `blocked_provider_rate_limit`, `blocked_provider_unavailable`, `blocked_provider_configuration`, `blocked_provider_failure`, `blocked_provider_output` | Provider-resumable blocked states | `provider_resume_*` fields present and consistent | **Can re-invoke the provider.** The `provider_resume_state` is built as a `migrated_legacy` variant. On resume, the controller creates the child request (or reuses a saved one) and `ProviderAttemptArmV2` from the legacy prompt, with `invocation_origin: provider_block_resume`, `content_retry_permitted: false`, and `resume_authority_variant: migrated_legacy`; the prompt is copied inline and bounded by `controller_prompt_maximum_bytes`, and the child request is persisted before `build_attempt` (a crash in that window reuses the saved child, never creating a second). A malformed resume result blocks as `blocked_provider_output` with no further invocation. Historical `provider_runs` records are retained as completed/unlinked attempts, not as durable arms. The new invocation uses the current adapter descriptor hash. |
| `blocked_provider_timeout`, `blocked_provider_interrupted`, `blocked_interrupted_provider` | Terminal provider-failure blocks | Provider outcome was terminal or unrecoverable | **Not provider-resumable.** Remain blocked, matching current semantics. |
| `blocked_policy_ambiguity` | Human-gated transition that can lead to provider work | `terra_resolution` and policy approval request | Remain blocked until `approve-policy` or `decline-policy`; after approval the workflow may invoke the next provider under schema-14 arming rules. |
| `awaiting_commit_approval`, `commit_declined`, `awaiting_push_approval`, `push_declined` | Human-gated publication transitions | Approval request/decision | Remain blocked until human approval; no provider work is invoked. |
| `pushed_awaiting_merge` | Post-publication state | Commit pushed | Manual merge remains outside the controller; no provider work. |
| `implementing`, `correcting`, `blocked_writer_retry_required`, `blocked_writer_partial_changes`, `blocked_writer_state_unknown` | Writer-recovery states | `active_writer_attempt` | Existing writer-recovery rules apply: `retry-restored` or `adopt-current` with an audited operator note; ordinary resume does not invoke Luna. |
| `spec_review_failed`, `blocked_dirty_repo`, `blocked_unexpected_repo_state`, `blocked_no_changes`, `blocked_spec_review`, `blocked_repeated_finding`, `blocked_correction_budget`, `blocked_git_failure` | Terminal/controller blocks | No pending provider | Remain blocked; human/controller action can release or restart as today. |
| any stage with a non-null pre-existing historical migration audit | Historical execution-refused | `configuration_migration_audit`, `provider_invocation_migration_audit`, `approval_migration_audit`, `policy_migration_audit`, `identity_migration_audit`, `review_migration_audit`, or `migration_audit` present | Execution-refused regardless of stage; inspection/report only. |
| any stage with `resolved_configuration: null` | Unconfigured | `resolved_configuration` null | Execution-refused (already enforced by schema-13 validation). |

## Resume behavior for schema-14 ordinary records

When a schema-14 run is loaded in a provider-pending or provider-blocked
state:

1. The controller validates `provider_resume_state` against `resolved_configuration`:
   - route/account/profile/capability/effort/output hashes must match the saved
     binding;
   - the adapter descriptor hash must match the currently loaded descriptor;
   - the `provider_resume_state` must be a `schema_14_armed` variant
      referencing a known `ProviderInvocationRequestV2` and arm, a
      `schema_14_resumable_block` variant referencing a known failed
      read-only invocation, or a `migrated_legacy` variant (never carrying a
      writer resume stage).
2. For a `schema_14_armed` variant:
   - It locates the referenced `ProviderAttemptArmV2`.
   - If the arm is `status: armed` or `status: uncertain` and there is no
     completed `ProviderRecordV2` with the same `provider_attempt_id`, the run
     blocks as `blocked_interrupted_provider` (read-only) or enters the existing
     writer-recovery path (writer).
   - If the arm is `status: completed` and the matching `ProviderAttemptOutcome` is
      `status: unconsumed`, the controller consumes the saved record without
      re-invoking the provider. The recovery parse never issues a content
      retry: a failed re-parse persists the `ProviderProtocolFailureRecord`,
      the consumed blocked outcome, and the `blocked_provider_output`
      transition with its resumable state; no retry invocation request is
      created.
   - If the outcome says `next_action: retry_transport` and the ordinal rules
     permit, the controller creates the next attempt arm for the same invocation
     and executes it.
   - If the outcome says `next_action: block`, the run blocks. If the blocked
      stage transition was not yet persisted (the run stage is still the
      in-progress stage), the controller completes that transition
      deterministically from the outcome's failure evidence — including the
      atomic `schema_14_resumable_block` state when the resulting stage is
      provider-resumable (read-only source invocation only; writer outcomes
      transition into the writer-recovery path) — without re-invoking the
      provider.
    - If the outcome says `next_action: content_retry`, the controller resumes the
      content-retry invocation referenced by `content_retry_invocation_id` using the
      same schema-14 armed rules. It first revalidates the target's parent
      linkage against the source outcome (content-retry source/target
      linkage); a mismatch is incoherent and rejected:
      - If the retry invocation has no `ProviderAttemptArmV2`, the controller
        deterministically repeats `build_attempt` from the saved request, persists
        the new arm and schema-14 armed `provider_resume_state`, and then spawns.
      - If the retry invocation has an armed or uncertain arm but no completed
        `ProviderRecordV2` for that `attempt_id`, the run blocks as
        `blocked_interrupted_provider` (read-only) or enters the existing
        writer-recovery path (writer); it does not automatically execute an
        already-armed attempt after restart.
      - If the retry invocation has a completed record, the controller consumes
        that record without re-invoking the original malformed result. The
        retry invocation persists `content_retry_permitted: false`, so a
        malformed retry result blocks rather than retrying again.
3. For a `schema_14_resumable_block` variant:
    - Validation checks that the variant's `stage` is in the explicit
      provider-resumable block set, that the variant and the resolved
      `failed_invocation_id` are read-only (`capability_profile_id:
      continuo.read-only.v1`, identity role in the read-only role set), that
      `failed_invocation_id` resolves to a saved request, and that the saved
      controller prompt bytes and hash match that request's inline
      `controller_prompt_text`. A variant carrying writer capability or
      identity is incoherent and rejected; resume never creates a fresh Luna
      invocation from this variant. Any other mismatch blocks or refuses
      execution; there is no fallback to another prompt, route, account, or
      descriptor.
    - If `run.stage` equals the variant's blocked stage, the controller fixes
      the child request for the authority key `(schema_14_resumable_block,
      failed_invocation_id, stage)`: if no child request with that authority
      exists, it creates and persists one (new `invocation_id`,
      `invocation_origin: provider_block_resume`,
      `content_retry_permitted: false`, `parent_invocation_id`,
      `parent_attempt_id`, and `parent_outcome_id` set from the variant and
      the failed attempt's consumed block outcome, `source_blocked_stage`
      equal to the variant's stage) **before** `build_attempt`; if a matching
      saved child with no arm already exists, it reuses it and never creates a
      second. It then deterministically calls `build_attempt`, persists a new
      `ProviderAttemptArmV2` (ordinal 1) plus the `schema_14_armed` resume
      state atomically, returns to the corresponding in-progress stage, and
      only then executes. This preserves the current resumability of the
      provider-resumable blocks and survives a crash between the child request
      save and the arm save.
    - A saved child request that is duplicate, mismatched (parent linkage does
      not resolve to the variant and the failed outcome), orphaned (authority
      key does not match the persisted resume state), or already consumed
      (its attempt has a completed outcome) is incoherent and rejected; an
      armed child with no completed record follows the interrupted-attempt
      rules (`blocked_interrupted_provider`).
    - The failed invocation's ID is never reused, and its consumed or malformed
      result is never re-invoked. A malformed result of the resume invocation
      persists its `ProviderProtocolFailureRecord` evidence and blocks as
      `blocked_provider_output`; it never gains a content retry and no further
      invocation is spawned.
4. For a `migrated_legacy` variant:
    - Validation first checks that the variant's `stage` is in the explicit
      provider-resumable set. Any other stage (including in-progress provider
      stages) is invalid and incoherent; the run blocks or refuses execution.
      `provider_resume_state` is not treated as null and the stage does not fall
      back to the matrix rules. Only an actually absent `provider_resume_state`
      uses the migration matrix.
    - For a permitted provider-resumable blocked state, the controller fixes
      the child request for the authority key `(migrated_legacy, stage)`: if
      no child request with that authority exists, it creates and persists one
      with the legacy prompt (inline, bounded by
      `controller_prompt_maximum_bytes`), `invocation_origin:
      provider_block_resume`, `content_retry_permitted: false`,
      `resume_authority_variant: migrated_legacy`, `source_blocked_stage`
      equal to the variant's stage, and all three parent IDs null, **before**
      `build_attempt`; if a matching saved child with no arm already exists,
      it reuses it and never creates a second. It then deterministically calls
      `build_attempt`, persists a new `ProviderAttemptArmV2` (ordinal 1) and a
      `schema_14_armed` resume state atomically, and only then executes.
    - A historical prompt above `controller_prompt_maximum_bytes` cannot be
      stored faithfully; the run blocks as `blocked_interrupted_provider`
      rather than persisting unbounded bytes. A child carrying parent IDs, a
      different authority variant, or a non-resumable stage is incoherent and
      rejected.
    - It then executes the attempt under the schema-14 arming rules. A malformed
      result persists its `ProviderProtocolFailureRecord` evidence and blocks
      as `blocked_provider_output` with the `schema_14_resumable_block` resume
      state; it never gains a content retry and no further invocation is
      spawned.
    - No historical invocation or attempt ID is invented.
5. In-progress provider fallback: if the run is in an in-progress provider
   stage (`spec_reviewing`, `reviewing`, `terra_resolving`, `sol_escalating`,
   `implementing`, `correcting`) and `provider_resume_state` is absent or does
   not reference an arm for that stage (for example after a crash between the
   stage transition and the atomic arm+resume-state save), the run blocks as
   `blocked_interrupted_provider` (read-only) or enters the existing
   writer-recovery path (writer). No provider is re-invoked. A blocked stage
   that carries no `schema_14_resumable_block` variant remains blocked even if
   its stage is in the provider-resumable set; re-invocation requires the
   variant. Non-resumable blocked stages always remain blocked.

## Resume behavior for migrated schema-13 records

A migrated schema-13 run has empty `provider_invocations`,
`provider_attempt_arms`, `provider_attempt_outcomes`, and
`provider_protocol_failure_records`. Resume is governed by
its `stage` and the matrix above, not by a generic `migrated_legacy` rule:

- `verifying`: ordinary resume leaves the run unchanged; no provider work is
  invoked and no recovery authority is granted.
- In-progress read-only provider stages (`spec_reviewing`, `reviewing`,
  `terra_resolving`, `sol_escalating`):
  - If a matching completed `provider_runs` record exists for that stage and the
    saved `provider_resume_*` fields are consistent with it, the controller
    consumes the saved record and continues without re-invoking the provider.
  - If consuming that historical record fails to parse (re-parse failure), the
    run blocks as `blocked_provider_output` exactly as the current schema-13
    recovery does: an adversarial-review stage appends one historical
    `UnreadableReviewRecord` (schema-13 marker shape, closed schema-13 reason
    vocabulary) before blocking, and Sol/Terra stages block without a marker.
    No `ProviderProtocolFailureRecord` is created for a historical attempt.
    Because no schema-14 invocation exists for the failed historical attempt,
    this blocked transition persists a `migrated_legacy` resume variant (stage
    `blocked_provider_output`, built from the preserved historical
    `provider_resume_*` evidence) rather than a `schema_14_resumable_block`
    variant, keeping the block provider-resumable exactly as today.
  - If no matching completed record exists, the run blocks as
    `blocked_interrupted_provider`. The generic legacy resume rule cannot
    re-invoke the provider.
- Writer stages (`implementing`, `correcting`) and writer-recovery blocked
  stages follow the existing writer-recovery path; ordinary resume does not
  invoke Luna.
- Provider-resumable blocked stages (`blocked_provider_quota`,
  `blocked_provider_billing`, `blocked_provider_auth`,
  `blocked_provider_rate_limit`, `blocked_provider_unavailable`,
  `blocked_provider_configuration`, `blocked_provider_failure`,
  `blocked_provider_output`) with complete `provider_resume_*` fields create the
  first real schema-14 invocation and arm on resume, carrying
  `invocation_origin: provider_block_resume`,
  `content_retry_permitted: false`, `resume_authority_variant: migrated_legacy`,
  and `source_blocked_stage`; the prompt is copied inline from
  `provider_resume_prompt` and must satisfy `controller_prompt_maximum_bytes`
  (an oversized historical prompt blocks rather than persisting unbounded
  bytes). The child request is persisted before `build_attempt`; a crash in
  that window is recovered by reusing the saved child request — never creating
  a second — then persisting the arm and `schema_14_armed` state atomically
  before spawn. A malformed resume result blocks as
  `blocked_provider_output` with no further invocation.
- Terminal provider-failure blocks, terminal controller blocks, human-gated
  states, and post-publication states remain blocked or await the appropriate
  human/controller action.

## Ownership of command construction versus generic lifecycle

| Concern | Owner | Examples |
|---|---|---|
| Executable and CLI flags | Adapter | `claude`, `codex exec`, `--model`, `--sandbox`, `--tools`, `--config` |
| Model ID / effort policy | Saved route profile; adapter uses it | `gpt-5.6-luna`, `sonnet`, `provider_default` |
| Preamble injection | Adapter (fixed registered preamble) | Luna Git prohibition |
| Structured output wrapper | Adapter | Sonnet JSON schema and `--output-format json` |
| Provider-native envelope parsing | Adapter | Claude `type=result`/`is_error=true` |
| Prompt placement as final argument | Adapter, validated by controller | `-- <prompt>` |
| Minimal child environment | Adapter | sanitized `PATH`, no repository hooks/aliases |
| Process spawn, process group, timeout | Shared supervisor | `start_new_session`, TERM/KILL, heartbeat |
| Retry loop, delay, persistence | Controller-owned runner | 5 s / 15 s, max 3 read-only attempts, writer single-shot |
| Durable pre-spawn arming | Controller | request, attempt arm, resume state |
| Atomic persistence boundaries | Controller | arm+resume_state before spawn; record+arm+outcome before parse; parsed-result+transition+outcome, or `ProviderProtocolFailureRecord`+content-retry-request+outcome, or `ProviderProtocolFailureRecord`+blocked outcome+blocked transition after any other malformed result, after parse; content-retry arm+resume_state before retry spawn; child `provider_block_resume` request before `build_attempt`; read-only resumable-block resume state with the blocked transition |
| Malformed-output evidence era | Controller | `ProviderProtocolFailureRecord` for schema-14 armed read-only attempts only; historical `unreadable_review_records` never converted or dual-written |
| Content-retry eligibility | Controller | `invocation_origin`, `content_retry_permitted`, the fixed content-retry-eligible operation set |
| Completed-unconsumed recovery | Controller | `ProviderAttemptOutcome` |
| Writer pre/post fingerprinting | Controller | `_writer_snapshot`, `_arm_writer_attempt` |
| Failure-to-stage mapping | Controller | `_block_provider`, blocked stages |
| Structured-output parsing | Controller | `parse_sonnet_review`, `_parse_sol_response` |
| Approval gates / workflow transitions | Controller | `_approval_gates`, `_run_from` |

## Permission enforcement boundary

Gate 4.3 proves that the current compatibility commands preserve the existing
invariants:

- Sonnet: `claude -p`, `--model sonnet`, `--permission-mode plan`,
  `--tools Read,Glob,Grep`, `--output-format json`, `--json-schema` with the
  closed review JSON schema, prompt as final argument after `--`.
- Terra/Sol: `codex exec`, `--model <gpt-5.6-terra|gpt-5.6-sol>`,
  `--sandbox read-only`, prompt as final argument.
- Luna: `codex exec`, `--model gpt-5.6-luna`, `--sandbox workspace-write`,
  `--config approval_policy=never`,
  `--config sandbox_workspace_write.network_access=false`, prompt as final
  argument, Git-prohibition preamble.

The controller validates the returned `AttemptPlan` by checking these exact flags
and the expected `capability_profile_id` for the route. It does **not** perform
a generalized comparison between an arbitrary capability profile and the
command plan; that belongs to the later Gate 4 item that implements
machine-checkable capability-profile/permission-ceiling enforcement.

## Failure classification implications

The exact evidence precedence from Gate 3.5 is preserved:

1. supervisor timeout/interruption/cleanup outcome;
2. operating-system spawn/pipe failure;
3. adapter-specific structured native error envelope;
4. narrowly anchored bounded stderr diagnostics;
5. adapter-contract-enabled bounded stdout-tail transport diagnostics; and
6. nonzero return-code fallback.

Timeout and interruption remain terminal outcomes; they do not retry.
Only read-only `unavailable` triggers the bounded same-provider transport retry.
Model prose, prompts, diffs, and code remain non-transport evidence.

## Structured-output content retry

- A malformed Sonnet review or Sol escalation output of a content-retry-eligible
  `ordinary_workflow` invocation triggers one same-provider content retry, and
  only from the live workflow parse.
- The malformed attempt is durably recorded as an immutable
  `ProviderProtocolFailureRecord` carrying the closed bounded raw output
  evidence, the operation/output-contract identity, and the full provider
  invocation/attempt linkage of the schema-14 armed read-only source attempt.
  The record is persisted atomically with the consumed outcome and the new
  retry invocation request; a partial combination of those objects is not
  realizable.
- That retry is a **new logical invocation** with a new `invocation_id`,
  `invocation_origin: content_retry`, `content_retry_permitted: false`, and
  ordinal 1, using the exact saved route, account, capability, prompt, and
  adapter descriptor. It is not a transport retry and does not consume the
  unavailable retry budget.
- A second malformed result — or a malformed result of a provider-block-resume
  invocation, or a recovery re-parse failure — blocks as
  `blocked_provider_output`; the `ProviderProtocolFailureRecord` is saved
  atomically with the consumed blocked outcome, the blocked transition, and
  the `schema_14_resumable_block` resume state (read-only source invocation
  only). No further invocation is spawned.
- A provider-native or transport failure during a content retry blocks with that
  failure; it does not trigger a third content attempt or another route.
- Recovery re-parse of an unconsumed outcome never issues a content retry; the
  controller never reconstructs an unsaved content-retry decision after a
  crash.
- Historical schema-13 attempts never gain a `ProviderProtocolFailureRecord`;
  their malformed-output evidence remains the era-matched
  `unreadable_review_records` markers (see the era-matching rules).

## Dry-run / doctor implications

- `doctor` calls only `probe_local()`, per Gate 3.5.
- `dry-run` resolves route profiles and validates operation/adapter/capability/
  supervision compatibility without building a sensitive final prompt or invoking
  the adapter.

## Testing strategy

- All 218 existing deterministic tests must pass after the refactoring.
- Add schema-14 model validation tests, including tests that historical
  `ProviderRecordV2` values preserve `logical_invocation_id` and
  `physical_attempt_ordinal` while keeping `provider_invocation_id` and
  `provider_attempt_id` null.
- Add arming lifecycle tests: crash before attempt arm, crash after arm before
  completion, crash after completion before parse, uncertain state blocks.
- Add atomic-persistence tests:
  - arm+resume_state saved before spawn;
  - record+arm+outcome saved before parse;
  - parsed result + transition saved after parse;
  - `ProviderProtocolFailureRecord` + consumed outcome + new retry invocation
    request saved atomically after a first parse failure of a
    content-retry-eligible `ordinary_workflow` invocation for both Sonnet and
    Sol;
  - `ProviderProtocolFailureRecord` + consumed blocked outcome +
    `blocked_provider_output` transition + `schema_14_resumable_block` resume
    state saved atomically after a second parse failure;
  - content-retry arm+resume_state saved before retry spawn;
  - resumable-block resume state saved atomically with every read-only
    provider-resumable blocked transition;
  - no process starts before the corresponding arm+resume_state atomic save.
- Add protocol-failure atomic-state tests: only the two realizable crash states
  exist (crash before the atomic save leaves the outcome unconsumed with no
  record and no retry request; crash after it leaves all objects present); a
  persisted record-only, outcome-only, or retry-request-only partial state is
  rejected as incoherent and is never repaired by inventing objects; resume
  before the save re-parses through the recovery parse, which never issues a
  content retry — a successful re-parse persists the parsed result and
  transition, and a failed re-parse persists the protocol-failure record, the
  consumed blocked outcome, and the blocked transition — without re-invoking
  the provider.
- Add recovery-parse tests: an unconsumed outcome whose re-parse fails never
  issues a content retry and never creates a retry invocation request; it
  persists the protocol-failure record, the consumed blocked outcome, and the
  `blocked_provider_output` transition with its resumable state.
- Add raw-output evidence tests: exactly one evidence variant is permitted;
  missing, dual, and mismatched evidence are rejected; inline evidence above
  `inline_evidence_threshold_bytes` is rejected; evidence above
  `maximum_evidence_bytes` that was not first deterministically
  projected/truncated is rejected; evidence between the threshold and the
  maximum is stored as the `projected` variant; the policy relationship
  `0 < inline_evidence_threshold_bytes <= maximum_evidence_bytes` is
  validated; `raw_output_sha256` equals the sha256 of exactly the stored
  evidence text for both variants; projected evidence stays private and
  redacted under the command-audit rules; evidence text re-derives
  deterministically from the linked completed record, including the truncation
  step; both variants are inline UTF-8 text inside the run JSON and no
  evidence byte is ever stored outside it.
- Add inline-only storage tests: every schema-14 request persists
  `controller_prompt_text` inline within `controller_prompt_maximum_bytes`
  with a matching `controller_prompt_sha256`; no prompt or evidence bytes are
  stored outside the run JSON, and no `*_artifact_id` or other out-of-JSON
  reference appears in any persisted object; validation rejects an oversized
  prompt (never truncated), a prompt whose hash does not match its bytes, and
  any prompt or evidence reference that does not re-derive from the linked
  request or completed record; privacy views render only hashes, never prompt
  or evidence text; the `13_to_14` migration stores no prompt bytes and
  preserves `provider_resume_prompt` unchanged.
- Add `parse_failure_reason` registry tests: only the five fixed IDs of
  `continuo.protocol-failure-reasons.v1` validate; operation-appropriateness
  (review reasons for review operations, Sol reason for escalation) is
  enforced; adapter-, prompt-, or configuration-supplied or remapped reasons
  are rejected; a new reason requires a code change and a contract amendment.
- Add era-matched evidence tests: a schema-14 armed failure never appends an
  `UnreadableReviewRecord`; a historical attempt never gains a
  `ProviderProtocolFailureRecord`; no failure is dual-written; each attempt
  index appears at most once across `review_records`,
  `unreadable_review_records`, and `provider_protocol_failure_records`; a
  protocol-failure record with null or partial linkage is rejected; an
  unreadable marker referencing a schema-14 linked record is rejected; the
  migration preserves `unreadable_review_records` byte-for-byte and converts
  nothing; a migrated run whose recovery re-parse fails appends a historical
  unreadable marker (adversarial-review stages only) and blocks as
  `  blocked_provider_output` without creating a protocol-failure record; streak
  and escalation counts are unaffected by either marker type; the report
  renders the historical unreadable section unchanged and the additive
  protocol-failure section, and prompts render only terminal
  `implementation_review` protocol failures plus historical markers.
- Add resumable-block resume tests: a schema-14 run blocked at any
  provider-resumable block as a result of a read-only invocation resumes
  through the `schema_14_resumable_block` variant into a new
  `provider_block_resume` invocation and ordinal-1 arm under full arming; the
  failed invocation ID is never reused and its consumed result is never
  re-invoked; a non-resumable block carries no resumable-block variant and
  remains blocked; a variant carrying writer capability or identity is
  rejected; a completed writer failure never carries the variant and enters
  the existing repository-observation/writer-recovery path; ordinary resume
  never creates a fresh Luna invocation from this variant.
- Add invocation-origin and retry-eligibility tests: every request persists
  `invocation_origin` and `content_retry_permitted`; the bijection validation
  rejects disagreement; ordinary review/Sol workflow invocations are eligible;
  ordinary Terra and writer invocations are not; content-retry invocations and
  provider-block-resume invocations (`schema_14_resumable_block` and
  `migrated_legacy` resumes) are not; a malformed provider-block-resume result
  persists its protocol-failure evidence and blocks as `blocked_provider_output`
  without spawning another invocation; the origin is never inferred from prior
  records or IDs.
- Add content-retry linkage tests: the full source/target invariant set is
  validated — source `ordinary_workflow` origin and `content_retry_permitted:
  true`, source operation in the eligible set, source outcome consumed with
  `next_action: content_retry` referencing the target, target `content_retry`
  origin with `content_retry_permitted: false` and `retry_kind:
  structured_output_content_retry`, parent IDs resolving to the source
  invocation/attempt/outcome, byte-identical authority fields, permitted-only
  differences, exactly-one-reference (no two outcomes share a target and no
  outcome references two targets), and no duplicate/cross-linked/circular/
  orphaned requests; a retry request without its unique eligible source is
  rejected; a retry of a retry is rejected.
- Add resumable-block request-before-arm recovery tests: a crash after the
  child `provider_block_resume` request is saved but before its arm is saved
  resumes by reusing the saved child (same `invocation_id`), deterministically
  calling `build_attempt`, and persisting the arm plus `schema_14_armed` state
  before spawn — no second child request is created; a crash after the child
  arm is saved blocks as `blocked_interrupted_provider`; a duplicate,
  mismatched, orphaned, or already-consumed child is rejected as incoherent;
  a migrated-legacy child with parent IDs or a wrong authority variant is
  rejected; an oversized historical legacy prompt blocks instead of being
  stored.
- Add content-retry crash-recovery tests: a saved retry invocation request with no
  arm rebuilds the arm and resume state before spawn; a saved retry arm with no
  completed record blocks as `blocked_interrupted_provider` (content retry is
  read-only by construction), and never automatically executes an
  already-armed attempt after restart.
- Add completed-but-unconsumed crash tests: resume consumes the saved record
  without provider re-invocation.
- Add resume tests for schema-14 ordinary records and for migrated schema-13 records,
  including the in-progress fallback rule (absent or stale resume state in an
  in-progress provider stage blocks as `blocked_interrupted_provider` for
  read-only work or enters writer recovery; no re-invocation).
- Add legacy-resume tests: a migrated schema-13 provider-resumable block creates
  the first real schema-14 invocation (`invocation_origin: provider_block_resume`,
  `content_retry_permitted: false`, `resume_authority_variant: migrated_legacy`)
  and arm on resume without inventing IDs; the prompt is copied inline
  byte-for-byte from `provider_resume_prompt` and bounded by
  `controller_prompt_maximum_bytes`; a malformed resume result blocks as
  `blocked_provider_output` without further invocation; a `migrated_legacy`
  variant carrying a writer resume stage (`implementing`, `correcting`) is
  rejected.
- Add descriptor-hash validation tests: mismatch blocks resume.
- Add command-invariant tests: Sonnet read-only tools, Luna network/Git flags.
- Add migration tests: ordinary schema-13 records migrate with null descriptor
  hash, null `command_audit` and command-audit/prompt hashes, null completeness,
  and null `provider_invocation_id`/`provider_attempt_id`; existing
  `logical_invocation_id` and `physical_attempt_ordinal` are preserved exactly
  and are never reinterpreted as schema-14 invocation/attempt linkage; migrated
  records cannot resume provider work without creating a new schema-14
  invocation; historical migration-audit records remain refused;
  `provider_adapter_migration_audit` does not trigger refusal.
- Add historical `command_audit` validation tests: migrated `ProviderRecordV2`
  values require `command_audit: null` and `command_audit_sha256: null`; the raw
  historical `command` is preserved unchanged.
- Add migration/adversarial tests preventing fabricated historical audits: the
  migration must not convert the raw historical `command` into a schema-14
  structured `command_audit` or derive a `command_audit_sha256`.
- Add exhaustive-matrix tests: every schema-13 stage maps to exactly one matrix
  handling (the four in-progress read-only provider stages each map to exactly
  two mutually exclusive conditional rows); the five newly added stages
  (`spec_review_failed`, `verifying`, `correction_pending`,
  `correction_completed`, `implementation_reviewed`) and all pre-existing stages
  are present; no stage is omitted or handled by overlapping rows. `verifying`
  must have its own row and ordinary resume must leave it unchanged.
- Add migrated legacy-resume restriction tests: in-progress provider stages are
  never migrated to `migrated_legacy`; a generic `migrated_legacy` rule cannot
  bypass the stage-specific matrix rules.
- Verify `git diff --check` and documentation link consistency.

## Adversarial matrix

| ID | Scenario | Required outcome |
|---|---|---|
| G43-01 | Configuration or test tries to register an arbitrary executable, class path, or command builder as an adapter. | Rejected; only code-owned built-in descriptors are valid. |
| G43-02 | Saved route's `command_builder_policy_id` is not in the loaded descriptor's `command_builder_ids`. | Arming fails before spawn. |
| G43-03 | Loaded adapter descriptor hash does not match the saved descriptor hash in an armed invocation. | Resume blocks as `provider_adapter_descriptor_changed`; no fallback. |
| G43-04 | Adapter `build_attempt` omits a required sandbox flag (e.g., Luna `approval_policy=never`) or adds a disallowed flag. | Controller validation rejects the plan before spawn. |
| G43-05 | Adapter `build_attempt` tries to enable network, Git, or shell tools beyond the current compatibility invariants. | Rejected before spawn. |
| G43-06 | Adapter attempts to change the model ID, route ID, or account at runtime. | Ignored; controller uses the saved route/account from the resolved configuration. |
| G43-07 | Adapter descriptor is missing, duplicated, hash-incoherent, or dynamically supplied. | Route/arming fails before run creation or provider work. |
| G43-08 | Adapter `execute_attempt` retries internally instead of returning one attempt. | Rejected; retry is controller-owned. |
| G43-09 | Generic runner applies a stdout-tail contract that the adapter has not explicitly enabled. | Stdout-tail evidence is ignored. |
| G43-10 | Generic runner scans full stdout or stderr model prose for transport failures. | Prose is not transport evidence; classification falls back to return code. |
| G43-11 | Timeout or interruption occurs during an attempt. | Supervisor TERM/KILLs the process group; run blocks as `blocked_provider_timeout` or `blocked_provider_interrupted`. Not provider-resumable. |
| G43-12 | Controller crash after creating an armed attempt and `provider_resume_state` but before `execute_attempt`. | Atomic pre-spawn save completed; resume sees `status: armed` with no completed record and blocks as `blocked_interrupted_provider`. No duplicate attempt. |
| G43-13 | Controller crash between persisting the invocation request and creating the first attempt arm. | No attempt arm exists; resume blocks as `blocked_interrupted_provider`. |
| G43-14 | Controller crash after the completed record, arm, and `ProviderAttemptOutcome` are saved, but before parsing the output. | Resume sees `status: unconsumed`, `next_action: parse_output` and re-parses the saved output without invoking the provider; a failed re-parse blocks as `blocked_provider_output` with era-matched protocol evidence and never issues a content retry. |
| G43-15 | Controller crash after the completed record, arm, and outcome are saved, but before the transport-retry arm is created. | Resume sees `next_action: retry_transport`, creates the next ordinal arm for the same invocation, and executes it without re-invoking the earlier attempt. |
| G43-16 | A persisted run has a completed `ProviderRecordV2` and a completed arm for the same attempt but no `ProviderAttemptOutcome` (a state no controller crash can produce, since the three are saved atomically). | Validation rejects the run as incoherent; no provider re-invocation and no invented outcome. |
| G43-17 | Controller crash after parsing succeeds but before the parsed result and workflow transition are saved. | Resume sees the unconsumed outcome, re-parses the saved output, and applies the transition. No provider re-invocation. |
| G43-18 | Controller crash after parsing fails but before the `ProviderProtocolFailureRecord` and the consumed outcome are saved. | Resume sees the unconsumed outcome and re-parses the saved output through the recovery parse, which never issues a content retry: a failed re-parse atomically records the immutable `ProviderProtocolFailureRecord`, the consumed blocked outcome, and the `blocked_provider_output` transition with its resumable state. No provider re-invocation and no retry invocation request. |
| G43-19 | Controller crash after the content-retry invocation request is saved but before `build_attempt` for the retry. | Resume sees the consumed content-retry outcome and saved request, calls `build_attempt`, persists the retry arm+resume_state, then spawns. The malformed original result is not re-invoked. |
| G43-20 | Controller crash after `build_attempt` for the content retry but before the retry arm and resume_state are saved. | Resume repeats `build_attempt` from the saved request, persists the retry arm+resume_state, then spawns. No unarmed process starts. |
| G43-21 | Controller crash after the content-retry arm and resume_state are saved but before the retry process is spawned. | The armed retry attempt has no completed record, so the process may already have started. Resume blocks as `blocked_interrupted_provider` (content retry is read-only by construction); it does not automatically execute the already-armed retry attempt. The original malformed result remains consumed. |
| G43-22 | Two armed attempts exist with the same `invocation_id` and `physical_attempt_ordinal`. | Validation rejects the run as incoherent. |
| G43-23 | Completed `ProviderRecord` references an unknown or mismatched attempt arm. | Validation rejects the run as incoherent. |
| G43-24 | Schema-14 ordinary record has any old `provider_resume_*` field populated, whether or not `provider_resume_state` is non-null. | Validation rejects; schema-14 ordinary records use `provider_resume_state` only. Migrated records retain the historical fields by design. |
| G43-25 | Migrated schema-13 provider-resumable block uses a `schema_14_armed` resume variant. | Rejected; it must use `migrated_legacy` because no historical invocation/arm exists. |
| G43-26 | Migrated schema-13 run in a provider-resumable block (e.g., `blocked_provider_unavailable`) resumes provider work. | Controller creates (or reuses) the first real `ProviderInvocationRequestV2` (`invocation_origin: provider_block_resume`, `content_retry_permitted: false`, `resume_authority_variant: migrated_legacy`) and `ProviderAttemptArmV2` from the legacy prompt; no historical invocation/attempt ID is invented; the prompt is copied inline and bounded; a malformed resume result blocks as `blocked_provider_output` without further invocation. |
| G43-27 | Migrated schema-13 run in `blocked_provider_timeout` or `blocked_provider_interrupted` tries to resume provider work. | Not provider-resumable; remains blocked. |
| G43-28 | Migrated schema-13 run in an in-progress read-only stage with no matching provider record tries to resume provider work. | Blocks as `blocked_interrupted_provider`; no re-invocation. |
| G43-29 | Migrated schema-13 run in an in-progress read-only stage with a matching provider record tries to resume. | Consumes the saved record and continues; no re-invocation. |
| G43-30 | Migrated schema-13 run in a writer stage resumes. | Existing writer-recovery rules apply; ordinary resume does not invoke Luna. |
| G43-31 | Migrated schema-13 run with incomplete `provider_resume_*` fields tries to resume provider work. | `provider_resume_state` is null; resume blocks as `blocked_interrupted_provider`. |
| G43-32 | Migrated schema-13 run in an in-progress provider stage is migrated as `migrated_legacy` instead of being handled by the stage-specific matrix. | Validation rejects; in-progress provider stages are never represented as `migrated_legacy`. A persisted `migrated_legacy` with an invalid stage is incoherent and must block or refuse execution; it is not silently discarded or treated as null. |
| G43-33 | Migrated schema-13 historical record with a non-null pre-existing migration audit is asked to resume provider work. | Execution-refused; inspection/report only. |
| G43-34 | Ordinary schema-13 run migrated to schema-14 is incorrectly refused because `provider_adapter_migration_audit` is present. | `provider_adapter_migration_audit` does not trigger refusal; the run remains executable under the matrix. |
| G43-35 | `13_to_14` migration sets `provider_adapter_descriptor_id` or `descriptor_sha256` for a historical record. | Rejected; those fields remain null because the historical evidence cannot prove them. |
| G43-36 | `13_to_14` migration sets `command_audit`, `command_audit_sha256`, or `provider_facing_prompt_sha256` from the historical command vector or prompt. | Rejected; those fields remain null because the historical record did not durably persist them. The raw `command` is preserved unchanged and is never converted into a schema-14 structured audit or used to derive a hash. |
| G43-37 | `13_to_14` migration sets `stdout_complete` or `stderr_complete` to `true` or `false` for a historical record. | Rejected; those fields remain null (unknown) because completeness cannot be proven. |
| G43-38 | `13_to_14` migration invents a historical `provider_invocation_id` or `provider_attempt_id`. | Rejected; those fields remain null. |
| G43-38a | `13_to_14` migration overwrites or reinterprets a historical `logical_invocation_id` or `physical_attempt_ordinal`. | Rejected; existing values are preserved exactly and are never used as a linkage to a schema-14 `ProviderInvocationRequestV2` or `ProviderAttemptArmV2`. |
| G43-39 | `13_to_14` migration matrix omits a schema-13 stage that the controller can persist. | Rejected; the matrix must cover every persisted schema-13 stage exactly once (the four in-progress read-only provider stages each span exactly two mutually exclusive conditional rows). |
| G43-39a | `13_to_14` migration matrix treats `verifying` as a stage that can lead to fresh provider work under schema-14 arming. | Rejected; `verifying` must have its own row and ordinary resume must leave it unchanged. |
| G43-40 | Read-only adapter returns `unavailable` twice, then succeeds. | Controller produces exactly 3 attempts, delays 5 s then 15 s, same adapter, same invocation ID. |
| G43-41 | Writer adapter returns `unavailable`. | No retry; run blocks in a writer-recovery stage. |
| G43-42 | Transport failure (`quota`, `billing`, `auth`, `rate_limit`, `configuration`, `provider_error`) occurs during a read-only invocation. | No retry; invocation blocks with that failure. |
| G43-43 | Sonnet/Sol output is malformed once, then parses on retry. | New logical invocation with new ID, ordinal 1, `invocation_origin: content_retry`, and `content_retry_permitted: false`; same route/account/prompt. A `ProviderProtocolFailureRecord` is durably saved for the malformed attempt. |
| G43-44 | Sonnet/Sol output is malformed twice or a transport failure occurs on the content retry. | Blocks as `blocked_provider_output` or with the transport failure; no third attempt or alternate route. The second malformed attempt is durably saved as a `ProviderProtocolFailureRecord` atomically with the consumed blocked outcome, the blocked transition, and the resumable-block resume state. |
| G43-44a | Sol malformed escalation guidance is not persisted as a `ProviderProtocolFailureRecord`. | Rejected; the generic protocol-failure record must be created atomically with the consumed outcome and the retry invocation request (first failure) or with the consumed blocked outcome and blocked transition (second failure). |
| G43-45 | Adapter parses Sonnet output differently from the current parser. | Rejected; current `parse_sonnet_review` behavior is preserved. |
| G43-46 | Adapter places the prompt before the `--` end-of-options marker or injects extra arguments after it. | Controller validates the prompt is the final argument; rejects otherwise. |
| G43-47 | Adapter tries to read the resolved configuration, other routes, or credentials. | Adapter receives only the request for its single operation. |
| G43-48 | `dry-run` or `doctor` calls an adapter path that could invoke a provider or build a real prompt. | Blocked or redacted; no provider/network/credential access. |
| G43-49 | Gate 4.3 implementation tries to implement generalized capability-profile/permission-ceiling comparison beyond the four compatibility commands. | Out of scope; deferred to the later Gate 4 item. |
| G43-50 | `13_to_14` migration creates a non-null `command_audit` or `command_audit_sha256` from the raw historical `command` for a historical `ProviderRecord`. | Rejected; historical records require `command_audit: null` and `command_audit_sha256: null`; the raw `command` is preserved unchanged and is never converted into a schema-14 structured audit or used to derive a hash. |
| G43-51 | A persisted run shows a partial protocol-failure atomic state: a `ProviderProtocolFailureRecord` referenced by no consumed outcome, a consumed `content_retry` outcome whose referenced record or retry invocation request is missing, or a consumed block outcome whose referenced record is missing. | The state is not realizable by the controller; validation rejects the run as incoherent and never repairs it by inventing the missing objects. |
| G43-52 | A `ProviderProtocolFailureRecord` has raw evidence that is missing, has both `inline_text` and `projected_text` populated or both null, has `evidence_kind` disagreeing with the populated field, has inline evidence above `inline_evidence_threshold_bytes`, carries evidence text above `maximum_evidence_bytes` without deterministic projection/truncation, or has a `raw_output_sha256` that does not match the stored evidence text. | Validation rejects the record; exactly one closed inline evidence variant is permitted, both bounds are enforced, and the hash must cover exactly the stored evidence text. |
| G43-53 | A schema-14 armed failure is also appended to `unreadable_review_records`, a historical attempt gains a `ProviderProtocolFailureRecord`, or one attempt is represented in both lists. | Rejected; evidence is era-matched, never dual-written, and each attempt index appears at most once across the parsed/unreadable/protocol-failure lists. |
| G43-54 | A `ProviderProtocolFailureRecord` has a null or partially populated `provider_invocation_id`/`provider_attempt_id`, or references a historical schema-13 attempt. | Rejected; protocol-failure records are schema-14 armed-attempt evidence only and require full resolvable linkage. Historical malformed output stays an `unreadable_review_records` marker. |
| G43-55 | Schema-14 run blocked at a provider-resumable block as a result of a read-only invocation (e.g., `blocked_provider_output`, `blocked_provider_unavailable`) resumes provider work. | The `schema_14_resumable_block` variant (read-only only) creates (or reuses) a `ProviderInvocationRequestV2` child with `invocation_origin: provider_block_resume` and `content_retry_permitted: false`, plus an ordinal-1 `ProviderAttemptArmV2`, from the saved prompt; the child request is persisted before `build_attempt` and reused after a crash in that window — never creating a second; the failed invocation ID is never reused and its consumed result is never re-invoked; a malformed resume result persists its protocol-failure evidence and blocks as `blocked_provider_output` with no further invocation. |
| G43-56 | A read-only provider-resumable blocked transition for a schema-14 invocation is persisted without the `schema_14_resumable_block` resume state, a non-resumable block carries one, or a writer failure carries one. | Rejected; resumable-block state is atomic with the blocked transition for read-only resumable stages only (the migrated-recovery exception carries a `migrated_legacy` variant instead); writer failures enter the repository-observation/writer-recovery path; resumability never silently changes. |
| G43-57 | A provider-block-resume invocation (created from a `schema_14_resumable_block` or `migrated_legacy` resume) is persisted with `invocation_origin: ordinary_workflow` or `content_retry_permitted: true`, or a malformed resume result triggers a content retry. | Rejected / blocked; resume invocations are `provider_block_resume` with `content_retry_permitted: false`; a malformed resume result persists its `ProviderProtocolFailureRecord` evidence and blocks as `blocked_provider_output` with its resumable state, spawning no further invocation. |
| G43-58 | A `schema_14_resumable_block` variant carries a writer identity or `continuo.workspace-write.v1` capability profile, or resume would create a fresh Luna invocation from it. | Rejected as incoherent; the variant is read-only only; completed writer failures enter the existing repository-observation/writer-recovery path; ordinary resume never invokes Luna from this variant. |
| G43-59 | A `ProviderProtocolFailureRecord` references a workspace-write attempt, or a writer failure is persisted with resumable-block state. | Rejected; writer output is never parsed against a structured-output contract; protocol-failure records are read-only armed-attempt evidence only, and writer failures keep the existing writer-recovery semantics. |
| G43-60 | An adapter, prompt, configuration, or test records a `parse_failure_reason` outside the fixed `continuo.protocol-failure-reasons.v1` registry, or remaps a registered reason. | Rejected; the registry is closed and controller-owned with five fixed IDs and no runtime or configuration extension; adding a reason requires a code change and a contract amendment. |
| G43-61 | Evidence bytes above `maximum_evidence_bytes` are persisted without deterministic projection/truncation, or inline evidence exceeds `inline_evidence_threshold_bytes`. | Rejected; evidence above the maximum must first be deterministically projected/truncated; inline evidence is bounded by the threshold; both bounds are validated and re-derivable from the linked completed record. |
| G43-62 | Controller crash after the `provider_block_resume` child request is saved but before its arm is saved, then resume runs. | Resume reuses the saved child request for the same authority key (never creating a second), deterministically calls `build_attempt`, persists the arm plus `schema_14_armed` state atomically, and only then executes. |
| G43-63 | Two live child requests exist for the same resumable-block authority key. | Rejected as incoherent; one authority may create at most one live child request. |
| G43-64 | A `provider_block_resume` child request's parent linkage does not resolve to the persisted resumable-block variant and the failed attempt's consumed block outcome. | Rejected as incoherent; parent IDs must match the variant's `failed_invocation_id`, the failed attempt, and its block outcome. |
| G43-65 | A child request whose authority key no longer matches the persisted resume state (orphaned), or an already-consumed child (its attempt has a completed outcome) is re-armed. | Rejected; orphaned children are incoherent, and consumed children follow normal consume rules and are never re-armed. |
| G43-66 | A `migrated_legacy`-authority child request carries parent IDs, a different authority variant, or a non-resumable source stage. | Rejected; legacy children have all three parent IDs null, `resume_authority_variant: migrated_legacy`, and a resumable `source_blocked_stage`. |
| G43-67 | A `provider_block_resume` child request carries workspace-write capability or an `implementation` identity. | Rejected; resume children are read-only by construction and never create a fresh Luna invocation. |
| G43-68 | A `content_retry` request's parent linkage does not resolve to a unique eligible `ordinary_workflow` source (missing, mismatched, cross-linked, circular, or duplicate source/target pairing). | Rejected as incoherent; no content-retry request exists without its unique eligible source, and a retry of a retry is rejected. |
| G43-69 | A `content_retry` request differs from its source in an authority field (operation, role, route, route hash, account/profile hash, capability, effort, output contract, adapter descriptor, controller prompt bytes/hash, or repository snapshot authority). | Rejected; source and target must be identical in every authority field and differ only in permitted identity/time fields. |
| G43-70 | A source outcome references two content-retry targets, two source outcomes reference the same target, or a target's parent outcome is not the consumed `content_retry` outcome that references it. | Rejected; each target is referenced by exactly one source outcome and the parent linkage is bidirectional. |
| G43-71 | Any controller prompt or protocol-evidence byte is stored outside the run JSON, or a persisted object carries an `*_artifact_id` or other out-of-JSON reference. | Rejected; inline-only bounded storage is the only representation, and every atomic run save covers every byte it claims to persist. |
| G43-72 | `controller_prompt_text` exceeds `controller_prompt_maximum_bytes`, is stored truncated, or its hash does not match its bytes; or the migration stores prompt bytes. | Rejected; the prompt is authority evidence and is never truncated, the bound and hash are validated, and the migration itself stores no prompt bytes. |

## Acceptance / exit criteria

- `providers.py` is split into adapter-specific modules, a shared supervisor, and a
  controller-owned runner; provider-specific CLI details no longer appear in
  `orchestrator.py` or controller workflow logic.
- The four current process argv are produced by the adapters and are identical to
  the current commands.
- Run schema is bumped to 14 with `ProviderInvocationRequestV2`,
  `ProviderAttemptArmV2`, `ProviderAttemptOutcome`,
  `ProviderProtocolFailureRecord`, `ProviderRecordV2`, `ProviderResumeStateV2`,
  and a `13_to_14` migration that records the missing arming evidence.
- Every new provider invocation and every new physical attempt is durably armed
  before the process starts, with atomic persistence of the arm and
  `provider_resume_state` before spawn.
- Every completed attempt is atomically persisted with its arm completion and a
  `ProviderAttemptOutcome` before parsing.
- The parse/transition or `ProviderProtocolFailureRecord` decision is
  atomically persisted after parsing, so a crash between the two saves recovers
  the completed attempt without re-invoking the provider and without
  reconstructing an unsaved content-retry decision (recovery re-parse never
  issues one). The same generic protocol-failure
  record supports malformed Sonnet review output and malformed Sol escalation
  guidance. Only the two realizable atomic crash states exist (before the save:
  unconsumed outcome, no record; after the save: all objects present); a partial
  record-only or outcome-only state is rejected as incoherent.
- A content-retry is granted only to a content-retry-eligible
  `ordinary_workflow` invocation — `invocation_origin` and
  `content_retry_permitted` are persisted with the request and validated to
  match — and only from the live workflow parse. It follows the explicit
  durable sequence: `ProviderProtocolFailureRecord`, consumed outcome, and new
  logical invocation request (`invocation_origin: content_retry`,
  `content_retry_permitted: false`, `retry_kind:
  structured_output_content_retry`, and parent IDs resolving to the source
  invocation, attempt, and outcome) are persisted atomically; then
  `build_attempt` runs; then the new retry arm and schema-14 armed resume
  state are persisted atomically; only then the retry process spawns.
  Crash recovery at each boundary cannot re-invoke the original malformed
  result, spawn an unarmed process, or automatically execute an already-armed
  attempt after restart. Recovery re-parse of an unconsumed outcome and
  provider-block-resume invocations never issue a content retry: a malformed
  recovered or resume result persists its `ProviderProtocolFailureRecord`
  evidence and blocks as `blocked_provider_output`.
- Every content-retry request satisfies the complete source/target linkage:
  the source is an eligible `ordinary_workflow` request with
  `content_retry_permitted: true`; the source outcome is consumed with
  `next_action: content_retry` and references the target; the target is
  `content_retry` with `content_retry_permitted: false`; source and target
  are identical in every authority field and differ only in permitted
  identity/time fields; each target is referenced by exactly one source
  outcome; and duplicate, cross-linked, circular, mismatched, or orphaned
  requests are rejected.
- All controller prompts and protocol-evidence text are stored **inline inside
  the single atomically-replaced run JSON**; there is no external artifact
  store and no out-of-JSON reference, so every atomic run save covers every
  byte it claims to persist. Prompts are bounded by
  `continuo.controller-prompt-bounds.v1` (`controller_prompt_maximum_bytes`,
  never truncated) with a validated `controller_prompt_sha256`; evidence is
  bounded by `continuo.protocol-failure-evidence.v1` with both variants
  (`inline` and `projected`) inline. The `13_to_14` migration stores no
  prompt or evidence bytes.
- Each `ProviderProtocolFailureRecord` carries a closed raw-output evidence
  representation: exactly one of an inline evidence variant (at most
  `inline_evidence_threshold_bytes`) or a projected variant (at most
  `maximum_evidence_bytes`), both stored as inline UTF-8 text inside the run
  JSON, selected under
  `continuo.protocol-failure-evidence.v1` (with
  `0 < inline_evidence_threshold_bytes <= maximum_evidence_bytes`), with
  `raw_output_sha256` covering exactly the stored evidence text. Missing,
  dual, mismatched, unbounded, or unprojected evidence is rejected.
- `parse_failure_reason` values come only from the closed controller-owned
  registry `continuo.protocol-failure-reasons.v1` (five fixed IDs);
  adapters, prompts, and configuration cannot invent, register, or remap
  reasons.
- Malformed-output evidence is era-matched with no dual-write: schema-14 armed
  failures persist only as `ProviderProtocolFailureRecord` values with full
  linkage, and historical attempts persist only as `unreadable_review_records`
  markers. The migration preserves historical markers unchanged, converts none,
  and creates no protocol-failure record. Streak, escalation, prompt, and
  report readers consume the era-appropriate representation and cannot see the
  two as conflicting.
- The `ProviderResumeStateV2` supports a schema-14 armed variant, a
  `schema_14_resumable_block` variant valid only for read-only invocations, and
  a `migrated_legacy` variant for schema-13 provider-resumable blocks only; the
  resumable-block and legacy variants are consumed by creating a new schema-14
  invocation and arm carrying `invocation_origin: provider_block_resume`,
  `content_retry_permitted: false`, `resume_authority_variant`, and
  `source_blocked_stage`. Every read-only provider-resumable blocked
  transition persists its resumable state atomically — the resumable-block
  variant, or the `migrated_legacy` variant when the block results from
  migrated schema-13 recovery with no schema-14 invocation — so schema-14
  ordinary runs keep the current resumability of those blocks. Completed writer
  failures never carry resumable state: they enter the existing
  repository-observation/writer-recovery path, and ordinary resume never
  creates a fresh Luna invocation.
- A `provider_block_resume` child request persists resolvable parent authority
  (the resumable-block/legacy variant, the blocked stage, and for schema-14
  authorities the failed invocation, attempt, and block outcome), is persisted
  before `build_attempt`, and is recovered crash-safely: one authority may
  create at most one live child request; a crash after the child request save
  but before the arm save resumes by reusing the saved child (never creating a
  second); a crash after the arm save follows the interrupted-attempt rules;
  and duplicate, mismatched, orphaned, or already-consumed children are
  rejected as incoherent.
- In-progress migrated provider stages are never represented as `migrated_legacy`;
  a matching historical provider record is consumed, and a missing record blocks as
  `blocked_interrupted_provider` without re-invoking the provider.
- A persisted `migrated_legacy` variant with a stage outside the provider-resumable
  set is incoherent and blocks or refuses execution; it is not silently discarded
  or treated as null. Only an actually absent `provider_resume_state` uses the
  migration matrix.
- Every new record binds the exact adapter descriptor ID/hash and the saved
  route/account/effort/capability/output hashes.
- Resume validates the saved descriptor hash, route/account hashes, and capability
  profile; mismatches block without fallback.
- The persisted command audit is redacted and bounded.
- The schema-13 migration matrix is exhaustive and preserves current recovery
  semantics, including non-resumability of `blocked_provider_timeout` and
  `blocked_provider_interrupted`, explicit handling of `blocked_policy_ambiguity`,
  and a dedicated `verifying` row that leaves ordinary resume unchanged. Every
  persisted schema-13 stage is covered exactly once in the matrix (the four
  in-progress read-only provider stages each span exactly two mutually
  exclusive conditional rows).
- The migration does not invent historical adapter-descriptor identity/version,
  stdout/stderr completeness, `command_audit` objects, redacted command-audit hashes,
  provider-facing prompt hashes, or `provider_invocation_id`/`provider_attempt_id`.
  Existing `logical_invocation_id` and `physical_attempt_ordinal` values are preserved
  exactly and are never reinterpreted as schema-14 invocation/attempt linkage.
  Historical records preserve their raw `command`, `stdout`, and `stderr` fields and
  set the new unprovable fields, including `command_audit` and
  `command_audit_sha256`, to null.
- `provider_adapter_migration_audit` does not trigger execution refusal.
- All 218 existing deterministic tests pass after compatible updates.
- `git diff --check` passes and all local Markdown links resolve.
- No new provider, model, effort, route, account, auth, credential, capability,
  or fallback is introduced.
- No live provider, network, credential, Jobs checkout, or external target
  mutation occurs during development or validation.
- The repository owner has approved this contract before implementation begins.
