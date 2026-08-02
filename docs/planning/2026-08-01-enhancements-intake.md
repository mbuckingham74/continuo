# Enhancements intake: preserved source

> **Status:** Intake evidence, preserved for traceability. The content below was imported from `OE_ENHANCEMENTS.md` on 2026-08-01. It contains proposed findings and recommendations, not accepted requirements. Decisions and corrections belong in [the validated roadmap](../ENGINE_ROADMAP.md).

---

# Orchestration Engine — Enhancements and Orchestration Features

**Repo:** `mbuckingham74/orchestration-engine`
**Review date:** 2026-08-01
**Companion document:** `CRITICAL_FIXES.md`

> Nothing here should be built before **C-1** and **C-2** in the critical fixes document. **C-5** (role enum) and **E-1** (event log) are prerequisites for most of what follows.

---

## Framing: what you've actually built

Worth naming before the recommendations, because it changes what prior art is relevant.

This is not a chat-agent framework. It's a **durable-execution workflow engine with human approval gates** that happens to use models as activities. The tell-tale signs are all present:

- `provider_resume_stage` + `provider_resume_prompt` = activity checkpointing
- persist-before-and-after-the-boundary = write-ahead logging
- bounded same-provider retries with no fallback = activity retry policy with fixed routing
- `blocked_*` stages = named terminal states requiring external input

That is a more defensible design than the prompt-orchestrated alternatives, and the instinct to keep policy in Python is the conclusion most teams reach only after a year of the other approach. The enhancements below mostly consist of *finishing* the durable-execution model rather than adding agent features.

### Prior art worth reading (not adopting)

| System | What to steal | Why not adopt |
|---|---|---|
| **Temporal** (or Restate, Inngest) | Determinism and replay rules; the activity/idempotency-key model; how they separate workflow code from side effects | Heavy runtime for a single-operator tool. Read the docs, keep your Python. |
| **LangGraph** | Closest structural match: explicit node graph, checkpointed state, human-in-the-loop `interrupt`/resume semantics | Its checkpointer is weaker than what you'd end up with in E-1, and adopting it would mean giving up your explicit authority model. |
| **GitHub Actions** | The verification-plugin shape is a job matrix. Steal `timeout-minutes`, `continue-on-error`, artifact capture, and the step-result data model | Not an orchestrator for this. |
| **CrewAI / AutoGen / agent SDKs** | Mostly a control group — they do the model-decided-handoff thing you deliberately avoided | Confirms your instinct; little to import. |

---

## E-1 — Append-only event log, before any UI

**Priority:** Highest of the enhancements. Everything else is easier after this.

A UI is a projection of a data model. Mutable run JSON is a poor projection surface: you can't diff it, you can't stream it, you can't answer "what happened at 14:32" without re-reading the whole blob, and two writers clobber each other. Build the UI on it and you will write the UI twice.

### Shape

```python
class Event(BaseModel):
    event_id: str
    run_id: str
    seq: int                 # monotonic per run
    occurred_at: str
    kind: EventKind
    payload: dict[str, object]
```

Event kinds, roughly matching your existing transitions:

```
run_created            stage_entered           provider_attempt_started
provider_attempt_ended review_recorded         verification_ran
policy_requested       policy_approved         approval_requested
approval_decided       git_operation           run_blocked
```

Current state becomes a fold over events. Keep emitting `runs/<id>.json` as a materialized view for compatibility and human inspection, but make the log the source of truth.

### What it unlocks

- **UI** — the run detail page is a rendered event stream; live updates are `seq > last_seen`.
- **`report`** — a fold, not bespoke `Counter` logic scattered through `_run_report`.
- **Cross-run analytics** (E-8) — one query surface instead of globbing JSON files.
- **Replay** (E-7) — replay is literally re-folding the log.
- **Tamper evidence** — hash-chain each event (`prev_hash` in the payload) and you get the audit property your docs ask for, nearly for free.

SQLite is entirely sufficient. One table, `PRIMARY KEY (run_id, seq)`, JSON payload column. Export to JSON stays trivial.

---

