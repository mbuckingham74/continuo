# Gate 2.1 / C-9 historical-schema inventory contract (approved 2026-08-02)

**Authority.** [ENGINE_ROADMAP.md](../../ENGINE_ROADMAP.md) remains authoritative. This document contains the bounded contract, adversarial matrix, and durable evidence for its tracker entry.

**Tracker.** See [EXECUTION_PLAN.md](../../EXECUTION_PLAN.md) for gate status, sequencing, and links to every contract.

## Tracker evidence

- Planning evidence (2026-08-02): the approval-pending bounded contract and
    adversarial matrix below were derived from the authoritative C-9 decision,
    repository history from schema 1 through schema 6, the schema-6 additive
    bridges delivered in M0.2--M0.5, and the current load/save/status/recovery
    paths. This planning pass read no private run record, accessed no Jobs path,
    invoked no provider, and changed no runtime or persisted model. Execution
    remains unauthorized until the repository owner approves the classification
    vocabulary, historical treatment table, fixture boundary, and separation
    from the following migration implementation item.
  - Contract approval (2026-08-02): the repository owner approved the complete
    Gate 2.1 contract and authorized only its synthetic fixture, provenance-test,
    and finalized inventory scope. Runtime migration and every later Gate 2 item
    remain unauthorized.
  - Inventory evidence (2026-08-02): seven neutral JSON fixtures now represent
    every declared schema V1--V6 plus the fully populated current schema-6
    compatibility shape. Their manifest records exact SHA-256 values, introducing
    commits, adjacent structural transitions, all five additive schema-6
    generations, invalid-envelope derivations, treatments, dispositions, scope
    boundaries, and all 27 approved matrix rows. No fixture byte came from
    private run storage, Jobs, a provider capture, or another project.
  - Deterministic test evidence (2026-08-02): nine read-only inventory tests
    validate exact fixture checksums and provenance, V1--V6 structural changes,
    schema-6 absence/compatibility semantics, unsafe writer/ownership cases,
    invalid versions, strict JSON envelopes, repeatable in-memory derivations,
    bounded diagnostics, and complete matrix coverage without importing or
    invoking the orchestration runtime, Git, a provider, target coordination, or
    private run storage.
  - Validation evidence (2026-08-02): a separate read-only provenance check
    validated V1--V6 fixtures against the exact historical `WorkflowRun` classes
    at their introducing commits, and the current fixture against today's model.
    The complete 105-test deterministic suite and Python compilation pass. Ten
    non-planning local Markdown links/anchors and all 27 ordered Gate 2.1 matrix
    rows validate. Root CLI help plus `jobs-orchestrator` help and
    `src/jobs_orchestrator` import from a clean temporary editable install pass;
    `JOBS_REPO` remains unchanged. Scope inspection and `git diff --check` pass.
    No private run, Jobs path, live provider, target checkout, runtime model,
    migration, commit, or push was used.
  - Review decision (2026-08-02): the repository owner approved the complete
    eleven-file inventory diff and explicitly authorized commit, direct push to
    `origin/main`, and this tracker update. Gate 2.1 is complete; the explicit
    migration item remains separate and has not begun.
  - Publication evidence (2026-08-02): implementation commit
    `ecfc6bb04278270784bc185426d95b030057ca60` (`Inventory historical run
    schemas`) was approved for direct push to `origin/main` with all 105 tests
    passing.



**Status and boundary.** The repository owner approved this execution contract
on 2026-08-02. It covers only the first unchecked Gate 2 item: inventory every
known persisted run-record class and decide whether it is compatible, a
migration candidate, archive-only, or unsupported. Approved execution may add a
sanitized deterministic historical fixture corpus, fixture-provenance checks,
and final inventory evidence to this tracker. It must not change `WorkflowRun`,
`load_run()`, `persist()`, `_save()`, status/report behavior, a workflow
transition, or a private record under `runs/`. The next Gate 2 item separately
owns current-schema constants, executable migrations, rollback, and user-visible
runtime failure reporting.

