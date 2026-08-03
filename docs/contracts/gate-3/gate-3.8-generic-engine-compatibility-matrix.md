# Gate 3.8 contract — Generic-engine compatibility matrix

**Status:** owner-approved and published; documentation-only Gate 3 deliverable; implementation is Gate 4 work.

## Decision

Gate 4 will introduce one generic public surface while retaining the working
Jobs-specific surface as a compatibility profile, not as a second controller.
The canonical names are `orchestration-engine` (distribution and executable),
`orchestration_engine` (Python package), `ORCHESTRATION_TARGET_REPO`
(target-selection environment variable), and
`continuo.jobs-compat.v1` (the initial compatibility-profile ID). The legacy
`jobs-orchestrator`, `jobs_orchestrator`, `JOBS_REPO`, root
`orchestrator.py`, and current persisted record forms remain available only as
specified below.

Every canonical and legacy entry point must normalize into the same typed
request, target resolver, resolved configuration, controller, repository/task
adapters, storage rules, and Git/approval gates. The spelling that invoked a run
is presentation/audit provenance, never workflow authority. No alias may create
a second precedence system, choose a route/model, bypass trusted configuration,
reclassify persisted records, relax capability ceilings, change retries, or
supply a fallback.

This Gate is the authoritative migration map for the Milestone 2 package and
identifier transition. It defines no runtime rename, shim, migration, command,
environment lookup, package publication, or deprecation warning. Gate 4
implements the map atomically with tests and installed-package validation.

## Canonical names, legacy aliases, and support window

The first generic-engine release is the release that first exposes
`orchestration-engine` and `orchestration_engine`. Legacy aliases must remain
fully supported for at least two subsequent minor releases and 180 calendar days
after that release, whichever is later (the **compatibility window**). Before
removal, a later approved compatibility contract must name the first unsupported
version, publish a migration/readiness path, retain persisted-record readers,
and prove that no in-window invocation or stored state is silently reinterpreted.
A warning, an expired date, or a package reinstall never removes compatibility
by itself.

| Surface | Canonical identifier | Legacy identifier | Gate 4 treatment |
|---|---|---|---|
| Distribution | `orchestration-engine` | `jobs-orchestrator` | Canonical distribution owns the engine; legacy distribution is a thin dependency/shim package during the compatibility window. |
| Installed executable | `orchestration-engine` | `jobs-orchestrator` | Both call the same canonical CLI application in-process and accept the same supported command contract. |
| Python package | `orchestration_engine` | `jobs_orchestrator` | Legacy package re-exports only the documented compatibility entry point `main`; it has no independent controller. |
| Source CLI module | `orchestration_engine.cli` | root `orchestrator.py` | The root module is a thin direct-run compatibility shim to canonical CLI behavior. |
| Source implementation modules | `orchestration_engine.{models,providers,migrations}` | root `models.py`, `providers.py`, `run_migrations.py` | Root modules become same-version forwarding shims for checkout/test compatibility; they cannot contain divergent policy. |
| Target environment | `ORCHESTRATION_TARGET_REPO` | `JOBS_REPO` | Both feed one target resolver under the exact precedence/error rules below. |
| Target option | `--target-repo` | `--repo` | Both feed one typed target option on every command that currently accepts `--repo`. |
| Configuration profile | project-selected generic profile | `continuo.jobs-compat.v1` | The legacy profile preserves current Jobs task/repository behavior; it is resolved configuration data, not a command or environment alias. |
| Persisted run | future versioned generic run schema | schemas 1–12 and their migration classifications | Readers classify before interpreting; legacy records never acquire generic facts by alias or default. |
| Machine output | existing versioned `continuo.*.v1` contracts, then separately versioned successors | same current versions | Invocation spelling does not change schema/version or field meanings. |

