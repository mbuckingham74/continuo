# Gate 3.1 contract — Versioned resolved configuration

**Status:** approved; documentation-only Gate 3 deliverable; awaiting owner implementation review.

## Decision

Continuo will accept three declarative configuration inputs and derive one
complete, immutable `ResolvedConfiguration` before a new run is created. The
resolved value, its canonical SHA-256, and the configuration-schema version
are the only configuration facts that a run may use after creation. A resumed
run must use its persisted resolved value; it must not re-read defaults,
project configuration, environment values, or command-line overrides to alter
control flow.

Configuration resolution is deliberately capability-narrowing, not a generic
last-writer-wins deep merge. A higher-precedence input may choose only a route
that the project permits for that role. It may not supply a provider command,
expand a provider capability, change a retry/approval/Git rule, modify a task
or repository adapter, or add an unknown role.

This contract defines the resolution vocabulary and precedence only. Gate 3.2
will decide the trusted project-configuration location, ownership, writer
protection, and source-hash validation. Gate 3.5 will define adapter catalogs
and Gate 3.6 will define capability profiles. Gate 4 will implement parsing,
validation, persistence, CLI wiring, and migration.

## Configuration vocabulary

All supported configuration documents are strict UTF-8 JSON or YAML mappings.
Their decoded data model forbids unknown fields; duplicate mapping keys,
non-finite numbers, aliases/anchors with non-scalar expansion, and implicit
type coercion fail closed. Documents contain no credentials, environment
values, executable commands, shell fragments, prompt bodies, task text, or
repository paths derived from untrusted provider output.

The initial configuration schema is `1`. Its stable role IDs are the existing
`implementation`, `adversarial_review`, `escalation_executive`, and
`policy_authority` IDs. These are control identifiers, never display labels.
The initial compatibility profile is `continuo.jobs-compat.v1`; it preserves
the existing built-in route IDs, provider-adapter IDs, model IDs, commands,
capabilities, policies, and Jobs CLI behavior unchanged.

### Input layers

The three inputs have these precise, distinct purposes:

| Layer | Stable document field | May state | May not state |
|---|---|---|---|
| User defaults | `user_defaults_schema_version: 1` | An optional preferred `route_id` for each known role | A new route, provider adapter, model ID, command, capability, or policy |
| Project configuration | `project_configuration_schema_version: 1` | The compatibility/profile ID, the permitted route IDs and project default route ID for every required role, plus the policy selections that later contracts make configurable | An unrecognized role, executable/provider command, or a relaxation of controller authority |
| Explicit run overrides | `run_overrides_schema_version: 1` | An optional selected `route_id` for a known role and only future contract-defined, explicitly overridable policy choices | Any value not allowed by the resolved project configuration |

A source omits a role rather than assigning `null`. A project configuration
must name a project default route and a non-empty permitted set for every role
required by its profile. A user default or explicit override is valid only when
its role is required and its route is in that permitted set. Configuration
cannot introduce an optional role; adding or removing roles requires a new
profile/configuration schema and its own approved compatibility contract.

`route_id` selection refers to an adapter-owned, versioned route catalog. The
later provider-adapter contract defines catalog records, including stable
provider-adapter and model identifiers and display metadata. Selection never
copies those fields into user defaults or overrides; a display-name/model-name
rename cannot change selection or authorization.

## Resolution order and algorithm

Resolution runs once, in the following order:

1. Select the named built-in compatibility profile or a project profile whose
   identity and trust are accepted under Gate 3.2. Unknown, disabled, or
   unsupported profiles fail before any provider, Git, or run-state write.
2. Validate the project configuration structurally and resolve its allowed
   route set and default route for every required role. Validate each selected
   route against the trusted adapter catalog and the capability profile when
   those contracts are implemented.
3. For each role, choose the first present candidate in this order: explicit
   run override, user default, project default. Reject the entire resolution if
   the candidate is not project-permitted; do not silently fall back.
4. Resolve each future-configurable policy through the same conservative rule:
   project configuration sets its allowed domain and default; an explicit
   override may select only a project-permitted value; user defaults have no
   policy authority unless a later approved field explicitly grants it.
5. Construct, strictly validate, canonicalize, hash, and freeze exactly one
   `ResolvedConfiguration`. No unresolved source text, environment lookup, or
   precedence decision remains available to workflow transitions.

An absent user default or run override simply allows the next lower-precedence
candidate. An invalid higher-precedence value is an error, not absence. This
prevents a typo or an attacker-controlled option from silently changing a run
to the project default.

## Resolved configuration record

The persisted logical shape is below. Gate 4 may choose a typed Python model
and a storage schema version, but it must preserve these semantics exactly.

```yaml
resolved_configuration_schema_version: 1
profile_id: continuo.jobs-compat.v1
role_routes:
  implementation: builtin.implementation.v1
  adversarial_review: builtin.adversarial_review.v1
  escalation_executive: builtin.escalation_executive.v1
  policy_authority: builtin.policy_authority.v1
policy:
  correction: <existing fully resolved correction/escalation policy>
source_metadata:
  project_configuration: <stable source identity and canonical content hash>
  user_defaults: <present source identity and hash, or absent>
  run_overrides: <present canonical override hash, or absent>
configuration_sha256: <SHA-256 of the canonical resolved payload excluding this field>
```

The canonical payload uses a published deterministic JSON encoding: UTF-8,
object keys sorted lexicographically, no insignificant whitespace, decimal
numbers in their validated normal form, and a terminal newline. The SHA-256 is
the lowercase hexadecimal digest of those exact bytes. `source_metadata` is
audit provenance, not an authority source after run creation; a source-hash
change does not mutate an existing run.

