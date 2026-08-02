# Continuo

## Current orchestration architecture and future-generalization roadmap

*Deterministic notation for probabilistic work.*

- **Implementation status:** current source on 2026-08-01
- **Product identity:** Continuo
- **Persisted run schema:** version 6
- **Primary implementation:** `orchestrator.py`, `providers.py`, and `models.py`
- **Test suite:** `test_orchestrator.py`

This document describes the behavior that exists in the repository today and then separates it from a proposed path toward a reusable orchestration framework. It is an architecture reference, not a claim that the roadmap features are already implemented.

Continuo takes its name from the musical relationship between written structure and bounded realization: **the notation constrains the improviser, and the improviser cannot renegotiate the notation**. The deterministic controller is the notation; providers and models perform their assigned parts without gaining authority to rewrite the score.

## 1. Purpose

The engine coordinates one bounded task specification through specification review, implementation, deterministic repository checks, adversarial review, corrections, escalation, and explicit Git approval gates.

Its current target is the Jobs repository. A task identifier such as `009` resolves to exactly one Markdown specification matching `tasks/009-*.md`. The controller then coordinates specialized model roles while retaining workflow decisions in deterministic Python and final authority with a human.

The main goal is not merely to call several models. It is to make a model-assisted implementation run:

- bounded by one saved task specification;
- reproducible enough to inspect and resume;
- conservative about repository and Git state;
- explicit about who may implement, review, interpret policy, or authorize mutations;
- resistant to endless correction loops;
- stopped safely when provider access or output is unreliable; and
- auditable after the run.

## 2. Design philosophy

### Deterministic control, probabilistic specialists

Models supply implementation work, review findings, escalation guidance, and policy recommendations. They do not control the workflow state machine. Python code decides:

- which role is called;
- which prompt and repository are used;
- which stages can follow the current stage;
- whether a failure is retried or blocks the run;
- how repeat findings and correction budgets are counted;
- whether repository state is still safe;
- what is persisted;
- when human approval is required; and
- which Git operation, if any, may occur.

This division keeps orchestration policy out of model improvisation. The implementation prompt explicitly tells Luna to use deterministic Python for workflow/control decisions and not to invent policy.

### Least authority by role

Each provider receives only the permissions needed for its role. Review, escalation, and policy/architecture roles are read-only. The implementation role can write within the workspace but has no network access and is explicitly prohibited from Git operations. Git mutations belong to the controller and remain behind human gates.

### Stop instead of silently changing semantics

Ambiguous policy, exhausted correction paths, mismatched repository state, provider account failures, invalid provider output, and failed Git operations become named blocked stages. The controller does not silently switch providers, invent policy, ignore a changed checkout, or continue after an unclassified provider error.

### Persist before and after consequential boundaries

The controller saves the stage and exact provider prompt before invoking a provider, then records provider output before interpreting it. This supports audit and crash recovery without intentionally repeating completed work.

### Human authority is granular

Policy approval, commit approval, and push approval are separate decisions. Approving a policy clarification does not approve a future commit, push, or merge. Merge is not automated.

## 3. Roles, authority, and hierarchy

The operating model has six named human/model roles. Five appear directly in the current workflow design; the conversational role sits outside the Python process. The deterministic controller is the enforcement layer between them, so it is included in the table even though it is software rather than a human/model role.

| Role | Current identity | Authority and responsibility | Enforced boundary |
|---|---|---|---|
| Human | Operator | Final authority for policy decisions, commits, pushes, and merges | Confirmations default to no; merge remains manual |
| ChatGPT conversational orchestrator | The human-facing planning and coordination layer | Helps the human frame work, interpret status, and decide what to do next | Not implemented, invoked, or persisted by this Python controller |
| Deterministic controller | Python `Controller` and supporting functions | Owns state transitions, routing, budgets, validation, persistence, repository checks, Git gates, and reporting | Provider output cannot directly advance stages or run Git commands |
| Terra | `gpt-5.6-terra`, labeled Terra High | Read-only architecture/policy ambiguity authority; identifies the missing decision and recommends narrow approval text | Codex read-only sandbox; cannot approve its own recommendation |
| Sol | `gpt-5.6-sol`, labeled Sol High | Read-only escalation executive for a repeat implementation defect; diagnoses persistence and guides a bounded correction, or identifies policy ambiguity | Codex read-only sandbox and closed textual response forms |
| Sonnet | Claude `sonnet`, labeled Sonnet 5 High | Fresh-context, read-only specification review and adversarial implementation QA | Plan mode; only `Read,Glob,Grep`; schema-constrained output |
| Luna | `gpt-5.6-luna`, labeled Luna High | Performs the initial implementation and bounded corrections | Workspace-write sandbox, network disabled, approvals disabled, Git explicitly prohibited |

The practical authority order is:

1. The human makes final policy and repository-publication decisions.
2. The deterministic controller enforces the approved orchestration policy.
3. Terra recommends clarification where architecture or policy is ambiguous.
4. Sol diagnoses persistent implementation failures and can refer ambiguity to Terra.
5. Sonnet independently evaluates specifications and implementations.
6. Luna writes the bounded implementation or correction.

This is a separation of duties, not a general ranking of model capability. In particular, Terra and Sol are advisory. Neither can approve policy, authorize Git, or bypass controller rules.

### Important current coupling

The model and provider names above are hard-coded implementation details in `models.py`, command builders in `providers.py`, prompt text, provider labels, recovery maps, metrics, and tests. They must not become permanent orchestration policy.

A reusable engine should route abstract roles such as `implementer`, `adversarial_reviewer`, `escalation_executive`, and `policy_authority` through configuration and provider adapters. Models can then change over time without changing the workflow policy, persisted semantics, correction rules, or authority boundaries.

## 4. Current component map

### `models.py`

Defines the persisted Pydantic models and current model routes:

- `ModelRoute` for the hard-coded role/CLI/model triples;
- `ReviewResult` and its closed status/category vocabulary;
- `RepoState` for the initial repository snapshot;
- `ProviderRecord` and `GitRecord` for the audit trail;
- immutable `PolicyDecision` records; and
- `WorkflowRun`, the versioned run state.

### `providers.py`

Owns provider command construction and subprocess execution boundaries:

- constructs role-specific CLI commands and sandbox settings;
- adds Luna's Git prohibitions to every implementation prompt;
- emits start, completion, and heartbeat messages;
- captures duration and output per attempt;
- enforces provider deadlines with process-group TERM/KILL cleanup;
- classifies provider failures;
- performs bounded same-provider unavailability retries; and
- validates Sonnet's schema-constrained review output.

It intentionally does not decide workflow transitions.

### `orchestrator.py`

Owns the deterministic workflow:

- task resolution and initial repository snapshot;
- prompt assembly;
- stage transitions and block conditions;
- verification and working-tree fingerprints;
- implementation/review/correction/escalation policy;
- human policy approval;
- crash and provider-failure recovery;
- commit and push gates;
- JSON persistence; and
- CLI status and reporting.

### `src/jobs_orchestrator/__init__.py`

Exposes the installed `jobs-orchestrator` entry point while retaining `orchestrator.py` as the directly runnable root CLI.

## 5. Repository and task preflight

### Repository selection

The repository is selected in this order:

1. explicit `--repo` option;
2. `JOBS_REPO` environment variable; or
3. the hard-coded default `~/Documents/my-apps/jobs`.

The resolved directory must:

- exist and be a directory;
- be the root of its Git checkout, not a subdirectory;
- be on a named branch, not a detached `HEAD`; and
- have an `origin` remote.

The initial snapshot records the absolute repository path, branch, `HEAD`, clean/dirty state, and origin URL. A new run is persisted even when it is immediately blocked for a dirty repository, preserving evidence of the refused start.

### Task-spec handling

`task_ref` must match a conservative identifier grammar: it starts with an alphanumeric character and may then contain alphanumerics, `.`, `_`, or `-`. It cannot contain path separators or glob syntax.

The current task adapter is fixed to the Jobs convention:

```text
tasks/<task-ref>-*.md
```

Exactly one file must match. Zero matches and multiple matches are hard errors. The controller reads the file as UTF-8 and persists:

- the task reference;
- relative task filename;
- complete specification text; and
- SHA-256 of that text.

All later prompts use the persisted specification, supplemented by any human-approved policy decisions. This prevents a provider from choosing a different task, but there is not yet a pluggable task source or schema-level semantic validator.

## 6. Workflow and stage model

### Normal path

```text
created
  -> spec_reviewing
  -> spec_review_passed
  -> implementing
  -> implementation_completed
  -> verifying
  -> implementation_verified
  -> reviewing
  -> implementation_reviewed
  -> awaiting_commit_approval
  -> awaiting_push_approval
  -> pushed_awaiting_merge
```

The run may pause at either approval gate as `commit_declined` or `push_declined`, then re-enter that gate on an explicit resume.

### Correction path

An implementation failure classified as `IMPLEMENTATION_DEFECT` or `SCOPE_VIOLATION` enters this loop:

```text
implementation_reviewed
  -> correction_pending
  -> correcting
  -> correction_completed
  -> verifying
  -> implementation_verified
  -> reviewing
```

A repeat finding can insert:

```text
implementation_reviewed
  -> sol_escalating
  -> sol_guidance_ready
  -> correction_pending
```

Every initial implementation and correction is followed by deterministic verification before Sonnet reviews it.

### Policy path

Specification review, implementation review, or Sol escalation can identify `POLICY_AMBIGUITY`:

```text
... -> terra_resolving -> blocked_policy_ambiguity
                         |
                    human approval
                         |
          created (spec ambiguity) or correction_pending (implementation ambiguity)
```

### Terminal and safety blocks

Current named block classes include:

- preflight and repository safety: `blocked_dirty_repo`, `blocked_unexpected_repo_state`, `blocked_no_changes`;
- review and bounded-correction safety: `blocked_spec_review`, `blocked_repeated_finding`, `blocked_correction_budget`;
- policy: `blocked_policy_ambiguity`;
- provider access: `blocked_provider_quota`, `blocked_provider_billing`, `blocked_provider_auth`, `blocked_provider_rate_limit`, `blocked_provider_unavailable`, `blocked_provider_configuration`, `blocked_provider_failure`;
- provider protocol/recovery: `blocked_provider_output`, `blocked_interrupted_provider`; and
- Git: `blocked_git_failure`.

Legacy `blocked_after_correction` and `blocked_after_escalation` runs have a compatibility resume path when their saved state qualifies under the current correction policy.

## 7. Specification review

Before any implementation, Sonnet receives a fresh-context, read-only specification-review prompt. It must return one structured result with:

- `status`: `PASS` or `FAIL`;
- `category`: `PASS`, `IMPLEMENTATION_DEFECT`, `POLICY_AMBIGUITY`, or `SCOPE_VIOLATION`;
- `finding_key`: `PASS` for a passing review or a stable defect identity for a failure; and
- `summary`: the finding explanation.

The parser rejects extra schema fields, invalid enum values, inconsistent PASS status/category pairs, PASS results with a non-PASS key, and failures with the PASS key.

A PASS allows implementation. `POLICY_AMBIGUITY` routes to Terra. Other specification failures block as `blocked_spec_review`; the controller does not ask Luna to repair a specification.

## 8. Implementation, review, and correction loop

### Initial implementation

Luna receives the persisted specification plus all approved policy decisions and an instruction to implement only that task. The provider boundary adds a standing prohibition on commit, push, branch changes, merge, rebase, reset, and `.git` mutation.

On successful provider exit, deterministic verification runs. Only then does Sonnet receive:

- the authoritative task prompt;
- up to eight recent implementation findings;
- the controller's changed-file list; and
- the full tracked diff plus readable contents of untracked files.

Sonnet is told to reuse an existing `finding_key` when the defect is materially the same and to create a new key only for a genuinely different defect.

### Ordinary correction

The first occurrence of a non-policy implementation finding gets one bounded Luna correction without Sol. The correction prompt includes the current review summary and asks for the smallest change that fixes it without changing policy or using Git.

If a later review reports a different finding key, that new defect also gets its own ordinary bounded correction, subject to the global correction budget.

### Scope violations

`SCOPE_VIOLATION` follows the implementation-defect correction machinery. It is not treated as policy ambiguity. The stable finding identity and budgets still apply.

## 9. Finding identity and repeat-defect escalation

Finding identity is central to avoiding both premature human escalation and endless retries.

For current structured reviews, identity is the Sonnet-supplied stable `finding_key`. For legacy persisted results without a key, the controller normalizes the summary, hashes it with SHA-256, and uses a deterministic `legacy-<16 hex characters>` key.

The controller calculates the current consecutive streak of the same finding key from parseable Sonnet implementation-review records. A different intervening finding resets the streak for escalation purposes.

The policy is:

1. **Streak 1:** one ordinary bounded Luna correction.
2. **Streak 2:** Sol escalation round 1, followed by one Sol-guided Luna correction.
3. **Streak 3:** Sol escalation round 2, followed by one final Sol-guided Luna correction.
4. **Streak 4:** block as `blocked_repeated_finding`.

Thus a persistent finding receives at most two Sol-guided corrections, after its initial ordinary correction. The block message identifies the finding key and states that it still fails after two Sol-guided corrections.

Sol must return exactly one of:

```text
GUIDANCE: <bounded root-cause diagnosis and correction guidance>
POLICY_AMBIGUITY: <reason authority is required>
```

Guidance is stored and inserted into the next Luna correction prompt. A Sol policy response routes to Terra and then to the human.

### Global correction budget

`MAX_TOTAL_CORRECTIONS` is currently 12 per run. It counts ordinary corrections, Sol-guided corrections, and post-policy implementation corrections. It does not count the initial implementation.

The per-finding two-Sol limit prevents one persistent defect from cycling forever. The 12-correction global budget is an independent emergency ceiling for a sequence of distinct defects. Reaching it blocks the run as `blocked_correction_budget`.

## 10. Policy ambiguity and auditable human decisions

The controller never treats a model recommendation as policy approval.

When policy ambiguity is identified:

