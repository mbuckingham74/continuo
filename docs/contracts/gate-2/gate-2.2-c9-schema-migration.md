# Gate 2.2 / C-9 schema migration contract (approved 2026-08-02)

**Authority.** [ENGINE_ROADMAP.md](../../ENGINE_ROADMAP.md) remains authoritative. This document contains the bounded contract, adversarial matrix, and durable evidence for its tracker entry.

**Tracker.** See [EXECUTION_PLAN.md](../../EXECUTION_PLAN.md) for gate status, sequencing, and links to every contract.

## Tracker evidence

- Planning evidence (2026-08-02): the approval-pending Gate 2.2 contract and
    adversarial matrix below were derived from authoritative C-9, the completed
    Gate 2.1 fixture inventory, current schema-6 load/save/status behavior, the
    M0.5 target-ownership boundary, and M0.6 private atomic storage. This pass
    changed documentation only. No runtime model, loader, saver, fixture, test,
    private run, target checkout, provider, commit, or push was touched.
    Implementation remains unauthorized until the repository owner approves the
    version-7 baseline, explicit migration command, audit/disposition fields,
    rollback definition, diagnostics, and later-item boundaries.
  - Contract approval (2026-08-02): the repository owner approved the complete
    Gate 2.2 contract and authorized implementation without authorizing any
    later Gate 2 item, private-record migration, provider use, commit, or push.
  - Implementation evidence (2026-08-02): `CURRENT_RUN_SCHEMA_VERSION = 7`
    and `OLDEST_MIGRATABLE_RUN_SCHEMA_VERSION = 1` now govern construction,
    load, and persistence. Strict byte classification dispatches exact V1--V7
    contracts before current-model validation. A closed ordered registry applies
    `1_to_2` through `6_to_7`, validates every intermediate contract, and writes
    an immutable audit containing source version/class/hash, ordered steps,
    bounded absence reasons, migration identity/time, and a deferred or blocked
    execution disposition. New records have no migration audit.
  - Write/crash evidence (2026-08-02): `migrate-run <run-id>` presents a bounded
    plan and uses default-no confirmation. Approval re-reads the exact source,
    transforms only in memory, writes a private temporary current record, and
    rechecks source device/inode, bytes, and SHA-256 under a local migration-only
    directory lock before atomic replacement. Decline, transform/final-
    validation/write failures, source changes, and pre-replace crashes preserve
    the original; post-replace crashes leave one complete V7 record; concurrent
    commands yield one winner and one `source_changed` refusal; rerun is
    idempotent. No backup, reverse, bulk, force, archive, or delete path exists.
  - Read/authority evidence (2026-08-02): status distinguishes `CURRENT`,
    `MIGRATION_REQUIRED`, `RESUME_BLOCKED`, `ARCHIVE_ONLY`, and `UNSUPPORTED`
    with schema and bounded reason metadata. Complete sensitive status JSON and
    derived reports remain available only for ordinary valid current records.
    Direct load never migrates. Every controller mutation rejects a migrated
    audit before target coordination, provider work, verification, policy
    mutation, or Git; Gate 2.2 provides no eligibility override.
  - Deterministic test evidence (2026-08-02): 18 new Gate 2.2 tests exercise all
    42 approved matrix rows using only the seven committed synthetic historical
    fixtures, in-memory derivatives, private temporary directories, injected
    clocks/IDs/confirmation/failures, threads, and existing local child-process
    coverage. The complete 123-test suite passes in 44.446 seconds. It covers
    exact historical preservation, strict envelopes/versions, all adjacent
    steps, V6 structural/coherence classes, default-no/current/archive behavior,
    audit immutability, permission hardening, source CAS, failures before/after
    every transform and final validation, atomic rollback, concurrency,
    crash/idempotency, bounded read surfaces, controller refusal, ordinary
    current regressions, and compatibility identifiers without live providers,
    private runs, Jobs, external targets, or network services.
  - Validation evidence (2026-08-02): Python compilation, all 123 tests, ten
    non-planning local Markdown links, all 42 ordered Gate 2.2 matrix IDs,
    `git diff --check`, root CLI help, and installed `jobs-orchestrator` help
    pass. Both CLIs expose `migrate-run`; `JOBS_REPO` and
    `src/jobs_orchestrator` remain intact. The macOS Python 3.12 environment
    reproduced the stale compatibility failure: the regenerated editable
    `jobs_orchestrator.pth` acquired `UF_HIDDEN`, causing `site.py` to skip it
    again after source-triggered resync. Validation now uses uv's supported
    `UV_NO_EDITABLE=1` mode on macOS. The installed compatibility package can
    resolve the local checkout from its direct-URL metadata in that mode, so
    repeated rebuilds, import, and installed help pass from inside and outside
    the checkout. This packaging repair changes no runtime identifier or command
    contract.
  - Review decision (2026-08-02): the repository owner approved the complete
    nine-file implementation diff and explicitly authorized commit, direct push
    to `origin/main`, and this tracker update. Gate 2.2 is complete; the stable-
    identity item remains separate and unchecked.
  - Publication evidence (2026-08-02): implementation commit
    `bccde195db42f9497a489e8e51c262aa4e2f0d54` (`Add explicit run schema
    migrations`) was pushed directly to `origin/main` with all 123 tests passing.



