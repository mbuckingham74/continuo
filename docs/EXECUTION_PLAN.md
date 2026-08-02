# Continuo execution plan

## Purpose and authority

This document is the progress tracker for evolving Continuo from its current
Jobs-specific reference implementation into a reusable orchestration engine.
It is intentionally written before further engine changes so implementation can
proceed as a sequence of bounded, reviewable decisions rather than as a rewrite.

[`ENGINE_ROADMAP.md`](ENGINE_ROADMAP.md) remains authoritative for priority,
scope, sequencing constraints, and accepted or rejected recommendations. This
tracker translates that roadmap into execution gates. The preserved files under
`docs/planning/` remain intake evidence, not requirements.

If this tracker conflicts with the roadmap, stop and reconcile the documents
before implementing code.

## Product outcome

Continuo should coordinate AI-assisted work for multiple projects without
copying or forking its controller. A project supplies trusted configuration and,
where necessary, adapters. Continuo retains deterministic workflow authority,
provider boundaries, persistence, recovery, verification, and human publication
gates.

Jobs is the first compatibility pilot, not the architectural owner of the
engine. Its current behavior must remain available while generic contracts are
introduced. A second, structurally different project is required before the
generic-core milestone can be considered proven.

## Non-negotiable constraints

- The notation constrains the improviser; providers cannot renegotiate workflow
  policy, authority, retry behavior, or approval gates.
- Complete P0 work before consequential live writer use.
- Complete P1 persisted-contract work before generic adapter implementation.
- Do not rewrite the controller or move it into the Jobs repository.
- Do not touch `/Users/michaelbuckingham/Documents/my-apps/jobs` without explicit
  approval. In particular, do not resume or modify its in-flight T008 checkout.
- Do not invoke live providers or run Continuo against Jobs without explicit
  approval.
- Keep `jobs-orchestrator`, `JOBS_REPO`, and `src/jobs_orchestrator` as
  compatibility identifiers until the planned alias migration.
- Preserve separate safety policies for read-only provider attempts and
  write-capable recovery.
- Implement and review one bounded item at a time. Do not combine unrelated
  cleanup, renaming, or feature work.
- Do not commit or push without explicit approval.

## How progress is recorded

Use these status values:

- `[ ]` — not started
- `[~]` — in progress
- `[x]` — completed and reviewed
- `[!]` — blocked; the evidence entry must identify the blocker

Every bounded item must record:

- the invariant protected and the reproduced problem;
- in-scope and explicitly out-of-scope behavior;
- persistence, migration, retry, crash/resume, and audit effects;
- read-only versus write-capable authority implications;
- failure-path tests and relevant regression tests;
- `git diff --check` result;
- documentation changes, or a recorded reason none were needed;
- the reviewed diff and human decision before the next item starts; and
- commit and pull-request identifiers only after separately approved publication.

Progress notes belong beneath the applicable item and should include the date,
result, and links to durable evidence. Checking a box means the exit criteria
were demonstrated, not merely that code was written.

## Gate 0 — Approve the execution contract

**Goal:** agree on the migration shape before changing engine behavior.

- [x] Verify the clean Continuo baseline, branch, origin, and HEAD.
  - Evidence (2026-08-02): clean `main` aligned with `origin/main` at
    `9fea0f3629b678ef3481e7bf313d41e1d330260f`.
- [x] Inventory explicit and semantic Jobs coupling in source, packaging, tests,
  and documentation.
- [x] Decide that Continuo remains the engine and Jobs becomes the first project
  compatibility pilot.
- [x] Decide against a wholesale rewrite or copying the controller into Jobs.
- [x] Review and approve this execution plan.
  - Evidence (2026-08-02): approved for commit and push by the repository owner.
- [x] Convert the first implementation item, M0.1/C-1, into a bounded execution
  note with its adversarial test matrix.
  - Evidence (2026-08-02): the draft execution contract and adversarial matrix
    below were derived from the authoritative C-1 decision, current consumers,
    installed Git documentation, and an isolated temporary-repository probe.
    They were approved by the repository owner before M0.1 implementation began.
  - Validation (2026-08-02): all 19 existing unit tests passed with fake
    providers; 10 local Markdown link targets and all 20 matrix rows were
    checked; `git diff --check` passed. Only this documentation file changed.

### M0.1 / C-1 bounded execution note (approved 2026-08-02)

**Status and boundary.** This note specifies the first Gate 1 implementation
item. The repository owner approved it on 2026-08-02 before source and test
changes began.