`ORCHESTRATOR_REPO` is deliberately unsupported. It is neither an alias nor a
lower-precedence fallback. If it is present for a command that resolves a target,
the command must fail with the bounded reason
`unsupported_environment_identifier` before configuration, run creation,
provider work, target coordination, Git, or mutation. This prevents an ambiguous
name from becoming latent authority.

## CLI and target-resolution contract

The canonical and legacy executables expose the same command names during the
window: `run`, `resume`, `recover-writer`, `release-target`,
`approve-policy`, `report`, `migrate-run`, `status`, and `doctor`.
The current root direct form
`python orchestrator.py <command>` and future direct canonical form
`python -m orchestration_engine.cli <command>` are likewise equivalent.
Commands, positional arguments, exit status, read-only behavior, human gates,
versioned JSON contracts, redaction, and error semantics must be identical after
normalization. No compatibility executable may shell out to another executable,
parse human output, or rewrite a command line; both import and invoke the
canonical application with typed arguments.

For commands with a target option, the resolver follows this exact order:

1. an explicit `--target-repo` or `--repo` value;
2. `ORCHESTRATION_TARGET_REPO`;
3. `JOBS_REPO`; then
4. the existing Jobs compatibility default
   `~/Documents/my-apps/jobs`, and only while `continuo.jobs-compat.v1` is
   the resolved profile.

If both option spellings occur, their normalized absolute paths must be equal or
resolution fails. If both supported environment variables occur, their
normalized absolute paths must be equal or resolution fails as
`conflicting_target_environment`. An explicit option may differ from either
environment value because it is higher precedence. Empty, NUL-containing,
nonexistent, non-directory, unsafe, or non-root target paths retain the existing
preflight failure behavior. Environment values are target-location input only:
they cannot select a project configuration source, compatibility profile,
provider, route, capability, policy, task adapter, repository adapter, storage
directory, run ID, retry, approval, Git, or publication authority.

The `--target-repo` and `--repo` aliases must have the same type, help
semantics, default behavior, completion behavior, and validation. A generic
profile may require an explicit target or define its own trusted repository
adapter default under a later approved contract; it must never cause
`JOBS_REPO` or the Jobs path to leak into another project.

## Python import and package contract

The canonical supported programmatic entry point is
`orchestration_engine.cli:main`. The canonical model/provider/migration modules
are internal package implementation modules unless a later public API contract
names particular symbols. A caller must not infer stable behavior from a root
module, private attribute, class name, or package layout.

During the compatibility window:

- `jobs_orchestrator:main` invokes exactly `orchestration_engine.cli:main`;
- root `orchestrator.py` invokes/re-exports the same CLI application so
  existing direct-run use remains valid;
- root `models.py`, `providers.py`, and `run_migrations.py` forward to
  same-version canonical modules for checkout-local existing test/import
  compatibility; and
- the legacy package and root shims perform no direct-URL checkout discovery,
  working-directory probing, dynamic source-file loading, import-path mutation,
  alternate configuration lookup, or fallback implementation selection.

Installed packages must be self-contained: an installed legacy executable may
depend on the canonical distribution, but it may not search the current
directory, a direct-URL checkout, or a source tree to find the engine. This
replaces the current `jobs_orchestrator` direct-URL fallback only when Gate 4
has a tested canonical installation path. A missing canonical dependency fails
with a bounded installation error; it never imports arbitrary similarly named
modules.

Legacy imports must preserve their documented entry point only; they must not
create a promise that undocumented module globals or internal classes remain
public forever. In all cases, one process loads one canonical controller/model
class identity. Mixing canonical and legacy imports cannot create duplicate
registries, Pydantic model types, Typer applications, storage locks, or
migration registries.

## Configuration and persisted-state compatibility

Gate 3.1–3.7 records remain the authority for generic configuration, trusted
project configuration, task envelopes, repository adapters, provider routes,
capability profiles, verification, and correction policy. The legacy CLI and
environment aliases must use the same Gate 3.1 resolution order and Gate 3.2
trusted source. They never obtain a Jobs-only configuration file or environment
precedence path.