**Status and boundary.** The repository owner approved this note before
implementation. It specifies only the second Gate 2 item and
introduces an explicitly governed current run schema, strict pre-validation
dispatch, pure adjacent historical transforms, an explicit operator migration
action, atomic failure rollback, and bounded visible diagnostics. It does not
make any historical run automatically resumable and does not begin stable role
identity, immutable parsed-review, persisted-policy, approval-record, metrics,
machine-output, dry-run/doctor, or event/state work.

**Invariant and current failure.** A run record must be interpreted by the
contract named in its bytes before current code validates, resumes, or rewrites
it. Every persisted write must already be the approved current schema; a save
must never turn an arbitrary historical integer into the current version merely
by assignment. Migration must either replace the complete source with one fully
validated current record or leave the original bytes intact. An invalid,
unknown, future, archive-only, or concurrently changed source must never reach a
provider, target coordinator, verification step, or Git operation.

At baseline `8da04e98d694b5f99740e09e1e221f0530d5a895`,
`WorkflowRun.schema_version` remains an unconstrained `int` defaulting to `6`;
`load_run()` feeds raw JSON directly into today's closed model; and
`Controller._save()` unconditionally assigns `6` before persistence. A past or
future integer can therefore be accepted if its remaining shape happens to fit,
while a recognized historical record can fail only as the coarse message
`run state is invalid`. No-argument `status` collapses every parse, schema, and
semantic problem to `INVALID`. There is no current-version constant, migration
registry, explicit mutation command, source compare-and-swap, or migration
audit.

Gate 2.1 established exact synthetic V1--V6 fixtures and proved that schema 6 is
a family: base failure/resume state, supervisor outcomes, optional provenance,
writer evidence, target ownership, and a storage-only M0.6 generation share the
same declared integer. It also decided that no historical record is immediately
resumable merely because it can be transformed structurally.

**Current-schema decision for approval.** Gate 2.2 will establish
`CURRENT_RUN_SCHEMA_VERSION = 7` and `OLDEST_MIGRATABLE_RUN_SCHEMA_VERSION = 1`
as the only runtime-owned run-schema bounds. Version 7 is the first schema whose
load/save rules are governed by an explicit migration registry; it does not
claim that stable identities, parsed reviews, or persisted correction policy
already exist.

`WorkflowRun` will accept only schema 7. New runs default to the current
constant. `persist()` and `Controller._save()` require an already-current model
and reject any mismatch; `_save()` no longer stamps a version. Historical bytes
can enter `WorkflowRun` only through the migration boundary. Unknown past
integers, version 8+, missing/Boolean/coerced versions, and structurally similar
future records never validate as current.

Later persisted-contract items that alter the record shape must increment this
constant and add exactly one adjacent migration. They must not append another
shape under version 7 as M0.2--M0.5 necessarily did under version 6.

**Strict envelope and classification boundary.** Before Pydantic validation,
one bounded reader will:

1. use the M0.6 private-storage preflight and read bytes without following an
   unsafe link or exposing content;
