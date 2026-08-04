# Gate 4.2 contract — Validated and persisted resolved configuration

**Status:** repository-owner approved on 2026-08-03; runtime implementation and
verification complete on 2026-08-03; published at commit
`9fd0de0e8141d26e2f8f995fc3c63e11c08f024c`.

## Decision

Gate 4.2 will implement the approved schema-2 configuration and trusted-source
contracts, resolve one complete immutable configuration for every new run, and
persist it in run schema 13 before target ownership or provider work.

This is a compatibility-first implementation item. It seeds only the four
existing built-in routes, their `provider_default` effort records, the existing
capability/policy identifiers needed to validate their static shape, and the two
built-in externally managed CLI-session account profiles approved by
[Gate 4.1](gate-4.1-effort-provider-account-amendment.md). It adds no alternate
provider, model, effort, account, endpoint, command, permission, retry, or
fallback.

The configuration resolver is real and complete even though the selectable
catalog initially has one valid binding per compatibility role. Later Gate 4
catalog/routing work may register additional complete bindings without changing
the schema, precedence, canonicalization, persistence, or trust boundary defined
here.

No configuration mutation command is added in this item. Existing trusted files
are read when deliberately installed by the local operator; test fixtures inject
an isolated controller root directly. Gate 4.5 remains responsible for the
controller-owned install/replace commands, provider-account registry, Keychain,
and Rust TUI.

## Invariant and reproduced gap

The current controller constructs schema-12 runs with a correction policy but no
resolved configuration, trusted-source metadata, route/account binding, route
hash, or configuration hash. Provider selection remains the process-global
four-entry `ProviderRouteIdentity` catalog. A run can therefore neither prove
which future configuration inputs won nor remain pinned to the exact generic
configuration it started with.

Gate 4.2 protects these invariants:

1. malformed, unsafe, untrusted, incompatible, or changing configuration cannot
   create a run, claim a target, arm a provider, or mutate a repository;
2. every ordinary schema-13 run contains one hash-coherent schema-2 resolved
   configuration with all four required role bindings and correction policy;
3. the complete configuration is persisted before target ownership and provider
   work, and all later transitions use the saved value;
4. configuration precedence can narrow only to a complete project-permitted
   route/account pair;
5. source, presentation, task, provider, environment, or CLI text cannot create
   routes, accounts, effort, capability, executable, or credential authority;
   and
6. historical runs gain no generic facts by migration or current defaults.

## Bounded implementation scope

In scope:

- strict schema-2 models for user defaults, project configuration, typed run
  overrides, source metadata, route/account bindings, and resolved
  configuration;
- strict JSON and YAML decoding, duplicate/alias rejection, canonical JSON, and
  SHA-256 helpers;
- read-only descriptor-safe acquisition of fixed trusted-source paths beneath
  the private controller root;
- the exact three-layer resolution algorithm and built-in compatibility
  fallback;
- immutable seed records for the existing compatibility routes/accounts;
- schema-13 run persistence and an explicit `12 -> 13` migration step that
  preserves absence and execution-refuses historical records;
- new-run, resume, dry-run, doctor, status/report, and migration integration;
- deterministic failure-path, crash, race, migration, CLI, and installed-package
  tests; and
- documentation of the implemented file shapes and manual pre-Gate-4.5 source
  preparation.

Out of scope:

- provider-adapter extraction or new invocation/attempt schemas;
- custom provider-account profiles, API keys, Keychain, credential rotation,
  connectivity tests, or remote-principal verification;
- alternate providers, models, effort levels, routes, capabilities, project
  profiles, repository/task adapters, or automatic discovery;
- public configuration mutation commands, a picker, Rust, TUI, browser, daemon,
  or asynchronous approval;
- generic package consolidation, new executable/environment aliases, Jobs
  access, live provider work, or Git publication; and
- automatic route/configuration migration for an existing run.

## Configuration module and dependency boundary

Configuration parsing, canonicalization, catalog seed records, source loading,
resolution, and validation live behind one importable module boundary rather
than being distributed through CLI callbacks and workflow branches. Persistent
models may remain in `models.py` until the later generic-package consolidation,
but there is one canonical model identity and no duplicate schema in tests or
CLI code.

