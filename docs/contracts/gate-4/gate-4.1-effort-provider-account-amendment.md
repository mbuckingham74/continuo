# Gate 4.1 contract amendment — Effort and provider-account bindings

**Status:** repository-owner approved on 2026-08-03; documentation-only Gate 4
prerequisite; publication pending.

## Decision

Before implementing generic configuration, Continuo will amend the approved
[Gate 3.1](../gate-3/gate-3.1-versioned-resolved-configuration.md),
[Gate 3.2](../gate-3/gate-3.2-trusted-project-configuration.md),
[Gate 3.5](../gate-3/gate-3.5-provider-adapter-route-profile.md), and
[Gate 3.6](../gate-3/gate-3.6-capability-profiles-permission-ceilings.md)
contracts in two bounded ways:

1. every complete registered provider route will state one closed,
   adapter-enforced effort policy; and
2. every resolved role will bind that complete route to one stable, non-secret
   provider-account profile compatible with the route's adapter.

The route/account pair is the smallest selectable control unit. Configuration,
CLI commands, the future Rust TUI, tasks, providers, and display metadata may
select only a complete project-permitted binding. They may not independently
splice a provider, model, effort value, account, endpoint, builder, capability,
or permission into another route.

The deterministic controller remains the only resolver and authority writer.
The adapter remains the only credential consumer. Credential material is not
configuration and never becomes part of a route, account profile, resolved
configuration, run record, prompt, command audit, log, fixture, or machine
response.

This amendment changes no provider command, model, credential, retry, fallback,
sandbox, target, Git action, or persisted run. It defines the contract that the
later bounded Gate 4 implementation items must satisfy.

## Terminology and authority boundaries

- A **route profile** is an immutable registered binding of role, operations,
  provider adapter, model, effort policy, command builder, output contract,
  capability profile, supervision, and retry policy.
- A **provider-account profile** is an immutable controller-owned, non-secret
  identity for one adapter authentication slot and transport scope. It is not
  an API key, token, remote principal assertion, environment-variable name, or
  credential-store path.
- A **role binding** is exactly one complete route profile plus one compatible
  provider-account profile.
- A **credential generation** is an optional non-secret monotonic audit value
  maintained by the future controller-owned credential service when Continuo
  manages credential rotation. It is attempt evidence, not routing policy.
- A **remote principal** is a provider-native account, organization, tenant, or
  billing identity. Continuo claims one only when an adapter can obtain and
  validate a stable non-secret identifier through a separately explicit probe.

Provider-account identity therefore means the stable local control-plane
profile selected by the operator. For an externally managed CLI session it does
not falsely claim knowledge of the remote principal. The profile records its
identity assurance so `doctor`, setup readiness, reports, and the future TUI can
distinguish a controller-managed slot from a provider-verified principal.

## Effort policy amendment to Gate 3.5

### Closed effort record

The amended `ProviderRouteProfile` uses
`provider_route_profile_schema_version: 2` and gains this required nested
control record. Published schema-1 payloads are not reinterpreted or accepted
under their old hash:

```yaml
effort:
  mode: explicit
  effort_id: codex.reasoning.high
  enforcement_policy_id: codex.reasoning-effort-flag.v1
```

`mode` is exactly one of:

- `explicit`: the registered builder enforces the exact adapter-owned
  `effort_id` through a documented provider mechanism;
- `provider_default`: the builder intentionally omits an effort control and no
  specific effort level is claimed; or
- `not_supported`: the adapter/model exposes no configurable effort mechanism.

For `explicit`, both `effort_id` and `enforcement_policy_id` are required stable
IDs. For `provider_default`, `effort_id` is absent and an adapter-owned omission
policy ID is required. For `not_supported`, both are absent. Unknown modes,
free-form values, display labels, numbers, aliases such as `max`, and
provider-returned values fail closed unless registered in a new complete route
under an approved adapter contract.