2. require strict UTF-8 and a standard JSON object with unique keys, rejecting
   `NaN`, infinity, duplicate keys, arrays/scalars, and trailing/malformed data;
3. require `schema_version` to be an exact positive integer, not a Boolean or a
   coercible string/float;
4. dispatch versions 1--6 to their exact historical contract and version 7 to
   the current model before any default can obscure the source shape; and
5. return a typed classification containing only version, structural class,
   treatment, execution disposition, source SHA-256, and bounded reason/field
   codes.

The historical validators and field maps are migration-only types derived from
the Gate 2.1 fixtures and introducing commits. They cannot be imported into
workflow policy or used to create new records. Version-6 structural
classification uses field presence plus closed coherence checks, never provider
labels, model prose, timestamps, or guessed production dates. A record can be
classified only as strongly as its persisted evidence permits.

Recognized valid V1--V6 records are migration candidates subject to the
dispositions below. A recognized version with missing/extra/invalid fields or
contradictory control evidence is `archive` when bounded identity/version
diagnosis is trustworthy, otherwise `unsupported`. Invalid envelopes and
unknown/future versions are `unsupported`. Neither treatment writes a file.

**Adjacent migration registry.** The registry is closed and ordered:
`1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7`. Each function accepts only its exact source
shape, copies rather than mutates its input, sets only the next version, validates
the exact target contract, and returns a new dictionary plus bounded audit
reason codes. The executor refuses a missing, duplicate, skipped, backward, or
out-of-order step.

The approved transforms are:

| Step | Structural additions | Required preservation |
|---|---|---|
| `1 -> 2` | Add absent Sol guidance as `null` | Preserve the recorded correction count and stage; do not grant the later three-correction policy or infer escalation. |
| `2 -> 3` | Represent absent review finding keys as `null` | Preserve supplied review text exactly; do not derive a legacy key into source history or grant the twelve-correction policy. |
| `3 -> 4` | Add an empty policy-decision collection | Record that no decision was persisted; do not assert that no human decision occurred outside the record. |
| `4 -> 5` | Add absent run update time and provider durations as `null` | Do not synthesize timestamps or elapsed time; retain legacy-untimed evidence. |
| `5 -> 6` | Add absent failure provenance/capability/fingerprints as `null`, retry flag as the historical default, empty writer/recovery state, and absent ownership | Never classify raw streams, infer a provider capability, invent writer evidence, claim a target, or treat defaulted retry state as observed audit. Reason codes make each provenance gap visible. |
| `6 -> 7` | Add immutable migration audit and execution disposition, then materialize current optional/default fields | Preserve the highest evidenced V6 structural class, every supplied value, and every absence reason. Do not repair incoherent writer/ownership links or change workflow stage. |

The final current model dump may encode typed absence as `null` or an empty
collection, but the immutable migration audit preserves which facts were absent
from the source. No transform re-parses raw review stdout, recomputes a finding,
normalizes provider labels, changes correction counts, updates `updated_at`,
alters repository fingerprints, or rewrites task/provider/Git text.

**Migration audit and execution disposition decision.** Version 7 adds one
immutable `RunMigrationAudit` for a migrated record, containing:

- migration ID and operator-confirmed timestamp;
- source and target schema versions;
- Gate 2.1 structural class;
- SHA-256 of the exact source bytes;
- the ordered adjacent step IDs applied;
- bounded provenance/absence reason codes; and
- one execution disposition.

The closed dispositions are `resume_eligibility_deferred`, `resume_blocked`, and
`inspection_only`. New version-7 runs have no migration audit and are ordinary
current records. Migrated V1--V5, V6-base/provenance, valid legacy-owner, and
otherwise coherent V6-current records use `resume_eligibility_deferred` because
their correction/escalation policy and control identities were not persisted at
creation. Timeout/interruption and other already nonresumable V6 outcomes retain
`resume_blocked`. Archive/unsupported/incoherent records are not migrated at all
and remain `inspection_only` classifications outside `WorkflowRun`.

Every controller mutation entry point rejects a non-null migration disposition
before target ownership, provider, verification, or Git work. Gate 2.2 provides
no command to clear or override it. Later items may define an audited eligibility
transition only after their missing persisted contracts exist; they must not
edit or discard the original migration audit.