**Invariant and current evidence.** A record's declared schema must select an
explicit interpretation before current code can read, resume, or rewrite it.
Loading an old record as today's model and stamping it with today's version on
the next ordinary save can erase which contract produced its state, silently
apply a newer correction policy, or imply audit evidence that never existed.
Unknown, future, malformed, or semantically unsafe records must remain intact
and visible rather than being guessed into a resumable state.

Repository history at baseline
`e0f1ad9b99a127be8885f1bc2fada24aba0dc0ad` establishes six declared run
schemas. The current implementation provides no run-schema constant or
migration registry: `WorkflowRun.schema_version` is an unconstrained `int`
defaulting to `6`; `load_run()` validates raw JSON directly against the current
model; and `Controller._save()` unconditionally assigns `6`. Top-level unknown
fields fail because `WorkflowRun` uses `extra="forbid"`, but a past or future
integer version is not itself rejected. No-argument `status` reduces every
parse or validation failure to `INVALID`, while direct load/report surfaces only
the sanitized path-level message `run state is invalid`.

Schema 6 is not one structural generation. M0.2 expanded failure values; M0.3
added optional failure provenance; M0.4 added optional capability, writer
fingerprints, active-writer state, and immutable recovery decisions; M0.5 added
optional target ownership; and M0.6 changed filesystem handling without changing
record content. Those bridges intentionally preserved version 6. Therefore the
inventory must record both the declared schema and an evidence-based structural
generation; absent optional fields remain absence, never proof that a later
event occurred.

The roadmap records aggregate evidence that three of five private/local records
failed then-current validation. This contract does not inspect those sensitive
files or claim their specific failure classes. Reproducible evidence will come
only from repository history and synthetic local fixtures containing no copied
task, prompt, provider, repository, decision, or diff content.

**Classification vocabulary.** Treatment and execution eligibility are
separate fields in the inventory:

- `compatible` means the exact bytes validate under their declared, recognized
  contract and need no structural transform for safe inspection. It does not by
  itself authorize resume.
- `migrate` means a recognized historical contract has enough typed information
  for a future explicit, stepwise transform. Migration must preserve absence and
  provenance and may still yield a non-resumable record.
- `archive` means the schema or identity is recognizable enough for bounded
  read-only diagnosis, but missing or contradictory control evidence makes
  automated transformation or continuation unsafe. Archiving means retain the
  original bytes and deny workflow writes; it does not mean moving or deleting a
  file in this item.
- `unsupported` means no trusted contract can interpret the bytes, including an
  absent/invalid/unknown version, malformed or ambiguous JSON, or an unknown
  future schema. It remains preserved and visibly rejected; it is never
  down-migrated, normalized, deleted, or silently skipped.

Every classified record also receives an execution disposition of
`inspection_only`, `resume_blocked`, or `resume_eligibility_deferred`. This item
does not classify any legacy record as immediately resumable. Resumability
depends on the next item's atomic migration contract and, for policy-sensitive
runs, the later persisted-policy item.

**Authoritative historical inventory and treatment decision.** The fixture
provenance for each row must name the introducing commit and derive its shape
from that committed `models.py`, not from memory or a private run.