The record contains selected stable route IDs, not mutable presentation fields.
At validation time, every selected route must resolve to exactly one catalog
record. The fully resolved provider identity used for an invocation remains
persisted in its existing immutable provider record. The resolved configuration
adds the run-wide selection snapshot; it does not replace historical attempt
evidence.

## Persistence, resume, and compatibility contract

Gate 4 must persist the whole resolved record and its hash atomically before
the first provider invocation or writer arming operation. No run can be created
with a partial configuration. A transition that lacks a valid persisted
resolved configuration must block before external work.

Resume loads and validates the persisted resolved record and uses its selected
routes and policy. It must fail closed if the record is missing, malformed,
hash-incoherent, version-unsupported, internally inconsistent, or refers to a
route unavailable in the installed compatible catalog. It must not substitute
the currently configured default, a same-display-name route, a provider/model
fallback, or a newly available route.

For the Gate 4 compatibility release, new runs without an explicit generic
configuration resolve through `continuo.jobs-compat.v1`. The deprecated
`jobs-orchestrator`, `JOBS_REPO`, and `src/jobs_orchestrator` interfaces retain
their documented target-selection behavior. They neither bypass resolved
configuration creation nor create a second precedence system. Existing runs
remain readable through the established run-schema migration/classification
rules; their exact classification and any migration to a configuration-bearing
run schema require an approved Gate 4 migration contract. A historical run is
never silently assigned a newly changed route.

## Authority and non-goals

Configuration is data, not executable policy. It cannot grant a provider Git,
network, workspace-write, retry, correction, escalation, approval, or publish
authority beyond the controller and approved capability profile. It cannot
alter target ownership, storage safety, schema migrations, provider retry
semantics, writer recovery, Git gates, or compatibility identifiers.

This Gate does not add a config file, loader, environment variable, CLI flag,
provider/model picker, adapter, catalog, model, migration, persisted field,
or runtime behavior. It does not decide the project configuration path or
permissions, invoke a live provider, or access the Jobs repository.

## Adversarial matrix

| ID | Scenario | Required outcome |
|---|---|---|
| G31-01 | No generic inputs are supplied to a Gate 4 compatibility run. | Resolution produces the complete `continuo.jobs-compat.v1` snapshot with current stable route IDs and existing policy; legacy CLI behavior is retained. |
| G31-02 | A user default selects a permitted implementation route and no override exists. | The user default wins over the project default and its selected route is persisted in the resolved record. |
| G31-03 | An explicit run override and a user default select different permitted routes. | The explicit override wins; both source hashes/absence facts remain auditable. |
| G31-04 | A user default or explicit override selects a route outside the project-permitted set. | Resolution fails before provider, Git, run-state, lock, or target mutation; it never falls back silently. |
| G31-05 | A project configuration omits a required role, has an empty permitted set, or has a default outside its permitted set. | Structural validation fails closed before a run is created. |
| G31-06 | A configuration contains an unknown role, duplicate key, unknown field, non-finite number, executable command, credential, prompt body, or provider-derived text. | Strict parsing/validation rejects the document without attempting a provider or command. |
| G31-07 | A display name or model label changes while the selected stable route ID remains valid. | Control selection and authorization remain unchanged; presentation data cannot be used as a lookup key. |
| G31-08 | A selected route ID resolves to zero or multiple compatible catalog records. | Resolution fails visibly before external work; no adapter/model fallback occurs. |
| G31-09 | The project tries to give a route workspace-write, Git, network, retry, or approval authority not allowed by its capability profile. | Capability validation rejects it; configuration cannot widen controller authority. |
| G31-10 | Canonically equivalent YAML and JSON inputs are supplied. | Their validated resolved payload and `configuration_sha256` are identical. |
| G31-11 | A source document changes after a run starts. | The existing run continues/resumes only with its persisted resolved configuration; source rereading cannot alter it. |
| G31-12 | The persisted configuration hash, schema version, role map, policy, or route reference is corrupt or unsupported. | Inspection reports the bounded reason and resume blocks without creating a replacement configuration or invoking a provider. |
| G31-13 | A formerly selected route is removed or no longer capability-compatible after a run starts. | Resume blocks visibly; it never replaces the saved route with a current default or same-provider/model alternative. |
| G31-14 | A pre-configuration historical run is loaded after Gate 4. | Existing migration/classification rules determine its treatment; it is never silently assigned a current configuration. |
| G31-15 | A deprecated Jobs command uses `JOBS_REPO` while a generic command uses its future target alias. | Both use the one documented resolver and produce the same resolved-configuration semantics; neither bypasses Git/storage/ownership gates. |
| G31-16 | A proposal adds a provider/model picker, catalog, project-path rule, task adapter, capability contract, or implementation to this Gate. | It is out of scope and requires the corresponding approved Gate 3 or Gate 4 contract. |

## Approval and implementation evidence

The owner approved this Gate 3.1 contract on 2026-08-03. Gate 3.1 is a
documentation-only contract-definition deliverable: implementation of parsing,
validation, persistence, CLI wiring, run-schema migration, and installed-package
validation belongs to the explicitly later Gate 4 configuration item. No runtime
source, test, fixture, run record, provider, target checkout, Git side effect,
commit, or push changed for this Gate.

Validation on clean synchronized `main` at
`0790c425aa554d3dccadd86f2c054f5ea7094ea4` confirmed that authoritative local
Markdown links resolve, all 16 `G31-*` matrix IDs are unique, and tracked and
new-file `git diff --check` whitespace checks pass. No live-provider or
Jobs-repository validation was performed.