**Explicit operator action.** Add the same compatibility-preserving command to
the root and installed CLIs:

```text
jobs-orchestrator migrate-run <run-id>
```

The command performs strict read-only classification first, prints only the run
ID, source version/class, source SHA-256, proposed steps, treatment, and final
disposition, and asks for a default-no confirmation. Decline performs no write.
Approval re-reads and revalidates the same private source before the first
transform. Only `migrate`/compatible recognized records may proceed; current
version 7 returns `already current` without rewriting. Archive/unsupported
records receive remediation-oriented bounded diagnostics and remain untouched.

Migration is never implicit in `load_run()`, `status`, `report`, `resume`, policy
approval, writer recovery, target release, or controller save. In particular,
ordinary inspection may remove unsafe POSIX permissions under M0.6 but may not
rewrite content. There is no bulk migrate, startup sweep, recursive archive,
automatic backup, or `--force` path in this item.

**Atomic write, rollback, idempotency, and concurrency.** Migration reuses the
M0.6 secure temporary-file and atomic-replace boundary, with an additional
source compare-and-swap:

- capture the source regular-file device/inode, bytes, and SHA-256 after secure
  open;
- apply and validate every step in memory without changing the source;
- create the replacement as `0600` inside the verified `0700` run directory;
- immediately before replace, revalidate source path topology, device/inode,
  bytes, and SHA-256; and
- replace only when every value still matches, otherwise discard the temporary
  result and report `source_changed`.

For Gate 2.2, **rollback** means aborting an incomplete or failed forward
migration before atomic replacement so the original file remains byte-for-byte
unchanged. It does not mean a post-success downgrade, reverse transform, backup
restore, or deletion. After successful replacement the version-7 record and its
source hash/audit are authoritative; this item creates no retained duplicate of
the sensitive source.

Each step and final validation is side-effect free and idempotent. A handled
failure removes only the securely created temporary file when identity is
proven. An abrupt crash before replace may leave a private orphan ignored by
load/status; the source remains intact. A crash after replace but before terminal
output leaves one complete version-7 record; rerunning reports `already current`
and does not append a second audit.

Two concurrent migration commands can both inspect, but only one source
compare-and-swap can replace; the loser observes changed identity/hash and writes
nothing. New-version controller actions cannot operate on V1--V6 and therefore
cannot race through `load_run()`. An already-running older binary that ignores
version 7 is a non-cooperating process outside this local controller guarantee;
the command warns the operator to stop older Continuo processes and makes no
claim of cross-version distributed locking.

Migration does not open, claim, release, repair, or delete an M0.5 target
coordination database. It preserves target-ownership bytes and disposition but
cannot use migration as an ownership escape hatch. Provider retry budgets,
writer retry/recovery, correction counts, and human Git gates are not consumed.

**Visible read behavior and failure reporting.** No-argument `status` will add
bounded `Schema` and `Record state` columns. Each recent record is reported as
`CURRENT`, `MIGRATION_REQUIRED`, `RESUME_BLOCKED`, `ARCHIVE_ONLY`, or
`UNSUPPORTED`, with a bounded reason code rather than the single word `INVALID`.
Current version-7 rows retain task/stage/ownership metrics. Historical rows show
only safely parsed identity/version fields and no inferred workflow metrics.

`status <run-id>` retains complete sensitive JSON output only for a valid current
record. For historical/archive/unsupported input it prints the same bounded
classification and source hash, not raw JSON. `report <run-id>` works only for a
valid current record; otherwise it reports the migration/treatment requirement
and exits without reparsing provider history. Controller actions and direct
`load_run()` failures expose stable reason codes such as `migration_required`,
`future_schema`, `invalid_envelope`, `archive_only`, `source_changed`, or
`migration_step_failed`, plus a bounded field path where safe.

Diagnostics never include specification, prompts, stdout/stderr, diffs, policy
or recovery text, raw JSON, or arbitrary validation values. This item does not
add versioned `--json`; machine-readable CLI contracts remain a later Gate 2
item. The full local source file remains available to the operator outside these
commands under the M0.6 sensitive-inspection policy.

