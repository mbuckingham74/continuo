# Gate 3.3 contract — Immutable normalized task envelope

**Status:** owner-approved and published; documentation-only Gate 3 deliverable; implementation is Gate 4 work.

## Decision

Every task source adapter produces one complete, immutable
`NormalizedTaskEnvelope` before a run is created. Workflow policy and provider
prompts consume that persisted envelope, never the source adapter, a live issue,
or mutable task-file contents. The envelope separates untrusted task intent
from controller authority: task text, scope, acceptance criteria, and
verification requests may narrow or request work, but cannot grant provider,
repository, Git, network, retry, approval, or publication permissions.

The initial envelope schema is `1`. It is strict, frozen, extra-forbid, fully
validated, and contains source identity, source revision, canonical text and
checksum, provenance, requested scope, explicit acceptance criteria, and
verification requests. Ordered collections retain source order, IDs are unique
within their collection, and no provider may add, delete, rewrite, or mark any
envelope field satisfied.

This Gate defines the normalized task value and the compatibility behavior of
the future `LocalMarkdownTaskSpecAdapter`. Gate 3.4 defines how repository policy
intersects task-requested scope; Gate 3.7 defines verifier catalogs, results,
and correction semantics. Gate 4 implements the adapter, persistence, schema
migration, and prompt wiring.

## Envelope schema

The persisted logical shape is:

```yaml
task_envelope_schema_version: 1
source_identity:
  task_adapter_id: local_markdown_task_spec.v1
  source_namespace: <stable adapter-owned namespace>
  source_record_id: <stable adapter-owned task identity>
source_revision:
  revision_id: <immutable adapter-owned revision token>
  source_content_sha256: <SHA-256 of exact source bytes>
canonical_text: <normalized task text>
canonical_text_sha256: <SHA-256 of canonical_text UTF-8 bytes>
provenance:
  source_kind: local_markdown
  source_locator: tasks/009-example.md
  observed_repository_head: <Git object ID or absent for a non-repository source>
scope:
  requested_paths: []
  excluded_paths: []
  constraints: []
acceptance_criteria: []
verification_requests: []
task_envelope_sha256: <SHA-256 of the canonical envelope payload excluding this field>
```

All strings are strict strings rather than coerced scalars. Stable IDs use
bounded ASCII vocabularies defined by their owning adapter/registry; display
labels and titles never serve as IDs. Hashes are 64 lowercase hexadecimal
SHA-256 values. The envelope is rejected if required fields are absent, an
unknown field exists, IDs duplicate, a collection is not a list, or any nested
record fails its closed schema.

`task_envelope_sha256` uses the deterministic JSON encoding defined by Gate 3.1:
UTF-8, lexicographically sorted object keys, no insignificant whitespace,
validated normal-form numbers, and a terminal newline. The hash covers every
field except itself. `canonical_text_sha256` separately lets readers verify the
exact provider-facing task text, while `source_content_sha256` proves which raw
source artifact the adapter observed. None is an authenticity signature.

## Source identity, revision, and provenance

`source_identity` answers which logical task this is. Its three fields form one
control identity:

- `task_adapter_id` identifies a versioned adapter contract;
- `source_namespace` identifies the adapter-owned system/project/container; and
- `source_record_id` identifies the logical task within that namespace.

`source_revision.revision_id` identifies the exact immutable source version.
It must change whenever control-relevant source content or structured metadata
changes. A timestamp, mutable title, display label, path basename, list order,
or “latest” alias is insufficient. When a source system lacks a native immutable
revision, its adapter uses a versioned content-addressed revision derived from
the exact source bytes plus all normalized metadata inputs.

`provenance` is bounded audit evidence, not authority. `source_kind` is a closed
adapter-owned value; `source_locator` is a safe redacted locator suitable for
inspection, not a path the controller later opens; and
`observed_repository_head` records the Git snapshot when applicable. Credentials,
tokens, request headers, provider text, full API responses, user-home paths from
untrusted sources, and mutable source objects are forbidden.

