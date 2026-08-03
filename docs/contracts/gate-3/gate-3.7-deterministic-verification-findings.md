# Gate 3.7 contract — Deterministic verification results and findings

**Status:** owner-approved and published; documentation-only Gate 3 deliverable; implementation is later Gate 4 and Milestone 3 work.

## Decision

Continuo will represent every deterministic verification attempt as one immutable,
machine-checkable `VerificationResult` and every actionable deterministic failure
as one or more immutable `VerificationFinding` records. A result proves only the
exact configured verification profile, task-envelope revision, repository
evidence, and bounded output that it records. It never proves a broader
acceptance claim, substitutes for adversarial review, or allows a provider to
claim a check passed.

Required verification must be complete and successful before the workflow may
advance to a dependent stage. Incomplete, unavailable, unsupported, malformed,
cancelled, or internally inconsistent evidence is not a pass and blocks with a
bounded reason; it does not enter correction. A complete required failure may
enter correction only through typed deterministic `correctable` findings and
the saved correction policy. Scope or safety findings block directly. Optional
requests remain visible as executed, failed, unsupported, or not selected; they
are never silently reported as run or passing and do not consume correction
budget unless a future trusted project policy independently makes them required.

Gate 3.7 defines result, finding, identity, ordering, and budget semantics.
Gate 4 and Milestone 3 implement verifier catalogs, the safe command runner,
configuration instances, persistence, migration, reporting, and workflow
wiring. It preserves the current repository-state proxy and Gate 2.5
correction-policy meanings until implementation is approved.

## Trusted inputs and closed vocabulary

A verifier is selected only by a trusted versioned verification-profile catalog
and the persisted resolved configuration. A task-envelope request may reference
a configured profile ID and criterion IDs under Gate 3.3, but cannot provide a
command, executable, working directory, environment, credential, timeout,
parser, profile payload, severity, disposition, or output claim. The catalog and
repository adapter—not a task, provider, or display label—supply the stable
profile ID, profile revision/hash, capability, command/parser identities,
allowed scope, evidence bounds, and finding-key algorithm.

The closed initial outcomes are `passed`, `failed`, `error`,
`unsupported`, `cancelled`, and `incomplete`. `passed` is valid only for
complete evidence with zero blocking or correctable findings. `failed` is
valid only for complete evidence with one or more findings. `error` denotes a
known runner, configuration, parser, or infrastructure failure;
`unsupported` an unavailable/disallowed configured profile; `cancelled`
controller cancellation; and `incomplete` missing, truncated, or
untrustworthy execution evidence. The last four outcomes carry no
correction-eligible findings.

The closed initial finding dispositions are:

- `correctable`: a complete deterministic failure eligible for the saved
  correction policy;
- `blocking`: a complete deterministic safety/scope failure that blocks
  without provider correction; and
- `informational`: a complete non-gating observation that remains auditable
  but cannot drive a transition or budget.

Only registered profiles may emit registered kinds and dispositions. The initial
generic kinds are `test_failure`, `lint_violation`, `type_error`,
`build_failure`, `acceptance_failure`, `allowed_path_violation`, and
`verification_configuration_error`. They identify capabilities, not an
ecosystem or package manager. `allowed_path_violation` is always `blocking`.
Unknown kinds, outcomes, dispositions, parser versions, or evidence fields fail
closed.

## Immutable result and finding records

Each completed or terminal attempt persists exactly one strict, frozen,
extra-forbid result before any workflow decision, provider invocation, sleep,
retry, correction reservation, Git action, or report that represents it:

```yaml
verification_result_schema_version: 1
verification_result_id: <controller-generated bounded unique ID>
verification_phase: post_write
verification_ordinal: 1
verification_profile_id: <stable catalog ID>
verification_profile_sha256: <saved exact profile hash>
task_envelope_sha256: <saved exact envelope hash>
verification_request_id: <envelope request ID or trusted project requirement ID>
required: true
repository_snapshot_before_sha256: <repository-adapter snapshot>
repository_snapshot_after_sha256: <same or post-run observed snapshot>
started_at: <UTC timestamp>
finished_at: <UTC timestamp>
outcome: failed
evidence:
  runner_identity: <registered stable runner ID/version>
  parser_identity: <registered stable parser ID/version>
  exit_status: <integer or absent when no process started>
  stdout_artifact: <private bounded artifact reference/hash/completeness>
  stderr_artifact: <private bounded artifact reference/hash/completeness>
  evidence_complete: true
  bounded_reason_code: <required except passed/failed>
criterion_coverage:
  - criterion_id: <existing envelope criterion ID>
    status: failed
findings: []
result_sha256: <canonical payload hash excluding this field>
```

