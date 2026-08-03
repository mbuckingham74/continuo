# Gate 3.6 contract — Capability profiles and permission ceilings

**Status:** owner-approved and published; documentation-only Gate 3 deliverable; implementation is Gate 4 work.

**Owner-approved additive amendment (2026-08-03):**
[`Gate 4.1`](../gate-4/gate-4.1-effort-provider-account-amendment.md) constrains
account-bound credential access to adapter-managed transport and confirms that
effort/account selection grants no additional capability.

## Decision

Continuo will make least authority a startup and arming invariant. Every
provider route selected for a run must name one complete, immutable,
machine-checkable `CapabilityProfile`; every orchestration role must have one
complete, immutable `PermissionCeiling`. A route is usable only when its
profile both satisfies the operation's required capabilities and is no broader
than the role ceiling. Unknown, absent, duplicated, hash-incoherent, or
incompatible declarations block before a run is created, a writer is armed, or
a provider process is started.

Capability profiles describe the effective authority that a registered adapter
and command builder enforce for one route. They are not provider marketing
claims, prompt instructions, mutable project configuration, or a request for
runtime discovery. Permission ceilings are controller-owned role policy. A
project, task, provider response, display name, environment value, CLI
override, or route picker may select only a registered complete route; none may
invent a profile, relax a ceiling, widen a tool set, or grant a new authority.

This Gate refines the coarse compatibility declarations from Gate 3.5 without
changing them: `read_only` maps to a read-only profile and `workspace_write`
maps to the implementation-writer profile below. Gate 4 implements catalog
loading, command enforcement, startup/arming validation, persistence, and any
necessary migration. No runtime behavior changes in this Gate.

## Closed vocabulary and comparison rules

The initial capability-profile schema is `1`. Every profile is a strict UTF-8
JSON or YAML catalog record with no unknown fields, duplicate keys, aliases,
implicit coercions, non-finite numbers, executable paths, shell fragments,
credentials, or provider-derived values. Stable IDs are lower-case opaque
control identifiers; display text is optional presentation only.

An authority comparison is fieldwise and fail-closed:

- `filesystem_access` is ordered `none < workspace_read < workspace_write`.
- A set of allowed tools is no broader only when it is a subset of the ceiling's
  allowed set. `workspace_shell` is a distinct tool class, not an alias for
  read tools.
- `tool_network_access`, `git_control`, `configuration_mutation`,
  `publication`, and `approval_decision` are boolean grants. A route may grant
  one only if its ceiling grants it. This Gate grants none of them.
- `credential_access` is one of `none` or `adapter_managed_transport_only`.
  The latter permits only adapter-owned credential lookup needed to contact its
  provider; it never exposes credentials to prompts, tool processes, logs,
  configuration, or persisted records.
- `process_access` is `none`, `read_tools_only`, or `workspace_tools_only`.
  The latter is valid only with `workspace_write`; it permits registered
  sandboxed workspace tools, never arbitrary host process authority. Its tool
  allowlist is still compared as a set.

`structured_output_contract_ids`, cancellation support, and bounded-context
limits are compatibility facts, not grants. A route must support the exact
operation output contract and meet its finite declared minimums. It cannot use
larger context, streaming, or transport support to imply permission to read,
write, network, retry, Git, approve, or publish.

`tool_network_access: false` means no provider tool may make a network
connection. The provider adapter's own authenticated transport is outside that
tool authority and remains restricted to its registered adapter boundary from
Gate 3.5; it does not create general network access for the model or workspace.

## Capability-profile record

Each registered profile has this logical shape. The profile hash is the
lower-case SHA-256 of the canonical JSON payload (UTF-8, sorted keys, no
insignificant whitespace, terminal newline), excluding `profile_sha256`.

```yaml
capability_profile_schema_version: 1
capability_profile_id: continuo.capability.implementation-writer.v1
profile_contract_id: continuo.capability-profile.v1
compatibility_capability: workspace_write
permissions:
  filesystem_access: workspace_write
  process_access: workspace_tools_only
  allowed_tool_ids: [workspace_shell, read_file, glob, grep]
  tool_network_access: false
  git_control: false
  configuration_mutation: false
  publication: false
  approval_decision: false
  credential_access: adapter_managed_transport_only
compatibility:
  structured_output_contract_ids: [continuo.unstructured-text.v1]
  cancellation: supervised_process_group
  heartbeat: controller_owned
  max_prompt_bytes: <finite positive registered bound>
  max_output_bytes: <finite positive registered bound>
profile_sha256: <canonical payload hash>
```

