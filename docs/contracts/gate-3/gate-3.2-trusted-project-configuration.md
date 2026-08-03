# Gate 3.2 contract — Trusted project configuration

**Status:** owner-approved and published; documentation-only Gate 3 deliverable; implementation is Gate 4 work.

## Decision

Trusted project configuration is an operator-controlled private controller
artifact. It does not live in the target Git checkout and is never loaded from
a task file, prompt, provider output, provider-controlled environment value, or
provider-writable path. The future default physical location for one exact
checkout is:

```text
~/.config/continuo/projects/<target-key>/project-configuration.yaml
```

`<target-key>` is the existing lowercase SHA-256 checkout identity calculated
from the target root's device and inode. The document also binds that key and
the exact canonical checkout path. A copied, recloned, moved, or replacement
checkout needs a deliberately installed configuration; no remote URL, display
name, basename, branch name, or prefix match can select another project's
policy.

The logical authority is the local human operator. Only a future
controller-owned configuration command executing as that effective UID may
read, install, replace, or remove a trusted source. Direct file editing is an
operator action, but has the same validation and resume-invalidation effects.
Providers, adapters, Git hooks, target-repository code, UI/API clients, and
environment variables cannot write or select this file.

This Gate settles the trust and invalidation boundary for [Gate 3.1's resolved
model](gate-3.1-versioned-resolved-configuration.md). It adds no directory,
parser, command, environment variable, sandbox rule, model, migration, or
runtime behavior; Gate 4 owns implementation.

## Why the checkout is not a trusted source

The current implementation role has workspace-write authority over the target
checkout. A file such as `.continuo/config.yaml` could be edited by a writer,
Git hook, build tool, or malicious task change and affect a later run.
Persisting its hash protects an already-created run, but cannot prevent the
changed file from authorizing the next run.

Project configuration is consequently a per-checkout controller artifact, not
a version-controlled project file. A target repository may contain documentation
or an untrusted configuration request, but it is not executable policy and is
never imported automatically. Portability between checkouts is an explicit
operator installation/review action, not a trust shortcut.

## Source identity and format

The source is strict UTF-8 YAML or JSON as defined by Gate 3.1. Its decoded,
extra-forbid model includes these controller-owned fields in addition to the
Gate 3.1 route and policy fields:

```yaml
project_configuration_schema_version: 1
project_configuration_id: project-config-v1:<target-key>
target_binding:
  target_key: <64 lowercase hexadecimal characters>
  canonical_repo: <absolute resolved target-root path>
profile_id: <approved profile identifier>
```

The controller gets `target_key` and `canonical_repo` from a successful target
identity inspection. It requires an existing target Git root whose resolved
device/inode remains stable through validation. Every binding must match
exactly; it does not normalize case, expand a glob, trust `origin`, or repair a
mismatch. The only data-derived path component is the validated 64-character
target key.

The source hash is the lowercase SHA-256 of Gate 3.1's canonical validated
project-configuration payload. It is not a hash of YAML formatting/comments,
permissions, a directory tree, provider output, or another resolution layer.
The resolved record's `source_metadata.project_configuration` contains exactly:

```yaml
project_configuration_id: project-config-v1:<target-key>
target_key: <64 lowercase hexadecimal characters>
canonical_repo: <absolute resolved path>
project_configuration_schema_version: 1
canonical_payload_sha256: <64 lowercase hexadecimal characters>
```

This metadata binds one configuration snapshot to one checkout. It does not
expose source text in prompts or create a second control source after run
creation.

## Private storage and writer protection

The controller root (`~/.config/continuo`), `projects/`, every per-target
directory, and controller-created temporary directory use exact mode `0700`.
Final and temporary source files use exact mode `0600`. The controller creates
them without relying on umask, validates descriptors, atomically replaces only
an expected private regular file, and never follows a symlink or accepts a
hard-linked file. It rejects a symlink, non-directory, foreign-owned directory,
special file, non-regular source, foreign-owned source, or multi-linked source.

This protects against other local UIDs, not the same UID, administrators,
backups, ACLs outside this POSIX model, or a provider sandbox escape. Generic
configuration mode is unsafe unless a workspace-write provider has no write
mount or inherited path to this root; it must then be rejected before provider
invocation. Later adapter/capability contracts must prove that providers receive
only role-appropriate resolved data, not the source store.

Only an explicit future controller configuration command may create or replace
a source. It requires local human confirmation by default, validates binding
before replacement, shows the target key and old/new hash without source text,
and never treats provider-proposed path/content as authority. Deletion requires
its own approval/retention contract.

Read-only commands, including `doctor` and dry-run, inspect existing sources
only with non-mutating descriptor checks. They may not create roots, files,
locks, temporary files, SQLite artifacts, migrations, or chmod repairs. Missing
or unsafe storage is a visible readiness failure.

## Creation and resume invalidation

For a new run, the future controller acquires one stable source snapshot after
target preflight and before creating a run, claiming ownership, arming external
work, or invoking a provider. It validates binding, computes the canonical
source hash, resolves Gate 3.1 from validated data, then atomically persists the
complete resolved configuration and source metadata before external work. A
source changed during acquisition fails closed; no partial bytes are used.

Before resuming a configuration-bearing run, the controller safely reads the
current source and compares all five persisted metadata fields exactly. Missing,
unsafe, unreadable, invalid, unsupported, binding-mismatched, identity-mismatched,
or hash-mismatched sources block as `configuration_source_changed`. The saved
configuration remains inspectable, but continuation may not invoke a provider,
alter the target, acquire/release ownership, or replace the snapshot with
current configuration.

