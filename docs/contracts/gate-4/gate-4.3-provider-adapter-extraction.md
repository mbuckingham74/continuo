# Gate 4.3 contract — Extract current provider commands into provider adapters

**Status:** repository-owner approved on 2026-08-04; implementation and
deterministic verification are complete. The architectural reset was committed
at `402b85128925a38fc5cbc4e23e4a6600f14bdd8f`, and repository-owner approval
was recorded at `1ff8479fdd50795e071852cf1170df1588c50dba`.

Implementation and correction evidence:

- `5e9287d3f8f4fa2f4b6c4773d62d29584bdd7ab9` — initial Gate 4.3 implementation;
- `eac8fd4ccec47c5918b35e601f28424d24ffc9b9` — adapter-boundary corrections;
- `2ea6673505b807a25e7ac0f21e8c9fddee1606b5` — single-pass Sonnet normalization
  correction.

Final merge/publication evidence is the documentation completion commit and
the resulting `main` tip.

**Runtime baseline:**
`9fd0de0e8141d26e2f8f995fc3c63e11c08f024c` (the published Gate 4.2
implementation). Runtime and tests at the recovery commit are byte-identical to
that baseline.

## Decision

Gate 4.3 is a behavior-preserving adapter extraction, not a provider-lifecycle
or persistence redesign.

It introduces the two code-owned compatibility adapters, `claude_cli` and
`codex_cli`, behind the three operations approved by
[Gate 3.5](../gate-3/gate-3.5-provider-adapter-route-profile.md):
`probe_local`, `build_attempt`, and `execute_attempt`. One call to
`execute_attempt` performs exactly one physical attempt. A controller-owned
compatibility runner retains the current in-memory same-provider retry loop and
returns the existing `ProviderExecution` / `ProviderAttempt` values to the
existing controller paths.

This item keeps run schema 13 and every persisted schema-13 field unchanged. It
does not add a generalized durable invocation lifecycle, pre-spawn invocation
or physical-attempt records, an invocation graph, a new protocol-evidence
system, or a `13 -> 14` migration.

That is an explicit split, not a rejection of the approved Gate 3.5 contract as
amended by Gate 4.1. Those contracts require additional controller-owned
durability, account/effort evidence and revalidation, bounded audit/output
evidence, child-environment hardening, retry revalidation, and reporting
distinctions. Those requirements move intact to the immediately following
bounded Gate 4.4 conformance item, before provider catalogs or selectable routes
can claim Gate 3.5 complete. The
[execution plan](../../EXECUTION_PLAN.md) tracks that follow-on separately.

The split is the smallest implementation-ready boundary that delivers the
tracker's exact outcome:

> Extract current provider commands into provider adapters without changing
> no-fallback policy or permission ceilings.

## Why this boundary follows from approved sources

The sources establish four different classes of work:

| Class | Source-backed conclusion for Gate 4.3 |
|---|---|
| Adapter extraction required now | Gate 3.5 defines the three closed adapter operations and makes one adapter execution one physical attempt. The [execution plan](../../EXECUTION_PLAN.md) gives command extraction its own bounded Gate 4 item. |
| Baseline behavior that must remain exact | The [architecture reference](../../ORCHESTRATION_ENGINE.md), [Gate 3.5](../gate-3/gate-3.5-provider-adapter-route-profile.md), [Gate 3.6](../gate-3/gate-3.6-capability-profiles-permission-ceilings.md), and the baseline [`providers.py`](../../../providers.py) / [`orchestrator.py`](../../../orchestrator.py) fix command, prompt, retry, failure, recovery, writer, and authority behavior. |
| Approved work split to the next Gate 4 item | Gate 3.5 requires durable request/attempt arming, per-attempt persistence, bounded command/output evidence, a minimal child environment, between-attempt revalidation, and reporting distinctions. [Gate 4.1](gate-4.1-effort-provider-account-amendment.md) adds account/effort evidence to that future lifecycle. [Gate 4.2](gate-4.2-validated-resolved-configuration.md) expressly states that schema-13 `ProviderRecord` does not yet implement it. |
| Later durability architecture | The roadmap treats an event/audit storage architecture as Milestone 4 work, and Gate 8 tracks durable operation. Neither source makes an event log, generalized invocation platform, or recovery rewrite a prerequisite for command extraction. See the [roadmap](../../ENGINE_ROADMAP.md) and [execution plan](../../EXECUTION_PLAN.md). |

The baseline already persists a completed provider execution before parsing its
output and recovers a matching saved result without invoking the provider
again. The prior draft's claim that this separation does not exist was
incorrect. What schema 13 lacks is narrower: it does not durably arm each
generic physical attempt or persist each retry decision before sleeping and
starting the next process. That missing approved durability is real, but it is
independent of moving command construction behind adapters.

The split also preserves the roadmap's rule to implement and review one bounded
item at a time. Gate 4.3 creates the one-attempt seam that the later conformance
item will persist around; it does not create a throwaway multi-attempt adapter.

## Mechanism decisions

| Question | Gate 4.3 decision | Later obligation, if any |
|---|---|---|
| Run schema 14 | **No.** This item changes no persisted value. Schema numbers change only with a persisted contract. | The conformance item will select the then-next schema version when it adds the mandatory durable fields; it must not assume that number in advance. |
| Generalized durable invocation lifecycle | **No.** Keep the current schema-13 lifecycle and recovery exactly. | Implement only the bounded Gate 3.5 request/arm/result/retry contract for approved operations. Full event/audit architecture remains Gate 8. |
| Pre-spawn invocation and physical-attempt records | **No new generic records.** Preserve the existing saved provider context and the writer-specific pre-spawn marker. | **Mandatory under Gate 3.5.** The next conformance item must add the minimum durable request and arm needed before a process starts. |
| Logical/physical attempt identity | Keep the existing post-return `logical_invocation_id` and contiguous `physical_attempt_ordinal` behavior unchanged. | The conformance item moves creation/persistence to the approved pre-spawn boundary without inventing a second identity system. |
| Invocation parent/child graph | **No.** It is not required by an approved source or by baseline parity. | A content retry must be identifiable as a new logical invocation, but a graph, DAG, parent triplet, cycle detection, and traversal API are not presumed. |
| Protocol-failure evidence record | **No new record in this extraction.** Retain existing `UnreadableReviewRecord` behavior exactly. | Gate 3.5 requires a minimal immutable malformed-output marker linked to the saved attempt before an eligible content retry. The lifecycle contract must solve that narrow gap, including Sol, without designing an evidence platform. |
| Exhaustive schema-13 migration/recovery redesign | **No.** No migration exists in this item. | Any later schema migration preserves provable facts, invents none, and execution-refuses unsafe provider work. It need not redesign every historical stage. |
| Output artifacts, completeness flags, capture budgets | **No.** Raw stdout/stderr capture stays exactly as it is. | These are separately bounded lifecycle/input-hardening decisions. No unapproved constants or mini artifact store are introduced here. |
| Minimal child-environment allowlist | **No.** Real subprocesses continue to inherit the controller environment. | Gate 3.5 requires a future adapter-owned policy, but the exact CLI-auth-safe allowlist needs a separate bounded decision and validation. |
| Redacted command audit | `build_attempt` returns a bounded in-memory audit view, but schema 13 continues to persist its raw command list. | Gate 4.4 must persist the approved redacted audit and final-prompt hash/artifact without losing historical facts. |

