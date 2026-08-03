# Gate 3.5 contract — Provider adapters and route profiles

**Status:** owner-approved and published; documentation-only Gate 3 deliverable; implementation is Gate 4 work.

**Owner-approved additive amendment (2026-08-03):**
[`Gate 4.1`](../gate-4/gate-4.1-effort-provider-account-amendment.md) adds the
schema-2 closed effort record and stable provider-account binding to route,
invocation, and attempt semantics.

## Decision

Continuo separates provider transport from workflow routing through two strict,
versioned contracts:

- a `ProviderAdapter` owns one provider CLI/API boundary, command construction,
  one physical attempt, process supervision, and normalized transport evidence;
  and
- a `ProviderRouteProfile` binds one orchestration role and allowed operation set
  to an adapter, model, capability declaration, output contract, deadlines, and
  bounded retry policy.

The deterministic controller selects a persisted route, creates logical
invocation and physical-attempt identities, durably arms work, invokes the
adapter, persists each normalized attempt, classifies retry eligibility, and
chooses the next workflow transition. An adapter cannot select a role, route,
fallback, retry, correction, approval, Git action, or recovery action.

One adapter call performs exactly one physical provider attempt. Automatic
same-provider retries move out of adapter-internal loops and become
controller-owned attempts within one logical invocation. This retains the
current observable retry policy while ensuring each completed attempt and retry
decision are durable before any sleep or subsequent process starts.

This Gate defines contracts only. Gate 4 extracts the current Codex and Claude
CLI paths. Gate 3.6 separately defines complete capability profiles and
permission ceilings; Gate 3.5 preserves and validates the existing coarse
`read_only` and `workspace_write` declarations until then.

## Provider adapter descriptor

Every installed adapter exposes one strict immutable descriptor:

```yaml
provider_adapter_schema_version: 1
provider_adapter_id: codex_cli
adapter_contract_id: continuo.provider-adapter.v1
transport_kind: local_process
command_builder_ids: []
failure_classifier_id: <stable adapter-owned ID>
local_probe_id: <stable adapter-owned ID>
supports_process_group_supervision: true
supports_partial_output_capture: true
descriptor_sha256: <canonical payload hash>
```

`provider_adapter_id` retains the existing `codex_cli` and `claude_cli` control
identifiers. It is not an executable name, package import, provider display
name, organization, credential profile, or model label. Descriptor lookup is
exact and unique; configuration cannot register Python objects, executable
paths, shell fragments, classifiers, or arbitrary command builders.

An adapter has three closed operations:

1. `probe_local()` performs a deterministic, non-network, non-mutating readiness
   inspection suitable for `doctor`;
2. `build_attempt(request)` returns one argument vector plus a redacted audit
   view from a registered command builder; and
3. `execute_attempt(plan)` performs one supervised attempt and returns one
   normalized result.

There is no generic `run(command)`, shell mode, provider fallback, configuration
write, authentication repair, or retry method. Adapters receive controller-owned
typed requests only. They do not read task/configuration files or persisted run
records directly.

## Route-profile catalog

Each route is a strict immutable catalog record:

```yaml
provider_route_profile_schema_version: 1
route_id: builtin.implementation.v1
role_id: implementation
provider_adapter_id: codex_cli
model_id: gpt-5.6-luna
display_name: Luna High
allowed_operation_ids:
  - implementation_write
  - correction_write
command_builder_id: codex.workspace-write.v1
output_contract_id: continuo.unstructured-text.v1
capability_declaration: workspace_write
capability_profile_id: <Gate 3.6 stable ID>
invocation_preamble_id: continuo.writer-git-prohibitions.v1
supervision_policy_id: continuo.workspace-write-supervision.v1
retry_policy_id: continuo.workspace-write-no-auto-retry.v1
content_retry_policy_id: continuo.no-content-retry.v1
route_profile_sha256: <canonical payload hash>
```

The four initial compatibility routes retain their current stable identities:

| Route ID | Role | Adapter/model | Operations | Coarse capability |
|---|---|---|---|---|
| `builtin.implementation.v1` | `implementation` | `codex_cli` / `gpt-5.6-luna` | `implementation_write`, `correction_write` | `workspace_write` |
| `builtin.adversarial_review.v1` | `adversarial_review` | `claude_cli` / `sonnet` | `specification_review`, `implementation_review` | `read_only` |
| `builtin.escalation_executive.v1` | `escalation_executive` | `codex_cli` / `gpt-5.6-sol` | `escalation_guidance` | `read_only` |
| `builtin.policy_authority.v1` | `policy_authority` | `codex_cli` / `gpt-5.6-terra` | `policy_clarification` | `read_only` |

Role, adapter, route, model, operation, builder, output, capability, supervision,
and retry IDs are control fields. `display_name` is presentation only. A model
rename requires a new or explicitly compatible catalog record; a display rename
does not affect routing. Model/provider pickers may select only complete
registered route IDs, never splice an arbitrary model into another route.

The resolved configuration persists the entire selected route-profile payload
and hash for every required role. Resume uses that saved routing table. If its
adapter/profile is unavailable, changed, hash-incoherent, unsupported, or no
longer capability-compatible, execution blocks; it never chooses a current
default, same model, same provider, display-name match, or alternate adapter.

## Invocation request and durable arming

Before any provider process starts, the controller persists a strict immutable
`ProviderInvocationRequest`:

```yaml
provider_invocation_schema_version: 1
logical_invocation_id: <bounded controller-generated ID>
operation_id: specification_review
role_id: adversarial_review
route_id: builtin.adversarial_review.v1
route_profile_sha256: <saved route hash>
prompt_artifact_id: <controller-owned immutable prompt ID>
prompt_sha256: <hash of exact provider-facing UTF-8 prompt>
output_contract_id: continuo.review-result.v1
capability_profile_id: <saved Gate 3.6 ID>
repository_snapshot_before_sha256: <required for workspace write, otherwise absent>
requested_at: <UTC timestamp>
```

The controller builds prompts from persisted task/configuration/workflow facts.
The adapter may add only its registered fixed preamble and structured-output
transport wrapper. The exact final prompt or an immutable private prompt
artifact plus its hash is persisted before spawn. Provider output, display
metadata, environment text, or a source file cannot change the request.

Operation, role, route, output contract, and capability must agree with the
saved profile. A workspace-write request additionally requires target ownership,
the execution mutex, a saved repository snapshot/change fingerprint, and the
existing armed writer record. A read-only request must not carry write authority.

The invocation record is saved before the first attempt. Each attempt is armed
with the same logical ID, next contiguous ordinal, route hash, prompt hash,
capability, deadline policy, and pre-attempt repository evidence before the
adapter is called. A crash before spawn is distinguishable from a crash after
arming; absence of a result never proves that a process did not start.

## Command construction and environment

Registered builders produce an argument vector directly; no shell parses it.
The executable and fixed flags are adapter-owned code/catalog data, not project
configuration. Model ID and prompt occupy explicit data arguments with an
end-of-options boundary where supported. Builders reject NULs, invalid IDs,
unexpected operations, unsupported output schemas, and arguments that would
relax the saved capability.

The compatibility builders preserve:

- Claude review: `claude -p`, saved `sonnet` model, plan permission mode,
  read/glob/grep tools only, JSON output, and the closed review JSON schema;
- Codex Terra/Sol: `codex exec`, saved model, read-only sandbox;
- Codex Luna: `codex exec`, saved model, workspace-write sandbox,
  `approval_policy=never`, network disabled, and controller-owned Git
  prohibitions; and
- no provider command with commit, push, branch, merge, reset, or configuration
  authority.

The adapter constructs a minimal allowlisted child environment. Credentials may
be inherited only through adapter-owned mechanisms required by the installed
CLI and are never placed in route profiles, prompts, commands, results, logs, or
diagnostics. Repository/task-controlled executables, aliases, config files,
hooks, environment variables, working-directory changes, and shell startup
files cannot replace a registered builder or expand authority.