The result hash uses Gate 3.1 canonical JSON rules. IDs and hashes are control
fields. `verification_phase` is initially `post_write`; another phase
requires a registered value and profile behavior, not inference from a stage
label. Ordinals are contiguous per saved profile/request/phase sequence from
one. Snapshot values are repository-adapter evidence, never verifier/provider
assertions. A required missing or mismatched snapshot, workspace change during a
read-only verifier, or failed post-run observation makes the result
`incomplete` and blocks.

Each finding in a complete `failed` result is strict, frozen, and extra-forbid:

```yaml
verification_finding_schema_version: 1
finding_id: <SHA-256 of canonical finding instance payload>
finding_key: verification:<profile ID>@<profile hash>:<profile-defined stable key>
finding_kind: test_failure
disposition: correctable
summary: <bounded parser-derived safe text>
criterion_ids: []
evidence_locator: <bounded private artifact offsets/diagnostic IDs>
source_verification_result_id: <result ID>
source_result_sha256: <result hash>
```

`finding_id` identifies one observed instance and includes its source result
ID/hash. `finding_key` is the durable identity for deduplication, reporting,
and recurrence. It must be computed only from profile ID/hash, parser
identity/version, kind, and the profile's documented normalized
rule/subject/location fields. It must not derive from a display name, free-form
summary, provider prose, timestamps, raw-output ordering, environment, or
noncanonical path spelling. A profile/parser hash change therefore cannot be
treated as the same persistent defect.

Keys are unique within one result. Findings are canonically ordered by
disposition (`blocking`, then `correctable`, then `informational`), kind,
and UTF-8 bytewise key. Summaries, artifacts, and criterion links are evidence
only; they cannot change identity, ordering, disposition, or authority.

`criterion_coverage` contains only criteria named by the applicable request or
trusted project requirement. Each status is `passed`, `failed`, or
`not_evaluated`. A criterion is deterministically satisfied only when every
required applicable profile has complete passed coverage for the same saved
task envelope and repository evidence. An empty Jobs compatibility criterion
list creates no implied PASS.

## Evaluation and workflow semantics

At run creation, required verification profiles must resolve from saved
configuration and task envelope before provider work. An unknown, disallowed, or
incompatible required profile blocks before provider work, writer arming, lock,
or target mutation. An optional unsupported request persists as
`unsupported`; it cannot be collapsed into absence or pass.

After a writer produces repository evidence, the controller runs required
post-write verifiers in canonical profile-ID/request-ID order and persists each
terminal result independently. It does not continue to probabilistic review,
Git approval, or publication until every required verifier has complete terminal
success or the run has blocked/corrected under this contract. This puts
deterministic evidence before implementation review. The current repository
identity, change enumeration, diff check, changed-file capture, and working-tree
fingerprint remain controller/repository-adapter evidence; Gate 4 may turn them
into trusted verification profiles only with approved catalog and migration work.

For a complete required failure, the controller persists all findings and then:

1. blocks on the first canonical `blocking` finding, without provider work or
   budget reservation;
2. otherwise selects the first canonical `correctable` finding and evaluates
   the saved correction policy; or
3. otherwise visibly blocks because a required verifier failed—informational
   evidence cannot advance or invent correction.

An optional failure never enters this rule. `error`, `unsupported`,
`cancelled`, and `incomplete` block before it because they have not proved a
trustworthy failure to correct.

## Correction budget and recurrence

Gate 2.5's saved `ResolvedCorrectionPolicy` remains the sole authority for
global capacity and the ordinary/Sol-guided/block schedule. A
verification-driven correction uses the same persisted `correction_cycles`
counter as a reviewer-driven correction: it increments exactly once immediately
before `correction_pending` is saved and a writer is armed. It is not a count
of checks, findings, provider calls, successful writes, retries, or verifier
reruns. A blocked, incomplete, optional, informational, or unexecuted result
consumes no slot.