**Read/write, crash/retry, and authority effects.** Classification, status, and
report refusal are read-only apart from approved monotonic permission hardening.
Only an explicitly confirmed `migrate-run` writes one run JSON. Migration makes
no provider call, schedules no retry, changes no target file, opens no network
service, performs no Git operation, and grants no policy/commit/push/merge
authority. Deterministic parse, validation, CAS, and write failures are not
retried automatically. A human may correct external storage conditions and run
the explicit command again against the still-original source.

**Adversarial test matrix.** Tests use only the committed Gate 2.1 synthetic
fixtures, deterministic in-memory derivatives, private temporary run
directories, injected clocks/IDs/confirmation, and local child processes for
crash points. No private run, Jobs path, provider CLI, network service, target
checkout, coordination database, commit, or push is used.

| ID | Fixture / event | Required assertions |
|---|---|---|
| C1 | New run and direct `WorkflowRun` construction | Schema is exactly the version-7 constant; migration audit/disposition is absent. |
| C2 | `persist()` or `_save()` receives version 1--6, 8+, Boolean, or otherwise noncurrent state | Write is rejected before temporary creation; no version is stamped or coerced. |
| C3 | Registry inspection | Exactly one ordered callable exists for each adjacent `1 -> 2` through `6 -> 7`; skips, duplicates, gaps, and reverse requests fail closed. |
| H1 | Strict reader receives each unchanged Gate 2.1 V1--V6 fixture | Exact version/structural class/treatment/source checksum are deterministic and fixture bytes remain unchanged. |
| H2 | Current fully populated fixture is transformed to V7 | Every supplied nested value survives; source class and hash are immutable audit; disposition is deferred rather than implicitly resumable. |
| V1 | Schema-1 minimal/full stage variants | Ordered six-step migration preserves count/stage/text and records absent Sol/finding/policy/timing/failure/capability/ownership evidence. |
| V2 | Schema-2 guidance present/absent | Five-step migration preserves exact guidance state and never infers escalation history. |
| V3 | Schema-3 finding key present/absent | Four-step migration preserves supplied key and records absence without deriving a source key. |
| V4 | Schema-4 zero/multiple decisions | Three-step migration preserves decision order/text and distinguishes no recorded decision from proof none occurred. |
| V5 | Schema-5 timed/untimed attempts | Two-step migration preserves exact timing/null and never classifies historical streams or invents capability. |
| V6A | Base/provenance V6 with missing optional fields | One-step migration records the highest evidenced class and explicit absence reason codes; saved failure kind remains authoritative. |
| V6B | V6 timeout/interrupted record | Final disposition is `resume_blocked`; migration does not make provider resume available. |
| V6C | Coherent writer evidence and recovery decisions | Links/fingerprints/notes round-trip exactly under deferred disposition; no provider success or retry is fabricated. |
| V6D | Writer marker missing evidence, out-of-range/mismatched link, or contradictory fingerprints | Classification is archive-only; migration, provider, adoption, and target operations remain zero. |
| V6E | Coherent active/released/legacy ownership | Ownership bytes round-trip without database access, claim, release, or eligibility change. |
| V6F | Contradictory ownership release fields or target identity | Classification is archive-only and source remains untouched. |
| E1 | Missing/null/Boolean/string/fractional/nonpositive schema | Strict reader returns unsupported reason code before historical/current model validation. |
| E2 | Unknown past integer or version 8+ whose remaining shape matches current | It is unsupported; structural resemblance never authorizes migration or down-validation. |
| E3 | Malformed/truncated/duplicate-key/non-object/invalid-UTF-8/nonfinite JSON | It is unsupported with bounded diagnostics and no parser-dependent last-key or numeric behavior. |
| E4 | Recognized version has missing/extra field, invalid enum/type/bound, or nested contradiction | Archive versus unsupported follows the approved identity rule; no invalid field is silently dropped/defaulted. |
| M1 | `migrate-run` classification presentation | Output contains only run ID, version/class, checksum, steps, treatment, disposition, and bounded codes. |
| M2 | Default-no confirmation or explicit decline | Source bytes, inode, mtime, mode, workflow state, and all call counts remain unchanged. |
| M3 | Approved valid migration | One version-7 atomic replacement occurs with `0600`, ordered audit, source hash, and no intermediate on-disk schema. |
| M4 | Current version-7 source | Command reports already current and performs no rewrite, timestamp change, or second audit. |
| M5 | Archive/unsupported source | Command refuses before temp creation and gives bounded remediation without moving/deleting/exporting the record. |
| A1 | Exception injected before/after every adjacent transform and final validation | Original bytes/inode/mtime remain exact; in-memory partial state is discarded and no current record is fabricated. |
| A2 | Write/open/chmod/flush/replace failure | Existing M0.6 path safety holds; original survives and diagnostics disclose no content. |
| A3 | Source path/bytes/device/inode changes between read and replace | Compare-and-swap reports `source_changed`; temporary output is removed when safe and neither version wins silently. |
| A4 | Two concurrent migrations of one source | Exactly one replacement/audit exists; loser performs no write and cannot append a second migration. |
| A5 | Crash before replace | Source remains loadable as the original version; private orphan is ignored and later explicit migration succeeds once. |
| A6 | Crash immediately after replace before CLI completion | One complete V7 record exists; rerun is idempotent and reports already current. |
| R1 | No-argument status over current, migratable, blocked, archive, unsupported, and corrupt records | Schema/state/reason are distinct and bounded; invalid classes no longer collapse to indistinguishable `INVALID`. |
| R2 | `status <run-id>` on current versus noncurrent records | Current retains documented full-sensitive JSON; noncurrent returns classification only and never raw bytes. |
| R3 | `report` and every controller mutation receive noncurrent or migrated-deferred state | They refuse before provider, target coordination, verification, Git, or policy mutation with stable reason code. |
| R4 | Valid current run follows normal workflow | Existing resume, target ownership, writer recovery, approvals, reporting, and Git gates remain behaviorally unchanged. |
| P1 | Migration succeeds for a broadly permitted recognized legacy file | M0.6 hardens before read; source hash covers post-read exact bytes; replacement is private and no content enters diagnostics. |
| P2 | Symlink/hard link/foreign owner/nonregular path or unsafe run ID | Existing secure-storage rejection precedes parse/migration and never follows, replaces, claims, or quotes content. |
| P3 | Fixture contains secret-looking model prose, paths, diffs, and decisions | Classification/migration choices ignore text values; all refusal/status output remains redacted by construction. |
| B1 | Source and CLI inspection | No automatic migration, bulk sweep, backup/archive/delete, downgrade, force flag, event log, or target-database mutation exists. |
| B2 | Policy and control inspection | Correction bound/policy, display-label control flow, raw review reconstruction, approval records, and metric semantics are unchanged. |
| B3 | Compatibility smoke checks | Root CLI, `jobs-orchestrator`, `JOBS_REPO`, and `src/jobs_orchestrator` remain; both CLIs expose the same migration command. |
| B4 | Complete scope validation | No private run, Jobs access, live provider, external target, network service, commit, push, or later Gate 2/Gate 3 work occurs. |

