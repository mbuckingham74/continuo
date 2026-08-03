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

Detailed bounded contracts, adversarial matrices, and durable implementation
evidence live in [`contracts/`](contracts/README.md); this tracker keeps only
gate status, concise outcomes, and links.

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
  - Detailed contract, matrix, and evidence: [`gate-1/m0.1-c1-git-porcelain.md`](contracts/gate-1/m0.1-c1-git-porcelain.md).

## Gate 1 — Stabilize the existing engine (roadmap Milestone 0)

**Goal:** make the current implementation safe enough to serve as the behavioral
baseline for extraction. No live Jobs pilot occurs in this gate.

- [x] **M0.1 / C-1:** parse Git changes using verified NUL-delimited porcelain
  semantics; cover Unicode, spaces, quotes, literal ` -> ` names, and rename/copy
  field ordering.
  - Detailed contract, implementation evidence, and matrix: [`gate-1/m0.1-c1-git-porcelain.md`](contracts/gate-1/m0.1-c1-git-porcelain.md).

- [x] **M0.2 / C-2:** add provider deadlines, cancellation, process-group cleanup,
  partial-output capture, and real child-process failure tests.
  - Detailed contract, implementation evidence, and matrix: [`gate-1/m0.2-c2-provider-deadlines.md`](contracts/gate-1/m0.2-c2-provider-deadlines.md).

- [x] **M0.3 / C-3/C-4:** normalize failure evidence sources and distinguish Claude
  transport/envelope errors from invalid review content using recorded fixtures.
  - Detailed contract, implementation evidence, and matrix: [`gate-1/m0.3-c3-c4-failure-classification.md`](contracts/gate-1/m0.3-c3-c4-failure-classification.md).

- [x] **M0.4 / A-1:** make the provider-attempt lifecycle capability-aware; allow
  bounded read-only retry while blocking uncertain write-capable recovery when
  partial changes exist.
  - Detailed contract, implementation evidence, and matrix: [`gate-1/m0.4-a1-writer-recovery.md`](contracts/gate-1/m0.4-a1-writer-recovery.md).

- [x] **M0.5 / Q-2:** enforce one active run per canonical target and test clean
  release, crash recovery, stale ownership, and approval-pending ownership.
  - Detailed contract, implementation evidence, and matrix: [`gate-1/m0.5-q2-target-ownership.md`](contracts/gate-1/m0.5-q2-target-ownership.md).

- [x] **M0.6 / Q-6:** make run storage private by default and define explicit
  handling for legacy files, redaction, retention, and export.
  - Detailed contract, implementation evidence, and matrix: [`gate-1/m0.6-q6-private-storage.md`](contracts/gate-1/m0.6-q6-private-storage.md).

## Gate 2 — Stabilize persisted contracts (roadmap Milestone 1)

**Goal:** make saved state safe to evolve before adding generic configuration or
renaming control identities.

- [x] Inventory historical run schemas and decide migrate/archive/unsupported
  treatment for each known class.
  - Detailed contract, implementation evidence, and matrix: [`gate-2/gate-2.1-c9-schema-inventory.md`](contracts/gate-2/gate-2.1-c9-schema-inventory.md).

- [x] Add explicit current-schema constants, stepwise migrations, rollback tests,
  and visible failure reporting.
  - Detailed contract, implementation evidence, and matrix: [`gate-2/gate-2.2-c9-schema-migration.md`](contracts/gate-2/gate-2.2-c9-schema-migration.md).

- [x] Replace display labels in control flow with separate stable role, provider
  adapter, route, and model/display identities.
  - Detailed contract, implementation evidence, and matrix: [`gate-2/gate-2.3-c5-stable-identities.md`](contracts/gate-2/gate-2.3-c5-stable-identities.md).

- [x] Persist immutable parsed review records linked to raw attempts; make
  unreadable legacy history visible and non-silent.
  - Detailed contract, implementation evidence, and matrix: [`gate-2/gate-2.4-c6-parsed-review-history.md`](contracts/gate-2/gate-2.4-c6-parsed-review-history.md).

- [~] Persist the resolved correction and escalation policy with each run.
  - Uncommitted implementation evidence and approval contract: [`gate-2/gate-2.5-c9-correction-policy.md`](contracts/gate-2/gate-2.5-c9-correction-policy.md).

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