**Invariant and reproduced problem.** Every path reported by Git as changed must
remain an exact, unambiguous repository-relative path through changed-file
enumeration, reviewer input, working-tree fingerprinting, resume checks, and the
approval-gated staging pathspec. At baseline
`42f1d4bb7d523daf627e6074f789ca0abe24c5f9`, `changed_files()` parses the
human-readable, newline-delimited porcelain display by slicing each line and
splitting on ` -> `. Git may quote or escape that display. A literal ` -> ` in
a filename is indistinguishable from the display delimiter. As recorded in
[`ENGINE_ROADMAP.md`](ENGINE_ROADMAP.md#c-1--git-porcelain-parsing), the result
can omit actual untracked content from review and fingerprint coverage. The
observed staging failure was loud, so this note does not claim that the defect
silently published incorrect work.

The affected flow is deliberately small but safety-critical:

1. `_verify()` persists `changed_files` and the working-tree fingerprint.
2. `_implementation_diff()` uses the same enumeration to append readable
   untracked contents to the tracked diff supplied to reviewers.
3. `_resume_guard()` recomputes the fingerprint before resume and again around
   approval processing.
4. `_approval_gates()` passes the persisted paths after `--` to
   `git add -A`, then blocks on a nonzero staging result.

**Verified Git record contract.** M0.1 will request
`git status --porcelain=v1 -z --untracked-files=all` and parse the result as
bytes. In porcelain v1 `-z`, an ordinary record is `XY SP path NUL`. A rename or
copy has `R` or `C` in the two-byte status and is `XY SP target NUL source NUL`:
the destination is first and the origin is the following field. There is no
` -> ` token, quoting, or backslash escaping. The parser must treat a rename or
copy indicator in either status column as a two-path record and must not assume
Git record order is sorted.

This ordering was checked on 2026-08-02 against Apple Git 2.50.1 both in the
installed `git-status(1)` documentation and in an isolated repository. The raw
probe emitted `R  new -> 名称.txt\0old.txt\0` and, with copy detection enabled
for the fixture, `C  copied.txt\0source.txt\0`. The arrow in the rename target
was filename data, not a separator.

**Bounded implementation contract.** The M0.1 diff must:

- add one byte-oriented porcelain v1 `-z` parser and the smallest Git invocation
  boundary needed to supply it; do not convert unrelated Git commands to bytes
  or introduce the future repository adapter;
- validate the two status bytes, required separating space, nonempty path,
  terminating NUL, and required second path for rename/copy records; malformed
  or truncated output fails closed instead of skipping a record;
- split records before decoding paths, decode each complete path with
  `os.fsdecode()`, require an `os.fsencode()` round trip, preserve the platform's
  exact spelling without Unicode normalization, and never use replacement
  decoding;
- reject a path that cannot be represented by the current UTF-8/Pydantic JSON
  persistence contract with an explicit controller error. On POSIX this includes
  raw non-UTF-8 names decoded to surrogate escapes. A local probe confirmed that
  filesystem round-trip succeeds for such a name while current Pydantic JSON
  serialization fails; lossily changing the name is not acceptable;
- return a deterministic, de-duplicated, sorted list of actual path strings.
  Ordinary records contribute one path; rename/copy records contribute both the
  source and target even though Git presents the target first;
- preserve the existing review-diff policy: the full `git diff HEAD` represents
  tracked states and rename/copy metadata, while readable untracked files are
  appended using exact decoded paths. Deleted paths remain enumerated even
  though no worktree file can be read;
- make the fingerprint include every enumerated path identity whether or not the
  path currently exists, plus bytes for paths that are files. Retain the existing
  status/diff hash inputs for unaffected paths so M0.1 does not become a general
  fingerprint redesign;
- preserve approval authority and stage only a safe projection of the recorded
  paths. Existing paths and absent paths still represented in the index are
  passed after `git add -A --`; an already-staged deletion or rename source is
  omitted because Git treats that now-unmatched pathspec as fatal. A copy stages
  its existing source and target. A nonzero `git add` remains a persisted Git
  operation and `blocked_git_failure`; and
- add only deterministic temporary-repository tests with fake providers where a
  controller path is exercised. No live provider, external target, commit to
  `origin`, or push is permitted.

**State and lifecycle impact.** No persisted field or schema-version change is
planned: corrected paths continue to use `WorkflowRun.changed_files: list[str]`,
the fingerprint remains a string, and parser/failure evidence fits the existing
verification and `last_error` records. New records must contain decoded actual
paths, never Git's quoted display form. A change-enumeration failure during
verification must persist a failed verification result and block before review
or approval; the same failure during a resume guard must refuse resume. There is
no automatic provider retry because parsing is deterministic controller work.
The persisted `changed_files` list remains the complete audit view even when the
approval-time staging projection omits an absent path already removed from the
index. If every recorded path is already staged and absent, no `git add`
subprocess or fabricated Git-operation record is needed before the approved
commit uses the existing index.

M0.1 must not rewrite historical run JSON or trust a legacy affected path list.
Correctly incorporating all path identities means a pre-M0.1 fingerprint for an
affected special name, deletion, or rename can mismatch after upgrade; refusing
resume is the intended safe migration behavior. Unaffected ordinary-file
fingerprints should remain stable. A crash after successful verification is
handled by the existing saved fingerprint and exact-stage recovery: resume
re-enumerates the tree before provider or Git work. A crash or exception before
the corrected verification record is saved cannot advance to review or staging.
Raw provider records, retry budgets, correction policy, and approval semantics
do not change.

**Adversarial test matrix.** Each path assertion compares exact strings, not a
display-rendered Git line. Integration cases use temporary Git repositories and
must run without provider CLIs.

| ID | Fixture / state | Required assertions |
|---|---|---|
| P1 | Untracked names containing Unicode, spaces, embedded quotes, and a literal ` -> `, separately and combined | Each `??` record yields exactly one unchanged path; no quote characters or escape text are invented; the arrow is never split; readable content appears in reviewer input; content and path changes alter the fingerprint; staging addresses the exact file. |
| P2 | Tracked unstaged edit of a special-character path | ` M` yields that one path; `git diff HEAD` contains the edit; fingerprint changes with bytes; staging produces the expected cached modification. |
| P3 | Staged edit of a special-character path | `M ` yields that one path; review includes the staged diff; fingerprint includes the cached diff and file; approval staging is idempotent. |
| P4 | One tracked file changed differently in index and worktree | `MM` yields one de-duplicated path; review covers the combined `HEAD` diff; fingerprint changes when either layer changes. |
| P5 | Staged addition and a separate untracked file | `A ` and `??` both enumerate; review gets the added diff and untracked contents; staging includes both without broadening beyond the recorded list. |
| P6 | Unstaged deletion | ` D` retains the absent path; review includes the deletion; fingerprint includes its identity; because the index still tracks it, `git add -A -- <path>` stages the deletion. |
| P7 | Staged deletion | `D ` retains the absent path; cached deletion reaches review and fingerprint; staging safely omits the absent, already-removed index path rather than failing on an unmatched pathspec. |
| P8 | Staged rename whose source/target contain spaces, quotes, Unicode, or literal arrows | Raw record is asserted as `R? target\0source\0`; the parser consumes target then source but returns both exact paths; review includes rename metadata; fingerprint includes both identities and target bytes; staging includes the target and safely omits the absent, already-staged source. |
| P9 | Copy detection enabled in the fixture, with a changed source so Git emits a copy record | Raw record is asserted as `C? target\0source\0`; both exact paths enumerate in deterministic order; review/fingerprint include target and source effects; staging both is harmless and complete. |
| P10 | Mixed tracked, untracked, staged, unstaged, deleted, rename, and copy records created in deliberately nonsorted order | The result is the sorted unique union with no dropped or duplicate path; reviewer input, fingerprint coverage, and staged index collectively represent every state. |
| P11 | Filename changed after verification but before resume or a positive approval | Recomputed fingerprint differs and the existing guard refuses the transition; no provider retry, staging, commit, or push occurs. |
| F1 | Git status exits nonzero | Existing Git/controller error propagation is preserved; verification cannot pass and no partial list is persisted as valid evidence. |
| F2 | Synthetic payload has a short status, wrong separator, empty path, missing final NUL, or trailing partial record | Parser raises a bounded controller error for every form; it never silently continues with a prefix. |
| F3 | Synthetic rename/copy payload ends after its first NUL-delimited path | Parser reports a truncated two-path record and blocks without returning a partial list. |
| F4 | Platform-created Unicode filename | `os.fsdecode()`/`os.fsencode()` round-trips exactly and the path survives `WorkflowRun` persist/load; this runs on every supported platform without assuming macOS normalization. |
| F5 | POSIX-only filename containing a raw non-UTF-8 byte | Parsing preserves the byte through surrogate escape, then the explicit persistence-compatibility check rejects it before review or staging; no replacement character is allowed. |
| F6 | `git add -A -- <stageable recorded paths>` returns nonzero | The Git operation is audited, the run becomes `blocked_git_failure`, and commit/push are not attempted. |
| R1 | Persist and reload a verified run containing supported special paths | `changed_files`, verification evidence, and fingerprint round-trip unchanged; resume recomputation succeeds only for the same tree. |
| R2 | Resume a pre-M0.1-style record whose quoted/split paths or old deletion/rename fingerprint differ under corrected enumeration | Resume fails closed without rewriting the legacy record, invoking a provider, or staging. |
| R3 | Simulated crash immediately before and after corrected verification persistence | Before-save recovery cannot skip verification; after-save recovery uses the saved exact paths/fingerprint and does not intentionally repeat completed provider work. |

**Explicit exclusions.** M0.1 does not adopt porcelain v2; add diff-size,
binary-content, symlink, submodule, merge-conflict, or ignored-file policy; add
allowed-path enforcement; redesign Git audit records; harden `.git`; change
provider supervision/retry behavior; migrate the run schema; generalize the
Jobs compatibility profile; rename `jobs-orchestrator`, `JOBS_REPO`, or
`src/jobs_orchestrator`; or alter commit/push/human authority. Those remain in
their roadmap items. Tests may expose an unrelated defect, but fixing it requires
a separate bounded note and human decision.

**Exit criteria:** the plan is approved, no open sequencing disagreement remains,
and M0.1 has an agreed scope that excludes generalization and unrelated cleanup.

## Gate 1 — Stabilize the existing engine (roadmap Milestone 0)

**Goal:** make the current implementation safe enough to serve as the behavioral
baseline for extraction. No live Jobs pilot occurs in this gate.

- [x] **M0.1 / C-1:** parse Git changes using verified NUL-delimited porcelain
  semantics; cover Unicode, spaces, quotes, literal ` -> ` names, and rename/copy
  field ordering.
  - Implementation evidence (2026-08-02): `orchestrator.py` now parses
    byte-oriented porcelain v1 `-z` records, verifies target/source ordering,
    rejects malformed or non-persistable paths, and preserves exact decoded paths
    across enumeration, reviewer input, fingerprints, resume guards, and bounded
    staging.
  - State/recovery evidence (2026-08-02): supported paths round-trip through run
    persistence; parser failures persist failed verification and block before
    review; affected legacy fingerprints refuse resume; crashes around
    verification persistence do not repeat the writer; no schema, provider retry,
    role, or approval-authority contract changed.
  - Adversarial evidence (2026-08-02): temporary repositories cover Unicode,
    spaces, quotes, literal arrows, real Git rename/copy ordering, tracked,
    untracked, staged, unstaged, deleted, and mixed states, exact review content,
    fingerprints, staging projections/failures, persistence, and crash/resume.
    The suite passes all 31 tests with fake providers.
  - Validation (2026-08-02): the root CLI and `jobs-orchestrator` from a clean
    temporary editable install both pass their help smoke checks; no repository
    packaging change was needed. Local Markdown targets and the matrix structure
    validate; `git diff --check` passes. No live provider, external target,
    commit, or push was used.
  - Review decision (2026-08-02): the repository owner approved the complete
    five-file diff and explicitly authorized commit and push. M0.1 is complete;
    M0.2 remains a separate bounded item.
  - Publication evidence (2026-08-02): commit
    `7aa16a7e77f0f4213c0602b11bf885bdbecb41d6` (`Fix NUL-delimited Git change
    parsing`) was pushed directly to `origin/main` with all 31 tests passing.
- [x] **M0.2 / C-2:** add provider deadlines, cancellation, process-group cleanup,
  partial-output capture, and real child-process failure tests.
  - Start evidence (2026-08-02): M0.1 was published before M0.2 began. Current
    provider execution and persistence boundaries were reviewed before M0.2
    source changes began.
  - Implementation evidence (2026-08-02): real provider subprocesses now run in
    isolated process groups under approved 30/60-minute monotonic deadlines,
    retain five-second heartbeats, capture partial output, and escalate from TERM
    through a five-second grace to KILL before reaping the direct child.
  - State/recovery evidence (2026-08-02): `timeout` and `interrupted` extend the
    existing failure vocabulary without a schema bump, persist through existing
    attempt fields, map to distinct blocked stages, and are excluded from both
    automatic outage retry and ordinary blocked-provider resume. Recovery from a
    saved timeout attempt recreates the same block without provider reinvocation.
  - Adversarial evidence (2026-08-02): real local Python children cover success,
    partial output, graceful TERM, forced KILL, grandchild cleanup,
    `KeyboardInterrupt`, unexpected polling exceptions, inherited descendant
    pipes, deadline-boundary arbitration, invalid timing, and launch failure.
    Controller tests cover read-only/writer timeout, interruption, persistence,
    non-retry, non-resume, and crash recovery. All 44 tests pass without live
    providers.
  - Validation (2026-08-02): Python compilation, the root CLI help check, and the
    preserved `jobs-orchestrator` help entry point from a clean temporary
    editable install pass. Ten local Markdown targets and all 15 ordered M0.2
    matrix rows validate; `git diff --check` passes. No live provider, external
    target, commit, or push was used during implementation.
  - Review decision (2026-08-02): the repository owner approved the complete
    seven-file diff and explicitly authorized commit and push. M0.2 is complete;
    M0.3 remains a separate bounded item.
  - Publication evidence (2026-08-02): commit
    `fdcfa930e5570e9b667d0005a01c21a3551c5bbf` (`Supervise provider process
    lifetimes`) was pushed directly to `origin/main` with all 44 tests passing.
- [x] **M0.3 / C-3/C-4:** normalize failure evidence sources and distinguish Claude
  transport/envelope errors from invalid review content using recorded fixtures.
  - Planning evidence (2026-08-02): the draft bounded execution note and
    adversarial matrix below were derived from the authoritative C-3/C-4
    decisions and the current provider, persistence, retry, and recovery paths.
    This is planning only: no provider or controller implementation changed,
    no live provider was invoked, and implementation remains unauthorized until
    the repository owner reviews the note and fixture provenance.
  - Approval evidence (2026-08-02): the repository owner approved the bounded
    contract, then approved a two-invocation synthetic read-only fixture capture
    plus documented deterministic derivatives for malformed, missing-output, and
    schema-invalid cases. No Jobs data or repository content was supplied.
  - Fixture evidence (2026-08-02): checksummed sanitized fixtures preserve a
    prior recorded success envelope and newly captured
    `error_max_budget_usd`/`error_max_turns` envelopes from Claude Code 2.1.220.
    The Sonnet capture reached its `$0.10` guard at `$0.104028`; the Haiku
    max-turn capture reported `$0.0514985`. No further provider calls occurred.
  - Implementation evidence (2026-08-02): provider attempts now persist optional
    failure source/code provenance, preserve physical return codes, prioritize
    native/OS/supervisor evidence over narrow stderr and opt-in bounded stdout,
    and exclude model-controlled content from transport classification. Claude
    error envelopes take the transport path; only recognized success envelopes
    expose `structured_output` to the closed review parser.
  - State/recovery evidence (2026-08-02): optional provenance fields retain
    schema version 6 and load legacy records as explicit `null`; saved kinds stay
    authoritative, legacy missing classifications no longer scan full stdout,
    and exact-stage recovery consumes saved native failures without provider
    reinvocation. M0.4 writer reconciliation remains unchanged.
  - Adversarial evidence (2026-08-02): 15 new tests cover all 30 matrix rows
    across fixture checksums, native/OS/supervisor/stderr/stdout-tail precedence,
    prose/diff isolation, zero-exit native errors, transport/content retry
    exclusivity, persistence, legacy recovery, crash recovery, reporting inputs,
    and explicit read-only/writer implications. All 59 tests pass.
  - Validation (2026-08-02): Python compilation, the root CLI help check, and
    the preserved `jobs-orchestrator` entry point plus `src/jobs_orchestrator`
    import from a clean temporary editable install pass. Ten authoritative local
    Markdown links/anchors, all 30 ordered M0.3 matrix rows, and the exact six
    fixture records with saved stdout checksums and forbidden-string scanning
    validate. `git diff --check` passes. Apart from the separately approved
    fixture capture, validation used no live provider or external target; no
    commit or push was made before review.
  - Review decision (2026-08-02): the repository owner approved the complete
    14-file implementation diff and explicitly authorized commit and direct push
    to `origin/main`. M0.3 is complete; M0.4 remains a separate bounded item.
  - Publication evidence (2026-08-02): commit
    `4a3262eda14e41c60a21b7b3d3d152dffe48a286` (`Normalize provider failure
    evidence`) was pushed directly to `origin/main` with all 59 tests passing.
- [~] **M0.4 / A-1:** make the provider-attempt lifecycle capability-aware; allow
  bounded read-only retry while blocking uncertain write-capable recovery when
  partial changes exist.
  - Planning evidence (2026-08-02): the draft bounded execution note and
    adversarial matrix below were derived from authoritative A-1, the completed
    M0.2–M0.3 attempt vocabulary, and the current retry, writer, fingerprint,
    persistence, blocked-resume, and crash-recovery paths. This is planning only:
    no provider, model, controller, or test implementation changed, and
    implementation remains unauthorized until the repository owner reviews the
    capability, persistence, recovery-action, and destructive-operation
    decisions.
  - Baseline validation (2026-08-02): all 59 tests pass at published `main`
    commit `b2e3c656085a50ff3d34d1ca47bdf01f6529b858` without live providers or an
    external target.
  - Approval evidence (2026-08-02): the repository owner approved the bounded
    contract, including the capability vocabulary, additive schema-6 bridge,
    three writer block states, two explicit recovery actions, and
    non-destructive restoration policy. Implementation is authorized within
    this reviewed boundary.
  - Implementation evidence (2026-08-02): provider attempts now require an
    explicit `read_only` or `workspace_write` capability before launch. The
    existing read-only 5/15-second unavailability sequence is preserved while
    every writer attempt is single-shot. The controller atomically saves exact
    pre-writer paths/fingerprint and an active marker, records post-state beside
    the physical attempt, routes uncertain outcomes to the three approved writer
    blocks, excludes them from ordinary writer resume, and exposes only the
    audited `retry-restored` and `adopt-current` recovery actions. No automatic
    reset, checkout, clean, deletion, worktree, clone, lock, fallback, permission
    expansion, commit, or push behavior was added.
  - Adversarial evidence (2026-08-02): 16 new deterministic tests plus updated
    M0.2/M0.3 expectations cover all 39 matrix rows across capability validation,
    read-only retry, single-shot writer failure kinds, timeout/interruption,
    marker ordering, clean/dirty and adversarial Git snapshots, unchanged,
    partial, unknown, and no-op outcomes, both recovery actions and refusals,
    manual restoration/reconciliation, crash boundaries, correction-state
    preservation, schema-6 compatibility, reporting, notes, race disclosure, and
    shared-checkout behavior. All 75 tests pass using only temporary Git
    repositories, fake providers, recorded fixtures, and local child processes.
  - Validation evidence (2026-08-02): root and isolated editable-install
    `jobs-orchestrator` help expose the same required `recover-writer` command;
    `JOBS_REPO` and `src/jobs_orchestrator` remain intact. All ten authoritative
    local Markdown links/anchors and all 39 ordered M0.4 matrix rows validate;
    Python compilation, documentation checks, and `git diff --check` pass. No
    live provider or external target was invoked and no commit or push was made.
    The complete implementation diff remains uncommitted pending repository-owner
    review, so M0.4 stays in progress.
- [ ] **M0.5 / Q-2:** enforce one active run per canonical target and test clean
  release, crash recovery, stale ownership, and approval-pending ownership.
- [ ] **M0.6 / Q-6:** make run storage private by default and define explicit
  handling for legacy files, redaction, retention, and export.

M0.2 through M0.4 must share one designed provider-attempt vocabulary, but each
implementation and diff remains independently reviewable.

### M0.2 / C-2 bounded execution note (approved 2026-08-02)

**Status and boundary.** The repository owner approved this note and its
provisional deadline ceilings on 2026-08-02 before provider source or persisted
models changed. M0.2 owns process lifetime and cleanup only. M0.3 will separately
normalize provider error evidence and Claude envelopes; M0.4 will separately
make outage retry and uncertain writer recovery capability-aware.

**Invariant and current failure.** Every provider process and descendant must
have a controller-owned terminal lifetime. Deadline, operator interruption, or
polling cancellation must stop the complete process group, capture available
stdout/stderr, record one terminal attempt, and prevent further workflow work.
At `7aa16a7e77f0f4213c0602b11bf885bdbecb41d6`, `_run()` starts a provider with
`Popen`, polls `communicate(timeout=0.2)`, and emits five-second heartbeats, but
has no deadline, isolated process group, termination escalation, or
`KeyboardInterrupt` cleanup. The injected test runner bypasses the real process
boundary, and existing tests cannot prove descendant cleanup.

**Shared attempt vocabulary.** M0.2 will preserve the existing
`ProviderAttempt`/`ProviderExecution` boundary and add terminal failure kinds
`timeout` and `interrupted`. A timed-out or interrupted attempt is neither
`unavailable` nor eligible for automatic retry, regardless of role. The
controller will persist captured output and duration through the existing
`ProviderRecord` fields and map these outcomes to distinct
`blocked_provider_timeout` and `blocked_provider_interrupted` stages. These
stages will not be resumable until M0.4 defines capability-aware reconciliation;
this prevents explicit resume from blindly repeating a timed-out writer.

No new persisted field or schema-version change is planned. A concise
controller-generated termination diagnostic may follow captured stderr so the
existing audit record identifies deadline, TERM/grace outcome, and whether KILL
was required. `failure_kind` remains authoritative, so the diagnostic is not
reclassified as provider unavailability. Old run records remain loadable because
the existing optional field is only gaining accepted values.

**Bounded supervisor contract.** The M0.2 diff must:

- route real provider subprocesses through one shared synchronous supervisor;
  keep command construction, structured-output parsing, prompts, and provider
  permissions unchanged;
- require a positive finite deadline for every production provider operation,
  use `time.monotonic()`, retain five-second heartbeats, and make the polling
  interval independently testable without busy waiting;
- create a new process group/session (`start_new_session=True` on POSIX and the
  corresponding new-process-group flag on Windows) without invoking a shell;
- on deadline or interruption, signal the process group for graceful shutdown,
  wait a bounded grace period, force-kill the remaining group/tree, and always
  reap the direct child;
- preserve partial stdout/stderr without duplicating chunks across repeated
  `communicate()` calls, including output emitted by shutdown handlers;
- convert deadline and `KeyboardInterrupt` outcomes into terminal executions so
  the already-armed controller stage can persist the attempt before blocking;
  cleanup must also run before re-raising any other cancellation/base exception;
- never schedule the existing 5/15-second same-provider outage retry for
  `timeout` or `interrupted`; do not otherwise change unavailable retry policy in
  this item;
- retain launch-error behavior for `OSError`, restore any temporary signal state,
  and avoid leaving pipes, direct children, or descendants alive; and
- use real short-lived Python child/grandchild processes for cleanup tests. Fake
  runners remain useful only for existing retry classification tests and cannot
  satisfy the supervisor exit criteria.

**Deadline decision required.** The five local Continuo run records contain zero
timed provider attempts, so no measured duration distribution exists. The
recommended initial values are conservative hard safety ceilings, explicitly
not tuned performance targets:

| Operation capability | Proposed deadline | Rationale |
|---|---:|---|
| Read-only review/advice | 30 minutes | Long enough for current bounded prompts while preventing an abandoned CLI from living indefinitely. |
| Workspace writer | 60 minutes | Allows larger implementation work while still bounding uncertain side effects. |
| TERM grace before KILL | 5 seconds | Gives CLI shutdown handlers time to flush output without materially extending a timed-out run. |

These constants will live at the provider boundary only and will not become the
future configuration schema. The repository owner approved these provisional
ceilings on 2026-08-02; later observed durations may justify a separate
configuration task.

**Persistence, retry, crash/resume, and audit impact.** A timeout/interruption
attempt must be appended once with command, nonzero return code, partial streams,
duration, terminal failure kind, and `retry_scheduled=False` before the run moves
to its distinct blocked stage. The saved provider prompt/stage remain available
for diagnosis, but ordinary `resume` must not reinvoke it. A controller crash
after the record save consumes that failure record and reconstructs the same
block; a crash before any record remains `blocked_interrupted_provider` under the
existing recovery rule. No provider substitution, correction-budget change,
approval change, schema migration, or target-repository mutation is in scope.

**Adversarial test matrix.** Tests use only local temporary directories and the
current Python executable; no provider CLI or external target is invoked.

| ID | Fixture / event | Required assertions |
|---|---|---|
| S1 | Child exits successfully before deadline | Exact stdout/stderr and return code are captured; duration is nonnegative; no cleanup signal or retry occurs. |
| S2 | Child exceeds a short deadline after emitting both streams | Attempt ends as `timeout`, preserves partial output, records no retry, and returns within deadline plus grace tolerance. |
| S3 | Child handles TERM and emits final output | Graceful output is captured, direct child is reaped, and KILL is not reported. |
| S4 | Child ignores TERM | Supervisor waits only the configured grace, force-kills the group, reaps the child, and records that escalation. |
| S5 | Child spawns a sleeping grandchild | Deadline cleanup leaves neither direct child nor descendant running. |
| S6 | Descendant closes or inherits output pipes unusually | Cleanup and final collection remain bounded; no `communicate()` hang survives the grace/kill path. |
| S7 | Operator `KeyboardInterrupt` while a real child/grandchild runs | Group cleanup completes, partial output is retained as `interrupted`, no retry occurs, and the controller persists `blocked_provider_interrupted`. |
| S8 | Unexpected base exception during polling | Group is cleaned and reaped before the original exception propagates; no success record is fabricated. |
| S9 | Executable is missing or launch is denied | Existing configuration/launch failure remains bounded without attempting group cleanup for a nonexistent PID. |
| S10 | Deadline is zero, negative, NaN, or infinite | Validation rejects it before spawning a process. |
| S11 | Provider exits near the deadline boundary | Exactly one terminal outcome and one attempt record exist; no double signal, duplicate output, or retry occurs. |
| S12 | Existing mocked unavailable results | The 5/15-second retry sequence remains unchanged, proving M0.2 did not absorb M0.4. |
| C1 | Read-only provider timeout reaches controller | Raw attempt persists and run blocks as `blocked_provider_timeout`; no alternate provider or structured-output retry runs. |
| C2 | Writer timeout reaches controller | Same terminal block occurs with no automatic or explicit-resume reinvocation; repository reconciliation remains M0.4. |
| R1 | Crash after timeout attempt save but before block save | Recovery consumes the saved timeout attempt and deterministically restores the timeout block without rerunning the provider. |

**Explicit exclusions.** M0.2 does not narrow transcript-based failure
classification, parse Claude envelopes, make ordinary outage retries
capability-aware, fingerprint writer state before attempts, define partial-writer
reconciliation, add async execution, adopt an event log, add route configuration,
rename providers or compatibility identifiers, invoke live providers, or touch a
Jobs checkout. Those remain M0.3, M0.4, or later roadmap work.

**Exit criteria:** all Milestone 0 exit criteria in the roadmap pass, the full
test suite passes without live providers, and the current Jobs-compatible
behavior remains available.

### M0.3 / C-3/C-4 bounded execution note (approved 2026-08-02)

**Status and boundary.** The repository owner approved this contract on
2026-08-02 before provider, model, controller, or test code changed. The owner
later approved a bounded two-invocation fixture capture and documented derived
negative fixtures. M0.3 owns trusted failure-evidence selection and Claude
result-envelope interpretation. M0.4 separately owns capability-aware retry and
uncertain writer reconciliation.

**Invariants and current failures.** Transport classification must be derived
only from evidence controlled by the provider or operating-system boundary, not
from model-authored content. At baseline
`c176e44ef25d6a0fc37401fe2d083824ee10e7f9`,
`classify_provider_failure()` lowercases and scans complete stderr followed by
complete stdout, including prompts, transcripts, prose, code, and diffs, and its
bare numeric matches can classify ordinary line numbers such as `401`, `429`,
or `503` as transport failures. This can select a wrong blocked stage or schedule
the existing unavailability retry.

Claude result interpretation has a second ambiguity. `parse_sonnet_review()`
accepts either `structured_output` or the complete top-level JSON object as
review content. A zero-exit Claude provider-error or limit envelope can therefore
consume the one invalid-review retry and later block as `blocked_provider_output`
instead of preserving its provider failure. Conversely, malformed protocol data
must not be promoted to a transport failure merely because model text contains
an error phrase.

The required separation is:

1. classify a failed attempt from trusted transport evidence;
2. interpret a syntactically valid Claude result envelope as success or a
   provider-native failure;
3. validate only the `structured_output` of a recognized success envelope as
   review content; and
4. let the controller apply the existing transport-failure or invalid-content
   path to that typed outcome.

**Recorded-fixture gate.** M0.3 tests load deterministic, sanitized fixture
records from the repository; they do not invoke `claude`, `codex`, a hosted API,
or any other live provider. Recorded success and native-error fixtures preserve
the captured process return code, stdout, and stderr as strings, plus non-secret
provenance consisting of Claude CLI version where recorded, capture date,
command shape, sanitization, and a fixture checksum. Sanitization may replace
user/model text and unnecessary telemetry values but must not invent, remove, or
rename top-level envelope/error fields. A fixture README identifies every
replacement and each approved deterministic derivative.

The reviewed fixture corpus must include exactly bounded examples for:

- a successful result envelope with valid `structured_output`;
- a provider/transport error envelope;
- a max-turn or provider-limit envelope;
- a deterministic truncation of the recorded success envelope;
- the recorded success envelope with only `structured_output` removed; and
- the recorded success envelope whose `structured_output` is replaced by a
  schema-invalid review object.

Fixture records are test evidence, not a general cassette/replay system. They
must contain no credentials, repository paths, proprietary prompts, full model
transcripts, or unnecessary telemetry. Raw fixture bytes and metadata must be
reviewed before they are added to Git.

**Trusted failure-evidence precedence.** One normalized classification result
must contain the existing failure kind plus a machine-readable evidence source
and the raw provider-native or OS code when available. Conflicting evidence is
represented by the closed sources `provider_native`, `os_error`, `stderr`,
`stdout_tail`, and `returncode`; M0.2's already-normalized `supervisor` source is
also retained for timeout/interruption. `failure_code` contains only a native
code/subtype or OS type/errno, never free-form prose, and is length-bounded to
120 characters like the current finding key. A supervisor terminal outcome is a
controller fact and is not reclassified from captured streams. For all other
failed attempts, conflicting evidence is resolved in this strict order:

1. **Provider-native structured error.** For a provider whose recorded contract
   defines an error envelope, use only the allowlisted envelope discriminator,
   error flag, subtype/code, and status fields. Never scan result text,
   `structured_output`, model prose, or nested transcript content. Unknown codes
   remain `provider_error` while their exact bounded code is audited.
2. **OS/subprocess error.** Preserve the exception type and `errno` or equivalent
   launch/supervision code before rendering diagnostics. Missing or nonexecutable
   local provider commands are configuration failures, not model authentication
   failures.
3. **Narrow stderr.** For a nonzero process without stronger evidence, inspect
   only provider-specific, line-anchored diagnostic forms. HTTP numbers require
   an explicit `HTTP`, `status`, or provider error-code prefix; bare numbers and
   free prose do not match. Match limits and accepted prefixes must be declared
   beside their tests.
4. **Bounded stdout tail.** Inspect only the final 8 KiB of stdout, and only for
   a named provider/CLI contract whose recorded failure fixture proves that its
   diagnostics use stdout. Apply the same anchored forms as stderr. Providers
   without such a fixture do not receive stdout classification.
5. **Fallback.** A nonzero result with no trusted match is `provider_error`; a
   zero-exit result is not a transport success or failure until any applicable
   provider-native envelope is interpreted.

The original stdout and stderr remain unchanged in the raw audit record. The
8-KiB value bounds classifier input, not persisted output, model input, or the
later redaction/retention work. Classification must be deterministic under
conflicting fields and independent of the order of untrusted text.

**Claude envelope and review-content contract.** M0.3 must introduce the
smallest Claude-specific interpreter needed at the existing Sonnet boundary.
Its accepted field names and discriminators must come from the reviewed fixture
corpus, not assumptions about undocumented CLI behavior.

- A recognized success envelope with `structured_output` continues to validate
  through the closed `ReviewResult` schema and existing semantic consistency
  checks.
- A recognized provider-error envelope becomes a failed provider execution with
  normalized failure kind, evidence source `provider_native`, and its exact
  bounded native subtype/code. Its result/prose fields are not review content.
- A max-turn or provider-limit envelope is a provider-native terminal failure,
  not malformed review content. Unless the recorded native code maps
  unambiguously to an existing quota, rate-limit, configuration, or unavailable
  kind, it remains `provider_error` with the native code preserved.
- Invalid JSON, a non-object top level, an unknown/malformed envelope shape, a
  recognized success envelope missing `structured_output`, and schema-invalid or
  semantically inconsistent `structured_output` are invalid structured output.
  They receive the existing one same-provider content retry and then
  `blocked_provider_output`.
- The parser must not fall back to treating the entire Claude envelope as a
  `ReviewResult`. A bare review object is accepted only if a reviewed recorded
  contract proves the installed CLI can emit it for this exact command shape;
  otherwise it is malformed protocol output.

Transport retry and content retry are mutually exclusive for one attempt. A
provider-native failure never consumes the structured-output retry. Invalid
review content never enters transport classification, even when its summary,
finding key, or other model text contains quota, auth, rate-limit, timeout, or
HTTP-looking phrases.

**Bounded implementation contract.** The eventual M0.3 diff must:

- add one small normalized failure-evidence value and thread it through
  `ProviderAttempt`, `ProviderExecution`, `ProviderRecord`, `_record_provider()`,
  blocking, recovery, and reporting inputs without introducing provider adapters
  or stable route IDs early;
- keep `failure_kind` as the controller's transition input while recording the
  evidence source and bounded native/OS code separately; never rebuild a new
  record's control classification by rescanning raw model output;
- preserve the physical process return code exactly. A recognized native error
  envelope may have return code zero, so controller success checks and terminal
  display must use the normalized failure outcome rather than forge a nonzero
  code or assume that zero alone means success;
- replace complete-stream matching with the precedence and provider-specific
  channel rules above; remove all bare HTTP-status-number matching;
- preserve supervisor authority for `timeout` and `interrupted`, including their
  no-retry and nonresumable behavior established by M0.2;
- add the smallest per-attempt interpretation seam needed for a Claude native
  envelope to be normalized before `_run()` decides whether that physical
  attempt receives the existing transport retry; then let the controller choose
  transport versus invalid-content handling, including for zero-exit errors;
- preserve the current one same-provider retry for invalid Sonnet review content
  and Sol structured output, but do not generalize Claude envelope parsing to
  Codex output or change Sol's response contract;
- keep the existing 5/15-second automatic retry only when trusted evidence
  classifies an attempt as `unavailable`. M0.3 may change whether untrusted model
  text qualifies as that evidence, but must not make retry authorization depend
  on read/write capability; that is M0.4;
- retain raw stdout/stderr for audit and existing reports while ensuring prompts,
  transcripts, diffs, and model prose are never classification inputs;
- use only recorded fixtures, fake provider functions, temporary run storage,
  and local subprocess launch-error objects in tests; and
- preserve the root CLI and the compatibility identifiers `jobs-orchestrator`,
  `JOBS_REPO`, and `src/jobs_orchestrator` without accessing a Jobs checkout.

**Persistence and migration decision for review.** C-3 requires classification
source and provider-native code to be recorded, but the general migration
framework is scheduled under C-9. The proposed bounded exception is to add
optional `failure_source` and `failure_code` fields with `None` defaults
to the attempt/execution/audit models, retain workflow schema version 6, and add
round-trip tests. Existing run JSON then loads unchanged, while new records make
the classifier decision inspectable. This is an additive compatibility bridge,
not approval for general schema evolution.

M0.3 must not rewrite historical run files. A persisted non-`None`
`failure_kind` remains authoritative during recovery, including for a legacy
record that lacks the two new provenance fields. A legacy nonzero record with no
saved classification may be conservatively interpreted using its return code,
provider identity, narrow stderr, and an explicitly permitted stdout tail; full
stdout is never rescanned. A legacy zero-exit Claude record at an in-progress
review stage is interpreted using the reviewed envelope contract. Unknown or
ambiguous legacy evidence fails closed as `provider_error` or invalid provider
output rather than being silently upgraded. If review rejects additive fields
before migration machinery, implementation must stop and C-3's audit requirement
must be reconciled in the roadmap; it must not be hidden in stderr text.

**Retry, crash/resume, and audit effects.** Every physical attempt remains one
`ProviderRecord`. Provider-native error normalization must occur before the
record is saved, so a crash after attempt persistence can reconstruct the same
blocked stage without invoking the provider or reparsing model prose. A crash
after a successful Claude envelope is recorded but before review persistence
uses exact-stage recovery to interpret the same saved envelope once. A crash
before any attempt record retains the existing
`blocked_interrupted_provider` behavior.

For ordinary online execution, a first invalid-content result may invoke the
same provider once and records both attempts; a native transport failure invokes
only the existing transport retry policy, if its trusted kind qualifies, and
never also receives a content retry. Recovery of an already-recorded malformed
result continues to block rather than initiating a new retry, preserving the
current exact-stage recovery contract. Failure counts continue to aggregate the
existing failure kinds; provenance/code fields enrich per-attempt audit data but
do not create new policy categories or cross-run telemetry. M0.6 still owns
permissions, retention, export, and redaction.

**Read-only and writer implications.** Claude/Sonnet envelope interpretation is
read-only and cannot mutate the target. Trusted-channel classification applies
to every provider family, so a nonzero Luna result whose stdout merely quotes an
outage phrase will no longer qualify for retry. A genuinely trusted Luna
`unavailable` diagnostic still follows the current automatic retry behavior,
even though a writer may have left partial changes. M0.3 must test and disclose
that remaining risk but must not solve it by adding fingerprints, reconciliation,
or capability gates. M0.4 will prohibit or reconcile uncertain writer repeats
and will preserve bounded retries for eligible read-only attempts.

**Adversarial test matrix.** All provider responses below are repository fixtures
or constructed model-content values passed to pure functions. No test starts a
provider CLI or uses network access.

| ID | Fixture / state | Required assertions |
|---|---|---|
| E1 | Recorded Claude success envelope with valid PASS `structured_output` | Envelope is recognized as success; only `structured_output` is validated; review PASS is returned; no failure or retry is recorded. |
| E2 | Recorded Claude provider-error envelope, including conflicting outage/auth-looking result prose | Native discriminator wins; exact native code and `provider_native` source persist; result prose is ignored; no content retry occurs. |
| E3 | Recorded Claude max-turn/provider-limit envelope | Outcome is a provider-native terminal failure, not review content; native code persists; it maps to an existing kind only when the recorded code is unambiguous; no content retry occurs. |
| E4 | Documented deterministic truncation of the recorded Claude success envelope | Parsing fails as invalid structured output; one same-provider content retry is permitted online; a second malformed result blocks as `blocked_provider_output`. |
| E5 | Documented derivative of the success envelope with `structured_output` removed | It is invalid protocol output, never a bare top-level review; it follows only the content-retry path. |
| E6 | Documented derivative with schema-invalid `structured_output`, plus extra/missing fields, invalid enum, inconsistent PASS/category, PASS with non-PASS key, and failure with PASS key | Each schema or semantic variant is rejected as invalid review content; none becomes a transport failure because of text values. |
| P1 | Structured native code conflicts with OS-like, stderr, stdout-tail, and model-content phrases | Provider-native structured evidence deterministically wins and only its source/code are audited. |
| P2 | Supervisor result is `timeout` or `interrupted` while both streams contain quota/unavailable prose | Supervisor terminal kind wins; no retry is scheduled; existing distinct block and nonresume behavior remain. |
| P3 | Launch raises `FileNotFoundError`/`ENOENT` or `PermissionError`/`EACCES` and rendered text contains `permission denied` | OS type/code is retained and maps to local configuration failure, not provider auth; no nonexistent process cleanup or retry occurs. |
| P4 | Nonzero result with anchored stderr `HTTP 401`, `status code: 429`, and an allowlisted provider outage diagnostic | Each trusted diagnostic maps to auth, rate limit, or unavailable respectively; classification source is `stderr`; only unavailable receives the existing retry. |
| P5 | Nonzero stderr contains bare `401`, `402`, `403`, `429`, `500`, `502`, `503`, or `504`, source line numbers, prose quotations, or code literals | No bare number matches; without stronger evidence the outcome is `provider_error`. |
| P6 | Provider contract permits stdout diagnostics; matching anchored diagnostic occurs inside versus outside the final 8 KiB | Only the allowlisted form inside the bounded tail can classify; exact boundary behavior is deterministic and the complete raw stdout remains persisted. |
| P7 | Provider has no recorded stdout-diagnostic contract but its stdout tail contains a perfect-looking HTTP/quota error | Stdout is ignored and the nonzero attempt is `provider_error`. |
| U1 | Zero-exit Claude success contains quota, billing, auth, rate-limit, timeout, `503`, prompt text, transcript text, code, and a diff in `result` or review fields | None of that model-controlled content triggers failure classification or transport retry. |
| U2 | Nonzero provider stdout contains a model transcript, prompt, or diff with every legacy error phrase and status number | With no trusted native/OS/stderr/allowlisted-tail evidence, it is `provider_error`; no unavailability retry occurs. |
| U3 | Schema-invalid review summary/finding key contains transport phrases | It receives only the one invalid-content retry and then `blocked_provider_output`; failure counts do not claim quota/auth/outage. |
| T1 | Native `unavailable` envelope followed by a valid success fixture | Existing same-provider outage retry records separate attempts and succeeds; no structured-output retry or alternate provider occurs. |
| T2 | Native quota/auth/rate-limit/configuration/provider error envelope | Existing terminal block mapping is preserved; saved exact stage/prompt permit only current explicit blocked-provider resume behavior; no content retry or fallback occurs. |
| T3 | Invalid review fixture followed by valid success fixture | Exactly two Sonnet records exist; the retry is the structured-content retry, not an outage retry, and workflow advances using the validated second review. |
| T4 | Invalid review fixture followed by native transport error fixture | First attempt consumes the content retry; second is recorded and blocks by its transport kind, without a third attempt. |
| T5 | Native transport error fixture whose prose also looks schema-invalid | Transport path alone is taken; schema validation is never attempted and the content-retry budget is untouched. |
| R1 | Persist/load each normalized native, OS, stderr, stdout-tail, supervisor, fallback, and success record | Raw streams, failure kind, source, native/OS code, return code, duration, and retry flag round-trip exactly under the reviewed additive-field decision. |
| R2 | Load legacy schema-6 records with a saved failure kind but no provenance fields | Existing kind remains authoritative, missing provenance stays explicit, and no historical file is rewritten. |
| R3 | Load legacy nonzero record with no saved kind and adversarial full stdout | Conservative recovery uses only allowed trusted channels; model prose cannot recreate the legacy false classification. |
| R4 | Crash after normalized failed attempt persistence but before block save | Exact-stage recovery reconstructs the same transport block from saved normalized fields without provider invocation or model-text rescanning. |
| R5 | Crash after successful/malformed Claude attempt persistence before review state save | Recovery consumes the saved envelope without reinvocation; valid content advances once and invalid content blocks without silently granting a fresh retry. |
| A1 | Report over mixed new and legacy records | Existing failure-kind totals and retry counts remain stable; provenance gaps remain inspectable and malformed history is not counted as a different transport kind. |
| W1 | Read-only trusted unavailable attempt | Existing bounded 5/15-second same-provider retry remains until M0.4 and never changes provider or permission profile. |
| W2 | Writer stdout quotes outage prose with no trusted transport evidence | Attempt is `provider_error` and is not retried; repository content is not inspected or reconciled by M0.3. |
| W3 | Writer has trusted unavailable stderr after making partial changes | Test demonstrates the existing automatic retry remains possible and labels it as the M0.4 risk; M0.3 adds no pre-attempt fingerprint, cleanup, adoption, discard, or reconciliation behavior. |

**Explicit exclusions and M0.4 boundary.** Apart from the separately approved
two-invocation synthetic fixture capture, M0.3 does not invoke providers. It does
not add a general provider adapter or cassette framework; change model, route,
or display identifiers; add schema-migration machinery; persist parsed reviews;
alter correction budgets; add `--max-turns` to production commands; change
Claude tools/permission flags; add cross-provider fallback; or alter human,
commit, push, or merge authority.

In particular, M0.3 does not decide whether a write-capable attempt may be
automatically retried or explicitly resumed after a trusted outage. It does not
take a pre-writer fingerprint, detect or clean partial changes, create disposable
workspaces, or define reconcile/discard/adopt actions. Those are M0.4/A-1. Tests
may demonstrate the present writer risk but must not make an M0.4 source change.
M0.3 also does not rename `jobs-orchestrator`, `JOBS_REPO`, or
`src/jobs_orchestrator`, access a Jobs checkout, commit, or push.

**Exit criteria:** the repository owner approves the note, additive persistence
decision, and sanitized recorded-fixture provenance; all matrix rows are covered
by deterministic tests; the full existing suite plus new tests passes without
live providers; documentation and compatibility smoke checks pass;
`git diff --check` passes; the complete implementation diff is reviewed; and no
M0.4 behavior or other roadmap item enters the change.

### M0.4 / A-1 bounded execution note (approved 2026-08-02)

**Status and boundary.** The repository owner approved this contract on
2026-08-02 before provider, model, controller, or test implementation changed.
M0.4 owns capability-aware same-provider retry, pre/post repository evidence for
write-capable attempts, explicit recovery from uncertain writer outcomes, and
the audit trail for those decisions. M0.5 separately owns one-active-run target
locking, and later milestones own disposable workspaces, stable route IDs,
general migrations, and immutable approval identities.

**Invariant and current failure.** A write-capable provider must never be
automatically or blindly repeated after an attempt that may have changed the
target repository. At baseline
`b2e3c656085a50ff3d34d1ca47bdf01f6529b858`, `_run()` schedules the same 5/15
second unavailability retries for every command. `execute_luna_implementation()`
therefore can repeat Luna inside the provider boundary before the controller can
inspect the repository. The controller arms and saves `implementing` or
`correcting`, but records no pre-attempt repository fingerprint. A failed Luna
result enters a generic resumable provider block, and ordinary `resume` invokes
the saved writer prompt again without proving that the first attempt left the
working tree unchanged.

M0.2 already prevents automatic and ordinary-resume repetition for timeout and
interruption, but it cannot distinguish a clean writer stop from partial changes.
M0.3 proves that a trusted Luna unavailability diagnostic still receives an
automatic retry. A process crash after Luna changes files but before its result
is saved leaves only the armed writer stage and prompt; exact-stage recovery
cannot tell a never-started attempt from an unrecorded success or partial
failure.

The required lifecycle is:

1. declare the operation capability before any subprocess starts;
2. persist the target identity and pre-attempt writer fingerprint before every
   workspace-write invocation;
3. prohibit all automatic retries for workspace-write operations;
4. after a returned failure, interruption, or timeout, persist the attempt and
   compare the repository with its pre-attempt state;
5. block ordinary resume for every uncertain writer outcome; and
6. continue only after an explicit, audited operator choice whose repository
   preconditions are revalidated.

**Capability and retry decision for review.** Add the closed internal vocabulary
`ProviderCapability = Literal["read_only", "workspace_write"]` at the existing
provider-attempt boundary. It is an execution-safety fact, not the stable role,
provider-adapter, route, or display identity scheduled for Milestone 1.

Every production wrapper must pass its capability explicitly to `_run()`;
missing or unknown capabilities fail before process launch. Sonnet, Terra, and
Sol are `read_only`; Luna is `workspace_write`. A read-only `unavailable` result
retains the existing two automatic same-provider retries after 5 and 15 seconds.
A workspace-write result never sets `retry_scheduled=True`, regardless of
failure kind, trusted evidence source, elapsed time, or observed repository
state. The controller, rather than provider/model labels or command inspection,
supplies the capability when recording injected/fake executions.

The existing one same-provider structured-content retry remains available only
to the current read-only Sonnet and Sol paths. No failure or capability may
select a different provider, broaden permissions, or turn a writer into a
read-only operation.

**Writer repository evidence.** Before each initial implementation, correction,
or explicitly authorized writer retry, the controller must:

- revalidate repository root, branch, `HEAD`, and origin against the saved run;
- enumerate exact changed paths and compute `working_tree_fingerprint()` using
  the corrected M0.1 path contract;
- persist an active writer-attempt marker containing a unique attempt ID,
  `implementing` or `correcting` stage, purpose, pre-attempt fingerprint, and
  pre-attempt changed paths; and
- durably save that marker and the already-saved prompt before invoking Luna.

After Luna returns, the controller computes and persists the post-attempt
fingerprint and paths beside the physical provider attempt before making a
workflow transition. A fingerprint/enumeration error after provider return must
not discard the provider result or trigger a retry: persist the raw attempt,
retain the pre-state, record the inspection error, and block with unknown writer
state. A successful writer still follows normal deterministic verification; the
pre/post comparison is recovery evidence, not proof that the implementation is
correct.

Each new `ProviderAttempt`/`ProviderExecution`/`ProviderRecord` carries its
capability. New workspace-write `ProviderRecord` entries additionally carry the
bounded pre/post fingerprints when observed. Read-only records leave repository
fingerprint fields `None`; raw streams, physical return codes, M0.3 failure
provenance, duration, and retry flags remain unchanged.

**Returned writer-failure states.** A returned workspace-write failure,
timeout, or interruption always records one physical attempt and never performs
an automatic retry. The controller then chooses one explicit block:

- `blocked_writer_retry_required` when the post-attempt fingerprint exactly
  equals the pre-attempt fingerprint;
- `blocked_writer_partial_changes` when the fingerprints differ; or
- `blocked_writer_state_unknown` when repository identity, change enumeration,
  or fingerprinting cannot be trusted.

The underlying `failure_kind`, source, code, and streams remain on the provider
record and continue to contribute to existing failure totals. These writer
stages express side-effect state; they do not replace or reclassify the provider
failure. All three retain the active writer marker and are excluded from the
generic blocked-provider `resume` path.

**Explicit recovery decision for review.** Add a dedicated command with the
bounded shape:

```text
jobs-orchestrator recover-writer <run-id> \
  --action retry-restored|adopt-current --note <operator-text>
```

The equivalent root CLI command must remain available. The nonempty note records
operator intent but is not provider input. This command accepts only one of the
three writer-recovery blocks, rechecks repository identity and current state,
and persists an immutable `WriterRecoveryDecision` before continuing.

- `retry-restored` requires the current fingerprint to equal the saved
  pre-attempt fingerprint exactly. It covers both a genuinely unchanged failure
  and an operator who deliberately discarded/restored partial work outside
  Continuo. It records the decision, arms a new writer attempt with a new ID and
  fresh pre-state, and invokes the same saved prompt once. Any further failure
  blocks again and requires another explicit decision.
- `adopt-current` requires unchanged branch/`HEAD`/origin, a trustworthy current
  fingerprint different from the pre-attempt fingerprint, and at least one
  changed path. It covers direct adoption or operator reconciliation of partial
  work. It records the decision, does not fabricate a successful provider
  attempt, does not invoke Luna, advances the interrupted implementation or
  correction to its completed state, and runs the normal deterministic
  verification and adversarial review path.

Ordinary `resume` never performs either action and never invokes a writer from a
writer-recovery block. Leaving the run blocked is the abort choice. Continuo
does not offer an automatic `discard` action: deleting untracked files or
reversing tracked/staged changes is destructive and cannot be proven to preserve
operator work. An operator may restore the exact pre-state manually and then use
`retry-restored`. An operator may reconcile the current changes manually and
then use `adopt-current`; the chosen current fingerprint and note are audited.

**Persistence and migration decision for review.** A-1 requires durable
pre-attempt state and deliberate recovery evidence, while general migration
machinery remains C-9. The proposed bounded additive bridge retains workflow
schema version 6 and adds:

- optional capability and pre/post repository fingerprint fields to provider
  attempt/execution/audit records;
- optional `active_writer_attempt` state on `WorkflowRun`, with attempt ID,
  stage, purpose, pre/post fingerprints and paths, and inspection error; and
- a default-empty `writer_recovery_decisions` list containing action, decision
  timestamp, note, writer attempt/stage, saved pre/post fingerprints, and the
  fingerprint observed at decision time.

All new fields are closed, length-bounded where textual, and default to `None`
or an empty list so existing schema-6 JSON loads without rewriting. Legacy
records receive no invented capability or fingerprint. A legacy in-progress or
blocked Luna record without a durable pre-attempt fingerprint is not safe to
retry or adopt automatically; it blocks as `blocked_writer_state_unknown` and
requires manual resolution outside the new command. This exception does not
authorize general migrations, schema stamping changes, stable role IDs, or
approval-identity work.

**Crash/resume ordering.** The active writer marker is the write-ahead safety
record for these boundaries:

- crash after marker save but before provider start: recovery compares current
  state with the pre-fingerprint and blocks as retry-required, partial, or
  unknown without invoking Luna;
- crash after Luna side effects but before provider-result save: the same
  comparison detects unchanged versus partial state; no success is inferred;
- crash after failed provider record/post-state save but before block save:
  recovery consumes the saved evidence and reconstructs the same writer block;
- crash after a successful provider record save: exact-stage recovery consumes
  the success once and proceeds to verification without repeating Luna;
- crash after a recovery decision save but before a retry starts: the newly
  armed attempt marker prevents implicit invocation and requires another
  explicit recovery decision; and
- crash after `adopt-current` decision/state save but before verification:
  resume enters verification/review from the saved completed stage and does not
  invoke Luna.

The decision save and corresponding stage/active-marker update must be one
atomic run snapshot. Correction count, finding streak, Sol guidance, policy
decisions, saved prompt, and commit/push authority remain unchanged across
writer recovery.

**Audit and reporting effects.** Complete run JSON exposes the capability,
writer pre/post state, inspection errors, and recovery decisions. The concise
report adds the pending writer-state classification and recovery-decision count,
without treating adoption as a provider success or adding a provider call.
Existing provider failure-kind totals, physical-attempt counts, retry counts,
and timing remain derived from `ProviderRecord`. The active marker is not a
provider attempt and must not inflate metrics.

**Bounded implementation contract.** The eventual M0.4 implementation must:

- make `_run()` retry authorization explicit and capability-based before spawn;
- preserve the read-only 5/15-second unavailability retry while making every
  workspace-write physical attempt single-shot;
- centralize writer invocation so initial implementation, correction, explicit
  retry, fake providers, and crash recovery share one pre/post snapshot path;
- persist the active writer marker before provider invocation and persist a
  returned result plus post-state before routing its outcome;
- remove Luna stages from generic blocked-provider reinvocation and reject
  ordinary resume from all new writer-recovery blocks;
- implement only the two explicit, preconditioned recovery actions above;
- reuse existing repository identity, exact path enumeration, fingerprint,
  verification, review, failure-provenance, and atomic snapshot primitives;
- preserve provider permissions, prompts, deadlines, termination cleanup,
  correction/escalation budgets, and human Git gates; and
- use only fake provider functions, temporary Git repositories, and local child
  processes in tests. No test or implementation step invokes a live provider or
  accesses a Jobs checkout.

**Adversarial test matrix.** Tests use isolated temporary repositories and fake
providers. No provider CLI, network service, or external target is invoked.

| ID | Fixture / event | Required assertions |
|---|---|---|
| C1 | Read-only attempt returns trusted `unavailable` twice, then succeeds | Existing 5/15-second same-provider retries remain; three physical records carry `read_only`; provider, permissions, and prompt do not change. |
| C2 | Workspace-write attempt returns trusted `unavailable` | Exactly one physical attempt occurs; `retry_scheduled=False`; no sleeper or second provider call runs. |
| C3 | Workspace-write attempt times out or is interrupted | Process-group cleanup remains intact; exactly one attempt persists; no automatic or ordinary-resume reinvocation occurs. |
| C4 | Workspace-write attempt returns quota, auth, rate-limit, configuration, or fallback error | Every kind is single-shot and retains its M0.3 failure source/code without cross-provider fallback. |
| C5 | `_run()` receives a missing or invalid capability | Validation fails before runner/process spawn and before any attempt record is fabricated. |
| C6 | Sonnet or Sol produces invalid structured content | Existing single read-only content retry remains distinct from transport retry; no workspace-write path gains a content retry. |
| S1 | Initial implementation begins from a clean repository | Active marker with empty changed-path set and exact pre-fingerprint persists before the fake Luna observes its call. |
| S2 | Correction begins from an already modified verified repository | Pre-paths/fingerprint describe the current reviewed changes, match the saved resume guard, and do not assume cleanliness. |
| S3 | Writer succeeds and changes files | Physical record carries workspace-write and exact pre/post fingerprints; active marker survives until the success is durably routed, then normal verification runs. |
| S4 | Writer fails without changing repository state | Attempt persists and run enters `blocked_writer_retry_required`; provider failure remains audited; ordinary resume makes zero provider calls. |
| S5 | Writer fails after modifying tracked or untracked content | Different post-fingerprint persists and run enters `blocked_writer_partial_changes`; no verification, retry, or cleanup occurs automatically. |
| S6 | Writer changes a Unicode, quote, literal-arrow, rename, deletion, staged, or untracked path before failing | Corrected M0.1 enumeration/fingerprint detects each state exactly and never loses a path identity. |
| S7 | Repository enumeration/fingerprint fails after writer return | Raw provider attempt still persists, post inspection error is audited, run enters `blocked_writer_state_unknown`, and no retry occurs. |
| S8 | Writer returns success but makes no changes | Success is not a recovery block; existing deterministic verification reaches `blocked_no_changes` without fabricating partial-change recovery. |
| R1 | Read-only provider is in an existing resumable failure block | Ordinary explicit `resume` retains its current behavior and never changes capability or provider. |
| R2 | Any new writer-recovery block receives ordinary `resume` | Stage remains blocked and provider-call count stays zero; output directs the operator to `recover-writer`. |
| R3 | `retry-restored` observes the exact saved pre-fingerprint | Decision persists before one same-prompt workspace-write invocation with a new attempt ID and fresh pre-state. |
| R4 | `retry-restored` observes any fingerprint or repository-identity mismatch | Command refuses before provider invocation and leaves the active marker/block unchanged. |
| R5 | `adopt-current` observes valid changed state | Decision persists; no writer call or success record is created; normal verification and Sonnet review consume the current changes. |
| R6 | `adopt-current` observes no changed paths or the original pre-fingerprint | Command refuses; a no-op cannot be adopted as an implementation. |
| R7 | `adopt-current` observes branch, `HEAD`, origin, enumeration, or fingerprint failure | Command fails closed without provider, verification, staging, or state erasure. |
| R8 | Operator manually reconciles partial changes, then chooses `adopt-current` | Decision records saved post-state and newly observed chosen state plus note; reconciled files enter normal verification/review. |
| R9 | Operator manually restores/discards to the exact pre-state, then chooses `retry-restored` | Exact restoration is proven before a single explicit retry; Continuo performs no destructive cleanup itself. |
| R10 | Crash after active marker save with no provider record and unchanged repository | Resume invokes no writer and reconstructs `blocked_writer_retry_required`. |
| R11 | Crash after active marker save with no provider record and changed repository | Resume invokes no writer and reconstructs `blocked_writer_partial_changes`; no success is inferred. |
| R12 | Crash after failed provider/post-state save but before block save | Recovery reconstructs the same writer block and underlying failure audit without provider reinvocation. |
| R13 | Crash after successful provider record save but before completed-stage save | Recovery consumes the saved success once, clears the marker only through the normal path, and verifies without repeating Luna. |
| R14 | Crash after recovery decision save before retry/adopt continuation | Saved marker/stage makes the next resume deterministic; retry never starts implicitly and adopted changes never invoke Luna. |
| R15 | Failed correction is restored/retried or reconciled/adopted | Correction count, finding streak, Sol round/guidance, policy decisions, and prompt remain unchanged except for the audited recovery decision. |
| A1 | Persist/load a new read-only record, writer record, active marker, and both decision actions | All capability, fingerprint, path, note, timestamp, failure, duration, and retry fields round-trip exactly. |
| A2 | Load schema-6 records created before M0.4 | New optional fields default safely; historical JSON is not rewritten and no capability/fingerprint is invented. |
| A3 | Legacy Luna stage lacks a pre-attempt fingerprint | It blocks as writer state unknown; generic resume and `recover-writer` cannot invoke or adopt it. |
| A4 | Report contains mixed legacy, read-only, failed writer, and adopted writer history | Existing failure/attempt/retry totals remain stable; pending writer state and recovery count are visible; adoption is not provider success. |
| A5 | Recovery note contains provider-looking instructions or error prose | Note is length-bounded audit text only; it is never appended to a provider prompt or used for failure classification. |
| B1 | Partial writer changes include tracked, staged, deleted, or untracked content | M0.4 never runs reset, checkout, clean, unlink, or equivalent destructive restoration. |
| B2 | Writer fails in the shared target checkout | No disposable worktree/clone is created; isolation remains a later milestone and the run stays bound to its saved repository identity. |
| B3 | Another process changes the repository between snapshot and invocation | M0.4 fails closed where detected but adds no lock; one-active-run ownership and race closure remain M0.5/Q-2. |
| B4 | New capability and recovery records are persisted | They do not introduce stable role/route IDs, general schema migrations, event sourcing, approval identity, or configuration. |
| B5 | Compatibility and scope smoke checks | Root CLI, `jobs-orchestrator`, `JOBS_REPO`, and `src/jobs_orchestrator` remain; no Jobs access, live provider, commit, or push occurs. |

**Explicit exclusions and later boundaries.** M0.4 does not automatically
discard or revert repository content; create worktrees/clones; add target locks;
solve concurrent external mutation; change sandbox permissions; add provider or
route configuration; create stable role IDs; migrate historical schemas; add
general approval identities; modify correction policy; change commit/push/merge
authority; invoke live providers; or access a Jobs checkout.

M0.5/Q-2 owns one-active-run target locking and stale-owner behavior. General
disposable writer isolation remains a later repository/verification milestone.
C-9 owns migrations and persisted policy; C-5 owns stable role/provider/route
identity; approval records later add operator identity and richer gate semantics.
M0.4's capability and writer-recovery records are the smallest safety bridge
needed to prevent writer repetition before those abstractions exist.

**Exit criteria:** the repository owner approves the capability vocabulary,
additive schema-6 bridge, three writer block states, two explicit recovery
actions, and non-destructive policy; every matrix row has deterministic coverage;
all existing and new tests pass without live providers; root and compatibility
CLI smoke checks pass; documentation and `git diff --check` pass; the complete
implementation diff is reviewed; and no M0.5 or later behavior enters the
change.

## Gate 2 — Stabilize persisted contracts (roadmap Milestone 1)

**Goal:** make saved state safe to evolve before adding generic configuration or
renaming control identities.

- [ ] Inventory historical run schemas and decide migrate/archive/unsupported
  treatment for each known class.
- [ ] Add explicit current-schema constants, stepwise migrations, rollback tests,
  and visible failure reporting.
- [ ] Replace display labels in control flow with separate stable role, provider
  adapter, route, and model/display identities.
- [ ] Persist immutable parsed review records linked to raw attempts; make
  unreadable legacy history visible and non-silent.
- [ ] Persist the resolved correction and escalation policy with each run.
- [ ] Add immutable approval request and decision records with identity,
  timestamps, decision text, and repository fingerprints.
- [ ] Correct logical-call/physical-attempt and verification metric semantics.
- [ ] Add versioned machine-readable CLI output, `doctor`, and a genuinely
  read-only dry-run contract.
- [ ] Write the event/state architecture ADR without implicitly beginning an
  event-sourced rewrite.

**Exit criteria:** route display changes cannot alter policy; resume uses the
saved policy and schema; migrations have success and failure-path tests; and all
control-relevant provider results have durable parsed representations.

## Gate 3 — Define the generic-core contracts

**Goal:** approve the abstractions and trust boundaries before moving code behind
them. Design may begin during Gate 2, but implementation waits for Gate 2 exit.

- [ ] Define a versioned resolved-configuration model and precedence order for
  user defaults, project configuration, and explicit run overrides.
- [ ] Decide where trusted project configuration lives, who may modify it, how it
  is protected from writers, and how its hash invalidates resume.
- [ ] Define a normalized immutable task envelope: source identity, revision,
  canonical text, checksum, provenance, scope, acceptance criteria, and optional
  verification requests.
- [ ] Define the repository/project adapter contract: identity, snapshots,
  fingerprints, changes, diffs, allowed paths, branch/remote rules, commit-message
  policy, and approval-gated publication.
- [ ] Define the provider adapter and route-profile contracts using the
  provider-attempt lifecycle established in M0.2–M0.4.
- [ ] Define machine-checkable capability profiles and permission ceilings for
  every orchestration role.
- [ ] Define deterministic verification result/finding contracts and their
  correction-budget semantics.
- [ ] Write the compatibility matrix for old and new CLI, environment, package,
  import, configuration, and persisted-state identifiers.

**Exit criteria:** contracts, trust boundaries, migration behavior, and failure
policies are documented and adversarially reviewed; no interface relies on a
model or project display name for control flow.

## Gate 4 — Extract the generic engine (roadmap Milestone 2)

**Goal:** preserve behavior while moving project and provider assumptions behind
the approved contracts.

- [ ] Add validated, versioned configuration and persist its resolved form and
  hash at run creation.
- [ ] Extract current provider commands into provider adapters without changing
  no-fallback policy or permission ceilings.
- [ ] Add provider/model catalogs and configuration-backed route selection.
- [ ] Enforce capability compatibility before provider work begins.
- [ ] Extract the existing Git behavior as the first repository adapter.
- [ ] Extract `tasks/<ref>-*.md` as `LocalMarkdownTaskSpecAdapter`, preserving the
  exact current Jobs behavior as its initial compatibility profile.
- [ ] Feed the normalized task envelope, not its source system, into workflow
  policy and prompts.
- [ ] Consolidate implementation under the generic package while retaining a
  thin root shim.
- [ ] Add the generic CLI and `ORCHESTRATION_TARGET_REPO`; retain and test
  `jobs-orchestrator`, `JOBS_REPO`, and `src/jobs_orchestrator` as deprecated
  aliases for the defined transition period.
- [ ] Update documentation from “Jobs-specific implementation” to an accurate
  description of the generic core and compatibility profile.

**Exit criteria:** provider/model selection is configuration-only, a run resumes
with its persisted routes and configuration, existing Jobs-compatible commands
still work, and project-specific behavior no longer appears in workflow-control
branches.

## Gate 5 — Pilot with Jobs

**Goal:** prove compatibility without making Jobs the engine's owner.

- [ ] Obtain explicit approval to inspect or modify the Jobs checkout and confirm
  that its in-flight work is in a safe state.
- [ ] Define the Jobs project profile using the generic contracts; avoid custom
  adapter code unless configuration cannot faithfully express a requirement.
- [ ] Protect the project configuration from writer scope and snapshot its exact
  resolved form for each pilot run.
- [ ] Validate `doctor` and dry-run behavior against a disposable clone or
  isolated worktree first.
- [ ] Exercise controller transitions with deterministic fake/cassette providers.
- [ ] With separate approval, perform read-only provider validation.
- [ ] With separate consequential-run approval, perform one bounded writer pilot
  in an isolated workspace and stop at every human gate.
- [ ] Compare the pilot's task resolution, changes, fingerprints, review,
  verification, recovery, audit, commit, and push behavior with the compatibility
  baseline.
- [ ] Record every project-specific escape hatch discovered and either generalize
  it deliberately or document why it is a legitimate adapter responsibility.

**Exit criteria:** Jobs works as a project profile, not a fork; no unapproved live
provider or Git action occurred; and the pilot leaves a complete auditable record.

## Gate 6 — Prove reuse with a second project

**Goal:** demonstrate that “generic” means more than renaming Jobs concepts.

- [ ] Select a project with at least one materially different characteristic,
  such as task source, repository layout, verification stack, or publication
  policy.
- [ ] Integrate it without copying or modifying workflow policy.
- [ ] Run `doctor`, dry-run, fake-provider transitions, and deterministic
  verification before any approved live-provider work.
- [ ] Measure integration-specific code and explain every required adapter or
  plugin.
- [ ] Feed generic defects back into Continuo; keep genuine project policy in the
  project profile.
- [ ] Publish a concise, sanitized case study showing the same deterministic core
  constraining two different AI-assisted projects.

**Exit criteria:** two projects use the same controller and persisted contracts;
neither requires a controller fork; and portfolio claims are supported by
reproducible evidence rather than naming alone.

## Gate 7 — Add deterministic project quality (roadmap Milestone 3)

**Goal:** make correctness evidence project-aware while preserving deterministic
control.

- [ ] Build the shared safe verification command runner.
- [ ] Persist typed verification results and stable findings.
- [ ] Configure test, lint, type-check, build, and acceptance instances by
  capability rather than ecosystem-specific assumptions.
- [ ] Enforce allowed paths deterministically.
- [ ] Add diff/input budgets, binary handling, and incomplete-review blocks.
- [ ] Validate and harden Git metadata boundaries.
- [ ] Isolate write-capable roles in disposable workspaces.
- [ ] Fence untrusted inter-model guidance from authoritative task and policy
  content.

**Exit criteria:** deterministic checks precede probabilistic review where
configured, incomplete evidence cannot PASS, and a failed writer cannot
contaminate another run or integration checkout.

## Gate 8 — Durable operation and selected enhancements

**Goal:** add features only after the generic core and safety contracts can
support them.

- [ ] Implement the selected durable state/audit architecture.
- [ ] Make approval gates asynchronous through a single-writer boundary.
- [ ] Add safe notifications, redacted structured logs, and provider/account
  circuit breakers.
- [ ] Add normalized usage/cost telemetry and enforceable between-call ceilings.
- [ ] Improve operator views on versioned read/command APIs.
- [ ] Reassess replay, dual review, queues, and cross-project scheduling in the
  roadmap's dependency order.

**Exit criteria:** each enhancement preserves saved policy, target ownership,
least authority, bounded work, and explicit human publication authority.

## Portfolio completion evidence

Continuo is ready to present as a reusable portfolio system when the repository
can demonstrate:

- a documented deterministic state and authority model;
- adversarial failure-path coverage for provider and repository boundaries;
- safe persistence and migration of real historical states;
- capability-validated provider routing with no silent fallback;
- two project integrations using one controller;
- project-specific deterministic verification without controller forks;
- audited crash/resume and human approval behavior; and
- sanitized walkthroughs that distinguish tested behavior from future plans.

The portfolio story is not that AI agents can do anything. It is that Continuo
makes their permitted work, evidence, recovery, and publication authority
inspectable and deterministic.