Restoring the exact prior validated source semantics and safe topology permits
ordinary resume validation. Otherwise the owner starts a new run only after
separately resolving target ownership, writer recovery, and Git state. There is
no ignore flag, automatic rebind, current-default fallback, same-model
substitution, or remote/display-name matching. This honors Gate 3.1's frozen
run rule: it detects a changed source but never uses it to alter saved routes or
policy.

An active command uses only its persisted resolved snapshot. A source change
cannot alter an in-flight transition, but must be detected at the next resume
boundary. Default diagnostics report a bounded mismatch category and hash
prefixes only.

## Compatibility, migration, and non-goals

The first Gate 4 release creates a trusted `continuo.jobs-compat.v1` source
only through an explicit controller-owned operator action. Its root is outside
the Jobs checkout; `jobs-orchestrator`, `JOBS_REPO`, and `src/jobs_orchestrator`
remain target-selection compatibility aliases rather than configuration
authority. This Gate adds no generic configuration environment variable.

Pre-configuration records retain existing schema classification and migration
rules. They never gain source metadata by inference and never resume under a
newly installed source. The Gate 4 migration contract must version this metadata
and explicitly define historical-record treatment.

This Gate does not add source control, CI, remote configuration, signing,
multi-user authorization, encryption, task/repository adapters, route catalogs,
capability policy, allowed paths, or configuration-editing runtime features. It
does not weaken providers, authority, retries, ownership, Git gates, storage,
or compatibility identifiers; it invokes no live provider and accesses no Jobs
repository.

## Adversarial matrix

| ID | Scenario | Required outcome |
|---|---|---|
| G32-01 | A target checkout contains `.continuo/config.yaml` edited by a writer. | It is untrusted and cannot select routes, policy, or provider authority. |
| G32-02 | Two clones share remote/branch but differ in device/inode identity. | Each has a different target key and needs separately installed configuration. |
| G32-03 | A checkout is moved, replaced, recloned, or resolves differently. | Binding fails; no path/remote/basename heuristic reuses former configuration. |
| G32-04 | Binding key/path, schema, profile, or route policy is invalid. | Resolution fails before a run, ownership claim, provider, Git, or storage mutation. |
| G32-05 | Root, target directory, or source is a link, wrong type, foreign-owned, or hard-linked. | Controller rejects it without following, chmodding, replacing, deleting, or reading source bytes. |
| G32-06 | Controller artifacts are created under permissive umask. | Directories are `0700` and files `0600` from creation. |
| G32-07 | A workspace-write provider tries to access the configuration root. | Sandbox/mount denies it; if that is not provable, generic mode fails before invocation. |
| G32-08 | A prompt, task, hook, environment variable, or UI request supplies a source path/content. | It is untrusted input and cannot select, install, replace, or authorize configuration. |
| G32-09 | Replacement is interrupted. | Either old or new complete private source exists, never a partial file or link target. |
| G32-10 | Source changes after run creation and before resume. | Resume blocks as `configuration_source_changed`; it does not rewrite saved resolution or invoke a provider. |
| G32-11 | Source is deleted, unreadable, malformed, unsupported, or unsafe after run creation. | Resume blocks visibly and retains inspectable run state; it neither recreates nor falls back. |
| G32-12 | YAML comments/format change but canonical payload is identical. | Canonical source hash matches and resume is not invalidated. |
| G32-13 | A route, model, or policy source value changes. | Hash mismatch blocks even if the old route remains installed. |
| G32-14 | Source changes while a provider is armed/executing. | Active command uses its saved snapshot; later resume detects change and blocks without reinvocation. |
| G32-15 | `doctor` or dry-run finds missing/unsafe source storage. | Read-only readiness failure; no creation, lock, migration, chmod, or other mutation. |
| G32-16 | A historical run lacks source metadata. | Existing migration/classification decides treatment; no implicit configuration/hash is assigned. |
| G32-17 | An operator requests `--ignore-config-change` or auto-rebind by origin/display name. | Refused; exact saved binding and canonical hash are required. |
| G32-18 | Proposal adds source-controlled config, loader, environment variable, auto-registration, catalog, capability policy, adapter, or Gate 4 runtime code. | Out of scope pending its approved Gate 3/Gate 4 contract. |

## Approval and implementation evidence

The owner approved this Gate 3.2 contract on 2026-08-03. Gate 3.2 is a
documentation-only contract-definition deliverable: the private-store parser,
installer, provider isolation enforcement, resolved-state persistence,
resume guard, schema migration, CLI wiring, and installed-package validation
belong to the explicitly later Gate 4 configuration item. No runtime source,
test, fixture, run record, provider, target checkout, Git side effect, commit,
or push changed for this Gate.

Publication evidence: commit `981813befbd30017797336f9ff485fefd95cba94`
(`Define trusted project configuration`) is on `origin/main`.

The approved contract derives from the Gate 3.2 tracker item,
Milestone 2 configuration/hash/resume exit criteria, Gate 3.1, current
`target_identity()` device/inode binding, private-storage descriptor validation,
target coordination, resume persistence, workspace-write provider path, and
deterministic tests. Baseline: clean synchronized `main` at
`3a1a0f2acd4aa4bd7c048837940feac24c12d5c6`.

Validation confirmed authoritative local Markdown links, all 18 `G32-*` IDs
unique, and tracked/new-file `git diff --check` whitespace checks passing. No
live-provider or Jobs-repository validation was performed.