Effort is not a capability grant. A higher effort cannot widen filesystem,
process, tool, network, Git, configuration, publication, approval, retry,
timeout, or fallback authority. Capability comparison remains entirely governed
by Gate 3.6.

The effort record is included in `route_profile_sha256`, the saved complete
route snapshot, the invocation request, command-plan validation, and redacted
command audit. Every physical attempt within a logical invocation uses the same
saved effort policy. A same-route transport retry, content retry, correction,
or resume cannot change effort. Selecting a different effort for future work
selects a different complete registered route ID; it never mutates an existing
route ID or saved run.

### Compatibility-route effort

The current compatibility commands do not pass a separately verified effort
argument. Their display labels containing `High` are presentation evidence only
and cannot prove an invocation setting. Gate 4 therefore registers each existing
compatibility route with `mode: provider_default` and the applicable
adapter-owned omission policy. It must not infer `explicit: high` from `Luna
High`, `Sonnet 5 High`, `Sol High`, `Terra High`, a model name, or current provider
behavior.

The future picker and Rust TUI display `provider_default` or `not_supported`
honestly. They display a named effort such as `high` only for a complete route
whose builder contract enforces that exact registered value.

## Provider-account profile contract

### Immutable profile

Every selectable provider account has one strict immutable private controller
record:

```yaml
provider_account_profile_schema_version: 1
provider_account_profile_contract_id: continuo.provider-account-profile.v1
provider_account_profile_id: provider-account:codex:primary
provider_adapter_id: codex_cli
authentication_method_id: codex.external-cli-session.v1
transport_scope_profile_id: codex.official-service.v1
identity_assurance: controller_profile
remote_principal_id: null
provider_account_profile_sha256: <canonical non-secret payload hash>
```

The stable ID is a lower-case bounded opaque control identifier. It is never
derived from an API key, token, email address, display label, repository,
provider output, environment value, executable path, or remote URL. IDs are
unique and never reused, including after disablement or removal.

The immutable hashed payload contains only:

- schema and contract versions;
- the stable account-profile and adapter IDs;
- one registered authentication-method ID;
- one registered transport-scope profile ID;
- `identity_assurance`, exactly `controller_profile` or `provider_verified`;
  and
- a stable bounded provider-native `remote_principal_id` only when assurance is
  `provider_verified`.

Endpoint host, region, organization, tenant, or project values that affect
credential destination or quota scope must be represented by an adapter-owned
registered `transport_scope_profile_id`. Project configuration and UI input may
not supply an arbitrary URL, hostname, certificate rule, header, organization
value, or proxy. Adding a custom endpoint or transport scope requires a new
reviewed adapter-owned profile; this prevents an untrusted endpoint from
receiving a credential.

Mutable presentation such as a bounded display name is stored outside the
hashed authority payload and is never a lookup or compatibility key. Enabled,
disabled, credential-present, and locally-probed states are current readiness
facts, not immutable identity fields and not part of the resolved configuration
hash.

### Credentials are external transport material

The profile contains no secret, secret hash, credential-store locator, service
name, environment-variable name, shell fragment, command argument, header, or
cookie. A credential-store key must be deterministically derived inside the
controller-owned credential adapter from fixed application identity plus the
stable provider-account profile ID; callers cannot provide or persist it.

An authentication method declares one of these initial behaviors:

- `externally_managed_cli`: the installed provider CLI owns its session.
  Continuo may report only adapter-proven presence or bounded status and must
  not copy, export, rotate, delete, or claim a remote identity it cannot prove;
  or
- `controller_managed_api_key`: the future controller credential service owns
  non-echoing installation, rotation, presence checking, and deletion in the OS
  credential store. There is no plaintext fallback.

This amendment defines identity and runtime binding semantics only. The
credential command protocol, macOS Keychain implementation, confirmations,
atomic rotation, deletion retention, and Rust TUI behavior belong to Gate 4.5.