The selected correctable finding's `finding_key` is its correction identity.
The unified Gate 4 evaluator namespaces legacy parsed-review identities as
`review:<review category>:<existing finding key>`; deterministic identities
retain their `verification:` key. The contiguous history of correction-eligible
observations for that exact identity controls recurrence. Its first occurrence
receives the saved ordinary action; the next two receive the saved Sol-guided
actions; the next blocks, always subject to global capacity. A new identity
starts a new per-finding sequence but shares the global capacity.

Before reservation, the controller persists an immutable
`CorrectionEvidenceLink`: selected finding ID/key, source result ID/hash,
saved policy ID/hash, pre-correction snapshot, intended writer operation, and
next correction-cycle value. A prompt may receive bounded diagnostics but cannot
change selection, policy, budget, scope, route, or capability. After the writer,
all required post-write verifiers rerun against new repository evidence before
the finding can be considered remediated.

Remediation requires a later complete successful required result from the same
saved profile, phase, and task envelope, with repository evidence after the
linked correction. A provider assertion, clean diff, changed summary, profile
change, optional check, or absent output does not remediate it. Reappearance of
the same key after its linked correction is recurrence; a different key is new.
Multiple correctable findings persist, but only the selected one may reserve a
correction. After rerun, canonical ordering selects the next unresolved one.

No verifier or provider retries a writer. Rerun/recovery is controller-owned and
retains saved profile, task/configuration, capability ceiling, repository
evidence, and correction link. A crash after result/link persistence never
repeats a writer or spends another slot. A crash after verifier arming but before
a terminal result blocks until explicit recovery establishes new evidence; it
never invents outcome, finding, pass, or reservation.

## Persistence, resume, diagnostics, and migration

The ordered result/finding/link collections and hashes persist with the run
before effects. Resume uses only saved results, profiles, task envelope,
configuration, repository evidence, capability profiles, and correction policy.
Missing, malformed, unsupported, hash-incoherent, reordered, unavailable, or
changed facts block; resume never uses a current verifier/parser/default,
equivalent display name, alternate profile, or provider.

Reports and machine surfaces distinguish configured, selected, completed,
passed, failed, blocked, unsupported, cancelled, error, incomplete, and
unexecuted verification. They expose stable IDs/hashes, outcome,
disposition/counts, criterion coverage, and bounded reason codes by default;
raw command/output artifacts remain private. `doctor` and dry-run validate
only static profile/configuration/task compatibility; they do not execute a
verifier, construct a sensitive command, invoke a provider, contact a network,
create a run, or modify a target.

Existing schema-12 records retain their exact proxy `verification` map and
historical classification. A future migration may preserve known proxy facts but
cannot invent profile/hash, result, finding, parser, criterion coverage,
completeness, correction link, or remediation history. Migrated records remain
execution-refused unless approved Gate 4 migration proves every required fact.

## Non-goals

This Gate adds no verifier command, safe command runner, configuration format,
profile/catalog implementation, task adapter, test/lint/type-check/build
instance, allowed-path engine, parser, sandbox, network call, provider
invocation, run schema, migration, CLI, report field, correction-policy change,
target access, Git action, commit, or push. It does not access Jobs or alter
provider commands, retries, ownership, writer recovery, storage, Git gates,
existing proxy verification, or compatibility identifiers.

## Adversarial matrix