## Invariants protected

1. Workflow and policy decisions remain in deterministic controller code.
2. Configuration selects only the four existing complete route/account
   bindings; adapters cannot select or synthesize a route.
3. Adapter lookup is exact by the saved `provider_adapter_id`; failure never
   selects another adapter, route, model, account, or capability.
4. The four provider-facing argv vectors and prompts are byte-for-byte
   equivalent to the baseline.
5. Review and advisory operations remain read-only; the implementation route
   retains only its current workspace-write authority and never gains Git,
   publication, approval, configuration-mutation, or tool-network authority.
6. Read-only transport retry remains limited to `unavailable`, at delays of 5
   and 15 seconds, for at most three physical attempts. A writer remains
   single-shot.
7. The structured-output retry remains one same-route, same-prompt logical
   invocation on the live Sonnet/Sol path only.
8. Failure classification, evidence precedence, process supervision, return
   codes, heartbeats, and block-stage mapping remain exact.
9. Schema-13 persistence, recovery, reporting, migration classification, and
   raw full command list remain exact.
10. Writer pre-state arming, post-state observation, failure blocks, ordinary
    resume refusal, and explicit recovery remain exact.
11. `doctor` remains local and non-mutating; dry-run never builds a real prompt
    or executes an attempt.
12. No live provider, credential, network probe, external target, Jobs checkout,
    or Git publication is used to implement or verify this item.

## Bounded implementation scope

In scope:

- one strict code-owned adapter protocol and registry;
- code-owned descriptors for `claude_cli` and `codex_cli`;
- the three closed adapter operations;
- exact Claude and Codex compatibility command construction inside adapters;
- one physical attempt per `execute_attempt` call;
- a shared controller-owned compatibility runner for the existing retry loop;
- relocation of Claude-native failure-evidence extraction behind the Claude
  adapter;
- use of the saved schema-2 route/account binding as adapter input;
- `doctor` delegation to `probe_local` while preserving its version-2 output;
- compatibility facades where needed so existing imports and deterministic test
  seams continue to work without containing provider-specific argv logic; and
- focused parity, boundary, and regression tests.

Out of scope:

- any run-schema, run-model, migration-registry, run-file, report-schema, or
  recovery-state change;
- durable generic invocation requests, attempt arms, retry decisions, or
  protocol-failure records;
- a new provider, model, route, effort value, account, endpoint, authentication
  method, executable, command flag, output contract, or fallback;
- selectable provider/model/effort catalogs or a picker;
- generalized capability-profile and permission-ceiling enforcement, which is
  a later Gate 4 item;
- credential storage, credential repair, Keychain, authentication or
  connectivity probes, and environment allowlisting;
- output truncation, output artifacts, completeness flags, token/cost telemetry,
  redacted command replacement, an event log, or replay;
- repository/task adapters, generic package consolidation, new CLI aliases,
  Rust/TUI work, or project verification plugins; and
- runtime source or test changes before this contract receives owner approval.

## Adapter boundary

### Code-owned descriptors and registry

Each adapter exposes the strict descriptor shape approved by Gate 3.5:

```yaml
provider_adapter_schema_version: 1
provider_adapter_id: codex_cli
adapter_contract_id: continuo.provider-adapter.v1
transport_kind: local_process
command_builder_ids:
  - codex-cli.compatibility-builder.v1
failure_classifier_id: codex-cli.failure-classifier.v1
local_probe_id: codex-cli.local-probe.v1
supports_process_group_supervision: true
supports_partial_output_capture: true
descriptor_sha256: <canonical descriptor hash>
```

The Claude descriptor carries `claude-cli.compatibility-builder.v1`,
`claude-cli.failure-classifier.v1`, and `claude-cli.local-probe.v1`; the Codex
descriptor carries the corresponding `codex-cli.*` IDs shown above. Descriptor
lookup is exact and unique by `provider_adapter_id`. Configuration cannot
register adapters, Python objects, executable paths, classifiers, raw flags,
shell fragments, or probe behavior.

Descriptor validation is code/catalog validation only. Gate 4.3 does not add a
descriptor ID or hash to `WorkflowRun`, `ProviderRecord`, or any migration. The
approved durable lifecycle can bind any required descriptor evidence later
without forcing it into this extraction.

### Closed operations

The adapter interface contains exactly:

```text
probe_local() -> LocalProbeResult
build_attempt(request: AdapterAttemptRequest) -> AttemptPlan
execute_attempt(plan: AttemptPlan) -> ProviderAttempt
```

There is no adapter `run`, retry, sleep, fallback, route selection,
configuration load, persisted-run load, workflow transition, recovery action,
Git action, credential repair, or shell-string operation.

`execute_attempt` performs one and only one physical process attempt. It passes
the immutable plan unchanged to the shared one-attempt executor and contributes
only its registered provider-native evidence extractor to the shared
normalization step. It never calls itself or another adapter, calls
`probe_local`, sleeps, or decides whether another attempt is allowed.

### Transient request and plan

`AdapterAttemptRequest` and `AttemptPlan` are strict in-memory values. They are
not new persisted schemas and must not appear in run JSON.

The controller constructs the request only after the existing
`_require_provider_binding` and operation/capability checks succeed. It contains
only the facts needed for one command:

- the controller-owned operation ID;
- the complete saved route profile and hash;
- the complete saved non-secret provider-account profile and hash;
- the exact controller-facing prompt;
- the exact controller-selected repository working directory; and
- the current coarse capability, `read_only` or `workspace_write`.