A no-generic-input Gate 4 compatibility run resolves exactly
`continuo.jobs-compat.v1`; its persisted resolved configuration records that
profile and its hash before external work. Its initial route, task, repository,
capability, verification, correction, target-ownership, storage, retry, and
Git behavior retain the approved contracts. A generic profile must be selected
only through trusted configuration, never through executable/package/import/env
spelling or a model/provider display name.

Current schemas 1–12 remain readable only through existing strict
classification and explicit adjacent migration. Gate 4 may introduce a new
schema only with an approved migration contract. The migration must retain every
known legacy fact and audit lineage, add only proven generic fields, and keep
migrated historical records execution-refused unless all later execution
requirements are independently proven. It must not infer a target key, trusted
configuration hash, profile, route hash, capability profile, verification
result, generic package provenance, or migration eligibility from
`jobs-orchestrator`, `JOBS_REPO`, a current default, a path basename, a
display label, raw command text, or an import name.

Status, report, doctor, dry-run, and machine JSON must show stable profile/schema
identifiers and bounded compatibility classification where relevant. They may
display a deprecated invocation warning, but warning text is presentation only
and must not alter a persisted run or its next transition. A saved run resumes
under its saved schema/configuration/profile and never under the currently
invoked executable, package, alias, environment, or default.

## Deprecation and removal control

On every successful legacy CLI invocation, the canonical controller emits one
bounded deprecation warning to stderr (never stdout/JSON) naming the canonical
replacement. The warning is suppressed only when `--json` is active, in which
case the existing versioned JSON warning/diagnostic field carries the stable
code `deprecated_identifier`. It must contain no target path, configuration
content, prompt, credential, provider output, or private run data. A legacy
Python import may issue a standard once-per-process `DeprecationWarning`, but
must not print, mutate storage, or contact a target.

Removal requires all of the following in a future approved gate:

1. expiry of the compatibility window;
2. a release note naming exact removal versions and replacements;
3. an installed canonical package and migration/readiness command that work
   without a source checkout;
4. deterministic tests for old/new command, option, environment, package, and
   persisted-record paths; and
5. a compatibility audit confirming that protected historical readers and
   `migrate-run` treatment remain available.

No auto-rewrite of shell scripts, environment files, project configuration,
package metadata, persisted records, or user imports occurs. The controller
never deletes `JOBS_REPO`, edits a target, or upgrades a run simply because a
legacy identifier was observed.

## Non-goals

This Gate adds no package rename, source move, shim, script, environment
variable, target option, warning, configuration loader, adapter, catalog,
provider/model route, verifier, run schema, migration, CLI/JSON implementation,
release, package publication, live provider call, target access, Git action,
commit, or push. It does not access Jobs or alter provider commands, authority,
retries, ownership, storage, Git gates, current compatibility identifiers, or
historical record treatment.

## Adversarial matrix

