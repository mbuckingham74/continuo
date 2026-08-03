# Gate 2.3 / C-5 stable provider-identity contract (approved 2026-08-02)

**Authority.** [ENGINE_ROADMAP.md](../../ENGINE_ROADMAP.md) remains authoritative. This document contains the bounded contract, adversarial matrix, and durable evidence for its tracker entry.

**Tracker.** See [EXECUTION_PLAN.md](../../EXECUTION_PLAN.md) for gate status, sequencing, and links to every contract.

## Tracker evidence

- Planning evidence (2026-08-02): the Gate 2.3 contract and
    adversarial matrix below were derived from authoritative C-5, the completed
    Gate 2.2 version-7 migration boundary, and every current label-dependent
    history, normalization, recovery, writer-linkage, policy-source, reporting,
    retry, prompt, and CLI path. The inspected baseline is clean `main` aligned
    with `origin/main` at
    `722c1eafa1a91a02c066d0e74c90957633c139b0`. This planning pass changes only
    this tracker. No runtime model, migration, provider, fixture, test, private
    run, target checkout, Git operation, or later Gate 2 item changed.
  - Contract approval (2026-08-02): the repository owner approved the complete
    identity vocabulary, schema-8 persistence/migration boundary, backward-
    compatibility treatment, recovery and retry behavior, CLI behavior, later-
    gate exclusions, and 41-row adversarial matrix, and explicitly authorized
    commit, direct push to `origin/main`, and this tracker update. At that
    approval point, Gate 2.3 implementation remained a separate action.
  - Validation evidence (2026-08-02): all eight non-planning local Markdown
    links/anchors and 41 unique ordered Gate 2.3 matrix rows validate;
    `git diff --check` passes. The planning publication changed only this
    tracker and used no private run, Jobs path, live provider, target checkout,
    test fixture, runtime source, commit/push gate, or later Gate 2 work. The
    123-test deterministic suite was not rerun for this documentation-only diff.
  - Publication evidence (2026-08-02): contract commit
    `77a6b1a4450f082f38b38b65dc77cfa9f2e247d4` (`Define Gate 2.3 identity
    contract`) was pushed directly to `origin/main`.
  - Implementation evidence (2026-08-02): the uncommitted Gate 2.3 diff adds
    the four-entry immutable compatibility catalog, closed role/operation/
    adapter identities, schema 8, stable pending-call and policy-source links,
    role-keyed history/recovery/report control, exact historical V7 validation,
    and the adjacent `7_to_8` transform with a separate immutable identity
    migration audit. All six approved legacy provider/purpose pairs map only
    through the closed migration table; unknown or contradictory history
    remains archive-only; migrated records remain non-executable. Provider
    commands, capabilities, retry/deadline policy, target ownership, Git gates,
    fake-provider injection, and compatibility identifiers are unchanged.
  - Validation evidence (2026-08-02): all 135 deterministic tests pass using
    temporary repositories, synthetic historical records, recorded fixtures,
    fake providers, and local child processes. Coverage includes all 41 Gate
    2.3 matrix rows across display/model collisions, adapter-selected parsing,
    role/operation/capability rejection, atomic pending identity, physical retry
    identity, writer/policy links, V1--V7 migration, audit preservation,
    archive-only contradictions, crash/recovery, read surfaces, reporting, and
    later-gate boundaries. Python compilation, root and installed CLI help,
    compatibility import, documentation links/matrix checks, and
    `git diff --check` pass with `UV_NO_EDITABLE=1` for installed-package
    validation. No Jobs checkout, private run, live provider, network service,
    external target, commit, push, or later Gate 2 item was used.
  - Review decision (2026-08-02): the repository owner reviewed the complete
    uncommitted implementation diff and approved two review fixes before
    publication: policy-source records now pair in order with migrated
    decisions whenever decision and successful-record counts match, instead of
    dropping every provable link, and the unreachable legacy-audit disposition
    fallback in V8 classification and execution refusal was replaced with
    explicit total audit branches and refusal messages. Regression coverage for
    both fixes was added. The owner explicitly authorized commit, direct push
    to `origin/main`, and this tracker update.
  - Publication evidence (2026-08-02): implementation commit
    `d345878e50760b79e74bb4c13b223465ac97a390` (`Implement Gate 2.3 stable
    provider identities`) was pushed directly to `origin/main` with all 138
    deterministic tests passing.