## E-2 — UI, in ascending order of effort

### Tier 0 — `--json` on every CLI command (about an hour)

Add `--json` to `status`, `report`, `run`, `resume`. This alone:

- makes everything pipeable to `jq`
- lets any UI be a **separate process** that never imports the controller — which preserves your "the controller is the only writer" invariant by construction
- gives you a stable contract to build the UI against

Do this first regardless of which UI tier you pick.

### Tier 1 — Rich live view (a few hours)

You already depend on `rich`. A `rich.live.Live` panel during a run showing stage, elapsed, current provider, heartbeat age, correction count, and current finding streak is close to free and removes most of the "is it stuck?" anxiety that motivates a UI in the first place.

### Tier 2 — Textual TUI (a weekend)

No server, no ports, no auth. Run list with stage badges, run detail pane, live provider heartbeats, keyboard approve/deny on gates. Good if you'd rather not run a web service on your machine.

### Tier 3 — Local web dashboard (a week)

FastAPI + SSE + one HTML page. Not a SPA framework; the data model does the work.

**Run list:** ID, task ref, stage badge (color-coded by class: running / awaiting-human / blocked / done), age, cost, correction count.

**Run detail:**
- Stage timeline with durations — makes it obvious where time actually goes
- Provider attempts table: role, model, duration, exit code, failure kind, retry flag, cost
- Findings grouped by `finding_key`, **with streak count visible** — this is the single most useful view in the whole system, because streak is the thing that decides escalation and right now it's invisible until it blocks
- The diff, rendered
- Terra recommendation and Sol guidance, verbatim
- Approval gate buttons (see E-3)

The reason to prefer web over TUI is one thing only: **approving from your phone**. See E-3.

---

## E-3 — Asynchronous approval gates (the real unlock)

**This is the highest-value feature in this document.**

Today, `typer.confirm` means a human must be sitting at the terminal that owns the process, for a run that may take 40 minutes, at an unpredictable moment. That single design detail is what keeps this a supervised script rather than a system.

### Redesign

1. The controller reaches a gate, writes `approval_requested` to the log, sets stage `awaiting_commit_approval`, and **exits cleanly**.
2. A human decides from anywhere: CLI (`approve <run-id> --gate commit`), TUI, browser, phone.
3. The decision is appended as an `approval_decided` event with `{gate, decision, approved_by, approved_at, run_fingerprint}`.
4. A worker (or the next `resume`) consumes it, re-runs the resume guard, and proceeds.

```
POST /runs/{id}/approve
{ "gate": "commit", "decision": "approve", "approved_by": "matt" }
```

### Why this is strictly better than what you have

- **Approval becomes an auditable artifact.** Right now the human's decision exists only as a bool returned from `typer.confirm` — the run file records the resulting git operation but not who approved it or when (see quick-hit Q-3). For a system whose entire selling point is auditability, that's a real gap.
- **The gate can be re-verified.** Record the working-tree fingerprint *at request time* and compare at consumption time. If the tree changed between asking and approving, refuse — a property the interactive prompt can't offer at all.
- **Default-to-no is preserved.** No decision recorded = no action. The invariant survives the redesign.
- **You can walk away from a run.** Which is the whole point.

Add a notification hook at gate creation (ntfy, Pushover, a local webhook, macOS notification). Long runs plus silent gates is how work sits idle overnight.

---

## E-4 — Capture cost and token telemetry you're already throwing away

The Claude CLI's `--output-format json` envelope already carries `total_cost_usd`, `duration_ms`, `num_turns`, and `session_id`. You parse the envelope and discard all of it.

```python
class ProviderRecord(BaseModel):
    ...
    cost_usd: float | None = None
    num_turns: int | None = None
    session_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
```

Check whether `codex exec` exposes an equivalent structured/JSON output mode; if not, record what you can and leave the fields null rather than blocking on parity.

### Why it matters more than it sounds

Once you have per-role cost, `report` can show **cost per defect actually fixed** — corrections that converged, divided by spend. That is the number that tells you whether adversarial review is earning its keep, whether escalation to Sol is worth two extra round trips, and whether a cheaper reviewer would do. Without it you're optimizing an expensive loop blind.