### Identity assurance and external changes

`controller_profile` assures only that Continuo selected the same immutable
local account profile and transport scope. It does not assert that an external
CLI session or newly rotated key maps to the same remote billing principal.
Diagnostics must say `remote_identity_unknown` rather than infer one.

`provider_verified` requires an adapter-defined explicit network probe that
returns a stable bounded non-secret principal ID through a closed response
contract. The profile snapshot contains that ID. A later verified mismatch
blocks provider work as `provider_account_identity_changed`; it does not rewrite
the profile or silently accept the new principal. Ordinary `doctor`, dry-run,
configuration resolution, and offline setup readiness never perform this probe.

External CLI-login changes are outside Continuo's mutation boundary. If the
adapter can observe a stable local or remote identity, mismatch blocks. If it
can observe only presence, the run may use the same `controller_profile` under
the compatibility policy but diagnostics and audit must preserve the bounded
`remote_identity_unknown` limitation. No portfolio or security claim may call
that remote-principal pinning.

## Gate 3.1 configuration amendment

### Schema version and atomic selections

Because the published schema-1 logical shape contains only `role_routes`, the
first implemented generic configuration schema is version `2`; schema 1 is not
silently reinterpreted. User defaults, project configuration, explicit run
overrides, and resolved configuration each use their corresponding schema
version `2`.

Each selectable input is a complete pair:

```yaml
route_id: builtin.implementation.v1
provider_account_profile_id: provider-account:codex:local-session
```

Project configuration declares, for every required role:

- a non-empty set of complete permitted route/account pairs; and
- exactly one project-default pair contained in that set.

User defaults and explicit run overrides may each select one complete pair from
the project's permitted set. Resolution precedence remains exact: explicit run
override, user default, project default. A higher layer cannot override only the
route or only the account and inherit the other field from a lower layer. An
invalid or adapter-incompatible pair fails the whole resolution rather than
falling back or searching for another account.

This atomic-pair rule prevents a permissive cross-product in which separately
allowed routes and accounts combine into a binding the project owner never
approved.

### Resolved configuration

The schema-2 resolved record replaces `role_routes` with complete immutable
bindings:

```yaml
resolved_configuration_schema_version: 2
profile_id: continuo.jobs-compat.v1
role_bindings:
  implementation:
    route_profile: <complete registered route payload including effort and hash>
    provider_account_profile: <complete non-secret immutable account payload and hash>
  adversarial_review:
    route_profile: <complete registered route payload including effort and hash>
    provider_account_profile: <complete non-secret immutable account payload and hash>
  escalation_executive:
    route_profile: <complete registered route payload including effort and hash>
    provider_account_profile: <complete non-secret immutable account payload and hash>
  policy_authority:
    route_profile: <complete registered route payload including effort and hash>
    provider_account_profile: <complete non-secret immutable account payload and hash>
policy:
  correction: <existing fully resolved correction/escalation policy>
source_metadata: <Gate 3.1 and Gate 3.2 source identities and canonical hashes>
configuration_sha256: <canonical payload hash excluding this field>
```

The account payload in a run is non-secret and immutable. It excludes display,
enabled/disabled state, credential presence, credential generation, probe time,
and health. The configuration hash therefore remains stable across credential
rotation and transient provider health changes while still pinning the adapter,
authentication mechanism, transport scope, assurance, and verified principal
when present.

The complete route and account payloads may be normalized into deduplicated
internal storage, but the persisted logical value and canonical hash must be
equivalent to this self-contained representation. Resume cannot depend on
mutable defaults to reconstruct either payload.

## Gate 3.2 trusted-source amendment

Provider-account profiles are operator-controlled private controller artifacts,
not project files, task input, provider output, environment configuration, or UI
authority. Project configuration contains only project-permitted stable account
profile IDs inside complete role bindings; it cannot embed or create an account
profile.