The adapter validates, without re-resolving configuration:

- request role and operation agree with the saved route;
- saved adapter ID equals the selected adapter;
- saved command-builder policy is registered by its descriptor;
- saved model is the exact compatibility model for the route;
- saved account uses the same adapter;
- effort is the saved `provider_default` omission policy;
- capability and output-contract IDs equal the installed compatibility record;
- no would-be argv or working-directory string contains a NUL; and
- no task, configuration, request, prompt, provider output, or CLI option can
  replace the adapter-owned executable token, raw flag, tool, permission, or
  shell-free argv as authority.

`build_attempt` returns an immutable plan with the exact argv, working
directory, display label, coarse capability, output-contract ID, existing
supervision policy, SHA-256 of the exact provider-facing UTF-8 prompt, and a
bounded redacted command-audit view. That audit view retains stable adapter,
builder, route, model, operation, output-contract, prompt-hash, effort mode,
registered effort ID when present, and enforcement/omission policy ID facts; it
does not contain the raw prompt, credentials, inherited environment, or provider
output. It remains transient in Gate 4.3 because schema-13 persistence is
unchanged.

`build_attempt` is pure: it must not spawn or probe a process, call
`probe_local`, sleep, access a network, inspect credentials, write a file, mutate
the request, or mutate controller/repository state. Its plan is data for one
physical attempt. It does not contain a workflow next stage, retry decision,
fallback, Git action, or approval.

The runner may reuse that immutable plan for current same-provider transport
retries; therefore every physical attempt in the existing logical invocation
uses identical argv and repository context.

### One-attempt execution pipeline

The ownership pipeline is concrete and singular:

1. the controller-owned compatibility runner calls the selected adapter's
   `execute_attempt` with one immutable plan;
2. the adapter passes that plan's exact argv, working directory, process context,
   and supervision policy without mutation to the shared one-attempt executor;
3. the executor performs exactly one launch/supervision attempt and returns raw
   completed-process data plus any supervisor or operating-system evidence;
4. the adapter contributes optional provider-native evidence—only the Claude
   result-envelope evidence in this compatibility gate;
5. one shared normalizer applies the exact evidence precedence once and returns
   one normalized `ProviderAttempt`; and
6. the compatibility runner consumes that normalized attempt for retry policy
   and `ProviderExecution` assembly; it does not reclassify it.

The live adapter path is normalized exactly once before persistence.
`_record_provider` validates and records adapter results but does not classify
them again. If a retained compatibility facade accepts a legacy unnormalized
test/injected result, that facade performs the same normalization exactly once
before recording. Recovery of an already-saved historical/schema-13 result keeps
its existing reconstruction normalization; it is not a second live-attempt pass.

Ordinary provider execution does not preflight through `probe_local`. A missing
or unlaunchable fixed executable still constitutes exactly one attempted
execution: it produces the baseline synthetic return code 127 and normalized OS
evidence, then follows the existing configuration/provider-failure block without
retry or fallback.

## Exact compatibility commands

Let `P` be the exact controller-facing prompt. Let
`SONNET_REVIEW_SCHEMA_JSON` be the exact current minified JSON produced with
`json.dumps(SONNET_REVIEW_SCHEMA, separators=(",", ":"))`. The adapters must
build these lists exactly, including order, spelling, duplicate `--config`
flags, end-of-options marker, and final prompt position.

### Claude review adapter

For both `specification_review` and `implementation_review`:

```text
[
  "claude", "-p",
  "--model", "sonnet",
  "--permission-mode", "plan",
  "--tools", "Read,Glob,Grep",
  "--output-format", "json",
  "--json-schema", SONNET_REVIEW_SCHEMA_JSON,
  "--", P,
]
```

The adapter adds no prompt preamble. Claude native error-envelope inspection is
adapter-owned; validation of the operation's `ReviewResult` remains in the
existing parser/controller path and stays byte/semantics compatible.

### Codex advisory adapter

For `policy_clarification`:

```text
[
  "codex", "exec",
  "--model", "gpt-5.6-terra",
  "--sandbox", "read-only",
  "--", P,
]
```

For `escalation_guidance`:

```text
[
  "codex", "exec",
  "--model", "gpt-5.6-sol",
  "--sandbox", "read-only",
  "--", P,
]
```

### Codex writer adapter

For `implementation_write` and `correction_write`, define `G` as the exact
baseline constant:

```text
You have workspace-write only for bounded implementation edits. Do not commit, push, create or switch branches, merge, rebase, reset, or modify any Git metadata (.git). The controller alone has Git authority.
```

The provider-facing prompt is exactly `G + "\n\n" + P`, and argv is:

```text
[
  "codex", "exec",
  "--model", "gpt-5.6-luna",
  "--sandbox", "workspace-write",
  "--config", "approval_policy=never",
  "--config", "sandbox_workspace_write.network_access=false",
  "--", G + "\n\n" + P,
]
```

No compatibility command supplies an effort flag. This preserves the
`provider_default` effort records approved by Gate 4.1 and persisted by Gate
4.2.

### Process context

All four commands preserve the current process context:

- argv list execution with no shell;
- the exact controller-selected repository as `cwd`;
- inherited controller environment, because the baseline passes no `env`;
- captured text stdout/stderr;
- a new process group/session for real subprocesses; and
- the prompt as data after `--`, including prompts beginning with a dash.

Gate 4.3 neither claims that inherited environment is the final hardening model
nor changes it while exact externally managed CLI-session behavior is the
compatibility requirement. The fixed executable tokens remain `claude` and
`codex`; inherited `PATH` therefore continues to control local binary resolution.
No task/configuration/request field can replace those tokens. Binary provenance
and a CLI-auth-safe child-environment allowlist are deferred together to Gate
4.4.

## Prompt ownership

Controller prompt construction remains byte-equivalent to the baseline:

| Operation | Controller-owned source |
|---|---|
| `implementation_write` | `_task_prompt` |
| `correction_write` | `_task_prompt` plus the current correction text, finding summary, and optional Sol guidance |
| `specification_review` | `_spec_review_prompt` |
| `implementation_review` | `_implementation_review_prompt`, including current history, unreadable markers, changed files, and diff |
| `escalation_guidance` | `_sol_prompt` |
| `policy_clarification` | `_terra_prompt` |