| Class | Historical contract or evidence | Treatment after this inventory | Required preservation and execution decision |
|---|---|---|---|
| V1 | Schema 1 at `aa2120c`: one-correction bound; no Sol guidance, finding identity, policy decisions, timing, or provider-failure resume fields | `migrate` candidate | Preserve the exact count and missing evidence. `resume_eligibility_deferred`; never grant today's larger correction budget implicitly. |
| V2 | Schema 2 at `b7b90de`: Sol guidance and a three-correction bound | `migrate` candidate | Preserve guidance/count and absence of finding identity. `resume_eligibility_deferred`; no inferred escalation history. |
| V3 | Schema 3 at `20d011a`: optional finding key and twelve-correction bound | `migrate` candidate | Preserve missing keys as missing; a legacy derived key may be presentation/reconciliation evidence but not fabricated source data. `resume_eligibility_deferred`. |
| V4 | Schema 4 at `2aeb003`: immutable policy-decision list added | `migrate` candidate | An absent list means no recorded decision, not proof none occurred. Preserve all supplied decision text exactly. `resume_eligibility_deferred`. |
| V5 | Schema 5 at `3741118`: update timestamp and optional provider duration added | `migrate` candidate | Preserve `None` timing as legacy untimed evidence and do not synthesize timestamps. `resume_eligibility_deferred`. |
| V6-base | Schema 6 at `f7ac5ec`: failure kind/retry flag and provider-resume stage/prompt | `migrate`/normalize candidate | Preserve saved failure kind and prompt; never rescan model prose to invent transport evidence. Resume remains deferred to stage-specific checks. |
| V6-supervisor | M0.2 schema-6 records may contain `timeout`/`interrupted` | `compatible` when current-model valid | Preserve the terminal kind and non-retry semantics. Timeout/interruption remains resume-blocked. |
| V6-provenance | M0.3 schema-6 records may add failure source/code | `compatible` when current-model valid | Missing provenance remains explicit `null`; saved kind remains authoritative. No raw-stream reclassification. |
| V6-writer | M0.4 schema-6 records may add capability, pre/post fingerprints, active writer state, and recovery decisions | `compatible` only when current-model valid and links are coherent; otherwise `archive` | A writer stage without trustworthy pre-attempt/linkage evidence is `resume_blocked`; never invoke, adopt, or infer success. |
| V6-owner | M0.5 schema-6 records may add target ownership | `compatible` only when current-model valid and ownership is coherent; otherwise `archive` | Missing ownership remains legacy/unrecorded and may only follow the existing guarded claim path after future migration approval. Contradictory ownership is inspection-only. |
| V6-current | Current schema-6 shape at the baseline, including M0.6 storage guarantees | `compatible` when all closed-model and cross-field invariants hold | Ordinary current behavior is not changed by this inventory. Exact missing optional values and sensitive bytes remain unchanged. |
| Known-version invalid | Recognized schema 1--6 JSON object with missing required fields, forbidden extras, invalid enums/types/bounds, incoherent links, or contradictory stage evidence | `archive` if bounded identity/schema diagnostics are trustworthy; otherwise `unsupported` | Preserve raw bytes; no save, resume, provider, ownership claim, verification, or Git work. |
| Unversioned/invalid version | Missing, Boolean, non-integer, nonpositive, or otherwise invalid `schema_version` | `unsupported` | Do not assume schema 1 or the current default. Preserve bytes and report only bounded diagnostics. |
| Future/unknown version | Integer greater than the approved current version, or an unrecognized historical integer | `unsupported` | Never down-migrate or validate as the current model merely because fields happen to fit. |
| Invalid JSON envelope | Malformed/truncated JSON, duplicate object keys, non-object top level, invalid UTF-8, or non-finite numeric tokens | `unsupported` | Fail before model validation; preserve bytes; never accept parser-dependent last-key wins or nonstandard numbers. |

The structural maps needed by the following migration item are: V1 to V2 adds
Sol state while preserving the recorded correction count; V2 to V3 adds optional
finding identity; V3 to V4 adds policy decisions; V4 to V5 adds update/timing
fields; and V5 to V6 adds failure/retry-resume fields. The schema-6 generations
are compatibility variants, not fictional schema 7+ records. This inventory
does not decide the next current version, rewrite these maps as executable code,
or resolve the later requirement to persist correction/escalation policy.

**Deterministic fixture contract.** After approval, fixtures may be added only
under a repository test-fixture directory. Each is a minimal synthetic record
or a documented deterministic mutation of one, with neutral values such as a
temporary `/fixture/repo`, fake hashes, local commands, and invented provider
text. A manifest must record class ID, declared schema, source commit, derivation,
expected treatment/disposition, and SHA-256 of the exact fixture bytes. It must
explicitly state that no fixture came from `runs/`, Jobs, a provider capture, or
another project. Fixtures are immutable evidence for the next migration item;
changing one requires an intentional manifest checksum update and review.

