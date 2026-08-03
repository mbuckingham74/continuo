# Gate 3.4 contract — Repository/project adapter

**Status:** approved; documentation-only Gate 3 deliverable; awaiting owner implementation review.

## Decision

All repository inspection and mutation will pass through one versioned
`RepositoryProjectAdapter`. The generic workflow controller operates only on
typed adapter values and controller-owned approval commands; it does not branch
on project names, repository display labels, provider output, or Git-specific
strings. The initial adapter is `local_git_repository.v1`, which preserves the
current local Git behavior as the `continuo.jobs-compat.v1` profile.

The adapter owns repository identity, immutable snapshots, change enumeration,
fingerprints, bounded diff evidence, path-policy evaluation, branch/remote
validation, commit-message rendering, staging, commit, and publication. It does
not own workflow transitions, target ownership, provider authority, approval
decisions, retry policy, task meaning, or verification outcomes.

Read operations and write operations are separate closed interfaces. Only the
controller may call a mutating adapter method, and only after the applicable
persisted state and human approval are valid. An adapter never exposes an
arbitrary command runner, shell string, raw Git option list, or “force” escape
hatch to configuration, tasks, providers, or UI/API clients.

This Gate defines the contract only. Gate 4 extracts the current Git behavior
behind it. Later deterministic-quality work implements the full diff/input
budgets and Git-metadata hardening already required by this interface.

## Stable adapter and repository identity

Every adapter declares a strict immutable descriptor:

```yaml
repository_adapter_schema_version: 1
repository_adapter_id: local_git_repository.v1
repository_kind: git
capabilities:
  inspect: true
  workspace_write_observation: true
  stage: true
  commit: true
  publish: true
```

Capability values describe implemented adapter operations, not authorization
for a run. Missing required capability blocks during preflight. An adapter ID
is stable control vocabulary and cannot be a package name, class name, project
label, executable path, or user-facing display name.

`RepositoryIdentity` binds one exact checkout:

```yaml
repository_identity_schema_version: 1
repository_adapter_id: local_git_repository.v1
target_key: <64 lowercase hexadecimal characters>
canonical_root: <absolute resolved repository root>
filesystem_device: <nonnegative integer>
filesystem_inode: <nonnegative integer>
repository_metadata_identity: <adapter-owned immutable identity>
identity_sha256: <canonical payload hash>
```

For the initial adapter, `target_key` remains the existing versioned hash of
the resolved root device/inode. `repository_metadata_identity` binds the exact
Git common directory and worktree administrative identity without exposing it
to providers. The root must exist, be the exact Git worktree root, and retain
the same device/inode throughout inspection. A symlink alias, nested directory,
same-origin clone, copied worktree, replacement inode, or matching display name
is a different identity or fails validation.

Identity values are persisted before provider work and compared exactly on
resume and before every mutation. The adapter never selects a repository by
mtime, basename, branch name, remote URL alone, or “nearest Git root.”

## Repository policy

Trusted project configuration resolves one immutable
`ResolvedRepositoryPolicy` before run creation:

```yaml
repository_policy_schema_version: 1
repository_adapter_id: local_git_repository.v1
branch_policy:
  mode: current_named
  allowed_exact_names: []
  allowed_prefixes: []
remote_policy:
  publish_remote_name: origin
  allowed_remote_url_sha256: []
  destination_mode: same_named_branch
path_policy:
  allowed_paths: []
  protected_paths: []
commit_message_policy_id: jobs-compat.task-ref.v1
repository_policy_sha256: <canonical payload hash>
```

`current_named` requires any nonempty current branch at creation and pins that
exact branch for the run. A stricter profile may use an exact-name/prefix
allowlist; prefixes are literal normalized branch prefixes, not globs or regular
expressions. Detached HEAD, symbolic ambiguity, invalid ref names, and a branch
outside policy fail before provider work. The adapter never creates, switches,
renames, rebases, merges, or deletes a branch.