Two envelopes with equal source identity but different revision IDs are
different task revisions. Two adapters must not claim the same adapter ID or
namespace. The controller never merges task sources, guesses identity from
similar text, or silently upgrades a run to a newer source revision.

## Canonical text

Adapters decode source text as strict UTF-8, reject a leading or embedded NUL,
and normalize CRLF and bare CR line endings to LF. They preserve every other
Unicode code point, whitespace character, blank line, and terminal-newline
count exactly; there is no Unicode normalization, trimming, Markdown rendering,
HTML expansion, template interpolation, environment substitution, or provider
rewrite. The canonical text must contain at least one non-whitespace character
and is bounded to 1,000,000 UTF-8 bytes.

Canonical text is untrusted task intent. It may describe desired work but cannot
override the typed scope, acceptance criteria, verification requests, resolved
configuration, controller prompt boundary, or human decisions. Prompt builders
identify it as quoted task data and add controller-owned instructions outside
it. Text that resembles a system prompt, tool command, approval, route ID,
policy decision, or envelope delimiter remains inert text.

Human-approved policy decisions remain separate immutable run records. They may
clarify how the saved task is executed but never rewrite its envelope, checksum,
source identity, or revision.

## Requested scope

`scope` is always present. `requested_paths` and `excluded_paths` are ordered,
duplicate-free lists of normalized repository-relative POSIX paths or directory
prefixes ending in `/`. Version 1 forbids absolute paths, empty segments,
`.`/`..`, backslashes, NULs, wildcards, brace expansion, platform-dependent
case folding, and paths resolving through `.git`. `constraints` is an ordered
list of strict records with unique `constraint_id` and bounded nonempty `text`.

Task scope is a request, never a permission grant. Gate 3.4 must intersect it
with project/repository allowed paths and deterministic protected paths. An
empty `requested_paths` means the task did not provide a path restriction; it
does not mean every path is allowed. An excluded path always narrows the request
and cannot be canceled by task text or a requested path. Any irreconcilable or
unsupported scope blocks before provider work rather than being ignored.

## Acceptance criteria

Each criterion is an immutable record containing:

```yaml
criterion_id: <stable unique bounded ID>
text: <bounded nonempty canonical criterion text>
source_anchor: <bounded adapter-owned locator or absent>
```

Criteria retain source order. IDs are adapter-derived from explicit source IDs
or a versioned deterministic position/content rule; providers cannot invent
them. `source_anchor` is diagnostic provenance, not executable syntax. Criteria
are requirements for deterministic verification and adversarial review, but no
criterion stores mutable pass/fail state. Later verification/review records
reference criterion IDs and remain separate immutable evidence.

The local Markdown compatibility profile permits an empty criterion list
because existing Jobs task files have no required structured syntax. Empty
criteria do not imply acceptance or weaken review: canonical task text remains
authoritative task intent. A future profile may require explicit criteria only
through a new approved adapter/profile version. No probabilistic model extracts
criteria during normalization.

## Verification requests

Each optional request is an immutable record containing:

```yaml
request_id: <stable unique bounded ID>
verification_profile_id: <stable configured verifier/profile ID>
required: <strict boolean>
criterion_ids: []
```

`criterion_ids` is duplicate-free and references existing acceptance criteria;
an empty list means the request applies to the envelope as a whole. A request
contains no command, shell fragment, working directory, environment value,
credential, timeout override, or arbitrary parameter map. It asks the
controller to select an already trusted, configuration-backed verification
profile; it does not define or authorize execution.

Gate 3.7 determines result semantics. At minimum, an unknown or disallowed
required profile blocks before providers, while an optional unsupported request
must be represented visibly and may never be silently reported as executed or
passing. A task request cannot weaken a project-required verification or raise
its own execution permissions.

## Local Markdown compatibility adapter