Adapters do not read task files, diffs, run files, policy decisions, review
history, configuration sources, or provider output to construct a prompt. The
Claude adapter adds no text. The Codex adapter adds only the fixed Luna Git
prohibition for writer operations. No whitespace, delimiter, role name,
history bound, or wording changes are part of this item.

## Ownership of execution policy

### Adapter-owned

- local executable discoverability for its own fixed executable;
- exact provider-specific argv construction;
- its fixed provider-facing preamble or transport wrapper;
- one `execute_attempt` delegation to the shared one-attempt executor; and
- provider-native failure evidence peculiar to that adapter, currently the
  Claude result envelope.

### Shared controller-owned one-attempt executor and normalizer

- supervision-policy selection from the installed compatibility route;
- process launch, heartbeat, cleanup, raw output capture, and completion;
- normalized supervisor and OS-launch evidence;
- the single precedence pass over supervisor, OS, adapter-native, stderr,
  optional stdout-tail, and return-code evidence; and
- construction of exactly one normalized `ProviderAttempt`.

### Shared controller-owned compatibility runner

- start, completion, and retry messages;
- one-attempt calls into the selected adapter;
- the current capability-aware transport retry loop;
- fixed retry delays and sleeper; and
- assembly of the existing `ProviderExecution` with its ordered
  `ProviderAttempt` tuple.

The runner is controller-owned transport policy, not an adapter fallback layer.
It consumes already-normalized evidence and never reclassifies it or chooses a
workflow stage, correction, approval, Git action, or writer recovery.

### Deterministic controller-owned

- prompt construction;
- saved route/account selection and validation;
- adapter lookup;
- stage and block transitions;
- schema-13 persistence and logical-invocation recording;
- parsing operation output and deciding the one content retry;
- repository snapshots/fingerprints and writer recovery;
- correction/escalation budgets;
- human policy and Git approvals; and
- every Git command.

Provider output cannot change these ownership boundaries.

## Supervision parity

The shared supervisor is relocated or reused, not redesigned. It preserves:

| Policy fact | Read-only | Workspace write |
|---|---:|---:|
| hard deadline | 1,800 seconds | 3,600 seconds |
| TERM grace | 5 seconds | 5 seconds |
| poll interval | 0.2 seconds | 0.2 seconds |
| heartbeat interval | 5 seconds | 5 seconds |

For real processes it must continue to:

1. start an isolated process group/session at the exact repository;
2. capture text stdout and stderr;
3. emit the current start/heartbeat/completion text and role display label;
4. on deadline, send TERM, wait five seconds, then KILL if needed;
5. use the same cleanup on `KeyboardInterrupt` and reap the direct child;
6. preserve available partial output and append the same cleanup diagnostic;
7. return code 124 for timeout and 130 for interruption; and
8. clean up the process group before re-raising any other controller exception.

Gate 4.3 adds no output cap or completeness flag. The existing 8 KiB constant
bounds only an optionally enabled stdout-tail classifier channel; it does not
bound captured output.

## Failure-classification parity

The closed failure kinds remain:

```text
quota
billing
auth
rate_limit
unavailable
timeout
interrupted
configuration
provider_error
```

Effective evidence precedence remains:

1. supervisor timeout/interruption outcome;
2. operating-system launch failure;
3. Claude structured native error envelope for `claude_cli`;
4. narrowly line-anchored stderr diagnostics;
5. the bounded stdout tail only when explicitly enabled; and
6. nonzero return-code fallback.

Except for a recognized Claude native `is_error=true` envelope, stderr,
stdout-tail, and return-code normalization is entered only when the return code
is nonzero. A zero return code therefore stays transport-successful even when
stderr or ordinary stdout contains failure-like prose. All compatibility plans
keep stdout-tail classification disabled. Prompts, model prose, diffs, review
content, and ordinary stdout never classify a transport failure.

The mappings remain exact:

- HTTP 402 -> `billing`;
- HTTP 401/403 -> `auth`;
- HTTP 429 -> `rate_limit`;
- HTTP 500/502/503/504 -> `unavailable`;
- launch `EACCES`, `ENOENT`, `ENOEXEC`, or `EPERM` -> `configuration`;
- unknown launch/transport failure -> `provider_error`; and
- a Claude envelope is native failure evidence only when `type=result` and
  `is_error=true`.

An operating-system launch exception produces the same synthetic completed
result as the baseline: return code 127, empty stdout, diagnostic stderr, an
`os_error` source, and a bounded `TypeName[:errno]` failure code. It is one
normalized physical attempt, not a failed preflight.

A native Claude error may classify an exit-zero result as failure and takes
precedence over conflicting stderr. A successful Claude envelope still must
pass the existing exact success-envelope and `ReviewResult` validation.

Read-only failure-to-stage mapping remains:

| Failure | Stage |
|---|---|
| `quota` | `blocked_provider_quota` |
| `billing` | `blocked_provider_billing` |
| `auth` | `blocked_provider_auth` |
| `rate_limit` | `blocked_provider_rate_limit` |
| `unavailable` | `blocked_provider_unavailable` |
| `timeout` | `blocked_provider_timeout` |
| `interrupted` | `blocked_provider_interrupted` |
| `configuration` | `blocked_provider_configuration` |
| `provider_error` | `blocked_provider_failure` |

The block message continues to state that no alternate provider was invoked.
Writer failures continue through writer-state classification instead of this
read-only block map.

## Retry and no-fallback parity

### Transport retry

The controller-owned compatibility runner reproduces the current loop exactly:

- `execute_attempt` is called once for each physical attempt;
- only a `read_only` attempt normalized as `unavailable` is eligible;
- attempt 1 may schedule delay 5 seconds;
- attempt 2 may schedule delay 15 seconds;
- attempt 3 is final;
- every attempt uses the same adapter, plan, argv, route, model, account,
  prompt, capability, and repository;
- timeout, interruption, quota, billing, auth, rate limit, configuration, and
  provider error stop immediately; and
- `workspace_write` is always one physical attempt and never calls the sleeper.

The runner returns the same final `ProviderExecution`: final command/result,
summed duration, final classification, declared capability, and ordered attempt
tuple with the same `retry_scheduled` bits. Gate 4.3 deliberately preserves the
baseline's in-memory retry timing; it does not persist between attempts. The
next conformance item changes that durability boundary without changing adapter
argv or retry policy.

### Structured-output content retry

Content retry remains controller workflow policy, separate from transport
retry:

- only live Sonnet review parsing and live Sol escalation parsing are eligible;
- the exact same saved prompt, route, adapter, model, account, capability, and
  operation is invoked once more;
- `_record_provider` assigns that high-level retry a new
  `logical_invocation_id` and starts its physical ordinal at 1;
- that logical invocation may itself receive the ordinary read-only transport
  retry sequence;
- a native/transport failure does not consume or trigger content retry;
- a second malformed result blocks as `blocked_provider_output`; and
- Terra and Luna never receive a content retry.

The existing evidence asymmetry is preserved rather than silently redesigned:
a second malformed Sonnet result records an `UnreadableReviewRecord`; malformed
Sol guidance blocks without that review-specific marker. A first malformed
live result is present in raw `ProviderRecord` output but has no separate
protocol marker. Gate 3.5 requires the later conformance item to close this narrow
gap before claiming full compliance.

There is no cross-provider, cross-model, cross-route, cross-account, or
capability-changing fallback. An adapter cannot recommend or initiate one.

## Schema-13 persistence parity

`CURRENT_RUN_SCHEMA_VERSION` remains 13. This item does not change
[`models.py`](../../../models.py), [`run_migrations.py`](../../../run_migrations.py),
or the logical JSON shape of any run record.

The following remain exact:

- the complete resolved schema-2 configuration is persisted before provider
  work;
- `provider_resume_stage`, `provider_resume_prompt`,
  `provider_resume_identity`, and `provider_resume_operation_id` are complete
  or all null;
- callers persist that provider context and precise in-progress stage before
  the initial provider call;
- a completed high-level `ProviderExecution` is converted to one or more
  `ProviderRecord` values and saved before parsing or advancing;
- `_record_provider` creates one post-return `logical_invocation_id` for the
  execution and contiguous ordinals starting at 1;
- every record keeps the full command list, raw stdout/stderr, duration,
  failure evidence, coarse capability, writer fingerprints, and
  `retry_scheduled` bit;
- parsed reviews remain immutable `ReviewRecord` values linked by provider
  record index;
- current report grouping and metrics continue to use the existing logical IDs
  and physical ordinals; and
- all schema 1-13 classification, migration, refusal, atomic-save, privacy, and
  status/report behavior remains unchanged.

Gate 4.3 does not redact the stored command, replace prompts with hashes,
introduce artifacts, add adapter descriptor evidence to a record, or reinterpret
historical logical IDs. Those are persistence changes and require their own
approved migration contract.

## Recovery parity

### Read-only recovery

The current recovery rules remain exact:

- at `spec_reviewing`, `reviewing`, `terra_resolving`, or `sol_escalating`, only
  `provider_runs[-1]` is considered, and it is consumed without provider
  execution only when its operation/control identity and the pending saved
  operation/control identity both match the exact stage expectation;
- absence or mismatch blocks as `blocked_interrupted_provider` rather than
  guessing whether a process ran;
- recovered malformed Sonnet output records the existing unreadable marker and
  blocks without content retry; if that final provider-record index is already
  linked to an `UnreadableReviewRecord`, recovery reuses that fact and appends no
  duplicate marker;
- recovered malformed Sol output blocks without content retry;
- quota, billing, auth, rate limit, unavailable, configuration, generic
  provider failure, and invalid-output blocks remain explicitly resumable using
  the exact saved stage, route, operation, and prompt;
- timeout, interruption, and `blocked_interrupted_provider` remain
  non-resumable by the ordinary provider-block path; and
- a provider-block resume creates one new high-level same-route invocation and
  then consumes it through the existing recovery path, which never grants a
  content retry.

Adapter extraction must not change when provider context is cleared, retained,
or saved.

### Writer recovery

The existing writer-specific pre-spawn durability remains the authority:

1. validate repository path, branch, `HEAD`, and origin;
2. enumerate changed paths and compute the pre-attempt fingerprint;
3. persist the exact prompt, route/operation, stage, and
   `active_writer_attempt` before invoking Luna;
4. call the Codex adapter once;
5. inspect and save post-state even when the attempt fails;
6. link the saved `ProviderRecord` to the active writer attempt; and
7. classify uncertainty from repository evidence without modifying the
   workspace.

Outcomes remain:

- exact pre-state -> `blocked_writer_retry_required`;
- different trustworthy state -> `blocked_writer_partial_changes`; and
- untrustworthy inspection -> `blocked_writer_state_unknown`.

Ordinary resume never invokes Luna. `retry_restored` requires the exact saved
pre-state, records the operator decision, arms a fresh writer attempt, and calls
Luna once with the same saved prompt. `adopt_current` requires trustworthy,
nonempty current changes, records the reconciliation, invokes no provider,
fabricates no success, and enters verification. Neither adapters nor the runner
may reset, clean, checkout, stash, delete, or otherwise restore a workspace.

## Doctor and dry-run

`probe_local` is the adapter form of today's local executable check. For this
compatibility gate it may only:

- use local, non-mutating executable discovery for its fixed `claude` or
  `codex` executable; and
- return a bounded available/missing result with authentication status unknown.

Executable discovery may consult inherited `PATH`, as today's `shutil.which`
does. The probe must not enumerate or report the environment, inspect credential
variables, run `--version`, invoke a model, contact a provider or network, test
credentials, repair authentication, or write configuration.

`doctor` remains `continuo.doctor.v2` with the same check IDs, ordering, status,
and reason codes. It may aggregate adapter probe results into the existing
`provider_binaries` and `route_capabilities` checks, but `provider_auth` remains
`unknown` / `auth_probe_unavailable`. Only the adapter probe is constrained to
executable discovery; doctor's existing local Git, configuration, storage, and
repository checks remain unchanged.

Dry-run continues to resolve the current configuration, validate its
compatibility bindings, and produce `continuo.run-plan.v2`. It does not call
`build_attempt`, construct a
sensitive final prompt, or call `execute_attempt`. Neither doctor nor dry-run
spawns a provider/adapter attempt; their existing local Git subprocesses are not
removed by this contract.

## Deferred Gate 4.4 conformance contract

The follow-on conformance item is mandatory before Continuo claims the Gate 3.5
provider-adapter lifecycle, as amended by Gate 4.1, implemented. Its bounded
contract must address only the approved gaps:

1. create the logical invocation identity and persist the approved
   `ProviderInvocationRequest` schema-2 value before the first process starts;
2. persist a physical-attempt arm before each `execute_attempt` call with the
   same logical ID, next contiguous ordinal, route/prompt hashes, capability,
   deadline/supervision policy, and required pre-attempt repository evidence;