1. The run enters `terra_resolving` and saves Terra's exact prompt.
2. Terra receives the ambiguity reason and authoritative task prompt in a read-only sandbox.
3. Terra is asked to identify the precise missing decision and propose a narrowly worded resolution, not to decide whether work proceeds.
4. The recommendation is persisted as `terra_resolution`.
5. The run blocks at `blocked_policy_ambiguity`.
6. A human uses `approve-policy`, reviews the recommendation and proposed text, and confirms approval. Confirmation defaults to no.
7. The controller persists the policy decision before invoking any provider again.

If `--decision` is omitted, the CLI can extract quoted lines following Terra's literal `Proposed approval text:` marker. If that narrow format is absent, the human must provide exact text explicitly.

Each `PolicyDecision` records:

- sequential ID such as `policy-01`;
- UTC approval timestamp;
- approver, fixed to `human`;
- source provider label;
- triggering finding key when available;
- trigger summary;
- Terra's full recommendation; and
- exact approved text.

The Pydantic decision object is frozen after creation. Approved decisions are appended to the run and included as authoritative context in future review and implementation prompts.

For specification ambiguity, approval returns the run to `created` so the augmented specification is reviewed again. For implementation ambiguity, approval schedules a correction and consumes one unit of the global correction budget. Policy approval explicitly does not approve a commit, push, or merge.

## 11. Provider safety boundaries

### Sonnet: read-only structured QA

The Claude CLI is invoked with:

- model `sonnet`;
- permission mode `plan`;
- tools limited to `Read,Glob,Grep`;
- JSON output; and
- a closed JSON schema.

Write, edit, and shell tools are not granted.

### Terra and Sol: read-only authority and diagnosis

Both use `codex exec` with explicitly selected models and `--sandbox read-only`. Their prompts also prohibit file edits and Git mutations.

### Luna: bounded workspace writer

Luna uses `--sandbox workspace-write`, `approval_policy=never`, and `sandbox_workspace_write.network_access=false`. Every Luna prompt is prefixed with Git prohibitions and a statement that the controller alone has Git authority.

This separation is enforced through provider CLI configuration plus prompt constraints. The current engine does not independently inspect process system calls or maintain a configurable filesystem path allowlist beyond the provider workspace sandbox and repository working directory.

### Controller-only Git

The controller invokes Git directly for inspection and, only after approval, staging, commit, and push. Reviewers and implementers are not delegated Git authority.

## 12. Provider failure and retry policy

The provider boundary classifies nonzero exits deterministically using conservative output patterns:

- `quota`;
- `billing`;
- `auth`;
- `rate_limit`;
- `unavailable`;
- `timeout`;
- `interrupted`;
- `configuration`; or
- `provider_error` for an unclassified failure.

### Account and access failures are hard workflow stops

Quota/usage-cap, billing, authentication, and rate-limit failures stop the entire current workflow run immediately. Configuration and unclassified provider failures do the same. The run keeps the exact provider stage and prompt needed for a later explicit resume.

“Global stop” here means no other role continues the current run and no alternate provider is invoked. It does **not** currently mean a shared circuit breaker that pauses other independently running run processes.

### Same-provider outage retries only

Only failures classified as `unavailable` are retried automatically. The delays are fixed at 5 seconds and 15 seconds, for at most three total attempts. Every attempt is recorded separately, including whether another retry was scheduled.

There is no automatic cross-provider fallback. A Sonnet outage is retried as Sonnet; the controller will not substitute Terra, Sol, Luna, or another vendor. After the bounded attempts, the run blocks as `blocked_provider_unavailable`.

This preserves role semantics, avoids permission drift, and prevents an infrastructure problem from silently changing the review or implementation policy.

### Timeout and interruption stops

Read-only provider operations have a 30-minute hard deadline and workspace-writer operations have a 60-minute hard deadline. Real subprocesses run in isolated process groups. On deadline or operator interruption, the controller sends TERM, allows a five-second grace period, escalates to KILL when necessary, reaps the direct child, and captures available output plus a cleanup diagnostic.

`timeout` and `interrupted` are terminal attempt outcomes. They never enter the ordinary unavailability retry and block as `blocked_provider_timeout` or `blocked_provider_interrupted`. Those blocks are deliberately not resumable until capability-aware writer reconciliation is implemented.

### Invalid structured output

Valid transport does not imply valid protocol output. Malformed Sonnet review output or malformed Sol escalation output receives one same-provider structured-output retry. A second parse failure blocks as `blocked_provider_output`. No other role is substituted.

## 13. Crash recovery and exact-stage resume

Before a provider invocation, the controller:

1. sets the precise in-progress stage;
2. saves `provider_resume_stage`;
3. saves `provider_resume_prompt`; and
4. persists the run.

After the provider returns, it appends one or more `ProviderRecord` entries and persists again before parsing or advancing.

On resume:

- a successfully recorded result at an in-progress stage is consumed without re-running that completed provider call;
- the latest record must match the provider and purpose expected for the saved stage;
- a stage with no matching recorded output blocks as `blocked_interrupted_provider` rather than guessing;
- a provider-failure block with saved stage and prompt re-invokes only that exact provider stage when explicitly resumed; and
- a successful resumed call continues from that stage without repeating the earlier implementation or review steps.

This is exact-stage recovery, not transaction rollback. Provider-side effects cannot be rolled back by the controller. Repository fingerprints and verification provide the complementary check for Luna's workspace changes.

## 14. Repository snapshot and fingerprint safety

### Initial snapshot

Every run records:

- absolute repository path;
- named branch;
- original `HEAD` commit;
- initial cleanliness; and
- origin URL.

Implementation cannot start from a dirty repository.

### Post-write verification

After each successful Luna call, the controller verifies that:

- branch still matches the snapshot;
- `HEAD` still matches the snapshot before controller commit;
- origin still matches;
- `git diff --check` passes; and
- at least one changed file exists.

It records the changed-file list and a SHA-256 working-tree fingerprint. The fingerprint covers porcelain status, unstaged binary diff, staged binary diff, every enumerated path identity, and the bytes of changed paths that exist as files. This includes readable untracked files and retains deleted or renamed source paths by identity.

### Resume guard

Before approval, policy mutation, or resumption, the controller checks the saved repository identity again:

- path, branch, and origin must match;
- before commit, `HEAD` must remain the original snapshot;
- after commit, `HEAD` must equal the controller-recorded commit and the tree must be clean;
- when a working-tree fingerprint exists, it must still match; and
- early pre-implementation stages require a clean tree.

A mismatch refuses resume with a controller error or blocks verification as `blocked_unexpected_repo_state`. The controller does not reset, discard, or overwrite unexpected user changes.

## 15. Verification

The implemented verification layer is deliberately lightweight and deterministic. It currently verifies repository identity, changed-file existence, and Git diff hygiene as described above.

It does **not** currently discover or run Jobs tests, linters, type checks, builds, security scans, or task-specific acceptance checks. Correctness is presently evaluated by Sonnet against the specification and diff after the repository checks pass.

That boundary matters when interpreting reports: the current `verification_runs` metric is derived from successful Luna implementation/correction calls, which correspond to paths that proceed into controller verification. It is not a count of project test-suite executions.

Pluggable verification is therefore one of the highest-priority generalization items.

## 16. Git approval gates

Git mutation is separated into explicit human gates.

### Commit gate

After Sonnet passes the implementation:

1. the controller enters `awaiting_commit_approval`;
2. prints the run report, changed files, and review summary;
3. asks `Commit these changes?`, defaulting to no;
4. rechecks repository safety after approval;
5. stages a safe projection of the recorded changed-file paths with `git add -A -- ...`, omitting only absent paths whose deletion is already represented in the index;
6. commits with `Implement task <task-ref>`; and
7. records Git commands, outputs, result codes, commit hash, and message.

A no becomes `commit_declined`, which is resumable back into the same gate. A Git failure becomes `blocked_git_failure`.

### Push gate

After commit, the controller separately asks `Push this commit to origin?`, also defaulting to no. Approval triggers another resume guard and pushes the saved branch to `origin`. A no becomes `push_declined`.

A successful push ends at `pushed_awaiting_merge`. Merge is a separate manual or future gate and is never performed by the current controller.

## 17. Persistence, schema, and audit records

Runs are stored as formatted JSON in `runs/<run-id>.json`. The run ID is the first 12 hexadecimal characters of a UUID4. Saves update a UTC timestamp and use a temporary file plus atomic replacement to reduce partial-write risk.

`WorkflowRun` currently uses schema version 6 and forbids unknown top-level fields. Its state includes:

- identity and timestamps;
- task reference, filename, SHA-256, and full specification;
- initial repository snapshot;
- current stage and last error;
- correction count;
- latest specification and implementation reviews;
- Terra resolution and Sol guidance;
- appended human policy decisions;
- provider resume stage and exact prompt;
- changed files and working-tree fingerprint;
- verification results;
- complete provider-attempt records;
- Git operation records; and
- commit hash and message.

Each provider record includes provider label, purpose, full command, return code, stdout, stderr, duration, failure classification, and whether another same-provider retry was scheduled. Because prompts are command arguments, the persisted command is also an input audit record.

Git records include the operation label, command, return code, stdout, and stderr.

Persistence is local and inspectable, but it is not yet a tamper-evident event log, database transaction system, concurrent-run lock, encrypted secret store, or formal schema-migration framework.

## 18. Observability, heartbeats, reporting, and metrics

### Live provider visibility

For real subprocess calls, the provider runner prints:

- role start;
- retry number when applicable;
- a heartbeat every five seconds while still running;
- completion duration and exit code; and
- scheduled same-provider retry delay.