YAML support uses one pinned direct dependency with a controller-owned loader.
The loader must reject duplicate keys and all alias/anchor tokens before model
validation. JSON decoding uses ordered-pair duplicate detection. Neither parser
constructs arbitrary Python objects or accepts custom tags.

No configuration module import reads the filesystem, environment, target,
provider CLI, or clock. Catalog construction and canonical hashes are
deterministic pure initialization. Production source acquisition occurs only
through an explicit resolver call after target preflight. Tests inject a
controller root as a typed `Path`; production has no environment variable or
CLI option for selecting a configuration root or source file.

## Closed configuration schemas

All models are strict, extra-forbid, and immutable after validation. Stable IDs
are lower-case bounded control strings; role keys are exactly
`implementation`, `adversarial_review`, `escalation_executive`, and
`policy_authority`. `null`, unknown roles, unknown fields, empty IDs, duplicate
bindings, and type coercion fail closed.

Each source is bounded to 262,144 bytes before decoding and to 32 nested mapping
or sequence levels during structural inspection. A project role permits at most
32 complete bindings, all unique. Strings and collections have explicit finite
bounds. Decoded non-finite numbers are rejected even in unused or unknown
fields.

### Complete selection

Every input selection is atomic:

```yaml
route_id: builtin.implementation.v1
provider_account_profile_id: builtin.codex-cli.local-session.v1
```

Route-only or account-only values are invalid. Selection never contains a
provider adapter, model, effort, display name, command, capability, endpoint,
credential, retry, prompt, or permission.

### User defaults

The optional source has this logical shape:

```yaml
user_defaults_schema_version: 2
role_bindings:
  implementation:
    route_id: builtin.implementation.v1
    provider_account_profile_id: builtin.codex-cli.local-session.v1
```

Roles may be omitted. Present roles require one complete selection. User
defaults have no correction-policy authority in schema 2.

### Trusted project configuration

The project source has this logical shape:

```yaml
project_configuration_schema_version: 2
project_configuration_id: project-config-v2:<target-key>
target_binding:
  target_key: <64 lowercase hexadecimal characters>
  canonical_repo: <exact absolute resolved Git root>
profile_id: continuo.jobs-compat.v1
role_bindings:
  implementation:
    permitted_bindings:
      - route_id: builtin.implementation.v1
        provider_account_profile_id: builtin.codex-cli.local-session.v1
    default_binding:
      route_id: builtin.implementation.v1
      provider_account_profile_id: builtin.codex-cli.local-session.v1
  adversarial_review: <same strict role policy shape>
  escalation_executive: <same strict role policy shape>
  policy_authority: <same strict role policy shape>
policy:
  correction_policy_id: builtin.correction_escalation.v1
```

All four roles are required. Each default appears exactly in its role's
non-empty permitted set. Schema 2 recognizes only the existing compatibility
profile and correction policy. The target key, canonical path, and
`project-config-v2:<target-key>` identity must exactly match a fresh stable
target inspection; no normalization, remote, basename, branch, or display
heuristic repairs a mismatch.

### Typed explicit run overrides

The strict logical shape is:

```yaml
run_overrides_schema_version: 2
role_bindings:
  adversarial_review:
    route_id: builtin.adversarial_review.v1
    provider_account_profile_id: builtin.claude-cli.local-session.v1
```

Gate 4.2 exposes this only as a typed controller/resolver API for deterministic
tests and the later command boundary. It adds no public path, raw JSON option,
environment variable, or repeated free-form CLI flag. Existing `run` invocations
supply an absent override. Gate 4.5 will expose versioned non-interactive and TUI
commands without changing this model.

## Seed route and account records

The implementation registers exactly these complete role/account selections:

| Role | Route | Account profile | Effort |
|---|---|---|---|
| `implementation` | `builtin.implementation.v1` | `builtin.codex-cli.local-session.v1` | `provider_default` |
| `adversarial_review` | `builtin.adversarial_review.v1` | `builtin.claude-cli.local-session.v1` | `provider_default` |
| `escalation_executive` | `builtin.escalation_executive.v1` | `builtin.codex-cli.local-session.v1` | `provider_default` |
| `policy_authority` | `builtin.policy_authority.v1` | `builtin.codex-cli.local-session.v1` | `provider_default` |