| ID | Scenario | Required outcome |
|---|---|---|
| G37-01 | Required verifier/profile/parser is unknown, duplicated, disallowed, hash-incoherent, or incompatible with saved task/configuration/capability facts. | Startup blocks before provider work, writer arming, lock, Git, or target mutation. |
| G37-02 | Task, provider, CLI override, environment, output, or display name supplies verifier command, parser, profile, timeout, disposition, or authority. | It is rejected/ignored as authority; only trusted registered records apply. |
| G37-03 | Optional verification is unsupported or not selected. | It is durably visible as unsupported or unexecuted, never passing/executed evidence or a correction candidate. |
| G37-04 | Result has unknown fields/outcome/kind/disposition, duplicate IDs/keys, corrupt hash, non-contiguous ordinal, or bad criterion reference. | Strict validation fails before transition, correction, Git, or resume. |
| G37-05 | Result claims passed with a finding, incomplete evidence, failing criterion, missing coverage, or mismatched repository evidence. | Validation rejects the claim and blocks; incomplete evidence cannot produce PASS. |
| G37-06 | Runner succeeds but output is truncated, parser input incomplete, artifact hash absent, or post-run observation fails. | Result is incomplete with bounded evidence and blocks without correction or budget use. |
| G37-07 | Runner has known launch/configuration/parser failure, cancellation, or unsupported capability. | It records error/cancelled/unsupported and blocks; no finding or correction is fabricated. |
| G37-08 | Parser emits an unregistered ecosystem-specific kind or forms a key from prose, timestamp, output order, display label, or noncanonical path. | Parsing/validation fails closed; catalog identity fields alone form keys. |
| G37-09 | Same rule/subject/location reappears under the same profile hash after linked correction. | Same verification identity advances the saved per-finding schedule. |
| G37-10 | Profile/parser hash changes while an apparently identical failure reappears. | A new identity results; resume does not treat it as a persistent prior finding. |
| G37-11 | One result has blocking, correctable, and informational findings. | All persist canonically; blocking wins and no correction is reserved. |
| G37-12 | Required failure has correctable findings only. | First canonical correctable finding alone evaluates saved policy before one possible writer reservation. |
| G37-13 | Required failure has informational findings only, or an optional verifier has correctable-looking evidence. | Required result blocks visibly; optional evidence does not reserve budget, invoke provider, or gate required path. |
| G37-14 | Global capacity is exhausted when a deterministic finding is selected. | Block before Sol/writer; no correction link or counter increment. |
| G37-15 | Deterministic and reviewer findings share text or model key. | Namespaced verification/review identities remain distinct. |
| G37-16 | Writer prompt asserts fix or requests policy/budget/scope change. | Saved correction link/policy/scope remain authoritative; only later complete required verification remediates. |
| G37-17 | Writer completes but result is from a different profile, phase, task revision, or repository snapshot. | It does not remediate linked finding; exact saved requirements remain required. |
| G37-18 | Registered allowed-path verifier reports forbidden change. | Blocking allowed-path violation blocks without provider correction, budget, Git, or publication. |
| G37-19 | Crash before terminal result, after result, after link, during writer, or after writer return. | Durable state prevents invented outcome, duplicate writer/ordinal, or double reservation; uncertainty blocks. |
| G37-20 | Resume finds saved verifier/profile/parser/configuration/task/capability/ceiling changed or unavailable. | Resume blocks; it does not use current default, alternate profile/parser, display match, or provider. |
| G37-21 | Historical schema-12 proxy verification map loads or migrates. | Proxy facts remain readable but typed verification facts are not invented; execution remains refused without approved proof. |
| G37-22 | Doctor or dry-run evaluates verification configuration. | Static validation only; no verifier/provider/network execution, sensitive command, run creation, or target mutation. |
| G37-23 | Proposal adds runner, command, ecosystem implementation, migration, report/CLI code, provider work, or Gate 4/Milestone 3 work. | Out of scope pending explicit later approval. |

## Approval and implementation evidence

The owner approved this Gate 3.7 contract on 2026-08-03. Gate 3.7 is a
documentation-only contract-definition deliverable: implementation of verifier
catalogs, the safe command runner, parser enforcement, result/finding/link
persistence, workflow wiring, schema migration, CLI/report surfaces, and
installed-package validation (with `UV_NO_EDITABLE=1` on macOS) belongs to the
explicitly later Gate 4 and Milestone 3 work. No runtime source, test, fixture,
run record, provider, target checkout, Git side effect, commit, or push changed
for this Gate.

Publication evidence: commit `a3fc8ed79c1c4769d19b6342f7a7a97c3a21c77f`
(`Define deterministic verification findings`) is on `origin/main`.

Validation on clean synchronized `main` at
`4bb8189a2485ea185dd476060618fa3310105334` confirmed that authoritative local
Markdown links resolve, all 23 `G37-*` matrix IDs are unique, and tracked and
new-file `git diff --check` whitespace checks pass. No live-provider or
Jobs-repository validation was performed.