Add to `report`: cost by role, cost per correction cycle, and cost of runs that ultimately blocked (i.e. money spent on work that never shipped).

---

## E-5 — Verification plugins: the biggest quality lever

Right now, correctness is "a model read a diff." A model reading a diff cannot tell you the tests pass. It can tell you the diff *looks* like it should work, which is a meaningfully weaker claim and the one your docs already flag in §15.

### Design

Per-project config at the *target repo's* root:

```toml
# orchestration.toml
[[verify]]
name     = "test"
command  = ["npm", "test", "--", "--run"]
timeout  = 600
severity = "blocking"
network  = false

[[verify]]
name     = "typecheck"
command  = ["npx", "tsc", "--noEmit"]
timeout  = 180
severity = "blocking"

[[verify]]
name     = "lint"
command  = ["npx", "eslint", "."]
timeout  = 120
severity = "advisory"

[scope]
allowed_paths = ["src/**", "tests/**"]
```

Each result persists as its own record: name, command, exit code, duration, output tail, severity.

### What changes about the system

1. **Blocking failure skips the reviewer entirely.** Go straight to correction with the failing test output attached. That's faster, cheaper, and far more reliable than paying a model to notice what a failing assertion already proved.
2. **The reviewer adjudicates evidence instead of speculating.** Feed verification results *into* the review prompt. Sonnet's job becomes "does this implementation match the spec, given that tests pass and types check" — a much better-posed question.
3. **Scope becomes deterministic.** `allowed_paths` is a hard check on `changed_files()`. That's strictly better than asking a model to detect `SCOPE_VIOLATION`, and it removes a whole review category from the probabilistic side of the line.

Start with three plugins (test, typecheck, lint) and the path allowlist. That's most of the value.

**Note the interaction with C-2:** plugins need the same timeout and process-group-kill machinery as providers. Build it once, use it for both.

---

## E-6 — Two-reviewer adversarial mode

You have three vendors available and you're using exactly one reviewer. That makes Sonnet a single point of judgment failure: if it's wrong, **nothing catches it** — the run proceeds straight to the commit gate with a human who is, realistically, trusting the PASS.

For a project whose name is *adversarial* review, that's the obvious gap.

### Design

```toml
[review]
mode = "dual"                      # "single" | "dual"
second_reviewer = "policy_authority_model"   # different vendor
require_agreement = true
```

- Both reviewers get the same fresh-context prompt, independently.
- Both PASS → proceed to the commit gate.
- Both FAIL → use the primary's `finding_key`; note agreement in the record.
- **Disagreement → route to the human**, showing both results side by side. Not to a tiebreaker model — disagreement between competent reviewers is exactly the signal that a human should look.

Roughly 50 lines given your existing structure, and once `Role` is an enum (C-5) the second reviewer is a config entry rather than a code change. Make it opt-in per task risk level so you're not doubling cost on trivial work.

This is a feature most orchestration frameworks don't offer, and it's the natural payoff of having built multi-provider routing in the first place.

---

## E-7 — Replay mode

You already persist every prompt and every stdout. That means deterministic replay is nearly free:

```
uv run python orchestrator.py replay <run-id> [--from-stage reviewing]
```

Feed recorded provider outputs back through the controller instead of calling providers. Then:

- **Controller changes get regression-tested against real history.** Change the escalation policy, replay ten real runs, diff the resulting stages. Your 19 unit tests use synthetic fakes; replay uses actual model output with all its real-world messiness.
- **Bug reproduction becomes trivial.** A weird block is reproducible offline, for free, as many times as you like.
- **New reviewers can be evaluated offline** — replay the same diffs against a candidate model and compare findings to what actually happened.

The only new requirement is that provider invocation be swappable by run configuration rather than only by constructor injection — which it nearly is already, given `Controller.__init__` takes the four callables.

---

## E-8 — Queue, worker, and cross-run circuit breaker

Once approval gates are async (E-3), a queue follows naturally:

```
uv run python orchestrator.py enqueue 009 010 011
uv run python orchestrator.py worker          # one repo lock, one active run
```