The heartbeat indicates liveness within the configured hard deadline; it does not extend the deadline. Timeout and interruption cleanup outcomes are included in the persisted attempt record.

### Run report

`report <run-id>` derives a concise audit summary from persisted state:

- wall-clock span between creation and latest update;
- total provider attempts;
- attempts and accumulated recorded duration per provider;
- legacy attempts without timing;
- total provider time;
- provider/infrastructure failures by classification;
- number of same-provider retries;
- total corrections;
- distinct non-policy defect identities found in parseable review history;
- Sol escalation count;
- verification-run count as currently defined;
- human policy-decision count and abbreviated approved text;
- final review status;
- commit status and hash; and
- push status.

Wall-clock time and summed provider time answer different questions and may overlap conceptually with local processing. The report does not yet aggregate cost, tokens, concurrency, queue time, or statistics across multiple runs.

### Status inspection

`status <run-id>` prints the complete saved JSON. `status` without an ID lists the ten most recently modified run files with run ID, task reference, stage, correction count, and policy-decision count. Invalid JSON files are shown as invalid rather than crashing the listing.

## 19. CLI reference

The documented direct form is:

```sh
uv run python orchestrator.py <command>
```

An installed `jobs-orchestrator` entry point exposes the same Typer application.

### Start a run

```sh
uv run python orchestrator.py run 009
uv run python orchestrator.py run 009 --repo /path/to/jobs
```

Creates, persists, and advances a new run until it completes a gate or reaches a block.

### Resume a run

```sh
uv run python orchestrator.py resume <run-id>
```

Loads the saved repository path unless `--repo` is explicitly supplied, applies the resume guard, and continues only from the saved stage.

### Approve a policy decision

```sh
uv run python orchestrator.py approve-policy <run-id>
uv run python orchestrator.py approve-policy <run-id> \
  --decision "Exact human-approved policy text."
```

Only valid at `blocked_policy_ambiguity`. It displays Terra's recommendation and the exact text, asks for human confirmation, persists the decision, and then resumes. It does not imply Git approval.

### Show a report

```sh
uv run python orchestrator.py report <run-id>
```

Prints derived timing, provider, correction, policy, review, and Git metrics.

### Inspect status

```sh
uv run python orchestrator.py status
uv run python orchestrator.py status <run-id>
```

Lists recent runs or prints one run's complete JSON.

## 20. Current tests and what they cover

The current suite has 44 `unittest` cases. It creates isolated temporary Git repositories, substitutes deterministic fake provider functions, and uses real local Python child/grandchild processes for supervisor tests; it does not call live model providers or mutate the real Jobs repository.

### Preflight and repository safety

- dirty repository blocks a new run and still persists state;
- ambiguous task glob resolution fails;
- branch/origin snapshot mismatch refuses resume.

### Core flow and approvals

- a passing spec and implementation reaches the commit gate;
- commit confirmation defaults to no;
- push confirmation is separate and defaults to no.

### Policy ambiguity

- Sonnet policy ambiguity calls Terra and stops before Luna;
- a human policy approval is fully recorded and propagated into the correction prompt;
- Terra's quoted proposed approval text can be extracted.

### Finding identity and corrections

- genuinely new findings receive ordinary corrections without unnecessary Sol escalation;
- one persistent finding gets exactly two Sol escalations/two Sol-guided corrections and then blocks;
- twelve distinct corrections exhaust the global budget;
- eligible legacy one-correction blocks resume under the current policy;
- an eligible legacy post-escalation run with a genuinely new finding receives a normal correction rather than being treated as the old defect.

### Provider safety and observability

- timing and run-report metrics are derived safely, including legacy untimed records;
- quota failure blocks the whole run and explicit resume retries only the interrupted review stage;
- unavailability retries only the same provider, with per-attempt audit fields;
- real provider processes enforce deadlines, preserve partial output, and clean up process groups through TERM/KILL escalation;
- timeout and interruption persist distinct non-resumable blocked stages without provider retry;
- quota, billing, auth, rate-limit, unavailable, and configuration classifications are deterministic for representative messages;
- malformed Sonnet output receives one same-provider retry;
- provider command construction preserves Sonnet read-only tools, Sol read-only sandboxing, and Luna workspace/network/Git restrictions.

### Not covered today

There are no live-provider integration tests, project-specific verification tests, full CLI end-to-end tests, concurrency tests, persistence-corruption recovery tests beyond status display, schema-migration tests, actual remote push tests, Windows process-tree integration tests, or tests for every blocked/recovery stage combination.

## 21. Current limitations and technical debt

The following are current constraints, not implemented capabilities:

### Hard-coded roles, models, and providers

- model IDs, CLI names, provider labels, and sandbox flags are embedded in code;
- recovery maps and reporting know literal names such as `Luna High` and `Sonnet 5 High`;
- prompt text also names roles directly;
- provider capability differences are assumed rather than declared.