The persisted command audit is redacted and bounded. It retains stable builder,
adapter, route, model, and operation IDs, but may replace prompt/credential
arguments with their hashes. Raw prompt/output artifacts remain private under
the established storage policy.

## Shared physical-attempt supervision

All local-process adapters use one controller-owned supervisor. The initial
compatibility policies preserve:

| Policy | Deadline | TERM grace | Poll | Heartbeat |
|---|---:|---:|---:|---:|
| read-only | 1,800 seconds | 5 seconds | 0.2 seconds | 5 seconds |
| workspace-write | 3,600 seconds | 5 seconds | 0.2 seconds | 5 seconds |

Values are finite, positive, internally coherent catalog values. This Gate does
not expose arbitrary timing overrides in user/task/provider input. Later
configuration may select only approved policy IDs or safely bounded values
under a separate contract; it cannot disable deadlines or cleanup.

The supervisor:

1. starts the child in an isolated process group/session at the exact approved
   repository context;
2. captures stdout/stderr and elapsed monotonic duration while emitting bounded
   controller-owned heartbeat text;
3. on deadline, cancellation, `KeyboardInterrupt`, or controller exception,
   sends group termination, waits the exact grace, then force-kills the group
   if necessary;
4. drains and closes pipes, waits/reaps the child, and preserves bounded partial
   output plus a typed terminal reason; and
5. returns one result even when executable launch itself fails.

No timeout or interruption is represented solely by a conventional return code.
The normalized result records supervisor source and terminal kind. Tests must
use real parent/grandchild processes for deadline, TERM, KILL, interruption,
pipe-drain, and orphan checks in addition to deterministic fakes.

Output capture has a controller-owned hard byte ceiling and explicit
`stdout_complete`/`stderr_complete` flags. Exact budgets belong to later bounded
input work. Truncation is never silent; incomplete structured output cannot be
accepted as a successful protocol result. Raw output is sensitive and remains
private; concise/default diagnostics use bounded reason codes rather than model
text.

## Normalized physical-attempt result

The adapter returns one strict immutable result:

```yaml
provider_attempt_result_schema_version: 1
logical_invocation_id: <request ID>
physical_attempt_id: <controller-generated ID>
physical_attempt_ordinal: 1
route_profile_sha256: <saved route hash>
operation_id: specification_review
command_audit_sha256: <redacted command-plan hash>
started_at: <UTC timestamp>
finished_at: <UTC timestamp>
duration_seconds: <finite nonnegative number>
return_code: <integer or absent on pre-spawn failure>
terminal_status: succeeded
stdout_artifact: <private bounded output reference/hash/completeness>
stderr_artifact: <private bounded output reference/hash/completeness>
failure_evidence: <typed evidence or absent>
repository_snapshot_before_sha256: <writer only>
repository_snapshot_after_sha256: <writer when safely observed>
```

`terminal_status` is one of `succeeded`, `failed`, `timed_out`, `interrupted`,
`launch_failed`, or `state_unknown`. A transport success means the physical
attempt completed without normalized infrastructure/provider failure. It does
not mean structured output parsed, review passed, implementation changed files,
acceptance criteria passed, or the workflow may advance.

The controller persists the normalized attempt before parsing output, sleeping,
scheduling another physical attempt, or choosing a workflow transition. Results
within one logical invocation share route, operation, command-plan, capability,
prompt, output contract, and repository-before evidence; ordinals are contiguous
from one. Invocation IDs never recur after another group begins.

Workspace-write results bind repository snapshots from the repository adapter,
not provider claims. Inspection failure produces `state_unknown`/missing after
evidence and blocks recovery; it never fabricates a clean or unchanged state.

## Failure normalization

