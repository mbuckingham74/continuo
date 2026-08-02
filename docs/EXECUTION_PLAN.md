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
- [ ] **M0.3 / C-3/C-4:** normalize failure evidence sources and distinguish Claude
  transport/envelope errors from invalid review content using recorded fixtures.
- [ ] **M0.4 / A-1:** make the provider-attempt lifecycle capability-aware; allow
  bounded read-only retry while blocking uncertain write-capable recovery when
  partial changes exist.
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