**Status and boundary.** This note specifies only the first unchecked Gate 2
item after the published Gate 2.2 migration work. It replaces human-facing
provider/model labels as control identifiers with separate stable orchestration
role, provider-adapter, configured-route, provider-model, and display
identities. It also gives each persisted physical attempt a stable operation ID
so the same role can perform more than one operation without falling back to a
presentation string. The repository owner approved this contract and its
adversarial matrix for publication on 2026-08-02. The bounded runtime
implementation is complete and published on `origin/main` as
`d345878e50760b79e74bb4c13b223465ac97a390`.

This item is a persisted-contract bridge, not the generic provider-adapter or
configuration implementation. Current commands, models, capabilities,
deadlines, tool/sandbox flags, provider callables, and no-fallback behavior stay
fixed. Gate 4 will later make the same identities configuration-backed and
persist the complete resolved routing table/configuration hash.

**Invariant and current evidence.** A display or model rename must not change
which history contributes to correction streaks, which parser interprets an
attempt, which record satisfies writer recovery, which provider stage may
resume, which calls count as escalation, or which route supplied a policy
recommendation. At baseline
`722c1eafa1a91a02c066d0e74c90957633c139b0`, those decisions still compare the
presentation strings `Sonnet 5 High`, `Luna High`, `Sol High`, and `Terra High`.

The load-bearing comparisons are reproducible in the current source:

- `_record_provider()` chooses Claude/Sonnet envelope normalization from the
  `Sonnet 5 High` label;
- implementation-review history and finding streaks select records using the
  Sonnet label plus the free-form purpose string;
- writer recovery and schema-6 coherence require the `Luna High` label;
- exact-stage crash recovery maps stages to label/purpose tuples and compares
  them with the last raw attempt;
- run reporting groups physical attempts by display label, derives Sol
  escalation counts from `Sol High`, and derives the verification proxy from
  `Luna High`;
- `PolicyDecision.source_provider` persists `Terra High` as the source identity;
  and
- `_provider_label()` infers presentation by searching command arguments for a
  model name.

The existing `ModelRoute` dataclass does not solve this problem. It is unused by
the controller and combines a role description, CLI name, and model ID without
a stable route ID, provider-adapter identity, display metadata, persisted
attempt linkage, or migration behavior.

**Identity semantics and approved vocabulary.** The implementation will define
one closed role vocabulary and one closed operation vocabulary for the current
workflow. IDs are lowercase machine identifiers. They are never translated
from display text during ordinary execution.

| Stable role ID | Stable operation IDs in this item | Authority ceiling |
|---|---|---|
| `implementation` | `implementation_write`, `correction_write` | `workspace_write`; no Git or network authority |
| `adversarial_review` | `specification_review`, `implementation_review` | `read_only`; structured review for both operations |
| `escalation_executive` | `escalation_guidance` | `read_only` |
| `policy_authority` | `policy_clarification` | `read_only`; recommendation only, never human approval authority |

`policy_authority` follows the roadmap's stable role vocabulary. It does not
change the existing invariant that only a human can approve policy. Operation
IDs identify why a physical call occurred; they do not grant authority and do
not replace workflow stages, correction findings, or future logical-call IDs.

One immutable in-code compatibility catalog supplies the currently hard-coded
route metadata:

| Stable role | Provider adapter ID | Stable route ID | Provider model ID | Display name |
|---|---|---|---|---|
| `implementation` | `codex_cli` | `builtin.implementation.v1` | `gpt-5.6-luna` | `Luna High` |
| `adversarial_review` | `claude_cli` | `builtin.adversarial_review.v1` | `sonnet` | `Sonnet 5 High` |
| `escalation_executive` | `codex_cli` | `builtin.escalation_executive.v1` | `gpt-5.6-sol` | `Sol High` |
| `policy_authority` | `codex_cli` | `builtin.policy_authority.v1` | `gpt-5.6-terra` | `Terra High` |

The five concepts have deliberately different meanings:

- **role ID** is the deterministic workflow responsibility and the only
  provider identity permitted in correction, escalation, policy, recovery, and
  role-based metric decisions;
- **provider adapter ID** names the execution/protocol integration. In this item
  it selects Claude-envelope versus ordinary Codex result normalization, not a
  display name or vendor fallback;
- **route ID** is an opaque stable identity for one current hard-coded role
  route. It is not a model ID and does not itself authorize a route change;
- **model ID** is exact provider metadata preserved per physical attempt and
  passed to the unchanged command builder; and
- **display name** is presentation-only metadata. It may appear in console
  progress, reports, prompts, and human-readable errors, but no transition,
  parser, retry, recovery, audit link, or metric key may branch on it.

The two review operations intentionally share the
`adversarial_review` role/route. Their stable operation IDs distinguish
specification history from implementation QA history. Route IDs are immutable
compatibility-profile identities: changing adapter, model, role, or authority
requires a new route ID and a separate reviewed migration/configuration change;
a display-only rename does not. Gate 4 may place these values in versioned
configuration but must preserve their meanings.

**Persisted schema and write contract.** This is a structural persisted-model
change and therefore increments `CURRENT_RUN_SCHEMA_VERSION` from 7 to 8. New
version-8 records use a closed `ProviderRouteIdentity` value containing
`role_id`, `provider_adapter_id`, `route_id`, `model_id`, and `display_name`.
Every `ProviderRecord` replaces the legacy `provider` and `purpose` fields with
that identity plus a stable `operation_id`; raw command, streams, physical
return code, duration, failure evidence, capability, fingerprints, and retry
flag remain byte-for-byte or value-for-value audit fields.

The runtime write paths must behave as follows:

- new runs are version 8 and do not persist legacy `provider`/`purpose` control
  strings;
- `_record_provider()` receives an approved route identity and operation ID
  explicitly from the controller before recording either injected or real
  results; it never derives identity from command text, stdout/stderr, model
  prose, or display name;
- every physical retry record receives the same role, adapter, route, model,
  display, and operation identity as the original physical attempt;
- `provider_resume_stage` and `provider_resume_prompt` gain a closed saved
  `provider_resume_identity` and `provider_resume_operation_id`. The stage,
  prompt, identity, and operation are armed in the same atomic run snapshot and
  are cleared together;
- `PolicyDecision.source_provider` is replaced by stable source role/route IDs
  and an optional raw-provider-record index. New decisions link the successful
  `policy_authority` attempt exactly; migrated decisions retain an explicit
  missing-link reason rather than inventing a physical attempt; and
- the unused conflating `ModelRoute` constants are replaced by the one static
  compatibility catalog. Provider command construction reads model/display
  metadata from that catalog but remains otherwise unchanged.

Version-8 cross-field validation requires each role/operation pair to be in the
table above, each new ordinary record's capability to satisfy its role ceiling,
each pending resume identity to match its stage/operation, and each active writer
link to identify an `implementation` record with the matching write operation
and `workspace_write` capability. Provider adapter/model/display metadata may
never override those checks. Migrated records may preserve a historically
absent capability or provider-record link only when their immutable migration
audit records that absence and their execution disposition remains blocked.