The implementation must prove a profile's permissions through registered
adapter/builder/supervision enforcement, not merely declare them. In particular,
the effective writer sandbox must deny network, Git control, repository
configuration mutation, publication, and approval decisions even if a prompt,
task, environment, provider output, or tool argument asks otherwise. A profile
cannot claim a denial that the selected adapter/builder cannot enforce.

The capability catalog initially contains exactly these profiles:

| Profile ID | Coarse compatibility | Filesystem / processes / tools | Invariant denials |
|---|---|---|---|
| `continuo.capability.review-readonly.v1` | `read_only` | `workspace_read`; `read_tools_only`; `read_file`, `glob`, `grep` | tool network, Git, config mutation, publication, approval, and workspace writes |
| `continuo.capability.advisory-readonly.v1` | `read_only` | `workspace_read`; `read_tools_only`; `read_file`, `glob`, `grep` | tool network, Git, config mutation, publication, approval, and workspace writes |
| `continuo.capability.implementation-writer.v1` | `workspace_write` | `workspace_write`; `workspace_tools_only`; `workspace_shell`, `read_file`, `glob`, `grep` | tool network, Git, config mutation, publication, and approval |

The two read-only profiles remain distinct stable IDs so a future approved
change cannot silently give an advisory route reviewer-specific authority (or
vice versa). Their identical initial permission vectors do not make them
interchangeable. A profile can be retired only by disabling new selection; a
saved run requires its exact saved profile and hash at resume or blocks.

## Role ceilings and operation requirements

Role ceilings are controller-owned catalog records. They use the same schema,
canonicalization, and hash rules as capability profiles, contain a `role_id`,
the allowed operation IDs, the maximum permission vector, required compatibility
facts per operation, and `permission_ceiling_sha256`. They do not name a
provider, model, command, executable, account, display label, project, or
repository path.

The initial compatibility ceilings are:

| Role ID | Allowed operations | Maximum profile / required output | Ceiling |
|---|---|---|---|
| `implementation` | `implementation_write`, `correction_write` | `continuo.capability.implementation-writer.v1`; `continuo.unstructured-text.v1` | May write only the owned workspace through registered workspace tools; all network, Git, configuration, publication, and approval authority remains denied. |
| `adversarial_review` | `specification_review`, `implementation_review` | `continuo.capability.review-readonly.v1`; `continuo.review-result.v1` | Read-only workspace inspection through `read_file`, `glob`, and `grep`; no writer or control authority. |
| `escalation_executive` | `escalation_guidance` | `continuo.capability.advisory-readonly.v1`; `continuo.unstructured-text.v1` | Read-only advisory analysis; no writer or control authority. |
| `policy_authority` | `policy_clarification` | `continuo.capability.advisory-readonly.v1`; `continuo.unstructured-text.v1` | Read-only advisory analysis; no writer or control authority. |

Only the controller, outside all provider profiles, owns task/repository adapter
selection, target ownership and locks, run storage, retries, correction-budget
transitions, recovery, deterministic verification, Git mutations, commit/push
approval gates, and publication. An advisory result can recommend an action but
cannot perform or authorize one.

The role ceiling is both an upper and a lower bound: a route must be no broader
than the maximum permission vector and must provide every operation's listed
output contract, read/write access, process class, tool IDs, cancellation, and
finite bounds. Selecting a profile that is too weak is an error, not a request
to bypass or augment it with another route, provider, tool, or fallback.

## Validation, resolution, and arming

At new-run startup, after Gate 3.1 configuration resolution and Gate 3.5 route
lookup, the controller validates each required role in this order:

1. resolve exactly one registered role ceiling and one registered capability
   profile from stable IDs;
2. verify supported schema/contract IDs, canonical hashes, closed fields, and
   finite compatibility bounds;
3. verify the route's role, operation set, output contracts, adapter descriptor,
   command-builder policy, supervision policy, and capability-profile ID against
   the profile and ceiling; and
4. prove that the registered adapter/builder enforcement evidence is sufficient
   for every declared denial and grant.