**Explicit exclusions and later boundaries.** Gate 2.2 does not remove the fixed
`correction_cycles <= 12` model bound or persist a resolved
correction/escalation policy; those belong to the dedicated later Gate 2 item.
It does not replace display labels with stable role/provider/route IDs; persist
parsed reviews; add approval request/decision identity; correct metrics; add
versioned JSON, doctor, or general dry-run output; decide event/state
architecture; create generic config/adapters; or add verification plugins.

It also does not migrate private local records during development or validation;
the committed synthetic fixtures are the only historical inputs. It does not
retain source backups, reverse/downgrade a successful migration, create archive
directories, purge unsupported files, alter SQLite coordination schemas, solve
non-cooperating old-binary races, or make an archive/migrated record resumable.
Each requires separate authority and, where applicable, a later bounded item.

**Exit criteria:** the repository owner approves version 7, strict dispatch,
adjacent transforms, migration audit/dispositions, explicit default-no command,
atomic compare-and-swap, abort-before-replace rollback semantics, crash and
concurrency handling, bounded diagnostics, and later-item separation; every
matrix row has deterministic coverage; all existing and new tests pass without
private records or live providers; compatibility and documentation checks plus
`git diff --check` pass; the complete implementation diff is reviewed; and no
later Gate 2 item begins.