Each route uses `provider_route_profile_schema_version: 2` and carries the
existing stable role, adapter, model, operation, builder, output, capability,
preamble, supervision, retry, and content-retry IDs plus its Gate 4.1 effort
record. Each account uses the approved schema-1 immutable payload with
`controller_profile` assurance and remote identity unknown.

Catalog records are code-owned immutable data. Their control payloads are
canonically hashed and validated for unique IDs, exact role/adapter agreement,
complete operation sets, known policy IDs, and route/account adapter equality.
Presentation labels are outside authority hashes and resolved configuration.
Changing a label cannot change a route/account selection or configuration hash.

This static validation is not the full adapter-enforcement or permission-ceiling
proof scheduled by later Gate 4 items. Gate 4.2 makes no new claim that the
current command builders independently enforce every future profile field.

## Trusted-source locations and acquisition

The production controller root remains:

```text
~/.config/continuo
```

Gate 4.2 recognizes only these fixed source paths:

```text
~/.config/continuo/user-defaults.yaml
~/.config/continuo/projects/<target-key>/project-configuration.yaml
```

The root cannot be selected by an environment variable, project file, target
content, provider, CLI option, or current working directory. Tests may inject an
absolute temporary root through the internal typed API.

Source acquisition is read-only and separate from the existing run-storage
hardening helpers. It never creates a directory/file, chmods, repairs, renames,
deletes, migrates, locks, or writes a temporary artifact. Every existing
directory must be owned by the effective UID, be an actual directory rather than
a link, and have exact mode `0700`. Every source must be an owner-owned,
single-linked regular file with exact mode `0600`.

Traversal uses descriptor-relative fixed components with `O_NOFOLLOW` where
available, rejects `..`, links, special files, foreign ownership, multiple hard
links, device/inode changes, and path replacement. The loader bounds bytes before
decoding, requires strict UTF-8, and compares descriptor metadata before and
after reading. New-run resolution revalidates every selected physical source
immediately before the initial run persistence; a changed/replaced source aborts
without a run or ownership claim.

### Built-in compatibility source

When no exact per-target directory or project source exists, the legacy and
current CLI synthesize one target-bound `continuo.jobs-compat.v1` project
configuration from the immutable seed catalog. Optional valid user defaults may
overlay that built-in project policy through the ordinary precedence rule. Its
canonical source identity and hash are persisted with `source_kind:
builtin_compatibility`; the target key and canonical repository remain exact.

If the controller root or per-target path exists but is unsafe, resolution
fails—it never hides unsafe state behind the fallback. An absent optional user
defaults file is valid. An existing exact per-target directory without its
project file is an incomplete installation and fails. Other safe per-target
directories have no selection effect.

A run created from the built-in source requires that exact source condition at
resume. Installing a physical project source for the same target later is a
trusted-source change and blocks that run rather than silently adopting or
ignoring the newly installed project policy.

This fallback preserves Gate 3.1's no-generic-input compatibility requirement
until Gate 4.5 can install an explicit source. It cannot select a non-Jobs
profile, a different route/account pair, or another target.

## Resolution and canonical hashes

After safe target identity/state inspection, resolution proceeds exactly:

1. acquire and validate the optional user defaults;
2. acquire the exact trusted project source or synthesize the built-in
   compatibility source under the rules above;
3. validate all project-permitted/default bindings against the seed catalogs;
4. validate the optional typed run overrides;
5. for each required role choose the first complete present binding in order:
   run override, user default, project default;
6. reject rather than fall back when the chosen higher-precedence binding is
   unpermitted, missing, duplicated, adapter-incompatible, or catalog-invalid;
7. resolve the exact built-in correction policy;
8. assemble complete route/account control payloads and source metadata;
9. canonicalize, hash, strictly revalidate, and freeze one
   `ResolvedConfiguration`; and
10. revalidate selected physical source snapshots before initial persistence.

Canonical JSON is UTF-8 with object keys sorted lexicographically, no
insignificant whitespace, JSON scalar spellings, `ensure_ascii=false`, no NaN or
infinity, and one terminal newline. Hashes are lowercase SHA-256 over those exact
bytes. Source hashes cover their complete validated logical payload. The
`configuration_sha256` covers the complete resolved authority payload excluding
only itself and presentation fields, which are not present in the resolved
model.

Semantically equivalent JSON/YAML and mapping order produce identical hashes.
Different Unicode code points are not silently normalized. Duplicate keys,
quoted type changes, YAML implicit booleans in string fields, and unknown fields
fail validation rather than canonicalizing into accepted values.