No complete resolved routing table or configuration hash is added to
`WorkflowRun` in this item. The stable identity on each completed/pending call
is sufficient to remove labels from current history and recovery control; Gate
4 still owns run-creation route resolution and pinning from configurable input.

**Stepwise migration and historical treatment.** Add an exact historical
version-7 model and one adjacent `7_to_8` transform. The `1_to_2` through
`5_to_6` functions remain unchanged. The `6_to_7` implementation must replace
its moving references to the current-version constant with literal historical
version 7 so its approved transform and audit bytes stay semantically exact
after the current version advances; it must not otherwise change that step.
Gate 2.1 fixtures remain unchanged. An explicit `migrate-run` may continue from
any recognized V1--V7 record through the ordered registry, but no direct load
or controller action migrates implicitly.

The `7_to_8` transform is the sole place where legacy display aliases may be
interpreted. It uses this closed mapping:

| Legacy provider / purpose | Version-8 identity and operation |
|---|---|
| `Luna High` / `implementation` | `implementation`, `builtin.implementation.v1`, `implementation_write` |
| `Luna High` / `correction` | `implementation`, `builtin.implementation.v1`, `correction_write` |
| `Sonnet 5 High` / `specification` | `adversarial_review`, `builtin.adversarial_review.v1`, `specification_review` |
| `Sonnet 5 High` / `implementation` | `adversarial_review`, `builtin.adversarial_review.v1`, `implementation_review` |
| `Sol High` / `escalation guidance` | `escalation_executive`, `builtin.escalation_executive.v1`, `escalation_guidance` |
| `Terra High` / `policy clarification` | `policy_authority`, `builtin.policy_authority.v1`, `policy_clarification` |

Provider adapter/model/display metadata comes from the reviewed historical
compatibility catalog, while the original command and raw output remain
unchanged. Migration never infers identity from arbitrary command arguments or
model prose. `PolicyDecision.source_provider == "Terra High"` maps only to the
stable policy source; any other value is not guessed. Pending resume identity
maps only from a recognized saved stage and coherent prompt/record evidence.

An unknown label, unknown purpose, known values in an invalid combination,
contradictory capability, incoherent writer link, mismatched pending stage, or
unknown policy source makes the recognized historical record `ARCHIVE_ONLY`
with a bounded identity reason code. It is never defaulted to the nearest model,
treated as a new provider, or allowed to resume. Supported migration removes
the legacy control fields from version 8 while preserving the historical display
name inside route metadata and preserving all raw audit fields.

Gate 2.2 made `RunMigrationAudit` immutable and explicitly prohibited later
items from editing or discarding it. Version 8 therefore preserves any existing
`migration_audit` exactly and adds a separate immutable
`identity_migration_audit` for this explicit transform. The new audit records
the exact physical source version/hash, target version 8, ordered step IDs,
bounded legacy-identity mapping/absence reasons, operator-confirmed migration
identity/time, and the inherited execution disposition. A direct V1--V6
migration may contain both the unchanged Gate 2.2 audit through version 7 and
the new identity audit through version 8; a version-7 ordinary record has only
the identity audit. New version-8 runs have neither audit.

Migrated records remain `resume_eligibility_deferred`, `resume_blocked`, or
`inspection_only` and every controller mutation rejects them before target
coordination or side effects. In particular, migrating a valid ordinary
version-7 run does not make it executable: that run did not persist the resolved
correction/escalation policy required by the following Gate 2 item. This item
adds no eligibility-clear or route-migration command. Atomic compare-and-swap,
default-no confirmation, private temporary replacement, rollback-before-replace,
concurrency, crash, idempotency, bounded diagnostics, and no private-record
validation retain the Gate 2.2 contract.

**Backward compatibility and read paths.** Root and installed CLI names,
`jobs-orchestrator`, `JOBS_REPO`, `src/jobs_orchestrator`, command names/options,
Jobs task resolution, and injectable fake-provider callables remain available.
This item does not create generic aliases yet. Historical JSON compatibility is
provided only through strict classification and explicit migration; version 8
does not accept legacy label fields as ordinary current-model aliases.