- One `flock` per target repo (quick-hit Q-2) enforces the single-active-run invariant.
- Tasks blocked on human approval yield the lock so the next task can start.

**Cross-run circuit breaker:** record provider failures to shared state (`{provider, kind, occurred_at}`). If `quota` was hit 90 seconds ago, run 3 should refuse to start rather than burn twenty minutes rediscovering the same wall. With SQLite this is a single query in preflight. Your docs call this out in §12 — it's cheap once the log exists.

---

## E-9 — De-hardcoding, in dependency order

1. **`Role` enum** (C-5). Everything else depends on it.
2. **Config file at the target project root** — `orchestration.toml`: role→model routes, verification plugins, path allowlist, commit-message template, escalation budgets. Resolve, validate, hash, and **persist with the run** so a resumed run can't silently inherit changed rules.
3. **Provider adapters** — one interface: build command / invoke / normalize errors / parse structured output / report telemetry / declare capabilities. Codex CLI and Claude CLI become two implementations; a hosted-API adapter becomes a third without touching the controller.
4. **Capability profiles** — declare `filesystem: none|read|write`, `network: bool`, `structured_output: bool`, `cancellable: bool` per route, and **reject at startup** a route whose capabilities exceed its role's policy. This turns least-authority from a collection of CLI flags into a checked invariant, which is the actual goal of §22.3.
5. **Renames, last** — `JOBS_REPO` → `ORCHESTRATOR_REPO` (accept the old name for one release), `jobs-orchestrator` → `orchestration-engine`, `src/jobs_orchestrator/` → `src/orchestration_engine/`, and templatize the `Implement task <ref>` commit message.

Do the renames last. They're the most visible and the least valuable, and doing them first would break the tests you'll need while doing the rest.

---

## E-10 — Smaller wins

| Item | Value |
|---|---|
| **Task-spec adapter interface** | Even a stub with one implementation (local Markdown) makes GitHub Issues / Linear a later config change rather than surgery. Normalize to `{source_id, revision, text, checksum, scope, acceptance_criteria}`. |
| **Acceptance criteria in the spec** | Have the spec carry explicit machine-checkable criteria; feed them to the reviewer as a checklist rather than relying on prose interpretation. Sharply reduces `POLICY_AMBIGUITY` findings. |
| **Dry-run / plan mode** | Run spec review and print the implementation prompt without invoking the writer. Cheap way to sanity-check a spec before spending 40 minutes. |
| **`doctor` command** | Preflight check: CLIs present and authenticated, models resolvable, repo clean, config valid. Turns a class of `blocked_provider_configuration` into a five-second check. |
| **Structured log sink** | JSON lines to stderr alongside the human-readable rich output. Makes `tail -f \| jq` work while a run is in flight, and costs nothing once E-1 exists. |
| **Run cost ceiling** | `--max-cost-usd` per run, checked between stages, blocking as `blocked_cost_budget`. You have correction budgets; you don't have a spend budget. |

---

## Suggested sequencing

| Phase | Contents | Rationale |
|---|---|---|
| **0** | Critical fixes C-1 through C-6 | Don't build on the seams that are currently broken. |
| **1** | E-1 (event log), E-4 (cost telemetry), Tier 0 `--json` | The data substrate. Cheap, and everything downstream assumes it. |
| **2** | E-3 (async gates) + notifications, Q-2 (run lock) | The single biggest change in day-to-day usability. |
| **3** | E-5 (verification plugins) + path allowlist | The single biggest change in output quality. |
| **4** | E-2 Tier 2 or 3 (TUI or web UI) | Now genuinely worth building, because there's a real model behind it. |
| **5** | E-7 (replay), E-6 (dual review), E-8 (queue) | Leverage features. Replay first — it de-risks everything after it. |
| **6** | E-9 (adapters, config, renames) | The generalization work your roadmap describes, now on solid ground. |

The through-line: **finish the durable-execution model before adding orchestration features.** Every item in phases 2–6 is dramatically cheaper once the event log exists and approval is asynchronous.