This is the most important generalization debt. Model/provider names must be abstracted behind role configuration and provider adapters so model upgrades do not require orchestration-policy edits.

### Jobs-specific repository and task conventions

- the default path points to the Jobs checkout;
- tasks must be local Markdown files under `tasks/` with a fixed filename convention;
- the default commit message is Jobs-task oriented;
- only Git repositories with an `origin` and named branch are supported;
- there is no project manifest describing allowed scope or commands.

### Verification gaps

- no tests, lint, type checks, builds, security checks, or acceptance commands run;
- no task-specific verification plan is parsed from the spec;
- scope compliance depends on Sonnet review rather than a deterministic path policy;
- `verification_runs` is currently a proxy derived from successful Luna calls, not explicit plugin executions.

### State-machine and persistence debt

- stages are free-form strings rather than an enum with an explicit transition table;
- orchestration is concentrated in one large controller module;
- JSON schema versioning exists, but there is no migration machinery;
- mutable run JSON is not signed or tamper-evident;
- there is no run lock, scheduler, queue, or enforced single-active-run invariant;
- full prompts and outputs can make run files large and may retain sensitive content;
- there is no retention or redaction policy.

### Provider protocol debt

- failure classification relies on output substring/regex matching;
- retry delays and limits are hard-coded;
- timeout/interruption enforcement exists, but deadline values are hard-coded and token, cost, and context-size ceilings are not enforced;
- Sonnet uses JSON Schema, while Sol and Terra rely on textual conventions;
- provider health is local to one invocation; there is no cross-run circuit breaker;
- there is intentionally no fallback, but there is not yet a configuration model for explicitly approved equivalent routes.

### Operational and reporting gaps

- reports cover one run at a time;
- no cross-run trends, success rates, cost, task throughput, defect recurrence, or provider reliability dashboards exist;
- no event streaming, structured log sink, notifications, or alerting exists;
- merge, pull-request creation, rollback, and deployment are outside the engine;
- the external ChatGPT conversational-orchestrator layer has no formal API or persisted handoff protocol.

## 22. Future-generalization architecture

The safest evolution is to preserve the current authority model while replacing Jobs- and model-specific details with explicit interfaces.

### 22.1 Separate orchestration policy from routing configuration

Introduce stable role IDs:

- `implementation`;
- `adversarial_review`;
- `escalation_executive`;
- `policy_authority`; and optionally
- `conversation_orchestrator` as an external integration role.

The controller should depend on role contracts, never model names. Configuration should map each role to a provider adapter, model ID, capability profile, and invocation settings. Changing from one model generation to another would then be a configuration change with compatibility validation, not a workflow-code change.

### 22.2 Provider adapters

Define a common provider interface for:

- command or API invocation;
- structured-output mechanism;
- sandbox/tool permissions;
- provider-native error normalization;
- attempt telemetry;
- cancellation and timeout;
- token/cost accounting; and
- capability discovery.

Adapters could support Codex CLI, Claude CLI, hosted APIs, or future providers without leaking CLI-specific flags into the controller.

Cross-provider fallback should remain disabled by default. If ever supported, it should require an explicit policy that proves role equivalence, required capabilities, permission parity, and human acceptance of the semantic change.

### 22.3 Provider capability profiles

Each configured route should declare machine-checkable capabilities, for example:

- filesystem access: none, read-only, or workspace-write;
- allowed tools;
- network access;
- structured-output support;
- context limits;
- streaming/heartbeat support;
- supported timeout/cancellation behavior; and
- whether the route may ever mutate repository state.

Controller startup should reject a route whose capabilities exceed or fail to meet the role policy. This makes least authority an invariant rather than a collection of command-line assumptions.

### 22.4 Project and repository adapters

Move Git/Jobs assumptions behind a `ProjectAdapter` or `RepositoryAdapter` that can provide:

- workspace identity and clean-state rules;
- snapshot and fingerprint implementation;
- change enumeration and allowed-path validation;
- diff production;
- branch/remote policy;
- commit-message policy;
- approval-gated publication operations; and
- optional pull-request or merge integrations.

The existing Git behavior can become the first adapter. Other projects could customize monorepo scope, multiple repositories, non-Git workspaces, generated files, or publication flows without changing correction policy.

### 22.5 Task-spec adapters

Replace `tasks/<ref>-*.md` with a `TaskSpecAdapter` contract that returns a normalized immutable task envelope:

- source identity and revision;
- canonical specification text;
- checksum;
- scope constraints;
- acceptance criteria;
- optional verification requests; and
- provenance metadata.

Adapters might load local Markdown, GitHub issues, Linear tasks, structured YAML, a database record, or a composed multi-file spec. The normalized envelope—not the source system—should feed orchestration.

### 22.6 Verification plugins