`local_markdown_task_spec.v1` preserves the current `tasks/<task-ref>-*.md`
selection contract: the task ref uses the existing safe identifier grammar and
must resolve to exactly one matching `.md` entry. The adapter rejects an absent
or ambiguous match, invalid UTF-8, unsafe file type, symlinked file or path
component, and any resolved path outside the target root. It reads one stable
file snapshot and detects identity/content change during normalization.

Its mappings are:

- `source_namespace`: the versioned target identity key;
- `source_record_id`: the validated task ref;
- `revision_id`: `local-markdown-sha256:<source_content_sha256>`;
- `source_locator`: the exact normalized repository-relative path;
- `observed_repository_head`: the preflight Git `HEAD`;
- `canonical_text`: the normalized file contents; and
- empty typed scope, acceptance-criteria, and verification-request collections
  unless a later version defines explicit deterministic Markdown syntax.

The adapter may read a uniquely matched ignored file under the compatibility
profile because the current clean-tree behavior permits it, but records its
exact byte and canonical hashes. It never follows a link or treats filename,
front matter, headings, prose, or model output as structured scope/criteria.
Gate 3.8 must record this compatibility behavior explicitly.

## Persistence, resume, and read surfaces

The future run schema persists the complete envelope and envelope hash atomically
before target ownership or provider work. Workflow stages, prompts, reports,
correction logic, and resume read only the saved envelope. The source adapter is
not called again for that run, and a changed/deleted upstream task neither
rewrites the run nor silently changes its revision. Starting the changed task
creates a new run with a new revision/hash.

Resume blocks before external work if the saved envelope is absent where
required, malformed, hash-incoherent, unsupported, or internally inconsistent.
It never reconstructs missing fields from legacy top-level task fields, current
files, prompts, provider records, or display labels. A tracked task change that
also changes repository state remains subject to the repository resume guard.

Concise and machine-readable surfaces expose bounded identity, revision, hashes,
counts, and safe locator data, not canonical text or criterion prose by default.
Explicit sensitive inspection may show the full persisted envelope under the
existing privacy warning. Dry-run may normalize the task read-only and report
the same bounded fields, but must not persist, lock, chmod, migrate, or invoke a
provider.

Pre-envelope run schemas retain their exact historical classification. A later
migration may preserve known ref/path/text/hash fields in a visibly migrated
execution-refused envelope, but cannot invent native revision, structured scope,
criteria, verification requests, or trustworthy provenance. Gate 4 owns the
exact schema and migration matrix.

## Non-goals

This Gate adds no task adapter implementation, source API, issue tracker,
configuration loader, repository adapter, verifier, prompt change, run schema,
migration, CLI, or provider behavior. It does not parse Markdown semantics,
execute task-supplied commands, authorize paths, evaluate criteria, invoke live
providers, or access the Jobs repository. Provider commands, authority, retries,
ownership, Git gates, storage, and compatibility identifiers remain unchanged.

## Adversarial matrix