Current read/control paths change as follows:

- review-history reconstruction selects `adversarial_review` plus
  `implementation_review`; unreadable raw review handling remains the next C-6
  item and is not silently improved here;
- Claude-envelope normalization selects `claude_cli`, while retry authorization
  continues to use `ProviderCapability`;
- writer linkage selects the implementation role/route and matching stable write
  operation;
- crash recovery compares the saved stage, role, route, operation, and latest
  raw record before parsing or invoking anything;
- correction and escalation policy counts stable roles/operations, never model
  or display metadata;
- policy decisions identify their recommendation source by stable role/route,
  while human approval remains separate; and
- reporting groups the existing physical-attempt proxy by stable role ID and may
  render route/model/display metadata alongside it. Renaming the proxy or
  separating logical calls from physical attempts remains the dedicated later
  metrics item.

`status <run-id>` continues to print the complete sensitive JSON for an ordinary
current record, now including version-8 identities. Historical version 7 and
older records receive bounded classification until explicitly migrated. Human
`report` output labels each role with its current display name but uses role IDs
as aggregation keys. No stable `--json` report/status schema, `doctor`, picker,
or dry-run behavior is introduced.

**Crash, retry, and audit behavior.** Stable identity is persisted before a
provider boundary and copied to every resulting physical-attempt audit. A crash
before a raw result exists leaves the saved role/route/operation marker; recovery
may use only the matching built-in callable and must block on any unknown or
incoherent identity. A crash after a result save but before the parsed workflow
state consumes only a record whose stable identity matches the armed marker.
No display/model comparison can satisfy that link.

Read-only unavailability keeps the existing 5/15-second same-route retry and
structured Sonnet/Sol output keeps one same-route content retry. All physical
attempts retain the original identity; neither retry may select another role,
adapter, route, model, capability, or display name. Workspace-write attempts
remain single-shot and keep M0.4 pre/post evidence. Timeout, interruption,
provider error, and writer-state blocks keep their existing semantics. A route
or model availability failure blocks as the existing configuration/provider
failure; there is no fallback or route migration.

Raw commands, stdout/stderr, failure provenance, fingerprints, prompts, and
human text remain full-fidelity sensitive audit data. Display labels remain in
old raw bytes and may remain in human-facing errors/prompts, but no recovery or
policy code reparses those strings. This item does not add parsed review records,
redaction, export, event logging, approval identity, or tamper evidence.

**Adversarial test matrix.** Tests use synthetic versioned records, committed
historical fixtures, temporary private run directories/repositories, fake
providers, injected clocks/IDs/confirmation, and existing local child-process
coverage. No private run, Jobs path, live provider, network service, external
target, commit, or push is used.