Gate 4.5 will define the exact physical account-registry layout under the private
controller root. Whatever layout is approved must retain Gate 3.2's ownership,
regular-file, link, descriptor-relative, atomic-write, `0700` directory, and
`0600` file invariants. A workspace-write provider receives neither a mount nor
an inherited path to the account registry or credential store. Generic writer
work fails before invocation when that separation cannot be proven.

At configuration resolution, the controller acquires one coherent snapshot of
the exact project configuration and every referenced immutable account profile.
`source_metadata` records each selected account-profile ID and canonical
non-secret profile hash in stable role order. It never records registry paths,
display metadata, enabled state, credential presence/generation, probe results,
or secret-store facts.

Resume requires the installed immutable profile for each saved account ID to
match its saved payload and hash. Missing, replaced, unsafe, or hash-incoherent
authority sources block. Mutable display changes do not invalidate resume;
disablement and credential readiness are evaluated separately at arming and do
not rewrite the saved configuration.

## Gate 3.6 capability amendment

Effort and provider-account selection add no capability fields and do not change
any approved permission ceiling. `credential_access:
adapter_managed_transport_only` authorizes only the registered adapter to obtain
transport material for the exact saved account profile while building or
executing its attempt. It does not authorize the model, prompt, provider tools,
workspace processes, project code, verifier, CLI/TUI client, or controller
configuration layer to read or choose credentials.

A route whose capability profile declares `credential_access: none` cannot bind
an account that requires credential access. A route declaring
`adapter_managed_transport_only` still must prove that its builder and sandbox do
not expose credential bytes, credential-store access, or general network access
to provider tools. Effort mode and value have no effect on this comparison.

The saved role binding therefore extends Gate 3.6 startup and arming evidence
with exact route/account adapter equality and authentication/transport-scope
compatibility; it does not expand the capability vocabulary or permit a weaker
enforcement proof.

## Validation and arming

Before run creation, the controller validates each role binding in this order:

1. resolve exactly one complete registered route and one immutable account
   profile from their stable IDs;
2. validate schemas, canonical hashes, unique IDs, supported adapter contracts,
   and the route's effort policy;
3. require exact equality between the route adapter and account adapter;
4. require the authentication method and transport scope to be allowed by that
   adapter and command/API builder;
5. apply the existing Gate 3.6 capability-profile and role-ceiling comparison;
6. require the complete pair to be project-permitted; and
7. persist the complete resolved configuration before a lock, invocation,
   provider attempt, Git action, or target mutation.

Before every provider invocation and physical attempt, arming revalidates the
saved route/account hashes, effort enforcement, adapter equality, account
availability, credential presence when locally observable, identity-assurance
requirements, capability/ceiling evidence, and existing repository/writer
evidence. It does not reread defaults or choose another account, effort, model,
route, provider, or endpoint.

The amended `ProviderInvocationRequest` and normalized physical-attempt result
use schema version `2`; published schema-1 payloads remain historical shapes.
Both schema-2 records gain the saved
`provider_account_profile_id`, `provider_account_profile_sha256`, effort mode,
registered effort ID when explicit, and effort enforcement policy ID when
applicable. A controller-managed attempt may additionally record the non-secret
credential generation used. It never records credential bytes, a secret hash,
the credential-store key, or inherited environment content.

## Rotation, disablement, removal, and resume

- Credential rotation is an explicit controller-owned provider-account action.
  It does not change the immutable account profile, route, or resolved
  configuration hash. A subsequent attempt may use the new credential
  generation and records that bounded generation as audit evidence.
- Rotation cannot change adapter, authentication method, transport scope,
  assurance, or verified principal. Such a change requires a new account profile
  ID and affects only future configuration or a separately approved route/account
  migration.
- A failed or interrupted controller-managed rotation must leave either the old
  generation usable or the profile visibly unavailable; it may never report the
  new generation active without confirmed credential-store completion. Gate 4.5
  defines the concrete atomic protocol.