The closed failure kinds remain `quota`, `billing`, `auth`, `rate_limit`,
`unavailable`, `timeout`, `interrupted`, `configuration`, and `provider_error`.
Evidence sources remain `provider_native`, `os_error`, `supervisor`, `stderr`,
explicitly enabled bounded `stdout_tail`, and `returncode`.

Evidence precedence is:

1. supervisor timeout/interruption/cleanup outcome;
2. operating-system spawn/pipe failure;
3. adapter-specific structured native error envelope;
4. narrowly anchored bounded stderr diagnostics;
5. adapter-contract-enabled bounded stdout-tail transport diagnostics; and
6. nonzero return-code fallback.

Prompts, task text, model prose, normal structured content, diffs, reviewer
summaries, and unbounded output are never scanned as transport failure evidence.
Claude native `is_error` result envelopes remain distinct from successful
review content. A successful process with malformed operation output is a
protocol/content failure handled after the attempt is persisted; it is not
reclassified as infrastructure unavailability.

Classifier ID/version and bounded reason code are persisted. Unknown evidence
becomes `provider_error`; it never defaults to unavailable or auth. Provider
failure text cannot authorize retry, fallback, or workflow continuation.

## Retry and fallback authority

The current retry policies remain exact:

- one read-only logical invocation permits at most three physical attempts;
- only normalized `unavailable` schedules another physical attempt;
- delays are exactly 5 then 15 seconds;
- timeout, interruption, quota, billing, auth, rate limit, configuration, and
  provider error terminate the invocation immediately;
- workspace-write logical invocations permit exactly one physical attempt; and
- no automatic cross-provider, cross-model, cross-route, or capability-changing
  fallback exists.

The controller persists the completed attempt and a typed retry decision before
sleep. It revalidates cancellation, saved route/configuration, target ownership,
and capability before the next attempt. A crash during delay resumes from the
durable decision without duplicating the completed attempt; a stale or unclear
decision blocks rather than guessing.

One malformed structured result may trigger the existing same-route content
retry for eligible read-only operations. That retry is a new logical invocation
with a new ID and ordinal one, using the exact saved route, capability, and
prompt. It occurs only after the raw attempt and an immutable unreadable/protocol
record are persisted. A provider-native or transport failure never consumes or
triggers the content retry, and a second malformed result blocks.

Retry counts and delays are route-profile policy, not provider advice. Route
profiles may be stricter than the compatibility policy but cannot exceed future
capability ceilings or silently change a saved run.

## Crash, resume, and writer recovery

A read-only invocation whose process outcome is uncertain may be resumed only
through an explicit controller recovery transition using the exact persisted
request and saved route. The recovery creates a new logical invocation rather
than inventing a missing physical result or appending to an ambiguously running
group. Persisted terminal timeout/interruption/failure is reconstructed without
reinvoking the provider unless an existing explicit read-only resume policy
separately authorizes a new invocation.

A workspace-write attempt is armed only after repository-before evidence is
persisted. After return or interruption, the controller captures repository
after evidence before interpreting success. Timeout, interruption, nonzero
return, missing result, controller crash, or post-inspection failure never
automatically retries or ordinarily resumes the writer.

Writer recovery retains the existing explicit choices:

- `retry_restored` is permitted only after the exact saved pre-attempt repository
  state is proven and creates a new armed attempt/invocation; and
- `adopt_current` records an explicit operator reconciliation and continues from
  observed changes without fabricating provider success or invoking the writer.

No adapter may reset, clean, checkout, stash, delete, or otherwise restore a
workspace. Target ownership and the repository adapter remain authoritative for
all state comparisons.

## Doctor, dry-run, audit, and migration

`doctor` calls only `probe_local()`. It may check registered descriptor coherence,
executable discoverability, and safe non-secret local authentication status. It
does not invoke a model, contact a provider/network, create credentials/config,
repair permissions, spawn a provider command, or expose environment values.
Unknown authentication is reported as unknown.

Dry-run resolves route profiles and validates operation/adapter/capability/
supervision compatibility without building a sensitive final prompt or invoking
the adapter. It reports stable IDs and hashes, not full commands, credentials,
prompts, or provider output.