| ID | Fixture / event | Required assertions |
|---|---|---|
| I1 | Static compatibility catalog inspection | Exactly four stable role/adapter/route/model/display entries exist; role and route IDs are unique as specified, and no entry derives identity from a label or command. |
| I2 | One role, adapter, route, model, or display field is substituted independently | Fields remain semantically distinct; only role/operation and capability govern workflow authority, while adapter governs protocol normalization. |
| I3 | Display names are renamed, duplicated across routes, or set to another role's old label | Review history, correction streaks, writer linkage, recovery, policy source, retries, and metric keys are unchanged. |
| I4 | Model IDs are equal across two synthetic routes or a display contains a model ID | No role or route is inferred; model/display collisions have no control effect. |
| I5 | Role/operation pair is invalid or role capability exceeds/fails its ceiling | Version-8 validation or pre-invocation checks fail before provider spawn, record creation, target mutation, or retry. |
| P1 | New run is constructed | Schema is exactly 8; both migration audits are absent; no legacy provider/purpose/source-provider control field exists. |
| P2 | Each real wrapper and injected fake returns one physical result | Persisted record contains explicit route identity and operation supplied before invocation; command/model prose is never inspected to create it. |
| P3 | Read-only transport retry creates three physical attempts | Every record has identical role, adapter, route, model, display, operation, and capability; retry flags and raw evidence remain per attempt. |
| P4 | Policy clarification succeeds and a human later approves text | Decision stores stable policy role/route and exact successful provider-record link; changing display text cannot change the source. |
| P5 | Provider resume is armed and cleared | Stage, prompt, identity, and operation appear and disappear in the same atomic snapshots; partial tuples are invalid or block safely. |
| P6 | Persist/load full current identity state | All IDs, display/model metadata, raw attempts, pending identity, policy linkage, writer linkage, and unrelated audit fields round-trip exactly. |
| M1 | Migration registry and historical validators are inspected | Existing `1_to_2` through `5_to_6` code/contracts remain unchanged; moving current-version references in `6_to_7` are frozen to literal 7 without changing its output contract; exactly one adjacent `7_to_8` step is added and every intermediate validates. |
| M2 | Each six approved legacy provider/purpose pair is migrated | It maps to the exact role/adapter/route/model/display/operation row and the legacy control fields are absent from version 8. |
| M3 | V1--V6 fixtures migrate through version 8 | Ordered prior transforms and values remain intact; legacy attempts receive only the closed mapping; no command/output text is reclassified. |
| M4 | Version-7 ordinary record with current labels | Explicit default-no migration is required; approval produces one private atomic V8 record with an identity audit and deferred disposition. |
| M5 | Version-7 record already carrying a Gate 2.2 migration audit | Original immutable audit is value-for-value unchanged; the identity audit is appended separately and execution remains blocked. |
| M6 | Historical policy decision uses `Terra High` with and without a provable raw-attempt link | Stable policy role/route is mapped; present linkage is preserved, absent linkage is audited as absent, and no attempt is invented. |
| M7 | Saved provider-resume stage/prompt is coherent | Exact stable pending role/route/operation is materialized without invoking a provider. |
| M8 | Unknown label/purpose, invalid known combination, unknown policy source, or pending-stage mismatch | Record is `ARCHIVE_ONLY` with bounded identity reason; no migration, default identity, provider, target coordination, or Git work occurs. |
| M9 | Legacy capability or writer-provider index contradicts the mapped role/operation | Classification fails closed; identity mapping cannot repair or authorize the record. |
| M10 | Legacy raw command names another executable/model or contains adversarial label text | Closed label/purpose mapping and coherence rules are unchanged; raw command is preserved and never becomes an identity oracle. |
| M11 | Current V8 source is passed to `migrate-run` | Command reports already current without rewrite, timestamp change, or second identity audit. |
| C1 | Sonnet display/model is renamed on a current record | Claude envelope normalization still follows `claude_cli`; a non-Claude adapter cannot gain that parser by copying the display. |
| C2 | Review records mix specification and implementation operations under one review role | Only `implementation_review` feeds implementation history/finding streaks; specification results remain excluded without label comparisons. |
| C3 | Active writer links to a record with copied Luna display but wrong role/route/operation | Link is rejected and writer state blocks unknown; no success, retry, or adoption is inferred. |
| C4 | Crash recovery's last record has the right display but wrong stable identity, or right identity but wrong stage/operation | Recovery blocks before parsing or invocation and retains the raw audit. |
| C5 | Report contains mixed roles, routes, models, display names, retries, and untimed records | Physical-attempt counts/durations are keyed by stable role; route/model/display are presentation metadata; Sol count uses `escalation_executive`. |
| C6 | Policy decision recommendation text names another provider/model | Source role/route and human authority remain unchanged; recommendation/model prose is never parsed for identity. |
| C7 | Prompt/error text still contains legacy display labels | Text remains presentation/audit only and cannot select parser, retry, transition, history, metric, or recovery behavior. |
| R1 | Read-only `unavailable` attempt retries | Same role/adapter/route/model/capability is retained for the 5/15-second sequence; no fallback or identity change occurs. |
| R2 | Writer returns any failure kind after partial or unchanged state | One implementation-role physical attempt persists and existing M0.4 writer block/recovery rules remain unchanged. |
| R3 | Invalid Sonnet or Sol structured output receives the one content retry | Retry uses the same stable route identity and operation; content and transport retry remain mutually exclusive. |
| R4 | Crash after pending identity save but before provider result | Resume invokes nothing implicitly when identity is missing/unknown/mismatched; coherent existing read-only recovery uses only the same role route. |
| R5 | Crash after raw result save but before review/guidance/policy state save | Exact-stage recovery consumes the matching stable record once without display comparison or provider reinvocation. |
| L1 | Human `report` and `status <run-id>` inspect an ordinary V8 run | Report aggregates by role and renders display/model separately; status exposes complete sensitive identity JSON under the existing privacy warning. |
| L2 | `status`, `report`, `resume`, and `migrate-run` inspect V7, migrated V8, archive, and unsupported records | Existing bounded classification/refusal behavior remains; only explicit approved migration writes and no migrated record executes. |
| L3 | Root and installed help/import smoke checks run with `UV_NO_EDITABLE=1` on macOS | Commands/options remain aligned; `jobs-orchestrator`, `JOBS_REPO`, and `src/jobs_orchestrator` are unchanged. |
| B1 | Source/diff boundary inspection | No configuration file, provider adapter interface, model catalog discovery, picker, route override/migration, capability discovery, or automatic fallback is added. |
| B2 | Persisted-control boundary inspection | Parsed reviews, resolved correction/escalation policy, approval records, logical-call metrics, versioned JSON, doctor/dry-run, and event/state ADR remain untouched. |
| B3 | Authority and scope inspection | Deadlines, retries, writer recovery, target ownership, verification, commit/push/merge gates, storage privacy, and Jobs compatibility behavior do not change. |
| B4 | Complete deterministic validation | No private record migration, Jobs access, live provider, network service, external target, commit, push, or later-gate implementation occurs. |