3. persist each approved normalized physical-attempt result schema-2 value and
   controller retry decision before sleep or the next process;
4. bind the exact adapter descriptor ID/hash, classifier ID/version,
   route/account/effort/capability, output contract, final provider-facing prompt
   or immutable private artifact plus hash, bounded redacted command audit,
   bounded reason code, and supervision policy/evidence;
5. persist the minimum immutable malformed-output evidence linked to the saved
   attempt before an eligible content retry;
6. preserve the writer-specific conservative recovery policy;
7. add the adapter-owned CLI-auth-safe child environment required by Gate 3.5,
   including an explicit binary-provenance decision;
8. add a controller-owned output byte ceiling and explicit stdout/stderr
   completeness flags, without accepting incomplete structured output;
9. before every invocation and physical attempt, revalidate the exact saved
   route/account IDs and hashes, effort mode/ID/enforcement policy, adapter
   equality, account availability, locally observable credential presence,
   identity assurance, capability/ceiling evidence, cancellation, saved
   configuration, target ownership, and repository/writer evidence without
   selecting a replacement;
10. report logical invocations, physical attempts, retry decisions, transport
    failures, protocol failures, and successful operation results distinctly;
11. after a crash during a durable retry delay, resume from the saved decision
    without duplicating the completed attempt, and block rather than guess when
    the decision is stale or unclear;
12. introduce only the then-required adjacent run-schema migration; and
13. preserve known historical facts, invent none, and execution-refuse provider
   work whose authority cannot be proven.

Gate 4.1 already fixes schema version 2 and the mandatory account/effort fields
for the logical request and normalized result; Gate 4.4 does not redesign those
published shapes. It must independently choose only their run-file persistence
layout, the minimum physical-arm and malformed-marker shapes, and the remaining
crash boundaries. Gate 4.3 does not pre-authorize:

- a separate persisted attempt-outcome record;
- invocation parent/child graphs or three-way parent pointers;
- a tagged replacement for all current provider resume fields;
- a raw-evidence mini-store, projection subsystem, or new artifact platform;
- adapter-descriptor IDs/hashes in every persisted object;
- a new protocol-failure reason registry beyond the narrow required marker;
- an exhaustive schema-13 stage matrix when conservative refusal suffices; or
- event sourcing, append-only events, replay, locking, or asynchronous approval.

The roadmap assigns the last group to later durability milestones. Any of the
other mechanisms would need evidence in the lifecycle contract that a simpler
shape cannot satisfy the approved invariant.

## Traceability matrix

Every major retained or deferred mechanism has an authority source:

| Mechanism | Authority |
|---|---|
| Deterministic controller owns workflow and transitions | [Architecture reference, Design philosophy](../../ORCHESTRATION_ENGINE.md); [Gate 3.5, Decision](../gate-3/gate-3.5-provider-adapter-route-profile.md) |
| Three adapter operations and no generic run/fallback method | [Gate 3.5, Provider adapter descriptor](../gate-3/gate-3.5-provider-adapter-route-profile.md) |
| One adapter execution is one physical attempt | [Gate 3.5, Decision](../gate-3/gate-3.5-provider-adapter-route-profile.md) |
| Exact four routes and operations | [Gate 3.5, Route-profile catalog](../gate-3/gate-3.5-provider-adapter-route-profile.md); [Gate 4.2, Seed route and account records](gate-4.2-validated-resolved-configuration.md) |
| Saved route/account and provider-default effort input | [Gate 4.1, Decision and effort/account amendments](gate-4.1-effort-provider-account-amendment.md); [Gate 4.2, Resolved configuration](gate-4.2-validated-resolved-configuration.md) |
| Exact Claude/Codex argv | [Gate 3.5, Command construction and environment](../gate-3/gate-3.5-provider-adapter-route-profile.md); baseline [`providers.py`](../../../providers.py) |
| Transient redacted audit and final-prompt hash | [Gate 3.5, Adapter descriptor, Invocation request, and Command construction](../gate-3/gate-3.5-provider-adapter-route-profile.md) |
| Exact controller prompts and Luna preamble | [Architecture reference, provider safety](../../ORCHESTRATION_ENGINE.md); baseline [`orchestrator.py`](../../../orchestrator.py) and [`providers.py`](../../../providers.py) |
| Permission ceilings and controller-only Git | [Gate 3.6, Role ceilings](../gate-3/gate-3.6-capability-profiles-permission-ceilings.md); [architecture provider boundaries](../../ORCHESTRATION_ENGINE.md) |
| Process deadline and cleanup | [Roadmap C-2](../../ENGINE_ROADMAP.md); [Gate 3.5, Shared physical-attempt supervision](../gate-3/gate-3.5-provider-adapter-route-profile.md); baseline [`providers.py`](../../../providers.py) |
| One shared normalization pass, failure kinds, and evidence precedence | [Gate 3.5, Normalized physical-attempt result and Failure normalization](../gate-3/gate-3.5-provider-adapter-route-profile.md); [architecture failure policy](../../ORCHESTRATION_ENGINE.md); baseline [`providers.py`](../../../providers.py) |
| Same-provider retry and no fallback | [Gate 3.5, Retry and fallback authority](../gate-3/gate-3.5-provider-adapter-route-profile.md); [architecture retry policy](../../ORCHESTRATION_ENGINE.md) |
| Live content retry and recovery-path no-retry behavior | [Gate 3.5, Retry and fallback authority](../gate-3/gate-3.5-provider-adapter-route-profile.md); baseline [`orchestrator.py`](../../../orchestrator.py) |
| Schema-13 fields and persistence ordering | [Gate 4.2, Run schema 13 and runtime integration](gate-4.2-validated-resolved-configuration.md); baseline [`models.py`](../../../models.py) and [`orchestrator.py`](../../../orchestrator.py) |
| Read-only exact-stage recovery | [Architecture crash recovery](../../ORCHESTRATION_ENGINE.md); baseline [`orchestrator.py`](../../../orchestrator.py) |
| Writer arming and explicit recovery | [Gate 3.5, Crash/resume/writer recovery](../gate-3/gate-3.5-provider-adapter-route-profile.md); [architecture writer recovery](../../ORCHESTRATION_ENGINE.md) |
| Local-only doctor probe | [Gate 3.5, Doctor/dry-run](../gate-3/gate-3.5-provider-adapter-route-profile.md); [Gate 4.2, Doctor](gate-4.2-validated-resolved-configuration.md) |
| Dry-run avoids final prompts and provider attempts | [Gate 3.5, Doctor/dry-run](../gate-3/gate-3.5-provider-adapter-route-profile.md); [Gate 4.2, Dry-run](gate-4.2-validated-resolved-configuration.md) |
| Future durable request/arm/result lifecycle and exact evidence binding | [Gate 3.5, Invocation request, Command audit, Normalized result, and Retry](../gate-3/gate-3.5-provider-adapter-route-profile.md); [Gate 4.1, Validation and arming](gate-4.1-effort-provider-account-amendment.md) |
| Future schema-2 account/effort evidence and per-attempt readiness revalidation | [Gate 4.1, Effort policy and Validation and arming](gate-4.1-effort-provider-account-amendment.md) |
| Future minimal child environment and binary provenance | [Gate 3.5, Command construction and environment](../gate-3/gate-3.5-provider-adapter-route-profile.md); baseline inherited-environment behavior in [`providers.py`](../../../providers.py) |
| Future output ceiling and completeness flags | [Gate 3.5, Shared physical-attempt supervision](../gate-3/gate-3.5-provider-adapter-route-profile.md) |
| Future between-attempt revalidation | [Gate 3.5, Retry and fallback authority](../gate-3/gate-3.5-provider-adapter-route-profile.md) |
| Future malformed-output evidence and report distinctions | [Gate 3.5, Retry; Doctor, dry-run, audit, and migration](../gate-3/gate-3.5-provider-adapter-route-profile.md) |
| Conservative future migration | [Gate 3.5, migration](../gate-3/gate-3.5-provider-adapter-route-profile.md); [Gate 4.1, compatibility and migration](gate-4.1-effort-provider-account-amendment.md); [roadmap planning rules](../../ENGINE_ROADMAP.md) |
| Event/audit platform deferred | [Roadmap E-1 and Milestone 4](../../ENGINE_ROADMAP.md); [execution-plan Gate 8](../../EXECUTION_PLAN.md) |