Reports distinguish logical invocations, physical attempts, retry decisions,
transport failures, protocol failures, and successful operation-specific
results. They use stable role/route/adapter/model/operation IDs; display labels
are optional presentation. Timing is physical-attempt time and never inferred
for historical records.

Existing schema-12 records retain their exact classification. A future migration
may preserve known route identities, operation IDs, attempt grouping, raw output,
failure evidence, capability, and repository fingerprints, but cannot infer a
route-profile hash, adapter descriptor version, prompt hash, command audit hash,
output completeness, pre-spawn armed state, or durable retry decision. Migrated
records remain execution-refused unless every authority fact is separately
proven by the Gate 4 migration contract.

## Non-goals

This Gate adds no adapter implementation, provider SDK/API, model discovery,
catalog UI, capability profile, new route/model, live probe, telemetry, token or
cost accounting, secret store, schema migration, CLI, fallback, or provider
invocation. It does not access Jobs, call live providers, or change current
commands, authority, retries, ownership, Git gates, storage, or compatibility
identifiers.

## Adversarial matrix

| ID | Scenario | Required outcome |
|---|---|---|
| G35-01 | Adapter descriptor is missing, duplicated, hash-incoherent, unsupported, or dynamically supplied by configuration. | Route resolution fails before run creation or provider work. |
| G35-02 | Route role, operation, adapter, model, builder, output, capability, supervision, or retry fields disagree. | Strict validation rejects the route; no field is inferred from display name. |
| G35-03 | Display name changes while the saved control fields/hash remain valid. | Presentation changes only; routing and authority remain unchanged. |
| G35-04 | Model/provider picker attempts to combine an arbitrary model with another route's permissions. | Rejected; selection is by complete registered route ID. |
| G35-05 | Saved route is removed, modified, unavailable, or differs from current default on resume. | Resume blocks; no provider/model/route fallback or silent catalog upgrade occurs. |
| G35-06 | Task, provider, config, or UI supplies executable path, builder, raw flags, shell text, classifier, or environment value. | Input is rejected/ignored as authority; only registered adapter code builds attempts. |
| G35-07 | Model ID or prompt begins with dashes, contains NUL, or attempts argument injection. | Typed validation/end-of-options handling prevents flag injection; invalid values fail before spawn. |
| G35-08 | Child environment contains secrets or repository-controlled executable/config hooks. | Minimal adapter-owned environment prevents authority expansion; secrets never enter persisted audit or diagnostics. |
| G35-09 | Invocation operation/role/route/output/capability does not match the saved profile. | Arming fails before process start. |
| G35-10 | Workspace-write invocation lacks target ownership, mutex, prompt hash, or repository-before evidence. | Controller refuses to arm or spawn it. |
| G35-11 | Controller crashes after arming but before observing process start. | State remains explicitly uncertain; absence of result is not treated as not-started or success. |
| G35-12 | Deadline expires while parent and grandchild remain alive. | Supervisor TERM/KILLs the process group, drains partial output, reaps it, and records typed timeout evidence. |
| G35-13 | Keyboard interrupt/cancellation/controller exception occurs during execution. | Same bounded group cleanup runs; typed interruption/uncertainty is persisted and no orphan remains. |
| G35-14 | Executable is missing or cannot launch. | One `launch_failed` attempt with OS/configuration evidence is returned; no retry/fallback occurs. |
| G35-15 | Timing values are zero, negative, nonfinite, incoherent, or attempt to disable cleanup. | Profile validation fails before spawn. |
| G35-16 | Output exceeds capture budget or pipe draining is incomplete. | Completeness flags/reasons are persisted; incomplete structured content cannot be accepted as success. |
| G35-17 | Model prose says quota/auth/503/timeout while transport succeeds. | Prose is not transport evidence and cannot cause retry or failure reclassification. |
| G35-18 | Structured provider-native error appears in the documented native envelope. | Adapter records native failure before content parsing; it does not consume malformed-content retry. |
| G35-19 | Stderr has anchored transport evidence, stdout tail has model prose, and return code is nonzero. | Documented precedence selects stderr; stdout scanning remains disabled unless adapter contract explicitly enables bounded transport evidence. |
| G35-20 | Evidence is ambiguous or unknown. | Normalizes to `provider_error`, never `unavailable`, auth, success, or fallback authority. |
| G35-21 | Read-only attempt returns `unavailable` twice then succeeds. | Three contiguous physical attempts share one logical invocation, with persisted retry decisions/delays 5 and 15 seconds. |
| G35-22 | Read-only attempt returns timeout, interruption, quota, billing, auth, rate limit, configuration, or provider error. | Invocation ends after that attempt; no automatic retry. |
| G35-23 | Workspace-write attempt returns unavailable or any other failure. | Exactly one physical attempt occurs; repository after-state is observed and writer recovery blocks appropriately. |
| G35-24 | Crash occurs after an attempt result but before retry sleep/next spawn. | Persisted result/decision prevents duplicate ordinals; uncertain retry state blocks rather than guesses. |
| G35-25 | Successful transport returns malformed review content. | Raw attempt and unreadable/protocol evidence persist; one eligible same-route content retry is a new logical invocation. |
| G35-26 | Content retry returns malformed output again or a native/transport failure. | It blocks with exact evidence; no third content invocation or alternate route occurs. |
| G35-27 | Adapter reports transport success but repository after-state is unknown or writer made no valid change. | Workflow does not treat it as implementation success; deterministic repository handling blocks. |
| G35-28 | Writer result is absent after crash or repository differs from pre-attempt state. | Ordinary resume never reinvokes; explicit restore/retry or adopt-current evidence is required. |
| G35-29 | `retry_restored` is requested without exact pre-state restoration. | Refused before provider invocation and no recovery decision is fabricated. |
| G35-30 | `adopt_current` is approved. | Reconciled repository evidence is persisted without a new writer call or invented provider success. |
| G35-31 | `doctor` or dry-run evaluates adapters/routes. | Only local non-mutating probes and static validation run; no provider/network call, credential repair, prompt, or write occurs. |
| G35-32 | Historical attempt lacks new route/adapter/prompt/completeness/arming evidence. | Migration preserves known facts but invents none and remains execution-refused unless separately proven. |
| G35-33 | Proposal adds adapter code, new provider/model, capability ceilings, picker UI, telemetry, secret management, schema migration, or Gate 4 work. | It is out of scope pending its separately approved contract and implementation gate. |