The publish remote is a validated literal Git remote name. Its observed URL is
stored as a credential-free redacted display plus a SHA-256 over the exact
control value; embedded credentials, control characters, unsafe URL forms, and
ambiguous multiple push URLs fail closed. An empty URL allowlist in the Jobs
compatibility profile means “pin the one safely observed URL at run creation,”
not “allow any later remote.” A nonempty list requires a hash match at creation.
No fetch, pull, remote mutation, mirror, tag publication, delete, or force push
is part of version 1.

The resolved policy and hash are persisted with the run. Configuration, task
text, provider output, or a display-label rename cannot change it after creation.

## Immutable snapshots and fingerprints

`RepositorySnapshot` is a complete immutable observation at a workflow boundary:

```yaml
repository_snapshot_schema_version: 1
repository_identity_sha256: <identity hash>
branch_name: <exact full branch name>
head_revision: <full adapter revision ID>
remote_name: origin
remote_url_sha256: <exact observed URL hash>
remote_display: <credential-free bounded display>
index_tree_sha256: <canonical index-state hash>
worktree_state_sha256: <canonical worktree-state hash>
clean: <strict boolean>
snapshot_sha256: <canonical payload hash>
```

The adapter collects fields from one stable observation window and detects a
root, metadata, branch, HEAD, remote, index, or worktree change during capture.
It retries no mutation and returns no partial snapshot. Hash inputs use
version-tagged, length-delimited canonical encodings; concatenation without
boundaries, locale-formatted output, quoted human porcelain, mtimes, and display
text are forbidden.

The initial run snapshot must be clean. Later snapshots may be dirty only in
stages that explicitly expect writer changes. Resume and writer recovery compare
the exact saved identity, branch, HEAD, remote, and appropriate change-set
fingerprint before deciding authority. A fingerprint is integrity evidence, not
a cleanup instruction or proof of authorship.

## Typed change set

Change enumeration returns an immutable `RepositoryChangeSet` linked to its base
snapshot. It contains ordered `ChangeRecord` values with:

- normalized repository-relative path as lossless UTF-8;
- optional normalized source path for rename/copy;
- closed kind: `added`, `modified`, `deleted`, `renamed`, `copied`,
  `type_changed`, `unmerged`, or `untracked`;
- separate index and worktree status;
- tracked/untracked and text/binary/symlink classifications;
- bounded current-content SHA-256 or an explicit unavailable/deleted marker; and
- no followed symlink target or bytes outside the repository root.

The adapter uses a verified NUL-delimited machine format. It preserves spaces,
newlines where the run-state encoding permits them, quotes, arrows, leading
dashes, Unicode, rename source/target pairs, staged and unstaged states, and
deletions without shell interpretation. Malformed, truncated, undecodable,
non-round-tripping, unmerged, submodule/gitlink, unsupported file-type, or
concurrently changing output blocks before review, staging, or publication.

Records are sorted by canonical encoded path and kind after parsing; duplicates
that represent distinct index/worktree states are combined only through the
specified typed fields. `change_set_sha256` covers the base snapshot hash and
every record. A flat changed-file list may remain a compatibility read view but
is never sufficient authority for resume, approval, staging, or commit.

## Path-policy resolution and enforcement

Paths use the Gate 3.3 normalized grammar: repository-relative POSIX paths or
directory prefixes, with no absolute path, empty segment, `.`/`..`, backslash,
NUL, wildcard, brace expansion, or platform-dependent case folding. The
controller computes one persisted `ResolvedPathPolicy` as the intersection of:

1. project `allowed_paths` from trusted configuration;
2. task `requested_paths`, when nonempty;
3. task `excluded_paths`; and
4. controller-owned protected paths.

An empty project allowlist in `continuo.jobs-compat.v1` retains the current
whole-worktree allowance subject to protected paths. An empty task requested
list grants nothing new. Every rename/copy source and destination is checked.
Directories match only themselves and descendants; lexical prefix coincidence
does not match. A symlink entry is checked at its repository path and never
authorizes traversal through its target.