Create a plugin pipeline with named, deterministic checks and explicit results. Example classes include:

- repository/diff hygiene (the current built-in check);
- unit and integration tests;
- lint and formatting;
- type checking;
- build/package validation;
- security and dependency checks;
- task-specific acceptance scripts; and
- policy-defined changed-path allowlists.

Each plugin should declare its command, timeout, working directory, environment/network requirements, failure severity, produced artifacts, and whether it is safe to rerun. Results should be persisted individually and supplied to the reviewer.

### 22.7 Configurable escalation policy

Represent correction policy as validated configuration while preserving safe defaults:

- ordinary corrections per new finding;
- Sol-equivalent escalation rounds per repeated finding;
- global correction budget;
- whether scope violations follow defect or immediate-human paths;
- policy-ambiguity routing;
- structured-output retry policy;
- provider unavailability retry/backoff; and
- mandatory human stops.

The controller should persist the resolved policy and its version at run creation so a resumed run cannot silently inherit changed rules.

### 22.8 Explicit state machine and event log

Replace free-form stage manipulation with:

- a stage enum;
- a declared transition graph;
- typed transition causes;
- idempotency keys for provider and Git operations;
- append-only events plus a materialized current state; and
- schema migrations.

An event log would improve crash recovery, audit, replay, and reporting. Tamper evidence, redaction, retention, and concurrent-run locking should be designed together rather than added piecemeal.

### 22.9 Reporting across runs

Build aggregate reporting from normalized events and attempt records:

- pass rate and time-to-pass by project/task type;
- corrections and repeated findings by stable defect family;
- provider reliability, outage, rate-limit, and invalid-output rates;
- model/provider duration, tokens, and cost by abstract role;
- policy ambiguity frequency and approval latency;
- verification failure distribution;
- approval-to-commit/push conversion; and
- resumptions, interrupted stages, and human-block dwell time.

Role-based reporting is important: historical trends should survive a model change.

### 22.10 Configuration files

Add a versioned project configuration file, with user- or environment-level secrets kept separate. A future configuration could describe:

- project/repository adapter;
- task-spec adapter;
- abstract role routes;
- provider capability profiles;
- sandbox and tool constraints;
- verification plugins;
- retry and escalation policy;
- Git approval/publication policy;
- persistence backend; and
- reporting/redaction settings.

Configuration should be validated at startup, normalized, hashed, and stored with each run. The exact schema should be designed after extracting interfaces from current behavior; the current hard-coded values provide the baseline defaults.

## 23. Recommended roadmap

### Phase 1: stabilize the current contract

- Convert stage strings, provider purposes, failure kinds, and role IDs to typed enums.
- Add a transition table and tests for every allowed resume/block path.
- Persist a resolved orchestration-policy version with each run.
- Add provider timeouts/cancellation and redaction rules.
- Correct or rename metrics whose current labels are only proxies.
- Expand recovery and malformed-output coverage.

### Phase 2: extract adapters without changing behavior

- Introduce role-based provider interfaces and move all model/CLI names into configuration.
- Add provider capability profiles and startup validation.
- Extract the current Git behavior as the first repository adapter.
- Extract `tasks/<ref>-*.md` as the first task-spec adapter.
- Keep the current no-fallback policy and approval gates unchanged.

### Phase 3: make correctness project-aware

- Add verification plugins and persist each result.
- Let projects configure deterministic acceptance checks and path scope.
- Feed verification evidence, not just diffs, into adversarial review.
- Add plugin safety declarations, timeouts, and reproducible environments.

### Phase 4: productionize state and audit

- Introduce append-only events, idempotency, locking, and migrations.
- Support a durable persistence backend while retaining exportable JSON.
- Add cross-run provider circuit breakers for account-wide failures.
- Add notifications for human policy and Git gates.
- Formalize the conversational-orchestrator handoff as an external API or event consumer.

### Phase 5: multi-project operations

- Add cross-run and cross-project reporting by abstract role and policy version.
- Support configured repository/task adapters per project.
- Add optional PR and merge workflows as new, independently approved gates.
- Validate model substitutions against capability profiles before activation.

## 24. Invariants to preserve during generalization

Future flexibility should not weaken the properties that make the current controller safe:

- workflow decisions remain deterministic and inspectable;
- model/provider identity remains separate from role policy;
- a writer cannot gain Git authority implicitly;
- reviewers and policy advisers remain read-only;
- policy recommendations require explicit human approval;
- commit, push, and merge remain separate authorities;
- provider failure never silently changes roles;
- retries are bounded and audited;
- repeated-defect and global budgets remain explicit;
- repository changes are fingerprinted and checked before resume;
- completed stages are not intentionally repeated after recovery; and
- every consequential transition is persisted with enough context to explain it.

Those invariants—not the current Jobs path or today's model names—are the reusable orchestration engine.