## Required deterministic tests

Tests use fakes, fixtures, temporary repositories, and fake sleepers only. They
must not read the operator's real configuration/credential state, invoke a live
provider, contact a network, or access an external target.

Required groups:

- descriptor/registry strictness, duplicate/missing adapter rejection, and
  configuration inability to register executable code;
- adapter request validation for operation, route, adapter, account, model,
  builder, effort, output, coarse capability, and NUL rejection;
- byte-for-byte argv golden tests for all four routes, the minified Claude
  schema, `--` placement, controller prompt, and Luna prefix;
- `build_attempt` purity and bounded redacted-audit tests proving no probe,
  spawn, sleep, network, credential inspection, file/repository mutation, or raw
  prompt/environment disclosure;
- proof that each valid `execute_attempt` call passes its immutable plan unchanged
  to exactly one physical executor call, returns exactly one normalized attempt
  even on launch failure, and never probes, sleeps, or resolves fallback;
- read-only `unavailable` 5/15-second retry tests and writer single-shot tests;
- single-pass Claude-native, OS, supervisor, anchored-stderr, nonzero-return-code,
  exit-zero prose, stdout-tail-disabled, timeout, interruption, TERM/KILL,
  partial-output, and orphan-process tests;
- live-path and recovery-path Sonnet/Sol content-retry parity tests;
- no-fallback tests for missing/mismatched adapters and attempted provider
  advice;
- exact schema-13 serialization/model/report/migration regression tests showing
  that no field or version changed;
- writer pre/post-state, failure-block, ordinary-resume, `retry_restored`, and
  `adopt_current` regression tests;
- doctor/dry-run no-provider-attempt, no-network, no-auth, version, ordering, and
  reason-code parity tests, while retaining their existing local Git checks;
- legacy callable/import compatibility tests where a facade remains; and
- the complete existing deterministic suite plus new Gate 4.3 tests.

## Adversarial matrix