Any failure blocks before locks, run-state creation, provider work, Git, or
target mutation. A successful run persists the full resolved route payload,
capability profile payload/hash, and role-ceiling payload/hash with the
configuration snapshot before the first invocation. A provider attempt records
the exact saved `capability_profile_id` and hash. This extends—not replaces—the
existing route, operation, prompt, supervision, retry, and writer-evidence
records specified by Gate 3.5.

Before each physical attempt, arming rechecks that the saved role, operation,
route hash, profile hash, ceiling hash, command-builder policy, and required
repository evidence still agree. It never re-resolves current defaults or
accepts a changed catalog record. A missing/changed/unsupported or
unenforceable saved profile or ceiling blocks resume and does not select an
equivalent-looking model, same provider, profile with the same display text, or
a less restrictive fallback.

`doctor` and dry-run perform only static catalog/compatibility validation and
the Gate 3.5 local non-mutating adapter probe. They do not invoke a provider,
build a sensitive prompt, contact a network, inspect credentials beyond the
safe probe result, create a run, or modify a target. Diagnostics expose stable
IDs, hashes, and bounded reason codes only.

## Configuration, migration, and compatibility

The Gate 3.1 project configuration may permit only complete registered route
IDs. It cannot supply a capability profile or ceiling, splice a model/tool into
another route, or override any authority field. A future profile/ceiling catalog
change requires a new stable ID and this contract's compatibility validation;
it cannot overwrite an existing stable ID or hash.

The Gate 4 compatibility profile `continuo.jobs-compat.v1` must map its four
existing route IDs exactly as follows:

| Route ID | Role | Saved capability profile |
|---|---|---|
| `builtin.implementation.v1` | `implementation` | `continuo.capability.implementation-writer.v1` |
| `builtin.adversarial_review.v1` | `adversarial_review` | `continuo.capability.review-readonly.v1` |
| `builtin.escalation_executive.v1` | `escalation_executive` | `continuo.capability.advisory-readonly.v1` |
| `builtin.policy_authority.v1` | `policy_authority` | `continuo.capability.advisory-readonly.v1` |

Existing records with only `read_only` or `workspace_write` retain their exact
historical classification. A future migration may preserve known coarse
capability and route facts but cannot invent a profile hash, ceiling hash,
enforcement proof, allowed tool set, or profile compatibility. Such records
remain execution-refused unless an approved Gate 4 migration proves every
required fact. Deprecated `jobs-orchestrator`, `JOBS_REPO`, and
`src/jobs_orchestrator` identifiers retain their documented compatibility
behavior and cannot bypass profile or ceiling validation.

## Non-goals

This Gate adds no runtime source, test, fixture, provider command, sandbox
implementation, configuration loader, catalog UI/picker, provider/model,
adapter, live capability probe, network call, storage schema, migration, CLI,
retry behavior, target access, Git action, commit, or push. It does not access
Jobs or alter existing provider commands, controller authority, ownership,
recovery, storage behavior, Git gates, or compatibility identifiers. It does
not define deterministic verification findings or correction budgets; those are
the next Gate 3.7 contract.

## Adversarial matrix