## Approval and implementation evidence

The owner approved this Gate 3.5 contract on 2026-08-03. Gate 3.5 is a
documentation-only contract-definition deliverable: adapter extraction,
route/profile catalogs, controller-owned attempt persistence/retry wiring,
supervisor changes, schema migration, CLI changes, and installed-package
validation belong to the explicitly later Gate 4 work. No runtime source, test,
fixture, run record, provider, target checkout, Git side effect, commit, or push
changed for this Gate.

Publication evidence: commit `b10f0b66831143721dcfb48df1661e56a1580dfb`
(`Define provider adapter route profiles`) is on `origin/main`.

The approved contract derives from the Gate 3.5 tracker item,
Milestone 2 provider-adapter/catalog/routing requirements and exit criteria,
C-2 and A-1, and the current stable route identities, operation map, command
builders, process supervisor, normalized failure evidence, provider execution
and attempt records, logical/physical metrics, structured-output retry, blocked
resume, writer arming/recovery, and deterministic tests. Baseline is clean
synchronized `main` at
`df07d7faf8766e93f63f9219a16b078bb0018090`.

Validation confirmed authoritative local Markdown links, all 33 `G35-*` IDs
unique, and tracked/new-file `git diff --check` whitespace checks passing. No
live-provider or Jobs-repository validation was performed.