## Resolved configuration and source metadata

The persisted schema-2 record follows Gate 4.1's `role_bindings` shape and
contains the complete immutable route/account control payload and hashes for all
four roles, the complete resolved correction policy, source metadata, and
`configuration_sha256`.

Source metadata is a closed tagged record:

- physical project source: project configuration ID, target key, canonical
  repository, schema version, canonical payload hash, and
  `source_kind: private_file`;
- built-in project source: deterministic compatibility source ID, target key,
  canonical repository, schema version, canonical payload hash, and
  `source_kind: builtin_compatibility`;
- user defaults: schema version and canonical payload hash, or exact `absent`;
- run overrides: schema version and canonical payload hash, or exact `absent`;
  and
- selected built-in account profiles: stable IDs and canonical profile hashes
  in role order.

No metadata contains source text, filesystem path to controller storage,
display label, environment value, task/provider text, credential state, remote
identity claim, or secret.

Project source and account/route catalog identity are resume-invalidating
authority sources. User defaults and run overrides are frozen audit provenance:
they are not reread on resume and later changes cannot alter or invalidate the
saved run. This preserves saved precedence while avoiding global-default changes
becoming a denial of service against existing runs.

## Run schema 13 and persistence ordering

`CURRENT_RUN_SCHEMA_VERSION` becomes `13`. Ordinary `WorkflowRun` records gain a
required immutable `resolved_configuration` and require its schema, complete role
set, route/account hashes, correction policy, source metadata, and configuration
hash to be coherent with the run's separate `resolved_correction_policy`.

New-run ordering is:

1. inspect exact target identity and read-only repository state;
2. acquire and resolve configuration;
3. resolve the task and create a schema-13 run in memory with the complete
   resolved configuration;
4. revalidate physical configuration sources;
5. persist the complete run before or atomically with the existing target
   ownership claim; and
6. only then enter workflow/provider transitions.

Configuration failure creates no run ID/file, ownership database/directory,
lock, provider record, or repository mutation. A dirty-repository blocked run
may retain the current observable behavior of creating a run record, but that
record must already contain a valid resolved configuration and must invoke no
provider.

Persistence uses the existing private atomic run-file mechanism. A crash before
the complete schema-13 record is replaced leaves no resumable partial run. A
crash after run persistence but before/within ownership follows existing target
coordination recovery using the saved configuration; it never resolves again.

## Migration and historical treatment

Add one adjacent `12 -> 13` migration. It preserves the entire source payload,
adds `resolved_configuration: null`, and adds one immutable
`ConfigurationMigrationAudit` with:

- the existing migration ID/time/source structural class/source SHA lineage;
- target schema 13 and final applied step `12_to_13`;
- reason `missing_resolved_configuration`; and
- the inherited execution-refused disposition.

All migrations from schemas 1–11 extend through the same final step and preserve
their existing audit lineage. Schema-12 becomes a recognized historical
structural class. No migration invents a target key, project source, profile,
route/account payload or hash, effort, account, correction policy, source
metadata, or configuration hash from current defaults, target paths, commands,
environment values, display labels, provider records, or task text.

A migrated schema-13 record remains inspectable and exportable but
`_require_executable` rejects every state-changing/resume path because its
configuration migration audit proves missing authority. A current schema-12
record is no longer directly executable once schema 13 ships; explicit migration
does not make it executable. Rollback/crash tests preserve the original bytes
unless the full atomic migration succeeds.

No migration path assigns the built-in compatibility source to a historical
run. A later separately approved route/configuration migration would be required
to establish execution eligibility.

## Runtime, resume, dry-run, and doctor integration

### Normal run and provider compatibility

The existing provider command builders and provider functions remain unchanged.
Before a controller provider stage, the saved role binding must match the exact
seed route/account pair for that role and the static compatibility catalog must
remain hash-coherent. Any mismatch blocks before the provider helper is called.
Because this item registers no alternative, current provider/model behavior is
preserved while configuration ceases to be absent or implicit.

Provider-adapter extraction will later make builders consume the full saved
route/account request. Gate 4.2 does not claim that current `ProviderRecord`
already implements Gate 4.1's future schema-2 invocation/attempt evidence.

### Resume