| ID | Scenario | Required outcome |
|---|---|---|
| G33-01 | An adapter omits a required field, adds an unknown field, coerces a scalar, or duplicates a nested ID. | Strict validation rejects the envelope before run creation or external work. |
| G33-02 | Source text uses CRLF versus LF with otherwise identical content. | Canonical text and its hash are identical; exact non-newline content remains unchanged. |
| G33-03 | Source contains invalid UTF-8, NUL, only whitespace, or exceeds 1,000,000 canonical UTF-8 bytes. | Normalization fails closed without a partial envelope. |
| G33-04 | Unicode confusables or normalization-equivalent code points appear. | They are preserved exactly and produce distinct hashes unless their UTF-8 text is identical. |
| G33-05 | Task text claims to change provider, policy, permission, approval, retry, Git, or envelope fields. | It remains inert untrusted text and grants no authority. |
| G33-06 | Same logical task has a different immutable source revision. | A new envelope/run records the new revision; an existing run is never silently upgraded. |
| G33-07 | Two adapters emit similar text or the same display title. | Source identity remains adapter/namespace/record based; envelopes are not merged by text or label. |
| G33-08 | Source provenance contains credentials, headers, full remote response, provider text, or unsafe locator data. | Validation/redaction rejects it; sensitive data is not persisted as provenance. |
| G33-09 | Requested or excluded path is absolute, traversing, wildcarded, backslash-based, `.git`-resolving, or otherwise noncanonical. | Envelope validation fails before repository/provider work. |
| G33-10 | Requested scope is empty. | It means no task-stated restriction and grants no path; project/repository policy remains authoritative. |
| G33-11 | Requested path exceeds project allowance or conflicts with an exclusion/protected path. | Later deterministic scope intersection blocks or narrows it; task data never widens authority. |
| G33-12 | Acceptance criteria are absent in an existing Jobs Markdown task. | Compatibility envelope stores an empty list without implying PASS or probabilistically inventing criteria. |
| G33-13 | Criterion IDs duplicate or a verification request references a missing criterion. | Validation rejects the envelope. |
| G33-14 | Provider claims a criterion passed or proposes a new criterion. | Envelope remains immutable; only separate typed evidence may reference existing IDs. |
| G33-15 | Verification request embeds a command, environment, path, timeout override, or arbitrary parameters. | Closed request schema rejects it; tasks cannot define executable verification. |
| G33-16 | Required verification profile is unknown/disallowed, or optional profile is unsupported. | Required blocks before providers; optional is visibly unsupported and never silently treated as PASS. |
| G33-17 | Local task ref matches zero or multiple Markdown files. | Adapter fails visibly with no envelope or provider call. |
| G33-18 | Matched local task is a symlink, traverses outside the root, changes during read, or is not a regular safe file. | Adapter fails closed and never follows or accepts the unstable source. |
| G33-19 | A uniquely matched ignored local task is used by the Jobs compatibility profile. | Exact source/canonical hashes and locator are recorded; no structured semantics are inferred. |
| G33-20 | Upstream task changes or disappears after run creation. | Existing run uses its persisted envelope; it never refreshes or changes revision during resume. |
| G33-21 | Persisted envelope/hash is corrupt or unsupported. | Inspection reports bounded failure and resume blocks before providers, ownership continuation, or Git. |
| G33-22 | Dry-run normalizes the task. | It reports bounded identity/revision/hash/count data without persisting, locking, repairing, or revealing task text. |
| G33-23 | Historical run has only ref/path/text/hash fields. | Migration never invents scope, criteria, verification requests, native revision, or trustworthy provenance; execution remains refused unless separately proven. |
| G33-24 | Proposal adds adapter runtime, Markdown parsing, repository authorization, verifier execution, schema migration, or later Gate work. | It is out of scope pending its separately approved contract and implementation gate. |

## Approval and implementation evidence

The owner approved this Gate 3.3 contract on 2026-08-03. Gate 3.3 is a
documentation-only contract-definition deliverable: task-adapter implementation,
prompt wiring, persisted envelope fields, schema migration, CLI/dry-run changes,
and installed-package validation belong to the explicitly later Gate 4 work.
No runtime source, test, fixture, run record, provider, target checkout, Git
side effect, commit, or push changed for this Gate.

Publication evidence: commit `21fbd4fbf1afa4266765c08bc368bf1f509c91d3`
(`Define normalized task envelope`) is on `origin/main`.

The approved contract derives from the Gate 3.3 tracker item,
Milestone 2 task-adapter/envelope/acceptance-criteria requirements and exit
criteria, the current `resolve_task()`, `WorkflowRun` task fields, prompt
builders, dry-run task summary, resume/persistence paths, and deterministic
tests. Baseline is clean synchronized `main` at
`981813befbd30017797336f9ff485fefd95cba94`.

Validation confirmed authoritative local Markdown links, all 24 `G33-*` IDs
unique, and tracked/new-file `git diff --check` whitespace checks passing. No
live-provider or Jobs-repository validation was performed.