No fixture test may invoke `Controller.resume()`, a provider wrapper, Git, or a
target coordinator. Tests for this inventory are read-only provenance and
classification-table tests. Executable load/migrate/write/rollback tests belong
to the separately approved next Gate 2 item.

**Persistence, migration, crash, retry, and audit boundaries.** This inventory
opens no run record and writes no run storage. It adds no schema stamp, inferred
default, migration marker, archive directory, quarantine file, backup, event,
or audit record. A failure or interruption while generating/checking committed
synthetic fixtures can leave only ordinary uncommitted repository files; rerun
is deterministic and cannot affect workflow state.

The following migration item must use an explicit current-version constant,
dispatch before current-model validation, reject unknown/future versions, apply
one adjacent transform at a time, validate each step, and preserve the original
private bytes until the fully migrated record is durably and atomically written.
It must test crash points and rollback from every supported step, never let a
failed migration invoke a provider or Git operation, and never let inspection
silently rewrite a record. Those are acceptance boundaries for later design,
not implementation authorized here.

Inventory output and fixture failures may include only class ID, schema value,
source commit/checksum, treatment, disposition, and bounded field-path/error
codes. They must not print specifications, prompts, stdout/stderr, diffs,
decisions, recovery notes, repository paths from private records, or raw JSON.
No retry applies to deterministic classification. `archive` and `unsupported`
are durable treatment decisions in documentation, not destructive filesystem
actions.

**Read/write and authority effects.** Planning and approved inventory execution
are read-only with respect to runtime state, target repositories, providers,
coordination databases, and Git history. The only authorized writes after
approval are synthetic fixture/test documentation within Continuo and tracker
evidence. No class may be made resumable, no correction budget may change, no
writer evidence may be invented, and no target ownership may be claimed. Human
policy, commit, push, and merge authority remain unchanged.

**Adversarial test matrix.** All cases use committed synthetic bytes or pure
in-memory deterministic mutations. No private run, provider CLI, network
service, target checkout, or Jobs path is accessed.