Before target coordination or any transition, resume validates the saved
configuration and re-acquires only its project source condition:

- `private_file` requires the exact safe, binding-valid canonical project
  payload and hash;
- `builtin_compatibility` requires the physical source still be absent and the
  exact built-in catalog/source payload still installed; and
- all saved route/account catalog payloads and hashes must remain exact.

Failure blocks as `configuration_source_changed` or a more specific bounded
configuration reason. It does not create/repair configuration, acquire/release
ownership, invoke a provider, alter the target, or substitute defaults. User
defaults and overrides are not reread.

### Dry-run

Dry-run executes the same target/configuration/task validation and resolution
but never performs source revalidation through a mutating helper and never
persists. Its nested plan contract advances to `continuo.run-plan.v2` and adds a
redacted `configuration` object containing profile ID, role-to-route/account IDs,
effort modes, source kinds/hashes, correction policy ID, and configuration hash.
It contains no source content or private controller path.

Two dry-runs over unchanged inputs are byte-identical. Missing or unsafe config
storage remains non-mutating. Provider fakes receive zero calls.

### Doctor

Doctor's nested result advances to `continuo.doctor.v2` and adds one ordered
`configuration` check covering source topology, target binding, schema,
catalog/binding validity, and canonical hash coherence. The existing
`route_capabilities` check remains static and explicitly does not claim the later
adapter/capability enforcement proof.

Doctor performs no source creation, repair, chmod, migration, run persistence,
provider/network call, target lock, or credential inspection beyond existing
safe local status. The built-in compatibility source is a bounded visible status,
not a claim that explicit project setup is complete for Gate 5.

The top-level `continuo.cli.v1` envelope remains unchanged. Versioned nested
doctor/plan objects distinguish the added fields. Existing human output gains
only bounded configuration IDs/hashes and failure reasons, never raw source.

### Status and report

Status, report, and their historical-record paths recognize schema 13,
configuration migration audit disposition, and the new current/non-current
classification without reading a live configuration source to reinterpret a
record. Existing `continuo.cli.v1` public run-summary fields remain unchanged in
this item; raw resolved configuration is not added to that machine envelope.
Human inspection of one current run may show the already-private persisted
configuration, while concise report text may add only profile ID and bounded
hash/route/account IDs. A later generic CLI contract may expose a separately
versioned configuration read representation.

## Error, audit, and redaction behavior

Pre-run failures use stable bounded codes including:

- `configuration_missing`;
- `configuration_storage_unsafe`;
- `configuration_source_changed`;
- `configuration_too_large`;
- `configuration_invalid_utf8`;
- `configuration_invalid_syntax`;
- `configuration_schema_unsupported`;
- `configuration_binding_mismatch`;
- `configuration_route_unavailable`;
- `configuration_account_unavailable`;
- `configuration_binding_unpermitted`;
- `configuration_catalog_incoherent`; and
- `configuration_hash_incoherent`.

Errors report only stable source kind, role/field path where safe, bounded code,
and hash prefixes when comparison is useful. They never print source bytes,
complete private paths, target-origin credentials, prompts, task text, provider
output, arbitrary YAML values, or tracebacks in expected CLI failures.

Configuration validation occurs before a provider attempt exists, so no
configuration failure is recorded as provider `auth`, `unavailable`,
`provider_error`, or a fabricated physical attempt.

## Required deterministic tests

Tests use temporary controller roots and temporary local Git repositories only.
They patch provider functions to fail if called on any configuration failure.
No test reads the operator's real `~/.config/continuo`, run storage, credentials,
or Jobs checkout.

Required groups:

- strict schema and parser unit tests, including JSON/YAML equivalence;
- catalog/hash and route/account compatibility tests;
- precedence/permitted-pair resolution tests;
- private source topology, descriptor race, replacement, size, and UTF-8 tests;
- new-run persistence ordering and crash-boundary tests;
- schema-13 model, classification, migration lineage, rollback, and
  execution-refusal tests;
- resume source/catalog invalidation tests;
- doctor/dry-run determinism and non-mutation snapshots;
- legacy text/JSON CLI regression tests;
- full deterministic unit suite and `git diff --check`; and
- installed `jobs-orchestrator` validation with `UV_NO_EDITABLE=1`.

No live provider, network, real credential, non-temporary target, Jobs checkout,
commit, or push is part of implementation validation.

