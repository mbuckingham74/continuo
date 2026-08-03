# Gate 2.6 / Q-3 immutable approval-record contract (draft)

**Authority.** [ENGINE_ROADMAP.md](../../ENGINE_ROADMAP.md) remains authoritative. This document contains the bounded contract, adversarial matrix, and implementation evidence for its tracker entry.

**Tracker.** See [EXECUTION_PLAN.md](../../EXECUTION_PLAN.md) for gate status, sequencing, and links to every contract.

## Planning evidence

- Planning evidence (2026-08-02): this draft is derived from roadmap [Q-3](../../ENGINE_ROADMAP.md#q-3--approvals-not-recorded), Milestone 1 item 5 and exit criteria, the later [E-3 approval boundary](../../ENGINE_ROADMAP.md#e-3--asynchronous-approval-gates), and the current `PolicyDecision`, `_policy_stop()`, `approve_policy()`, `_approval_gates()`, `_resume_guard()`, coordination, persistence, migration, CLI, report, and deterministic-test paths. Baseline: clean `main` aligned with `origin/main` at `c828ff4fb690c6c100b2efdfbb6cb1df42d7ae41`.
- Documentation-only validation (2026-08-02): all referenced local Markdown file targets resolve, all 34 `G26-*` matrix IDs are unique, and `git diff --check` passes. No runtime source, test, fixture, run record, provider, target checkout, Git side effect, commit, push, or later Gate 2 item changes in this planning diff.

## Status and boundary

This is the next unchecked Gate 2 item after published Gate 2.5. It makes policy, commit, and push approval a durable append-only fact: a request captures the protected repository state before input is collected, and a decision captures actor, exact text, time, and observed state before the controller can act.

This is not asynchronous operation. The current process retains target ownership across approval-pending stages and the CLI stays synchronous. No UI/API, queue, notification, process-independent command, expiry, revocation, remote authentication, role management, event store, or direct run-file writer is added. E-3 may add those only after this record, locking, and migration boundary is proven.

## Invariant and current evidence

At `c828ff4`, policy approval appends `PolicyDecision(approved_by="human")` without an auditable actor or repository fingerprints. Commit and push call an injected boolean then perform Git; a decline changes only `stage`. `_resume_guard()` runs before the gates and after an affirmative answer, but it does not preserve the request, answer, actor, time, decision text, requested state, or decision state.

No policy continuation, `git commit`, or `git push` may be authorized by an unrecorded boolean, a decision for another request/gate, or a decision whose protected repository fingerprint differs from its request. A current run exposes ordered immutable records; a crash cannot turn an affirmative response into untraceable Git authority; and a changed repository remains blocked by the existing resume guard rather than receiving an implicit fresh approval.

## Schema-11 model and identity contract

This structural change increments `CURRENT_RUN_SCHEMA_VERSION` from 10 to 11. Ordinary schema-11 runs have empty `approval_requests` and `approval_decisions`, with no migration audit. These are strict, frozen, extra-forbid models and controller write paths append but never rewrite them.

| Model | Immutable fields | Meaning |
|---|---|---|
| `ApprovalRequest` | `request_id`, `gate`, `requested_at`, `requested_stage`, `requested_fingerprint` | Controller-created request for `policy`, `commit`, or `push`, persisted before input. |
| `ApprovalDecision` | `decision_id`, `request_id`, `gate`, `decided_at`, `decided_by`, `decision`, `decision_text`, `decided_fingerprint`, optional `policy_decision_id` | Explicit `approved` or `declined` answer to exactly one request. |
| `ApprovalMigrationAudit` | migration lineage, `target_schema_version = 11`, reason `missing_approval_audit_history`, inherited disposition | Proof that a migrated V10 record had no approval-audit vocabulary. |

`gate` is the closed literal vocabulary `policy`, `commit`, `push`; it is never a stage, prompt, provider role, or display label. IDs are controller-generated bounded opaque values, never provider/CLI input. Timestamps use the existing UTC ISO-8601 string convention. Decision text is required, non-empty, bounded human text: policy uses the exact approved policy text; commit/push use bounded controller-owned approval/decline text. It cannot grant new scope or embed provider output.

`decided_by` is the local principal `local-os-uid:<effective-uid>`. It identifies the account executing the local CLI, not a proven natural person, authorization role, or remote identity. Tests inject a bounded synthetic principal. A future authenticated/API identity needs a new vocabulary and contract.

Both fingerprint fields are 64-character SHA-256 values from one controller helper over a canonical delimiter-safe encoding of repository identity (`repo`, `branch`, `head`, `origin`, `clean`) plus the existing complete working-tree fingerprint. It distinguishes clean pre- and post-commit states with different HEADs. It is controller evidence computed only for the configured target under the target lock, never input from a provider or operator.

Validation rejects duplicate request/decision IDs, missing request, gate mismatch, more than one decision for a request, malformed fingerprint/actor/time, empty or oversized text, extra fields, or an invalid migration-audit combination. At most one unanswered request per gate exists. An approved policy decision links exactly one existing `PolicyDecision` and has equal approved text; commit/push decisions never link one.

## Controller write, authority, and read contract

The controller persists a request in the same snapshot that first reaches the gate:

- `_policy_stop()` creates `policy` after successful Terra advice and before exposing `blocked_policy_ambiguity`;
- `_review_and_correct()` creates `commit` when a PASS reaches `awaiting_commit_approval`; and
- after a saved successful commit, `_approval_gates()` creates `push` at `awaiting_push_approval`.

Resume reuses the exact unanswered request at the same gate/stage/fingerprint and never duplicates it. A decline remains immutable, retains the existing declined/blocked stage, and a later deliberate retry creates a fresh request. If the state no longer matches before input, the controller accepts no answer and creates no replacement; `_resume_guard()` fails closed.

The confirmation seam returns a deterministic response plus bounded text. Default CLI prompts remain Typer `default=False` and use controller-owned text. Root and installed command names remain. `approve-policy` retains `--decision`/Terra-proposal behavior, but persists an approved or declined decision before returning/continuing. The existing `approve_policy()` API remains a compatible approved wrapper; an internal explicit-decision path handles both outcomes. Recording an answer never invokes a provider.

With the coordinator lock held, the controller recomputes the protected fingerprint after input. It appends/saves a decision only when it equals the request fingerprint, then calls `_resume_guard()` immediately before policy continuation, `git commit`, and `git push`. A matching approved decision is the sole durable authority for that transition. A crash after it saves but before the side effect resumes using it without re-prompting; a crash after an ambiguous Git side effect remains blocked by the existing HEAD/fingerprint guard and is never retried automatically.

Policy appends its matching `ApprovalDecision`, then its linked existing `PolicyDecision`, before correction/provider work. Commit approval persists before stage/add/commit; push approval persists before push. Decline never stages, commits, pushes, invokes a provider, or releases ownership. Retry/deadline/capability, correction policy, review history, writer recovery, verification, Git hooks, ownership, privacy, and merge authority are unchanged.

Sensitive `status` exposes records under its existing warning. `report` adds concise request/approved/declined counts plus latest gate/actor/time/fingerprint prefix; it does not change any provider-call, retry, verification, correction, Sol, or policy metric. No stable `--json` contract is added.

## Migration, failures, and recovery

Add an exact historical `_RunV10` validator and one pure `10_to_11` transform. V10 retains literal schema-10 policy/audit validation; `1_to_2` through `9_to_10` remain unchanged. The transform preserves each supplied V10 value and prior audit in decoded form, appends empty approval lists plus `ApprovalMigrationAudit`, and never infers a past request, response, actor, fingerprint, timestamp, policy, Git action, eligibility, or stage.

V1--V10 records migrate only through the existing explicit, default-no-confirmation, private atomic compare-and-swap command to schema-11 records with inherited execution-refused disposition. Direct V10 uses only `10_to_11`; earlier records retain all prior audit lineages and gain the full approval-audit step prefix ending `10_to_11`. Ordinary V10 runs are deliberately neither auto-upgraded nor resumable: their missing audit history remains visible after explicit migration. Current V11 records are never rewritten.

Malformed source/final records, audit-lineage conflict, invalid approval-like V10 extras, temp-write failure, source change, concurrency, or crash before replacement preserves original bytes under the existing atomicity contract. There is no reverse/bulk migration, repair/backfill, edit/delete/revoke command, or eligibility override. Invalid V11 approval evidence fails classification before coordination, providers, continuation, Git, report-as-current, or status-as-current.

## Adversarial test matrix

Tests use only synthetic V1--V10 records, temporary private run directories/repositories, injected clocks/IDs/actors/responses, fake providers, and local subprocesses. They do not access Jobs, invoke live providers, use external targets, or push.

| ID | Fixture / event | Required assertions |
|---|---|---|
| G26-I1 | Static inspection | Schema 11; exact frozen models/vocabularies; no boolean can authorize policy, commit, or push without saved evidence. |
| G26-I2 | Bad gate/decision/ID/fingerprint/actor/time/text/link or duplicate/missing/mismatched records | Validation fails closed before every side effect. |
| G26-I3 | Mutate historic request, decision, or audit | Freezing rejects it; controller only appends. |
| G26-I4 | Rename routes/models/displays or inject provider/task prose | Presentation/provider data gains no approval authority. |
| G26-W1 | Terra policy ambiguity | Saved policy request precedes blocked stage; no decision/correction/continuation yet. |
| G26-W2 | Implementation PASS | Saved commit request precedes prompt and all Git. |
| G26-W3 | Approved commit | Decision precedes stage/add/commit; saved push request has post-commit HEAD-bound fingerprint. |
| G26-W4 | Approve policy, commit, push | Each has one matching actor/text/time/fingerprint decision; policy links its `PolicyDecision`. |
| G26-W5 | Decline policy, commit, push | Each has one decline; existing blocked/declined stage remains; no side effect. |
| G26-W6 | Retry a declined unchanged gate | Fresh request/decision pair appends; historic values remain intact. |
| G26-W7 | Resume unanswered unchanged gate | Same request is reused once; no duplicate prompt/request. |
| G26-R1 | Branch/origin/HEAD/index/diff/untracked/path-byte change before prompt | Guard fails; no answer/replacement/side effect. |
| G26-R2 | State changes after prompt before persistence | Recomputed fingerprint rejects the answer; no decision/side effect. |
| G26-R3 | Crash after request save | Resume reuses request and prompts once; no fabricated result. |
| G26-R4 | Crash after approved decision before continuation/add/commit/push | Resume consumes exact approval once under fresh guard. |
| G26-R5 | Crash during/after Git before conclusive save | Existing guard blocks; no automatic repeat or second approval. |
| G26-R6 | Crash after decline | Exact state round-trips; normal retry creates a fresh request. |
| G26-M1 | Registry inspection | Exact V10 model; one `10_to_11`; older literal steps unchanged. |
| G26-M2 | V10 at created/policy/commit/push/declined/completed stages | Values preserve; empty lists plus missing-history audit; execution refused. |
| G26-M3 | V1--V9 migration chain | Prior audits preserve; full approval lineage ends `10_to_11`; no invented approval facts. |
| G26-M4 | Malformed V10 or approval-like unknown data | Archive/refuse before transform; no normalization/inference. |
| G26-M5 | Current/migrated V11 and transform/CAS/concurrency/crash failures | No rewrite; atomic rollback and idempotency hold. |
| G26-A1 | Current V11 status/report | Sensitive records visible; concise approval audit; old metrics unchanged. |
| G26-A2 | Read/mutate V1--V10, migrated V11, archive-only, malformed V11 | Existing bounded classification/refusal; no fabricated approvals. |
| G26-A3 | Terra proposal versus explicit policy decision | Exact text/link recorded; decline does not accept proposal. |
| G26-A4 | Default CLI and injected test actor | Prompts remain default-no; production actor is effective UID; no environment/provider identity is trusted. |
| G26-A5 | Sensitive approval text | Existing private modes/status warning hold; no redaction/export API. |
| G26-C1 | Ownership at gates/decline/crash/retry | Existing target owner is retained; no queue handoff or dirty checkout reuse. |
| G26-C2 | Read-only policy and write Git with retries/failures | Recording calls no provider; provider and Git retry rules stay separate. |
| G26-C3 | Policy/review/recovery/Git-failure paths | Saved-policy, parsed-history, recovery, and Git block behavior stay unchanged apart from audit facts. |
| G26-C4 | Root/installed CLI and Jobs compatibility names | Existing commands/options/identifiers remain; no generic rename. |
| G26-B1 | Scope inspection | No async/API/UI/queue/auth/expiry/revocation/event/JSON/doctor/dry-run/metric/later Gate 2 work. |
| G26-B2 | Authority inspection | Providers cannot approve; controller/human Git and policy authority remains unchanged. |
| G26-B3 | Full deterministic and installed validation | Fakes/fixtures only; tests, compilation, help/import, `UV_NO_EDITABLE=1`, links/IDs, and diff check pass. |

## Implementation evidence (2026-08-02, uncommitted)

Schema 11 adds the strict frozen `ApprovalRequest`, `ApprovalDecision`, and
`ApprovalMigrationAudit` values; current runs append requests and answers for
the existing policy, commit, and push gates. A canonical controller-owned
fingerprint binds repository identity, HEAD, clean state, and the existing
working-tree fingerprint; the default actor is the local effective UID. The
controller persists a request before prompting and an answer before policy
continuation, staging/commit, or push. Declines remain durable and retrying a
gate appends a fresh request/decision pair. Existing policy decision source
links, resume guards, Git authority, target ownership, and synchronous CLI
behavior are preserved.

The exact V10 historical validator and `10_to_11` migration retain source
values/audits, append only empty approval lists plus the missing-history audit,
and keep migrated records execution-refused. Focused tests cover immutable
links, mismatch refusal, migration, decline/retry history, and commit/push
fingerprint separation. All 186 deterministic tests pass using fixtures, fakes,
and temporary repositories only; bytecode compilation, root CLI help, and
`UV_NO_EDITABLE=1` installed-package help/import pass. No Jobs access, live
provider, external target, or repository publication occurred; Git actions in
the deterministic suite use temporary repositories only. Owner review remains
required before publication.

## Explicit exclusions and owner decision requested

No asynchronous approval, authentication beyond the local effective-UID principal, remote approval, expiry, revocation, UI/API, direct writes, notifications, queue, event sourcing, generic config/adapters, route selection, metric corrections, versioned JSON, `doctor`, dry-run, verification plugins, worktrees, Jobs access, live provider, commit, or push is authorized by this draft.

The repository owner approved this contract on 2026-08-02. The uncommitted implementation raises the current schema to 11; adds strict immutable request, decision, and V10-migration-audit models; records policy, commit, and push requests before input and decisions before continuation/Git; binds the records to a canonical repository snapshot and local effective-UID actor; preserves the existing synchronous target-ownership and Git gates; adds exact V10 classification and `10_to_11`; and adds deterministic migration/controller coverage. Owner review of the complete implementation diff remains required. No commit or push occurs without separate explicit approval.