Protected paths always include the Git common directory, per-worktree
administrative paths, `.git` at the root, controller configuration/state that
could appear within the checkout, and any profile-specific protected path.
No task, provider, user default, or explicit run override may remove protection.
If a changed path is outside the resolved allowance or intersects protection,
verification blocks with typed offending paths before probabilistic review,
approval, staging, commit, or push. The controller never silently drops an
offending path and commits the remainder.

This contract defines the required boundary but does not implement later Gate 7
C-8 metadata discovery/hardening. Until that protection can be proven for an
adapter, its write-capable generic mode must fail closed.

## Diff evidence

The adapter creates an immutable `RepositoryDiffArtifact` bound to the base
snapshot and exact change set:

```yaml
repository_diff_schema_version: 1
base_snapshot_sha256: <snapshot hash>
change_set_sha256: <change-set hash>
format_id: local-git-unified-diff.v1
complete: <strict boolean>
content_sha256: <hash of exact bounded diff representation>
byte_count: <nonnegative integer>
omissions: []
```

Tracked, staged, unstaged, deleted, renamed, and untracked changes are all
represented. Binary/symlink/unsupported content uses bounded typed metadata and
content hashes, never lossy text decoding or followed links. Diff generation
uses no external diff driver, textconv, pager, color, locale-dependent quoting,
or repository-supplied executable. The exact byte budget and chunking policy
belong to later C-7 implementation, but truncation or omission must set
`complete=false` with typed reasons.

An incomplete diff can be inspected but cannot yield review PASS, commit
approval eligibility, staging, commit, or publication. Provider prompts receive
only the bounded adapter artifact plus explicit incompleteness metadata; they
cannot convert incomplete evidence into complete evidence.

## Commit-message policy

Commit messages are controller-rendered from a stable registered
`commit_message_policy_id`, never supplied by a provider or copied from task
prose/review text. The Jobs compatibility policy renders exactly
`Implement task <source_record_id>`, preserving the current task-ref behavior.
A policy may use only approved normalized identifiers and fixed controller text.

The rendered message is UTF-8, 1–120 bytes, one line, and contains no NUL,
carriage return, line feed, control character, option-like leading dash, or
credential. It is persisted before commit approval and included in that
approval fingerprint. Unknown policy IDs, unsupported placeholders, invalid
rendering, or post-approval changes block rather than falling back.

## Approval-gated staging, commit, and publication

Read-only inspection never grants mutation authority. The controller persists
the complete snapshot, path-policy result, change set, diff artifact, rendered
message, and approval request before asking for commit approval. The request
fingerprint binds all those hashes plus run ID and task-envelope hash.

After an affirmative persisted decision and immediately before staging, the
adapter recaptures and compares the exact repository evidence. Any change makes
the approval stale. It stages exactly the approved paths using argument-safe
pathspec handling. It may not stage the whole repository implicitly, silently
exclude a path, or include a newly appeared path. An already-staged deletion is
preserved without fabricating a staging command. Staging failure is typed and
audited; no commit follows.

Commit uses the exact approved message and expected base HEAD. Ordinary local
hooks remain compatible, but any hook failure or hook-caused index/worktree/tree
deviation is detected and blocks. On success the adapter proves the new commit
has the expected parent, message, and tree corresponding exactly to the approved
change set, then persists the commit revision and post-commit snapshot. It never
amends, signs with an unapproved mechanism, bypasses hooks, merges, rebases,
resets, cleans, or discards changes automatically.

Push is a separate approval. Its request fingerprint binds the exact commit,
post-commit clean snapshot, pinned remote identity, source branch, and full
destination ref. Immediately before publication the adapter revalidates them.
It pushes one explicit non-force refspec
`refs/heads/<branch>:refs/heads/<branch>` to the pinned remote, with no tags,
deletes, wildcard, mirror, set-upstream, or alternate push URL. A non-fast-forward,
hook, authentication, network, or remote rejection fails visibly and is not
retried automatically. Success persists the observed publication result and
ends at `pushed_awaiting_merge`; merge remains outside adapter authority.