| ID | Scenario | Required outcome |
|---|---|---|
| G38-01 | Canonical and legacy executable invoke the same supported command with equivalent typed inputs. | One canonical application/controller behavior, exit status, JSON contract, redaction, and gate behavior result. |
| G38-02 | Legacy executable/package is installed without a source checkout or current directory containing Continuo. | It reaches its declared canonical dependency or fails with bounded installation error; no direct-URL/path probing or dynamic loading occurs. |
| G38-03 | Canonical and legacy package/module imports are mixed in one process. | One controller/model/registry identity is used; no duplicate application, lock, migration registry, or type split occurs. |
| G38-04 | A caller relies on an undocumented root-module global or private canonical symbol. | No compatibility promise is made; only documented entry points are protected. |
| G38-05 | Both `--target-repo` and `--repo` name equal normalized paths. | Resolution succeeds with one typed explicit target. |
| G38-06 | Both target options name different paths. | Resolution fails before configuration, lock, provider, Git, or target mutation. |
| G38-07 | Both supported target environment variables normalize to one path. | Resolution succeeds with one target and emits legacy warning if `JOBS_REPO` was consumed. |
| G38-08 | Supported target environment variables conflict. | Resolution fails as `conflicting_target_environment`; no precedence guess occurs. |
| G38-09 | Explicit target differs from environment values. | Explicit option wins; no warning changes target or authority. |
| G38-10 | `ORCHESTRATOR_REPO` is present, alone or with supported variables. | Target-resolving command fails as `unsupported_environment_identifier` before external work. |
| G38-11 | `JOBS_REPO` supplies a target for a generic invocation. | It selects location only and passes the same trusted configuration/profile validation; it cannot make a run Jobs-compatible by itself. |
| G38-12 | No option/environment is present for the Jobs compatibility profile. | Existing Jobs default is selected; a non-Jobs profile never inherits it. |
| G38-13 | Legacy command/env spelling attempts to select route, model, capability, policy, config source, storage, retry, approval, Git, or publish behavior. | Input is rejected/ignored as authority; one approved resolver/controller path remains. |
| G38-14 | Canonical or legacy command starts a no-generic-input Gate 4 compatibility run. | Exactly `continuo.jobs-compat.v1` is resolved, hashed, and persisted before external work. |
| G38-15 | Current schemas 1–12 are loaded under a generic executable or legacy alias. | Existing strict classification/migration behavior is unchanged; generic facts are never invented. |
| G38-16 | Migration sees a legacy identifier, current target default, display label, raw command, or source path resembling generic state. | It preserves known facts but does not infer generic profile/configuration/route/capability/verification/provenance or execution eligibility. |
| G38-17 | A saved generic run resumes through legacy executable/import/env/default or a saved compatibility run resumes through canonical names. | Saved schema/configuration/profile controls; invocation spelling cannot alter routes, policy, authority, or resume outcome. |
| G38-18 | A legacy command uses `--json`, or a human command consumes a legacy alias. | JSON remains parseable and carries stable deprecation diagnostic; human stderr warning is bounded and stdout/data contracts remain intact. |
| G38-19 | A warning, compatibility-window date, package reinstall, or missing old alias occurs. | No automatic record/config/shell/import rewrite, target modification, or silent alias removal occurs. |
| G38-20 | A proposed Gate 4 change removes an alias before window expiry or lacks release/migration/readiness evidence. | Release validation rejects it; later approved removal contract is required. |
| G38-21 | Doctor, dry-run, status, report, or migrate-run is called through canonical or legacy form. | Their established read-only/classification behavior is equivalent and does not mutate aliases, config, target, or historical records. |
| G38-22 | Proposal implements package move, scripts, env resolver, warning, migration, generic config, adapter, provider, or other Gate 4 code. | It is out of scope pending explicitly approved implementation work. |

## Approval and implementation evidence

The owner approved this Gate 3.8 contract on 2026-08-03. Gate 3.8 is a
documentation-only contract-definition deliverable: package/source relocation,
compatibility shims, executable scripts, target resolver, deprecation warnings,
configuration/persisted-state migration, CLI/JSON wiring, package publication,
and installed-package validation (with `UV_NO_EDITABLE=1` on macOS) belong to
the explicitly later Gate 4 work. No runtime source, test, fixture, run record,
provider, target checkout, Git side effect, commit, or push changed for this
Gate.

Publication evidence: commit `4b0746cb98e1db24c8ebad74edda57e7d97d26d1`
(`Define generic engine compatibility matrix`) is on `origin/main`.

Validation on clean synchronized `main` at
`a3fc8ed79c1c4769d19b6342f7a7a97c3a21c77f` confirmed that authoritative local
Markdown links resolve, all 22 `G38-*` matrix IDs are unique, and tracked and
new-file `git diff --check` whitespace checks pass. No live-provider or
Jobs-repository validation was performed.