## Adversarial matrix

| ID | Scenario | Required outcome |
|---|---|---|
| G42-01 | Valid JSON and YAML express the same schema-2 source with different key order/comments. | Validated canonical payloads and hashes are identical. |
| G42-02 | JSON/YAML contains duplicate keys, YAML anchors/aliases/custom tags, unknown fields, `null`, or coerced scalar types. | Strict decode/validation fails before run creation or external work. |
| G42-03 | Source exceeds 262,144 bytes or 32 nesting levels, is invalid UTF-8, non-finite, structurally invalid, or has oversized collections/strings. | Bounded configuration failure; no unbounded diagnostic or side effect. |
| G42-04 | User/project/override supplies provider, model, effort, endpoint, command, capability, permission, credential, task, or prompt fields. | Extra-forbid validation rejects the source; no authority is inferred. |
| G42-05 | Project omits a role, has empty/duplicate permitted bindings, or default is not exactly permitted. | Whole resolution fails before a run or ownership claim. |
| G42-06 | User default or override supplies only a route or only an account, an unknown role, or an unknown complete pair. | Strict validation/resolution fails; lower precedence is not used. |
| G42-07 | Override, user default, and project default differ but are all permitted. | Exact override > user > project precedence wins and complete provenance is hashed. |
| G42-08 | Higher-precedence binding is unpermitted while project default is valid. | Resolution fails rather than treating invalid input as absent. |
| G42-09 | Route and account are separately known but their pair is not permitted or adapters differ. | Binding fails; no cross-product or compatible-account search occurs. |
| G42-10 | Route/account/catalog ID is missing, duplicated, schema-unsupported, or hash-incoherent. | Catalog validation blocks all run creation; no display/model fallback. |
| G42-11 | Display label changes while authority records remain identical. | Resolved configuration and hash are unchanged; display is not a control input. |
| G42-12 | `High` display text is offered as effort evidence. | Compatibility route remains `provider_default`; no explicit effort is invented. |
| G42-13 | Controller root or source path is a link, wrong type, foreign-owned, permissive, hard-linked, or replaced during read. | Source is rejected without following, reading unsafe bytes, chmod, repair, or fallback. |
| G42-14 | Source changes after first read but before initial run persistence. | Revalidation fails; no run/ownership/provider/target side effect occurs. |
| G42-15 | Root and exact project path are absent with no defaults/overrides. | Deterministic target-bound built-in compatibility configuration resolves and persists. |
| G42-16 | Unsafe controller root exists while built-in fallback would otherwise apply. | Resolution fails; unsafe state is not ignored. |
| G42-17 | Exact target directory exists without its project source. | Incomplete installation fails as `configuration_missing`; no fallback. |
| G42-18 | Another checkout has a safe project source with same basename/remote/branch. | It has no effect; exact device/inode target key and canonical path are required. |
| G42-19 | Project target key/path/configuration ID/profile mismatch. | Binding fails without normalization, rebind, or remote/display heuristic. |
| G42-20 | Task/repository contains `.continuo` config, source path, or provider-proposed configuration. | Ignored as untrusted; fixed private sources alone have authority. |
| G42-21 | Configuration error occurs for `run`. | No run ID/file, target lock/database, provider call, Git action, or target mutation occurs. |
| G42-22 | Dirty target creates the existing blocked run. | Saved schema-13 run already contains a valid complete configuration; provider calls remain zero. |
| G42-23 | Crash occurs before initial run replace, after run persist, or during ownership claim. | No partial resumable config; existing atomic run/ownership recovery applies without re-resolution. |
| G42-24 | Ordinary schema-13 run lacks config, has a partial role set, policy mismatch, or bad nested/config hash. | Model/classification rejects it as non-current/incoherent. |
| G42-25 | Schema-12 run is loaded after upgrade. | Classified migration-required/inspection-only; it is not assigned current defaults or executed. |
| G42-26 | Explicit `12 -> 13` migration succeeds. | Exact prior facts persist, configuration remains null with immutable missing-config audit, and execution is refused. |
| G42-27 | Schemas 1–11 migrate through 13. | Existing lineage remains coherent and final `12_to_13` absence evidence is exact. |
| G42-28 | Migration tries to infer config from path, target, alias, route label, provider record, task, or current catalog. | No fact is inferred; absence and execution refusal remain visible. |
| G42-29 | Migration crashes or source bytes change during confirmation. | Original bytes remain or one complete schema-13 record replaces them; no partial file. |
| G42-30 | Saved private project source is reformatted but canonical semantics are identical. | Resume validation succeeds and uses the persisted configuration. |
| G42-31 | Saved private project source changes semantically, disappears, becomes unsafe, or is replaced. | Resume blocks before ownership/provider/target mutation; saved config remains inspectable. |
| G42-32 | Built-in-source run later sees a physical project source. | Resume blocks as source changed; it neither adopts nor ignores the installed policy. |
| G42-33 | User defaults change/delete/become unsafe after run creation. | Resume does not reread them; persisted binding remains authoritative. |
| G42-34 | Saved route/account catalog payload changes or disappears. | Resume/provider stage blocks; no same-model/account/default substitution. |
| G42-35 | Provider stage is reached with a saved binding not equal to the sole compatibility pair. | Provider helper call count stays zero and run blocks with bounded configuration evidence. |
| G42-36 | Dry-run resolves valid configuration twice over unchanged inputs. | Byte-identical `continuo.run-plan.v2`; no run, lock, chmod, config write, provider, or target mutation. |
| G42-37 | Dry-run encounters missing/unsafe/invalid configuration. | Read-only bounded failure; pre/post path modes, inodes, mtimes, and contents are unchanged. |
| G42-38 | Doctor inspects built-in, valid private, missing, or unsafe configuration. | Ordered `continuo.doctor.v2` configuration evidence; no repair, provider/network, run, or lock. |
| G42-39 | Human and JSON CLI receive configuration failure containing secret-looking/source text. | Stable redacted error only; one parseable CLI envelope in JSON mode and no value disclosure. |
| G42-40 | Production tries to select config root/source through environment, option, CWD, repository, or provider input. | Input has no authority or is rejected; fixed controller paths remain exact. |
| G42-41 | Existing provider commands, retries, recovery, approvals, Git gates, and compatibility CLI tests run. | Behavior remains unchanged except required schema/config evidence; all deterministic regressions pass. |
| G42-42 | Proposed diff adds alternate routes/accounts, adapters, credential storage, config mutation CLI, Rust/TUI, generic package rename, live provider, or Jobs access. | Out of scope; requires its later approved Gate 4/4.5 item. |