- Disabling an account prevents new selection and provider arming for saved runs.
  Re-enabling the same immutable profile may restore readiness after full
  validation; it does not alter saved configuration.
- Removal creates a tombstone and never permits ID reuse. A saved run remains
  inspectable but blocks provider work as `provider_account_unavailable`; no
  current default or same-adapter account is substituted.
- A missing controller-managed credential blocks as
  `provider_credential_missing`. An unavailable credential store blocks as
  `provider_credential_store_unavailable`. Neither is normalized as provider
  authentication failure because no provider attempt began.
- A provider-native rejection after spawn remains the existing `auth` failure
  with attempt evidence. The controller does not automatically rotate, repair,
  prompt for, or switch credentials during a run.
- Existing no-fallback and retry policies remain exact. Account, credential,
  effort, model, endpoint, and route changes are never retry behavior.

## Doctor, dry-run, setup readiness, and live probes

Ordinary `doctor`, dry-run, configuration planning, and offline setup readiness
may inspect only registered metadata, safe file/keychain presence status, local
CLI discoverability, bounded adapter-proven CLI authentication status, hashes,
and compatibility. They do not contact a provider, validate a remote principal,
create or rotate credentials, construct a provider prompt, or incur model cost.

A future explicit account connectivity command may contact only the registered
transport scope through its adapter. It must disclose network and possible-cost
effects before invocation and distinguish authentication, remote-identity,
model-availability, and billable model probes. Its result cannot authorize a
run, change configuration, select fallback, or silently update an account
profile. Gate 4.5 defines that command contract.

Machine/default output exposes stable route, adapter, effort, and account IDs;
immutable hashes; identity assurance; credential presence as `present`,
`missing`, `external`, or `unknown`; and bounded reason codes. It never exposes
profile-private metadata unnecessarily and never prints a secret, secret hash,
credential locator, environment value, full provider response, or arbitrary
endpoint.

## Compatibility and migration

The four existing compatibility routes retain their current route IDs and
behavior. Gate 4 adds `provider_default` effort records to their new complete
catalog payloads and supplies two built-in non-secret compatibility account
profiles:

| Account profile ID | Adapter | Authentication | Assurance |
|---|---|---|---|
| `builtin.codex-cli.local-session.v1` | `codex_cli` | externally managed CLI session | `controller_profile` / remote identity unknown |
| `builtin.claude-cli.local-session.v1` | `claude_cli` | externally managed CLI session | `controller_profile` / remote identity unknown |

These profiles preserve current installed-CLI behavior without claiming that
Continuo owns or remotely pins the underlying CLI login. The Jobs compatibility
profile permits the corresponding route/account pair for each role.

No schema-12 or earlier run contains a resolved schema-2 configuration,
provider-account profile, verified effort policy, or credential generation.
Migration preserves every known historical route/model/adapter fact and every
absence. It does not infer `explicit: high` from a display label, invent an
account profile from a CLI executable, attribute a remote principal, or create a
credential generation.

Historical runs remain governed by the existing migration/classification
contracts until a separate bounded Gate 4 run-schema migration proves which
non-provider transitions, if any, can continue. A historical run that would
require new provider work cannot be upgraded merely by applying current defaults
or the built-in local-session profile. Inspection and sanitized export remain
available under their existing contracts.

No source schema-1 generic configuration has shipped. If one is encountered,
it is unsupported input rather than implicitly receiving a provider account.
The controller reports the supported schema and requires an explicit
controller-owned schema-2 installation.

## Failure taxonomy

Pre-attempt account and credential readiness uses bounded configuration reason
codes rather than fabricated provider attempts:

- `provider_account_missing`;
- `provider_account_disabled`;
- `provider_account_unavailable`;
- `provider_account_incompatible`;
- `provider_account_identity_changed`;
- `provider_credential_missing`;
- `provider_credential_store_unavailable`;
- `provider_credential_state_unknown`; and
- `provider_effort_incompatible`.