| ID | Scenario | Required outcome |
|---|---|---|
| G36-01 | A route, profile, or ceiling is missing, duplicated, unsupported, hash-incoherent, or has an unknown field. | Startup blocks before run creation, provider work, lock, Git, or target mutation. |
| G36-02 | A display name, provider label, model name, package name, or project path is used as an authority lookup key. | Validation rejects it; stable control IDs and hashes alone govern authority. |
| G36-03 | Project configuration, CLI override, task, provider result, environment, or prompt supplies a profile, ceiling, tool, executable, shell flag, or permission value. | It is rejected/ignored as authority; only registered complete route/profile/ceiling records apply. |
| G36-04 | An operation is absent from its role ceiling or route operation set. | Route selection/arming fails; the controller does not reinterpret the operation or use another route. |
| G36-05 | A review/advisory route requests workspace write, `workspace_shell`, tool network, Git, configuration mutation, publication, or approval. | Ceiling comparison rejects the route before process start. |
| G36-06 | The implementation route requests Git, configuration mutation, publication, approval, or tool network authority. | Ceiling comparison rejects the route even though workspace write is otherwise permitted. |
| G36-07 | A route declares `read_only` while its effective builder/sandbox can write or run unlisted tools. | Enforcement proof fails; the declaration cannot mask effective authority. |
| G36-08 | A writer profile claims to deny Git/network/control authority but the registered builder cannot enforce that denial. | Startup fails closed; prompts and policy prose are insufficient evidence. |
| G36-09 | A route supplies required permissions but lacks the exact operation output contract, supervised cancellation, heartbeat, or finite bounds. | Compatibility validation rejects it before invocation. |
| G36-10 | A route has a broader context or streaming capability than another route. | That fact grants no extra authority and cannot relax a ceiling or select a fallback. |
| G36-11 | A profile contains zero, negative, non-finite, or unbounded prompt/output limits. | Strict validation rejects it before a prompt or process is created. |
| G36-12 | A tool ID is unknown, duplicated, implied by a broad process class, or outside the ceiling allowlist. | The profile fails closed; no wildcard or alias expands tool authority. |
| G36-13 | A profile grants `adapter_managed_transport_only` credential access and a prompt/tool/log requests credentials. | Credentials remain adapter-private; the request is denied/redacted and no authority expands. |
| G36-14 | The provider transport uses its adapter-owned authenticated connection while tool network access is false. | Static validation accepts only the registered adapter transport; the model/tools gain no general network access. |
| G36-15 | A model proposes commit, push, branch, reset, config edit, approval, or retry. | Provider authority remains denied; only controller-owned deterministic/Git/approval paths may act. |
| G36-16 | A selected route is too weak for its role (for example, an implementation route is read-only). | Startup blocks rather than augmenting it, relaxing the ceiling, or choosing a fallback. |
| G36-17 | A selected profile is broader than its role ceiling but has the same provider or a familiar display name. | Startup blocks; provider/model/display similarity has no compatibility effect. |
| G36-18 | Route/profile/ceiling passes startup, then one catalog hash or enforcement policy changes before arming. | Arming blocks before spawn; current defaults and catalog replacements are not consulted. |
| G36-19 | A saved run resumes after its exact profile or ceiling is removed, changed, unsupported, or unenforceable. | Resume blocks visibly with bounded evidence; no equivalent profile, model, provider, or fallback is chosen. |
| G36-20 | Crash occurs after route resolution but before the profile/ceiling snapshot is durable. | No provider work begins; incomplete authority state is never inferred on resume. |
| G36-21 | Attempt record role, operation, route hash, profile hash, ceiling hash, or command-builder policy differs from its saved snapshot. | Arming rejects it before process start. |
| G36-22 | A historical record contains only the legacy coarse capability. | Migration preserves that fact but invents no profile/ceiling/enforcement proof; execution remains refused absent approved proof. |
| G36-23 | Deprecated Jobs command/environment/package identifiers invoke a compatibility run. | The documented resolver preserves existing targeting behavior while applying the one capability/ceiling validation path. |
| G36-24 | `doctor` or dry-run evaluates incomplete or incompatible profiles. | It returns static bounded failure evidence without provider/network invocation, prompt construction, run creation, or target mutation. |
| G36-25 | A proposed implementation adds a live capability probe, dynamic tool discovery, new provider route, sandbox change, migration, picker, retry change, or Gate 4 runtime work. | It is out of scope pending explicit later approval and implementation work. |

## Approval and implementation evidence

The owner approved this Gate 3.6 contract on 2026-08-03. Gate 3.6 is a
documentation-only contract-definition deliverable: implementation of catalog
loading, adapter/builder enforcement, startup and arming validation, snapshot
persistence, migration, CLI wiring, and installed-package validation (with
`UV_NO_EDITABLE=1` on macOS) belongs to the explicitly later Gate 4 work. No
runtime source, test, fixture, run record, provider, target checkout, Git side
effect, commit, or push changed for this Gate.

Publication evidence: commit `4bb8189a2485ea185dd476060618fa3310105334`
(`Define capability profiles and permission ceilings`) is on `origin/main`.

Validation on clean synchronized `main` at
`b10f0b66831143721dcfb48df1661e56a1580dfb` confirmed that authoritative local
Markdown links resolve, all 25 `G36-*` matrix IDs are unique, and tracked and
new-file `git diff --check` whitespace checks pass. No live-provider or
Jobs-repository validation was performed.