No adapter mutation may run without the target coordinator's existing durable
ownership and execution mutex. Approval never replaces ownership, resume,
provider-recovery, path, or repository-state validation.

## Failure, audit, compatibility, and migration

Every adapter operation returns a typed result with stable operation ID,
bounded status/reason code, timing where applicable, input evidence hashes, and
redacted diagnostics. Raw commands are controller-owned audit detail, never an
authority source. Diagnostics must not expose credentials embedded in remotes,
environment values, hooks, paths outside the target, task text, provider output,
or unbounded Git stdout/stderr.

Read failures and state mismatches cause no repair. Write failures preserve
current evidence and never trigger automatic reset, checkout, clean, restore,
retry, fallback adapter, alternate remote, or partial publication. Writer
recovery continues to use the existing explicit restore/adopt decisions and
must consume adapter snapshots rather than bypassing them.

`local_git_repository.v1` preserves `jobs-orchestrator`, `JOBS_REPO`,
`src/jobs_orchestrator`, the `origin` remote name, named-current-branch behavior,
the current commit-message form, separate commit/push approvals, and manual
merge boundary. Gate 3.8 records exact compatibility differences introduced by
typed evidence and explicit refspecs.

Existing run schemas keep their classification. A future migration cannot infer
repository identity, typed changes, complete diff evidence, resolved path
policy, or bound publication approval from legacy `RepoState`, `changed_files`,
fingerprints, and `GitRecord` values. Any structural preservation remains
execution-refused unless the Gate 4 migration contract proves every authority
fact.

## Non-goals

This Gate adds no adapter code, VCS backend, remote API, worktree isolation,
queue, merge, pull request, branch creation, force push, submodule support,
configuration parser, task adapter, verifier, schema migration, or CLI. It does
not run Git against Jobs, invoke a live provider, access the Jobs repository,
or change current provider, ownership, Git, storage, or compatibility behavior.

## Adversarial matrix