**Explicit exclusions and later-gate boundaries.** Gate 2.3 does not persist an
immutable parsed `ReviewResult` history or stop the current raw-review fallback;
that is the immediately following C-6 item. It does not remove the fixed
correction bound or persist resolved escalation policy. It does not add approval
request/decision identity, correct logical-call/physical-attempt or verification
metric semantics, add versioned machine output, implement `doctor` or dry-run,
or decide the event/state architecture.

It also does not create generic configuration, provider adapters, dynamic model
catalogs, a picker, route precedence, resolved routing-table hashes, audited
route migration, capability discovery, startup authentication checks, project
or task adapters, generic package/CLI aliases, verification plugins, isolated
workspaces, event sourcing, asynchronous gates, telemetry, or UI. It does not
change provider commands, live models, permission ceilings, retry delays,
deadlines, prompts beyond identity-derived presentation wording, human policy or
Git authority, target ownership, storage modes, retention/export behavior, or
the compatibility identifiers `jobs-orchestrator`, `JOBS_REPO`, and
`src/jobs_orchestrator`.

**Exit criteria:** the repository owner approves the identity vocabulary,
static route catalog, schema-8 shape, exact legacy mapping, separate immutable
identity-migration audit, migrated-run execution refusal, read/write paths,
crash/retry behavior, CLI presentation, and later-item exclusions; every matrix
row has deterministic coverage; all existing and new tests pass using fixtures
and fakes only; macOS installed-package validation uses `UV_NO_EDITABLE=1`;
documentation and compatibility checks plus `git diff --check` pass; the
complete implementation diff is reviewed; and no subsequent Gate 2 or Gate 3
item begins.