| ID | Scenario | Required outcome |
|---|---|---|
| G43-01 | Adapter descriptor is missing, duplicated, unsupported, or hash-incoherent. | Static validation fails before command construction or provider work; no alternate adapter is selected. |
| G43-02 | Saved route adapter and selected adapter differ. | Request is rejected before execution; no display-name or model heuristic repairs it. |
| G43-03 | Task, configuration, request, prompt, provider output, or CLI option supplies an executable replacement, environment override, flag, builder, classifier, tool, or permission. | It has no adapter-registration authority and cannot replace the fixed executable token or change the plan; inherited `PATH` resolution remains baseline behavior. |
| G43-04 | Route operation, role, model, account adapter, effort, output, or coarse capability differs from the installed compatibility binding. | `build_attempt` rejects the request before spawn and without fallback. |
| G43-05 | Prompt begins with dashes or contains flag-like text. | It remains one final data argument after `--`; argv does not change. |
| G43-06 | Claude review plan differs by one flag, order, schema byte, tool, permission mode, or prompt byte. | Golden parity test fails. |
| G43-07 | Terra or Sol plan gains write access or another flag. | Golden/permission test fails before execution. |
| G43-08 | Luna plan omits/changes the Git prohibition, sandbox, approval policy, network denial, or prompt separator. | Golden/permission test fails before execution. |
| G43-09 | Adapter makes zero or multiple executor calls, mutates the plan, probes, sleeps, retries, or invokes another adapter from `execute_attempt`. | Boundary test fails; every valid call performs exactly one physical attempt with the unchanged plan. |
| G43-10 | Read-only attempt returns `unavailable` twice and then succeeds. | Same adapter/plan runs three times with delays 5 and 15; records retain ordinals 1-3 and retry bits true, true, false. |
| G43-11 | Read-only attempt returns timeout, interruption, quota, billing, auth, rate limit, configuration, or provider error. | It stops after that physical attempt and maps to the existing block behavior. |
| G43-12 | Writer attempt returns `unavailable` or any other failure. | Exactly one process runs; sleeper is not called; repository post-state controls the writer block. |
| G43-13 | Provider/model prose mentions quota, auth, 429, 503, or timeout on successful transport. | Prose is not failure evidence and cannot schedule retry. |
| G43-14 | Claude native error conflicts with stderr or has exit code zero. | Existing native-envelope precedence/classification wins. |
| G43-15 | Unknown nonzero result has no trusted evidence. | It becomes `provider_error`; never `unavailable`, success, or fallback authority. |
| G43-16 | Deadline or interruption leaves a parent/grandchild process. | Existing TERM/KILL, drain, reap, partial-output, code, and diagnostic behavior is preserved. |
| G43-17 | First live Sonnet or Sol result is malformed. | Exactly one new same-route logical content invocation uses the saved prompt; no alternate route runs. |
| G43-18 | The content retry is malformed or has a transport failure. | Existing output/failure block occurs; no third content invocation runs. |
| G43-19 | Crash recovery sees a final provider record whose operation/control identity and pending operation/control identity match the stage. | It consumes that exact saved output without calling `execute_attempt`; it never searches backward for another match. |
| G43-20 | Crash recovery lacks a matching provider record. | It blocks as `blocked_interrupted_provider`; it does not infer pre-spawn state or rerun. |
| G43-21 | Recovered saved Sonnet output is malformed, including repeated recovery after its unreadable marker was saved. | Existing recovery block occurs without content retry, and the already-linked marker is reused rather than duplicated. |
| G43-22 | A resumable read-only provider block is explicitly resumed. | The exact saved stage, route, operation, and prompt run once as a new high-level invocation; recovery parsing grants no content retry. |
| G43-23 | Timeout, interruption, or interrupted-provider block is ordinarily resumed. | No provider-block retry occurs; existing non-resumable behavior remains. |
| G43-24 | Writer crashes before a result is saved or leaves partial changes. | Existing repository observation chooses the conservative writer block; ordinary resume never invokes Luna. |
| G43-25 | `retry_restored` lacks an exact pre-state match, or `adopt_current` lacks trustworthy nonempty changes. | Recovery is refused before provider work and no decision/success is fabricated. |
| G43-26 | Schema-13 run is saved after extraction. | JSON shape, version, provider records, raw full command list, resume fields, and validators remain schema-13 compatible. |
| G43-27 | Historical schema 1-12 record is inspected or migrated. | Existing registry, facts, classification, migration lineage, and execution disposition are unchanged. |
| G43-28 | `doctor` probes adapters. | The adapter probe only performs fixed-token executable discovery through local `PATH`; auth stays unknown and no provider process, network, credential inspection, environment exposure, or mutation occurs. Doctor's other existing local checks remain. |
| G43-29 | Dry-run validates routes. | No real prompt is built and neither adapter execution nor provider/network work occurs. |
| G43-30 | Implementation adds schema 14, durable generic records, graphs, a generalized evidence platform, output caps, environment allowlisting, new routes, or generalized capability enforcement. | Scope test/review rejects the diff; each belongs to an explicitly later bounded item. |
| G43-31 | `build_attempt` probes, spawns, sleeps, accesses a network/credential, mutates state, or exposes the raw prompt/environment in its audit view. | Purity/audit test fails before `execute_attempt`; a valid plan contains only the exact command/context and approved bounded audit facts. |
| G43-32 | The fixed executable is absent or launch raises an OS error during ordinary execution. | No `probe_local` preflight runs; exactly one attempted execution returns code 127 plus baseline normalized OS evidence, then blocks without retry or fallback. |
| G43-33 | A prompt, argv data value, or working-directory value contains a NUL. | `build_attempt` rejects it deterministically before probe, spawn, persistence, retry, or fallback. |

## Acceptance / exit criteria

- `claude_cli` and `codex_cli` are reachable only through the strict code-owned
  adapter registry and expose exactly `probe_local`, `build_attempt`, and
  `execute_attempt`.
- Provider-specific executables, flags, and command ordering occur only inside
  adapter implementation and golden fixtures; controller workflow code and any
  compatibility facade contain no provider-specific argv construction.
- `build_attempt` rejects NULs, is otherwise pure, and returns the exact immutable
  plan, final-prompt hash, and bounded redacted audit view—including saved effort
  facts—without changing schema-13 persistence.
- Each valid `execute_attempt` call performs exactly one physical attempt and
  returns exactly one normalized result, including launch failure. The shared
  controller-owned runner, not an adapter, owns the current retry loop and does
  not reclassify the result.
- The four compatibility argv vectors and provider-facing prompts are
  byte-for-byte equal to the runtime baseline.
- Controller prompt builders, output parsers, workflow transitions, block
  mapping, correction policy, approvals, and Git behavior are unchanged.
- Failure classification, supervision, display/heartbeat output, read-only
  5/15-second retry, writer single-shot behavior, content retry, and no-fallback
  policy pass parity tests.
- The exact current permission boundaries are preserved by argv and prompt
  tests; Gate 4.3 makes no claim of generalized capability enforcement.
- `CURRENT_RUN_SCHEMA_VERSION` remains 13; `WorkflowRun`, `ProviderRecord`,
  provider-resume fields, writer records, reports, and the 1-13 migration
  registry have no logical shape or semantic change.
- Read-only recovery and writer recovery pass existing and focused parity tests,
  including consuming completed output without reinvocation.
- `doctor.v2` and `run-plan.v2` preserve their current observable contracts and
  perform no provider/network/credential work.
- The existing deterministic suite and all new Gate 4.3 tests pass without live
  providers, credentials, external targets, or the Jobs checkout.
- Bytecode/import checks, installed `jobs-orchestrator` compatibility checks,
  Markdown-link validation, code-fence validation, terminology searches, and
  `git diff --check` pass.
- No runtime implementation begins until the repository owner approves this
  reset contract and its explicit remaining-conformance split.

## Owner decisions remaining

Approval of this contract decides the only Gate 4.3 architecture question:
accept the explicit adapter-extraction / remaining-conformance split and the
corresponding tracker item.

The following decisions remain for the later conformance contract, not this one:

1. the run-file layout for the already-approved schema-2 request/result values,
   and the minimum physical-attempt arm and malformed-output marker shapes;
2. the then-next run schema number and conservative migration/execution
   eligibility for existing schema-13 runs;
3. representation and private-storage bounds for the mandated exact final prompt
   or immutable artifact plus hash and redacted command audit;
4. the exact controller-owned output byte ceiling and artifact representation;
5. an adapter-owned child-environment allowlist proven compatible with the two
   externally managed CLI sessions, including binary provenance; and
6. the minimum schema/report representation for the mandated lifecycle and
   protocol distinctions.

No owner decision remains about the current commands, prompts, retry policy,
fallback, permission ceilings, failure mapping, or writer behavior: baseline
parity fixes those values for Gate 4.3.