## Approval and implementation evidence

This contract was prepared on 2026-08-03 from the owner-approved Gate 3.1, 3.2,
3.5, 3.6, 3.8, and Gate 4.1 contracts plus the current schema-12 models,
migration registry, private run storage, target identity/coordination, new-run
ordering, resume guard, doctor/dry-run contracts, CLI envelope, packaging, and
deterministic test suite at clean synchronized `main`
`c69487016ecdb43991534dceafde91e80f1711be`.

The repository owner approved the decisions, implementation boundary, migration
treatment, built-in fallback, and 42-case adversarial matrix on 2026-08-03.
The bounded implementation adds the strict configuration models and resolver,
immutable compatibility route/account catalogs, `provider_default` effort
evidence, canonical hashes, descriptor-relative private-source reads,
schema-13 persistence and migration, lifecycle/provider guards, version-2
doctor and dry-run evidence, a pinned PyYAML dependency, manual source guidance,
and deterministic Gate 4.2 tests.

Verification completed on 2026-08-03:

- `uv run python -m unittest -q` passed all 218 deterministic tests using only
  fakes, fixtures, isolated controller roots, and temporary Git repositories;
- focused Gate 4.2 configuration tests, schema migration tests, legacy CLI
  contract tests, and controller compatibility tests passed;
- bytecode compilation and `git diff --check` passed;
- root CLI help plus `UV_NO_EDITABLE=1` installed `jobs-orchestrator` help and
  imports passed with current schema 13 and four compatibility routes; and
- all 56 local Markdown links in the changed documentation resolve, with 42
  unique contiguous `G42-*` matrix IDs.

No live provider, network credential, external target, Jobs checkout, or
configuration in the operator's real controller root was used during
implementation or verification.

Publication evidence (2026-08-03): the repository owner approved publication,
and the implementation was committed and pushed at
`9fd0de0e8141d26e2f8f995fc3c63e11c08f024c`.