Gate 4 implementation may map these reasons to an existing persisted blocked
stage or add a versioned closed stage under the separately approved run-schema
migration. It must not overload `auth`, `unavailable`, or `provider_error` before
a physical provider attempt exists.

## Non-goals

This item adds no Python or Rust source, configuration file, account registry,
Keychain entry, secret-store implementation, CLI/TUI command, provider SDK,
model or effort discovery, new provider/model/route, dynamic endpoint, live
probe, run-schema migration, credential, provider call, target access, Jobs
inspection, Git action, commit, or push.

It does not define the Gate 4.5 command protocol or TUI layout, automatic
fallback, account-wide circuit breakers, cost telemetry, remote approval,
multi-user authorization, credential synchronization, cloud secret stores, or
strong remote-principal assurance for adapters that cannot provide it.

## Adversarial matrix

| ID | Scenario | Required outcome |
|---|---|---|
| G41-01 | A route lacks `effort`, has an unknown mode, or has incoherent required/forbidden fields. | Catalog validation fails before configuration resolution or external work. |
| G41-02 | UI/configuration supplies raw `high`, a number, `max`, or a provider-returned effort value. | Rejected; only a complete registered route with a closed effort record is selectable. |
| G41-03 | A display name contains `High` but its builder supplies no verified effort control. | Route records `provider_default`; presentation text does not become control evidence. |
| G41-04 | An explicit-effort route's builder omits, changes, duplicates, or cannot enforce the saved effort. | Command-plan validation/arming fails as `provider_effort_incompatible`; no attempt starts. |
| G41-05 | Retry, content retry, correction, or resume requests a different effort. | Refused; the exact saved route and effort remain pinned. |
| G41-06 | A higher effort route has broader tools or permissions than its role ceiling. | Existing capability comparison rejects it; effort grants no authority. |
| G41-07 | Provider retires an effort value or current defaults change. | Saved run blocks if its exact route/enforcement is unavailable; no effort/model fallback occurs. |
| G41-08 | Account profile ID is derived from an API key, token hash, email, display label, repository, or provider prose. | Creation/validation rejects it and exposes none of the source value. |
| G41-09 | Account profile contains a secret, secret hash, environment name, credential locator, raw endpoint, header, or shell text. | Strict parsing rejects the profile; nothing is persisted or invoked. |
| G41-10 | Route adapter and account adapter differ. | Complete binding fails before run creation; no compatible account is searched automatically. |
| G41-11 | Project permits a route and an account separately but not their exact pair. | Pair is not selectable; no cross-product permission is inferred. |
| G41-12 | User default or run override supplies only route or only account. | Schema-2 input is incomplete and rejected rather than inheriting the missing half. |
| G41-13 | Higher-precedence binding is invalid while project default is valid. | Entire resolution fails; invalid input is not treated as absent. |
| G41-14 | Account transport scope uses an arbitrary endpoint supplied by UI, project, environment, task, or provider. | Rejected; only an adapter-owned registered transport-scope profile is allowed. |
| G41-15 | Mutable account display name changes. | Presentation changes only; profile/configuration hashes, routing, and authority remain unchanged. |
| G41-16 | Adapter, auth method, transport scope, assurance, or verified principal is edited in place. | Replacement is rejected; a new never-reused account profile ID is required. |
| G41-17 | Controller-managed credential rotates successfully. | Account and configuration hashes remain stable; next attempt records the new non-secret generation. |
| G41-18 | Rotation is interrupted between credential-store writes. | Old generation remains usable or account becomes visibly unavailable; new generation is never falsely reported active. |
| G41-19 | Account is disabled after run creation. | Inspection remains available; provider arming blocks without selecting another account. |
| G41-20 | Account is removed while a saved run references it. | Tombstone prevents ID reuse; provider work blocks as `provider_account_unavailable`. |
| G41-21 | Credential is missing or credential store is unavailable before spawn. | Bounded pre-attempt reason is persisted; no fabricated provider attempt or `auth` failure is recorded. |
| G41-22 | Provider rejects a credential after spawn. | Existing normalized `auth` attempt evidence applies; no automatic repair, rotation, retry, or account switch occurs. |
| G41-23 | External CLI login changes and adapter can prove a stable identity mismatch. | Provider work blocks as `provider_account_identity_changed`; profile is not rewritten. |
| G41-24 | External CLI exposes presence but no stable remote identity. | Profile remains `controller_profile`; diagnostics state `remote_identity_unknown` and make no pinning claim. |
| G41-25 | Ordinary `doctor`, dry-run, or setup readiness is requested. | Only local non-mutating checks run; no network, credential mutation, provider prompt, or model cost occurs. |
| G41-26 | Explicit connectivity test is requested without network/cost acknowledgement. | Test does not run; no account/configuration state changes. |
| G41-27 | Connectivity response proposes an endpoint, account change, fallback, or route. | Response is bounded evidence only and cannot mutate authority or selection. |
| G41-28 | Saved route/account payload or hash differs from current catalog/registry. | Resume/provider arming blocks; current defaults and equivalent-looking profiles are ignored. |
| G41-29 | Historical record has `Luna High` or another display label but no effort/account facts. | Migration preserves absence; it invents neither explicit effort nor provider account. |
| G41-30 | Generic schema-1 route-only configuration is supplied. | Report unsupported schema and require explicit schema-2 installation; no implicit account is added. |
| G41-31 | Credential-looking values appear in errors, display metadata, fixtures, or provider output. | Redacted bounded diagnostics and tests reveal no value and never use it as identity/control input. |
| G41-32 | Concurrent configuration/account mutation races with resolution or arming. | One coherent immutable snapshot wins or the operation blocks; no mixed route/account/credential state is used. |
| G41-33 | Proposal adds Keychain code, Rust TUI code, a new route/model, provider discovery, runtime migration, live call, or Jobs access. | Out of scope for this documentation-only amendment and requires its later bounded Gate 4/4.5 item. |
| G41-34 | An amended route, invocation request, or attempt result supplies schema version 1 with schema-2 fields, or retains a schema-1 hash after amendment. | Strict parsing/hash validation rejects it; published schemas are never reinterpreted. |
| G41-35 | Project, task, provider, environment, or UI embeds an account profile or chooses its source path. | Rejected; project configuration may reference only a preinstalled stable account ID in a complete permitted binding. |
| G41-36 | Writer can read or mutate the private account registry or credential store. | Generic writer route fails before invocation because authority/secret-store isolation is unproven. |
| G41-37 | Account display, enabled state, credential presence, generation, or probe status changes. | Immutable source/configuration hash remains stable; presentation/readiness is evaluated separately and cannot rewrite saved authority. |
| G41-38 | Route capability declares no credential access, or adapter transport exposes credentials/network to model tools. | Capability validation rejects the binding; account selection and effort cannot expand transport or tool authority. |

## Approval and validation evidence

This contract was prepared on 2026-08-03 from the owner-approved Gate 3.1, 3.2,
3.5, and 3.6 contracts plus the reconciled roadmap and execution plan. The
repository owner explicitly approved its decisions, non-goals, and 38-case
adversarial matrix on 2026-08-03.

Validation on `main` at
`c72e47b207dc240e4b787f8c2b5aac7a97ea7a08` confirmed that all 50 local
Markdown links in the changed tracker/index/contract files resolve, all 38
`G41-*` matrix IDs are unique and contiguous, and `git diff --check` passes.
Publication evidence will be recorded only after a separately approved commit
and push.

No runtime source, test fixture, configuration, credential, provider, target
checkout, Jobs repository, run record, Git side effect, commit, or push is
changed or invoked by this documentation item.