| ID | Fixture / evidence | Required inventory assertion |
|---|---|---|
| H1 | `models.py` at every schema-introducing commit from V1 through V6 | Manifest source commits and the field-transition table match history exactly; no schema generation is skipped or reordered. |
| H2 | M0.2--M0.6 commits that retained schema 6 | Each additive generation and the M0.6 storage-only change are recorded without inventing new version numbers. |
| H3 | Current `load_run()`, `_save()`, `status`, and closed `WorkflowRun` model | Inventory records direct current-model validation, unconditional version-6 stamping, coarse invalid reporting, and absence of migration dispatch; it does not change them. |
| V1 | Minimal and full schema-1 synthetic records at clean, approval, correction, and blocked stages | Class is `migrate`/`resume_eligibility_deferred`; one-correction policy evidence and missing later fields remain explicit. |
| V2 | Schema-2 record with and without Sol guidance at its valid stages | Class is `migrate`; no finding key or escalation event is invented. |
| V3 | Schema-3 PASS/failure reviews with present and absent finding keys | Class is `migrate`; absent key stays absent and exact supplied key survives fixture checks. |
| V4 | Schema-4 record with zero and multiple immutable policy decisions | Class is `migrate`; decision order/text are byte-derived evidence and are not summarized into new state. |
| V5 | Schema-5 providers with present and absent duration/update timestamps | Class is `migrate`; missing time stays legacy untimed and no timestamp is synthesized. |
| V6A | Base schema-6 failure/resume record | Class is V6-base; saved failure/prompt are preserved and model prose is not reclassified. |
| V6B | Timeout/interrupted schema-6 records | Class is V6-supervisor; terminal no-retry/resume-blocked treatment is explicit. |
| V6C | Schema-6 provenance present versus absent | Both generations are distinguishable; absent fields remain `null` evidence, not inferred codes. |
| V6D | Coherent writer record and writer stage missing marker/link/fingerprint | Coherent record is compatible; unsafe writer record is archive/resume-blocked with zero provider or adoption authority. |
| V6E | Coherent ownership, absent legacy ownership, and contradictory release fields | Coherent/absent forms receive their defined dispositions; contradiction is archive-only and never claims/releases a target. |
| V6F | Current fully populated schema-6 record | Class is compatible without any byte rewrite or implication that optional evidence was mandatory historically. |
| I1 | Recognized schema missing a required field or using invalid type/enum/bound | It is archive-only when identity/schema remain trustworthy; diagnostics name only bounded field paths/codes. |
| I2 | Recognized schema with unknown top-level or nested fields | It is not silently dropped; treatment is archive or unsupported according to whether trusted identity remains readable. |
| I3 | Writer provider index out of range/mismatched, contradictory stage, or incoherent fingerprint fields | It is archive/resume-blocked; no linkage, success, or side effect is inferred. |
| I4 | Ownership target identity/release fields contradict one another | It is archive/inspection-only; no database is opened and no ownership mutation is proposed. |
| U1 | Missing, `null`, Boolean, string, fractional, zero, or negative schema version | Every form is unsupported; none receives Pydantic/default coercion to a known contract. |
| U2 | Unknown past integer and current-plus-one future integer whose remaining fields fit today's model | Both are unsupported; structural resemblance never authorizes up/down migration. |
| U3 | Malformed/truncated JSON, duplicate keys, array/scalar top level, invalid UTF-8, `NaN`, or infinity | Every form is unsupported before model validation with no raw-content disclosure. |
| P1 | Run inventory/fixture checks repeated twice | Treatment, disposition, diagnostics, and manifest checksums are identical; fixture and runtime bytes remain unchanged. |
| P2 | Simulated exception during fixture generation/checking | No private storage, target, provider, coordination, Git operation, or partial runtime migration exists; rerun is deterministic. |
| A1 | Fixture values contain secret-looking prompts, provider errors, diffs, and absolute paths | Inventory diagnostics contain none of those values and do not use content to choose transport or control policy. |
| B1 | Source/diff boundary inspection | No run model, loader, saver, schema constant, migration registry, status/report, controller, provider, Git, or coordination behavior changed. |
| B2 | Compatibility smoke inspection | `jobs-orchestrator`, `JOBS_REPO`, and `src/jobs_orchestrator` remain exact; no generic rename or alias work begins. |
| B3 | Scope and provenance inspection | No file under `runs/`, no Jobs path, no planning-intake requirement, no live provider, no commit, and no push is used. |

**Explicit exclusions and later-gate boundaries.** This item does not implement
schema constants or migration dispatch; change the current schema; relax or
tighten a persisted Pydantic field; remove the fixed correction bound; persist a
resolved policy; add stable role/provider/route identities; persist parsed
reviews; add approval records; change metrics; add JSON/doctor/dry-run output;
write the event/state ADR; add generic configuration or adapters; archive, move,
delete, redact, or export a record; or add event sourcing.

The next Gate 2 item owns executable stepwise migrations, atomic rollback and
visible failure reporting. Later Gate 2 items own stable identities, immutable
parsed control records, saved correction/escalation policy, approval records,
metrics, machine interfaces, and the event/state ADR. Gates 3--8 retain their
configuration, adapters, verification, isolation, asynchronous operation, and
UI boundaries. Existing `jobs-orchestrator`, `JOBS_REPO`, and
`src/jobs_orchestrator` compatibility identifiers remain untouched.

**Exit criteria for the approved inventory execution:** the repository owner
approves this vocabulary, treatment table, synthetic-fixture boundary, and
separation from migration implementation; every matrix row has deterministic
fixture/provenance coverage; documentation checks and `git diff --check` pass;
the complete uncommitted diff is reviewed; and no runtime file, private run,
provider, target checkout, commit, push, or later Gate 2 item is touched.