| ID | Scenario | Required outcome |
|---|---|---|
| G34-01 | Adapter descriptor is unknown, duplicated, malformed, or lacks a required operation. | Preflight fails before run creation, provider invocation, or repository mutation. |
| G34-02 | Configured path is nested, symlink-aliased, replaced, or not the exact repository root. | Identity validation fails; nearest-root or display-name matching is forbidden. |
| G34-03 | Same origin/branch exists in a different clone or worktree. | Device/inode and metadata identity distinguish it; saved authority is not transferable. |
| G34-04 | Repository identity changes between observation and use. | Snapshot acquisition or pre-mutation validation fails with no partial trusted result. |
| G34-05 | HEAD is detached, branch invalid/outside policy, or branch changes after creation. | Run/resume/mutation blocks; adapter never creates or switches a branch. |
| G34-06 | Remote has embedded credentials, multiple ambiguous push URLs, changed URL, or disallowed URL hash. | Validation blocks and diagnostics redact the URL; no alternate remote is chosen. |
| G34-07 | Snapshot input contains ambiguous concatenated fields, locale output, or mutable display text. | Canonical length-delimited hashing rejects/avoids it; display values never control comparison. |
| G34-08 | NUL-delimited status is truncated, malformed, undecodable, or changes while parsed. | Enumeration fails closed before review, staging, commit, or push. |
| G34-09 | Paths contain spaces, quotes, arrows, leading dashes, or Unicode. | Exact paths round-trip as data and remain argument-safe through evidence and staging. |
| G34-10 | Change is renamed/copied, mixed staged/unstaged, deleted, untracked, type-changed, unmerged, or a gitlink. | Supported states are typed completely; unmerged/gitlink/unsupported states block visibly. |
| G34-11 | Symlink points outside the repository. | Adapter records/checks the link entry without following its target or hashing external bytes. |
| G34-12 | Task requests a path outside project allowance or inside an exclusion. | Resolved intersection blocks or narrows; task scope cannot widen project authority. |
| G34-13 | Rename source is allowed but destination is protected/outside scope, or vice versa. | Both paths are evaluated and the entire change set blocks; no partial commit occurs. |
| G34-14 | Writer touches `.git`, common-dir/worktree metadata, controller state/config, or a profile-protected path. | Verification blocks before probabilistic review or Git mutation. |
| G34-15 | Project/task allowlists are empty. | Jobs project default retains whole-worktree compatibility subject to protections; empty task scope grants nothing new. |
| G34-16 | Diff omits untracked/binary/staged content, exceeds budget, or changes during capture. | Artifact is explicitly incomplete; review PASS, approval, staging, commit, and push are ineligible. |
| G34-17 | Repository config supplies external diff driver, textconv, pager, hook output, or executable. | Read evidence disables/ignores executable presentation helpers and remains controller-bounded. |
| G34-18 | Provider/task text proposes a commit message or injects newline/options into an identifier. | Registered policy and strict rendering reject it; provider text never reaches commit arguments. |
| G34-19 | Repository changes after commit approval but before staging. | Recomputed evidence differs, approval becomes stale, and no staging/commit occurs. |
| G34-20 | Newly appeared unapproved file exists when staging begins. | Exact approved path staging plus post-stage comparison blocks; it is never included or silently ignored. |
| G34-21 | Staging fails or stages a tree different from approved change evidence. | Failure is audited and commit does not run; controller performs no cleanup/reset. |
| G34-22 | Commit hook fails or modifies message/index/worktree/tree. | Adapter verifies outcome and blocks any mismatch; it neither bypasses hook nor treats unproven commit as approved. |
| G34-23 | Commit succeeds but parent, tree, or message differs from approval. | Run blocks with preserved evidence and cannot proceed to push approval. |
| G34-24 | Remote/branch/commit changes after push approval. | Approval is stale and no push occurs. |
| G34-25 | Push request attempts force, delete, tags, wildcard, mirror, upstream change, alternate URL, or different destination. | Closed publication command rejects it before network activity. |
| G34-26 | Push is non-fast-forward, rejected, interrupted, or unavailable. | Typed failure is persisted; no automatic retry, fallback remote, reset, or merge occurs. |
| G34-27 | Push succeeds. | Exact result is persisted and state stops at `pushed_awaiting_merge`; adapter has no merge authority. |
| G34-28 | Adapter mutation lacks target ownership/mutex or applicable persisted approval. | Controller rejects it before Git mutation. |
| G34-29 | Historical record has only legacy repo/change/Git fields. | Migration invents no typed authority evidence and remains execution-refused unless separately proven. |
| G34-30 | Dry-run or doctor exercises adapter reads. | It performs no index refresh, lock, chmod, hook, provider, state write, or remote network operation. |
| G34-31 | Proposal adds adapter implementation, C-7/C-8 hardening, worktree isolation, another VCS, PR/merge, or later Gate work. | It is out of scope pending the applicable approved contract and implementation gate. |

## Approval and implementation evidence

The owner approved this Gate 3.4 contract on 2026-08-03. Gate 3.4 is a
documentation-only contract-definition deliverable: adapter extraction, typed
snapshot/change/diff persistence, path enforcement, commit/push wiring, schema
migration, CLI changes, and installed-package validation belong to the
explicitly later Gate 4 work. No runtime source, test, fixture, run record,
provider, target checkout, Git side effect, commit, or push changed for this
Gate.

The approved contract derives from the Gate 3.4 tracker item,
Milestone 2 repository/project-adapter requirement and exit criteria, current
`RepoState`, target identity/coordination, NUL-delimited change parsing,
working-tree and approval fingerprints, diff construction, verification,
writer recovery, Git audit, commit/push approval paths, and deterministic tests.
Baseline is clean synchronized `main` at
`21fbd4fbf1afa4266765c08bc368bf1f509c91d3`.

Validation confirmed authoritative local Markdown links, all 31 `G34-*` IDs
unique, and tracked/new-file `git diff --check` whitespace checks passing. No
live-provider or Jobs-repository validation was performed.
