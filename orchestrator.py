"""Deterministic jobs-repository orchestration controller."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import typer
from rich.console import Console
from rich.table import Table

from models import GitRecord, PolicyDecision, ProviderRecord, RepoState, ReviewResult, WorkflowRun
from providers import (
    DEFAULT_REPO,
    ProviderExecution,
    execute_luna_implementation,
    execute_sonnet_review,
    execute_sol_escalation,
    execute_terra_resolution,
    classify_provider_failure,
    parse_sonnet_review,
)

app = typer.Typer(
    no_args_is_help=True,
    help="Continuo — deterministic notation for probabilistic work.",
)
console = Console()
RUNS = Path(__file__).parent / "runs"


class ControllerError(RuntimeError):
    pass


def configured_repo(explicit: Path | None = None) -> Path:
    return (explicit or Path(os.environ.get("JOBS_REPO", str(DEFAULT_REPO)))).expanduser().resolve()


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ControllerError(f"git {' '.join(args)} failed: {detail}")
    return result


def _git_bytes(
    repo: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (
            result.stderr.decode(errors="replace").strip()
            or result.stdout.decode(errors="replace").strip()
        )
        raise ControllerError(f"git {' '.join(args)} failed: {detail}")
    return result


def git_text(repo: Path, *args: str) -> str:
    return _git(repo, *args).stdout.strip()


def repo_state(repo: Path) -> RepoState:
    if not repo.exists() or not repo.is_dir():
        raise ControllerError(f"jobs repo does not exist: {repo}")
    root = Path(git_text(repo, "rev-parse", "--show-toplevel")).resolve()
    if root != repo.resolve():
        raise ControllerError(f"configured jobs repo is not the Git root: {repo}")
    branch = git_text(repo, "branch", "--show-current")
    if not branch:
        raise ControllerError("jobs repo must be on a named branch; detached HEAD is unsafe")
    origin = git_text(repo, "remote", "get-url", "origin")
    return RepoState(
        repo=str(repo),
        branch=branch,
        head=git_text(repo, "rev-parse", "HEAD"),
        clean=not bool(git_text(repo, "status", "--porcelain=v1", "--untracked-files=all")),
        origin=origin,
    )


def resolve_task(repo: Path, task_ref: str) -> tuple[str, Path, str]:
    """Resolve exactly one tasks/<ref>-*.md and return its relative name/content."""

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", task_ref or ""):
        raise ControllerError("task-ref must be a single task identifier, such as 009")
    task_dir = repo / "tasks"
    matches = sorted(task_dir.glob(f"{task_ref}-*.md"))
    if len(matches) == 0:
        raise ControllerError(f"no task specification matches tasks/{task_ref}-*.md")
    if len(matches) > 1:
        names = ", ".join(str(p.relative_to(repo)) for p in matches)
        raise ControllerError(f"task-ref is ambiguous; matches: {names}")
    path = matches[0]
    content = path.read_text(encoding="utf-8")
    return str(path.relative_to(repo)), path, content


_PORCELAIN_V1_STATUS_BYTES = frozenset(b" MADRCUT?!X")


def _decode_porcelain_path(raw: bytes) -> str:
    try:
        path = os.fsdecode(raw)
    except UnicodeError as exc:
        raise ControllerError(
            "Git path cannot be decoded with the platform filesystem encoding"
        ) from exc
    if os.fsencode(path) != raw:
        raise ControllerError(
            "Git path does not round-trip through the platform filesystem encoding"
        )
    try:
        path.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ControllerError(
            "Git path cannot be represented by the current UTF-8 run-state format"
        ) from exc
    return path


def _parse_porcelain_v1_z(output: bytes) -> list[str]:
    files: list[str] = []
    cursor = 0

    while cursor < len(output):
        if len(output) - cursor < 4:
            raise ControllerError("Git porcelain status contains a truncated record")

        status = output[cursor : cursor + 2]
        if any(value not in _PORCELAIN_V1_STATUS_BYTES for value in status):
            raise ControllerError(
                "Git porcelain status contains an invalid status code"
            )
        if output[cursor + 2] != 0x20:
            raise ControllerError(
                "Git porcelain status record is missing its path separator"
            )

        path_start = cursor + 3
        path_end = output.find(b"\0", path_start)
        if path_end < 0:
            raise ControllerError("Git porcelain status contains an unterminated path")
        if path_end == path_start:
            raise ControllerError("Git porcelain status contains an empty path")

        files.append(_decode_porcelain_path(output[path_start:path_end]))
        cursor = path_end + 1

        if status[0] in b"RC" or status[1] in b"RC":
            source_end = output.find(b"\0", cursor)
            if source_end < 0:
                raise ControllerError(
                    "Git porcelain rename/copy record is missing its source path"
                )
            if source_end == cursor:
                raise ControllerError(
                    "Git porcelain rename/copy record contains an empty source path"
                )
            files.append(_decode_porcelain_path(output[cursor:source_end]))
            cursor = source_end + 1

    return sorted(set(files))


def changed_files(repo: Path) -> list[str]:
    result = _git_bytes(
        repo,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    return _parse_porcelain_v1_z(result.stdout)


def working_tree_fingerprint(repo: Path, files: list[str] | None = None) -> str:
    digest = hashlib.sha256()
    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout
    digest.update(status.encode())
    for command in (("diff", "--no-ext-diff", "--binary"), ("diff", "--cached", "--no-ext-diff", "--binary")):
        digest.update(_git(repo, *command).stdout.encode())
    for relative in files if files is not None else changed_files(repo):
        path = repo / relative
        digest.update(relative.encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def diff_check(repo: Path) -> tuple[bool, str]:
    result = _git(repo, "diff", "--check", check=False)
    return result.returncode == 0, (result.stdout + result.stderr).strip()


def persist(run: WorkflowRun, runs_dir: Path = RUNS) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"{run.run_id}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(run.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def load_run(run_id: str, runs_dir: Path = RUNS) -> WorkflowRun:
    path = runs_dir / f"{run_id}.json"
    if not path.exists():
        raise ControllerError(f"unknown run {run_id}")
    try:
        return WorkflowRun.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ControllerError(f"run state is invalid: {path}: {exc}") from exc


def _record_provider(
    run: WorkflowRun,
    purpose: str,
    provider: str,
    execution: ProviderExecution,
) -> None:
    if execution.attempts:
        for attempt in execution.attempts:
            run.provider_runs.append(
                ProviderRecord(
                    provider=provider,
                    purpose=purpose,
                    command=attempt.command,
                    returncode=attempt.returncode,
                    stdout=attempt.stdout,
                    stderr=attempt.stderr,
                    duration_seconds=attempt.duration_seconds,
                    failure_kind=attempt.failure_kind,
                    retry_scheduled=attempt.retry_scheduled,
                )
            )
        return

    run.provider_runs.append(
        ProviderRecord(
            provider=provider,
            purpose=purpose,
            command=execution.command,
            returncode=execution.returncode,
            stdout=execution.stdout,
            stderr=execution.stderr,
            duration_seconds=execution.duration_seconds,
            failure_kind=(
                execution.failure_kind
                or classify_provider_failure(
                    execution.returncode,
                    execution.stdout,
                    execution.stderr,
                )
            ),
        )
    )


def _record_git(run: WorkflowRun, operation: str, result: subprocess.CompletedProcess[str]) -> None:
    run.git_operations.append(
        GitRecord(
            operation=operation,
            command=["git", *result.args[3:]] if isinstance(result.args, list) else ["git"],
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    )


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unavailable"
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _report_review_history(run: WorkflowRun) -> list[ReviewResult]:
    history: list[ReviewResult] = []
    for record in run.provider_runs:
        if record.provider != "Sonnet 5 High" or record.purpose != "implementation":
            continue
        execution = ProviderExecution(
            command=record.command,
            returncode=record.returncode,
            stdout=record.stdout,
            stderr=record.stderr,
            duration_seconds=record.duration_seconds,
        )
        try:
            history.append(parse_sonnet_review(execution))
        except Exception:
            continue
    return history


def _run_report(run: WorkflowRun) -> dict[str, object]:
    provider_counts = Counter(record.provider for record in run.provider_runs)
    provider_seconds: Counter[str] = Counter()
    untimed_counts: Counter[str] = Counter()
    failure_counts: Counter[str] = Counter()
    retry_attempts = 0

    for record in run.provider_runs:
        if record.duration_seconds is None:
            untimed_counts[record.provider] += 1
        else:
            provider_seconds[record.provider] += record.duration_seconds
        if record.failure_kind:
            failure_counts[record.failure_kind] += 1
        if record.retry_scheduled:
            retry_attempts += 1

    reviews = _report_review_history(run)
    defect_keys = {
        _finding_key(review)
        for review in reviews
        if review.category not in {"PASS", "POLICY_AMBIGUITY"}
    }

    verification_runs = sum(
        1
        for record in run.provider_runs
        if record.provider == "Luna High"
        and record.purpose in {"implementation", "correction"}
        and record.returncode == 0
    )

    wall_seconds: float | None = None
    if run.updated_at:
        try:
            created = datetime.fromisoformat(run.created_at)
            updated = datetime.fromisoformat(run.updated_at)
            wall_seconds = max(0.0, (updated - created).total_seconds())
        except ValueError:
            pass

    pushed = any(
        record.operation == "push" and record.returncode == 0
        for record in run.git_operations
    )

    return {
        "provider_counts": dict(provider_counts),
        "provider_seconds": dict(provider_seconds),
        "untimed_counts": dict(untimed_counts),
        "provider_seconds_total": sum(provider_seconds.values()),
        "provider_calls_total": len(run.provider_runs),
        "provider_failure_counts": dict(failure_counts),
        "provider_retry_attempts": retry_attempts,
        "wall_seconds": wall_seconds,
        "corrections": run.correction_cycles,
        "distinct_defects": len(defect_keys),
        "sol_escalations": provider_counts.get("Sol High", 0),
        "policy_decisions": len(run.policy_decisions),
        "verification_runs": verification_runs,
        "final_review": (
            run.implementation_review.status
            if run.implementation_review is not None
            else None
        ),
        "committed": run.commit_hash is not None,
        "commit_hash": run.commit_hash,
        "pushed": pushed,
    }


def _print_run_report(run: WorkflowRun) -> None:
    report = _run_report(run)

    console.print(f"\n[bold]Run {run.run_id} — {run.stage}[/bold]")
    console.print(
        "Wall-clock span: "
        + _format_duration(report["wall_seconds"])
    )

    total_calls = int(report["provider_calls_total"])
    console.print(f"Provider calls: {total_calls}")

    counts = report["provider_counts"]
    seconds = report["provider_seconds"]
    untimed = report["untimed_counts"]
    preferred = ("Luna High", "Sonnet 5 High", "Sol High", "Terra High")
    providers = list(preferred)
    providers.extend(
        provider for provider in counts
        if provider not in providers
    )

    for provider in providers:
        count = counts.get(provider, 0)
        if not count:
            continue
        timed = seconds.get(provider, 0.0)
        legacy = untimed.get(provider, 0)
        timing = _format_duration(timed) if timed else "no recorded timing"
        suffix = f"; {legacy} untimed legacy" if legacy else ""
        console.print(f"  {provider}: {count} call(s), {timing}{suffix}")

    total_timed = float(report["provider_seconds_total"])
    total_untimed = sum(untimed.values())
    provider_time = _format_duration(total_timed) if total_timed else "no recorded timing"
    legacy_suffix = f" ({total_untimed} untimed legacy call(s))" if total_untimed else ""
    console.print(f"Provider time: {provider_time}{legacy_suffix}")

    failures = report["provider_failure_counts"]
    console.print(
        f"Infrastructure/provider failures: {sum(failures.values())}"
    )
    for kind, count in sorted(failures.items()):
        console.print(f"  {kind}: {count}")
    console.print(
        f"Same-provider retries: {report['provider_retry_attempts']}"
    )

    console.print(f"Corrections: {report['corrections']}")
    console.print(f"Distinct defects: {report['distinct_defects']}")
    console.print(f"Sol escalations: {report['sol_escalations']}")
    console.print(f"Verification runs: {report['verification_runs']}")

    console.print(f"Policy decisions: {report['policy_decisions']}")
    for decision in run.policy_decisions:
        text = " ".join(decision.approved_text.split())
        if len(text) > 110:
            text = text[:107] + "..."
        console.print(f"  {decision.decision_id}: {text}")

    final_review = report["final_review"] or "not available"
    console.print(f"Final review: {final_review}")

    if report["committed"]:
        console.print(f"Git commit: {report['commit_hash']}")
    else:
        console.print("Git commit: not made")

    console.print(f"Git push: {'completed' if report['pushed'] else 'not completed'}")


def _task_prompt(run: WorkflowRun) -> str:
    approved = ""
    if run.policy_decisions:
        decisions = "\n".join(
            f"- {decision.decision_id}: {decision.approved_text}"
            for decision in run.policy_decisions
        )
        approved = (
            "\n\nHuman-approved policy decisions (authoritative for this run):\n"
            + decisions
        )

    return (
        f"Task specification ({run.task_file}):\n\n{run.specification}"
        + approved
        + "\n\nImplement only this task in the jobs repository. Preserve useful existing code. "
        "Use deterministic Python for workflow/control decisions; do not invent policy."
    )


def _spec_review_prompt(run: WorkflowRun) -> str:
    return (
        "You are performing a fresh-context, read-only specification review. Read the task "
        "specification below. Return PASS only when it is sufficiently precise and bounded "
        "for implementation without inventing policy. Categorize a failure as exactly one "
        "of IMPLEMENTATION_DEFECT, POLICY_AMBIGUITY, or SCOPE_VIOLATION. Always return a "
        "finding_key: use PASS when passing; otherwise use a concise stable kebab-case defect "
        "identity. Do not edit files.\n\n"
        + _task_prompt(run)
    )


def _implementation_review_prompt(run: WorkflowRun, diff: str) -> str:
    prior = _implementation_review_history(run)[-8:]
    prior_text = "\n".join(
        f"- {_finding_key(item)}: {item.summary[:500]}"
        for item in prior
    ) or "(none)"

    return (
        "You are performing a fresh-context, read-only adversarial implementation review. "
        "Compare the actual changed files and diff to the task specification. Return PASS "
        "only if the implementation is correct. Otherwise categorize exactly one failure "
        "as IMPLEMENTATION_DEFECT, POLICY_AMBIGUITY, or SCOPE_VIOLATION. Always return a "
        "finding_key. Use PASS when passing. For a failure, use a concise stable kebab-case "
        "defect identity. If the current failure is materially the same defect as a prior "
        "finding below, REUSE THAT EXACT finding_key. If it is genuinely a different defect, "
        "create a new key. Do not edit files.\n\n"
        + _task_prompt(run)
        + "\n\nPrior implementation QA findings:\n"
        + prior_text
        + "\n\nChanged files:\n"
        + "\n".join(run.changed_files)
        + "\n\nDiff:\n"
        + diff
    )


def _terra_prompt(run: WorkflowRun, reason: str) -> str:
    return (
        "Read-only policy/architecture clarification. Do not edit files, run Git mutations, "
        "or decide whether to proceed. Identify the precise missing policy decision and "
        "propose a narrowly worded resolution for a human to approve.\n\n"
        f"Reason: {reason}\n\n{_task_prompt(run)}"
    )


def _implementation_diff(repo: Path) -> str:
    tracked = _git(repo, "diff", "HEAD", "--no-ext-diff").stdout
    untracked = [
        relative
        for relative in changed_files(repo)
        if not _git(
            repo,
            "ls-files",
            "--error-unmatch",
            "--",
            relative,
            check=False,
        ).stdout.strip()
    ]
    extra = []
    for relative in untracked:
        path = repo / relative
        if path.is_file():
            extra.append(f"\n--- untracked: {relative}\n{path.read_text(encoding='utf-8', errors='replace')}")
    return tracked + "".join(extra)


def _stageable_changed_files(repo: Path, files: list[str]) -> list[str]:
    """Exclude absent paths whose deletion is already represented in the index."""

    return [
        relative
        for relative in files
        if os.path.lexists(repo / relative)
        or bool(
            _git(
                repo,
                "ls-files",
                "--error-unmatch",
                "--",
                relative,
                check=False,
            ).stdout.strip()
        )
    ]


MAX_TOTAL_CORRECTIONS = 12
MAX_SOL_ESCALATIONS_PER_FINDING = 2


def _finding_key(result: ReviewResult) -> str:
    if result.finding_key:
        return result.finding_key
    normalized = " ".join(result.summary.lower().split())
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    return f"legacy-{digest}"


def _implementation_review_history(run: WorkflowRun) -> list[ReviewResult]:
    findings: list[ReviewResult] = []
    for record in run.provider_runs:
        if record.provider != "Sonnet 5 High" or record.purpose != "implementation":
            continue
        try:
            result = parse_sonnet_review(
                ProviderExecution(
                    record.command,
                    record.returncode,
                    record.stdout,
                    record.stderr,
                )
            )
        except Exception:
            continue
        if result.category != "PASS":
            findings.append(result)
    return findings


def _current_finding_streak(run: WorkflowRun, result: ReviewResult) -> int:
    target = _finding_key(result)
    streak = 0
    for prior in reversed(_implementation_review_history(run)):
        if _finding_key(prior) != target:
            break
        streak += 1
    return max(1, streak)


def _sol_prompt(
    run: WorkflowRun,
    repo: Path,
    finding_key: str,
    escalation_round: int,
) -> str:
    findings = _implementation_review_history(run)[-8:]
    history = "\n\n".join(
        f"Finding {index} [{_finding_key(finding)}]:\n{finding.summary}"
        for index, finding in enumerate(findings, start=1)
    ) or "(no parseable prior implementation findings)"

    prior_guidance = run.sol_guidance or "(none)"

    return f"""You are Sol High, the read-only escalation executive.

The SAME implementation defect has survived a Luna correction.
This is escalation round {escalation_round} for finding_key: {finding_key}.

Do not edit files and do not perform Git operations.

Diagnose why this specific defect persists and give the smallest useful guidance
for another bounded Luna correction. Focus on the current finding; do not expand
scope merely because other unrelated defects may exist.

If the remaining problem is genuinely a policy or architecture ambiguity that
requires authority rather than implementation debugging, report that instead.

Return exactly one of these forms:
GUIDANCE: <concise root-cause analysis and bounded correction guidance>
POLICY_AMBIGUITY: <concise reason authority is required>

Previous Sol guidance for this finding/run:
{prior_guidance}

Task specification:
{run.specification}

Recent Sonnet implementation findings:
{history}

Current implementation diff:
{_implementation_diff(repo)}
"""


def _parse_sol_response(execution: ProviderExecution) -> tuple[str, str]:
    if execution.returncode != 0:
        raise RuntimeError(execution.stderr or "Sol escalation failed")

    text = execution.stdout.strip()
    for kind in ("GUIDANCE", "POLICY_AMBIGUITY"):
        prefix = kind + ":"
        if text.startswith(prefix):
            body = text[len(prefix):].strip()
            if not body:
                raise ValueError("Sol returned an empty escalation result")
            return kind, body

    raise ValueError("Sol returned an invalid escalation result")


def _terra_proposed_approval_text(resolution: str) -> str | None:
    marker = "Proposed approval text:"
    if marker not in resolution:
        return None

    tail = resolution.split(marker, 1)[1].strip()
    quoted: list[str] = []
    collecting = False

    for raw in tail.splitlines():
        line = raw.strip()
        if line.startswith(">"):
            collecting = True
            body = line[1:].strip()
            if body:
                quoted.append(body)
            continue
        if collecting and line:
            break

    text = " ".join(quoted).strip()
    return text or None


_PROVIDER_RESUMABLE_BLOCKS = {
    "blocked_provider_quota",
    "blocked_provider_billing",
    "blocked_provider_auth",
    "blocked_provider_rate_limit",
    "blocked_provider_unavailable",
    "blocked_provider_configuration",
    "blocked_provider_failure",
    "blocked_provider_output",
}


class Controller:
    def __init__(
        self,
        repo: Path,
        runs_dir: Path = RUNS,
        sonnet: Callable[[str, Path], ProviderExecution] = execute_sonnet_review,
        terra: Callable[[str, Path], ProviderExecution] = execute_terra_resolution,
        sol: Callable[[str, Path], ProviderExecution] = execute_sol_escalation,
        luna: Callable[[str, Path], ProviderExecution] = execute_luna_implementation,
        approval: Callable[[str], bool] | None = None,
    ) -> None:
        self.repo = repo
        self.runs_dir = runs_dir
        self.sonnet = sonnet
        self.terra = terra
        self.sol = sol
        self.luna = luna
        self.approval = approval or (lambda prompt: typer.confirm(prompt, default=False))

    def _save(self, run: WorkflowRun) -> None:
        run.schema_version = 6
        run.updated_at = datetime.now(timezone.utc).isoformat()
        persist(run, self.runs_dir)

    def approve_policy(self, run_id: str, approved_text: str) -> WorkflowRun:
        run = load_run(run_id, self.runs_dir)
        self._resume_guard(run)

        if run.stage != "blocked_policy_ambiguity":
            raise ControllerError("policy approval requires blocked_policy_ambiguity stage")

        approved_text = approved_text.strip()
        if not approved_text:
            raise ControllerError("approved policy text cannot be empty")

        trigger_summary = (
            run.sol_guidance
            or (run.implementation_review.summary if run.implementation_review else None)
            or (run.spec_review.summary if run.spec_review else None)
            or run.last_error
            or "policy ambiguity"
        )
        trigger_key = (
            _finding_key(run.implementation_review)
            if run.implementation_review is not None
            else None
        )

        run.policy_decisions.append(
            PolicyDecision(
                decision_id=f"policy-{len(run.policy_decisions) + 1:02d}",
                approved_at=datetime.now(timezone.utc).isoformat(),
                trigger_finding_key=trigger_key,
                trigger_summary=trigger_summary,
                recommendation=run.terra_resolution or "",
                approved_text=approved_text,
            )
        )

        run.sol_guidance = None
        run.last_error = None

        # Specification ambiguity: re-review the specification with the
        # approved decision now included in the authoritative task prompt.
        if run.implementation_review is None:
            run.stage = "created"
        else:
            if run.correction_cycles >= MAX_TOTAL_CORRECTIONS:
                run.stage = "blocked_correction_budget"
                run.last_error = (
                    f"implementation still failing after "
                    f"{MAX_TOTAL_CORRECTIONS} total corrections"
                )
            else:
                run.correction_cycles += 1
                run.stage = "correction_pending"

        self._save(run)
        return run

    def _block(self, run: WorkflowRun, stage: str, message: str) -> WorkflowRun:
        run.stage = stage
        run.last_error = message
        self._save(run)
        console.print(f"[red]BLOCKED:[/red] {message}")
        _print_run_report(run)
        return run

    def _arm_provider(self, run: WorkflowRun, stage: str, prompt: str) -> None:
        run.provider_resume_stage = stage
        run.provider_resume_prompt = prompt

    def _clear_provider(self, run: WorkflowRun) -> None:
        run.provider_resume_stage = None
        run.provider_resume_prompt = None

    def _block_provider(
        self,
        run: WorkflowRun,
        provider: str,
        purpose: str,
        execution: ProviderExecution,
    ) -> WorkflowRun:
        kind = (
            execution.failure_kind
            or classify_provider_failure(
                execution.returncode,
                execution.stdout,
                execution.stderr,
            )
            or "provider_error"
        )

        stage = {
            "quota": "blocked_provider_quota",
            "billing": "blocked_provider_billing",
            "auth": "blocked_provider_auth",
            "rate_limit": "blocked_provider_rate_limit",
            "unavailable": "blocked_provider_unavailable",
            "timeout": "blocked_provider_timeout",
            "interrupted": "blocked_provider_interrupted",
            "configuration": "blocked_provider_configuration",
            "provider_error": "blocked_provider_failure",
        }[kind]

        description = {
            "quota": "quota or usage cap exhausted",
            "billing": "billing failure",
            "auth": "authentication failure",
            "rate_limit": "rate limit reached",
            "unavailable": "provider unavailable after bounded retries",
            "timeout": "provider deadline exceeded and process group stopped",
            "interrupted": "provider interrupted and process group stopped",
            "configuration": "provider CLI/model configuration failure",
            "provider_error": "unclassified provider failure",
        }[kind]

        return self._block(
            run,
            stage,
            f"{provider}: {description} during {purpose}; "
            "no alternate provider was invoked",
        )

    def _block_provider_output(
        self,
        run: WorkflowRun,
        provider: str,
        purpose: str,
        detail: str,
    ) -> WorkflowRun:
        return self._block(
            run,
            "blocked_provider_output",
            f"{provider}: invalid structured output during {purpose} "
            f"after one same-provider retry: {detail}",
        )

    def _retry_structured_once(
        self,
        run: WorkflowRun,
        provider_name: str,
        purpose: str,
        provider: Callable[[str, Path], ProviderExecution],
        parser: Callable[[ProviderExecution], object],
    ) -> object | None:
        prompt = run.provider_resume_prompt
        if not prompt:
            self._block(
                run,
                "blocked_interrupted_provider",
                "cannot safely retry malformed provider output without saved prompt",
            )
            return None

        console.print(
            f"[yellow]Invalid {provider_name} structured output; "
            "retrying the same provider once.[/yellow]"
        )
        execution = provider(prompt, self.repo)
        _record_provider(run, purpose, provider_name, execution)
        self._save(run)

        if execution.returncode != 0:
            self._block_provider(
                run,
                provider_name,
                purpose,
                execution,
            )
            return None

        try:
            return parser(execution)
        except Exception as exc:
            self._block_provider_output(
                run,
                provider_name,
                purpose,
                str(exc),
            )
            return None

    def _resume_blocked_provider(self, run: WorkflowRun) -> WorkflowRun:
        stage = run.provider_resume_stage
        prompt = run.provider_resume_prompt

        if not stage or not prompt:
            return self._block(
                run,
                "blocked_interrupted_provider",
                "provider failure lacks enough saved context for safe resumption",
            )

        provider_map = {
            "terra_resolving": (
                "policy clarification",
                "Terra High",
                self.terra,
            ),
            "sol_escalating": (
                "escalation guidance",
                "Sol High",
                self.sol,
            ),
            "spec_reviewing": (
                "specification",
                "Sonnet 5 High",
                self.sonnet,
            ),
            "reviewing": (
                "implementation",
                "Sonnet 5 High",
                self.sonnet,
            ),
            "implementing": (
                "implementation",
                "Luna High",
                self.luna,
            ),
            "correcting": (
                "correction",
                "Luna High",
                self.luna,
            ),
        }

        if stage not in provider_map:
            return self._block(
                run,
                "blocked_interrupted_provider",
                f"unsupported provider resume stage: {stage}",
            )

        purpose, provider_name, provider = provider_map[stage]
        run.stage = stage
        run.last_error = None
        self._save(run)

        console.print(
            f"[cyan]Stage:[/cyan] Resuming {stage} — {provider_name}"
        )
        execution = provider(prompt, self.repo)
        _record_provider(run, purpose, provider_name, execution)
        self._save(run)

        if execution.returncode != 0:
            return self._block_provider(
                run,
                provider_name,
                purpose,
                execution,
            )

        result = self._recover_provider_stage(run)

        # Do not erase context for a NEW provider failure reached while
        # continuing the workflow.
        if (
            not result.stage.startswith("blocked")
            and result.provider_resume_stage == stage
            and result.provider_resume_prompt == prompt
        ):
            self._clear_provider(result)
            self._save(result)

        return result

    def _policy_stop(self, run: WorkflowRun, reason: str) -> WorkflowRun:
        run.stage = "terra_resolving"
        prompt = _terra_prompt(run, reason)
        self._arm_provider(run, run.stage, prompt)
        self._save(run)

        console.print("[cyan]Stage:[/cyan] Policy clarification — Terra High")
        execution = self.terra(prompt, self.repo)
        _record_provider(
            run,
            "policy clarification",
            "Terra High",
            execution,
        )
        self._save(run)

        if execution.returncode != 0:
            return self._block_provider(
                run,
                "Terra High",
                "policy clarification",
                execution,
            )

        self._clear_provider(run)
        run.terra_resolution = execution.stdout or execution.stderr
        return self._block(
            run,
            "blocked_policy_ambiguity",
            "policy ambiguity requires human approval",
        )

    def _review(self, run: WorkflowRun, purpose: str) -> ReviewResult | None:
        run.stage = (
            "spec_reviewing"
            if purpose == "specification"
            else "reviewing"
        )
        label = (
            "Specification review"
            if purpose == "specification"
            else "Implementation review"
        )
        prompt = (
            _spec_review_prompt(run)
            if purpose == "specification"
            else _implementation_review_prompt(
                run,
                _implementation_diff(self.repo),
            )
        )
        self._arm_provider(run, run.stage, prompt)
        self._save(run)

        console.print(
            f"[cyan]Stage:[/cyan] {label} — Sonnet 5 High"
        )
        execution = self.sonnet(prompt, self.repo)
        _record_provider(
            run,
            purpose,
            "Sonnet 5 High",
            execution,
        )
        self._save(run)

        if execution.returncode != 0:
            self._block_provider(
                run,
                "Sonnet 5 High",
                purpose,
                execution,
            )
            return None

        try:
            result = parse_sonnet_review(execution)
        except Exception:
            retried = self._retry_structured_once(
                run,
                "Sonnet 5 High",
                purpose,
                self.sonnet,
                parse_sonnet_review,
            )
            if retried is None:
                return None
            result = retried

        self._clear_provider(run)

        if purpose == "specification":
            run.spec_review = result
            run.stage = (
                "spec_review_passed"
                if result.category == "PASS"
                else "spec_review_failed"
            )
        else:
            run.implementation_review = result
            run.stage = "implementation_reviewed"

        self._save(run)
        return result

    def _verify(self, run: WorkflowRun) -> bool:
        run.stage = "verifying"
        self._save(run)
        console.print("[cyan]Stage:[/cyan] Deterministic verification")
        current = repo_state(self.repo)
        checks: dict[str, object] = {
            "branch_matches": current.branch == run.repo.branch,
            "head_matches": current.head == run.repo.head,
            "origin_matches": current.origin == run.repo.origin,
        }
        try:
            files = changed_files(self.repo)
        except ControllerError as exc:
            checks.update(
                {
                    "change_enumeration": False,
                    "change_enumeration_error": str(exc),
                }
            )
            run.verification = checks
            self._save(run)
            self._block(
                run,
                "blocked_unexpected_repo_state",
                f"could not enumerate repository changes: {exc}",
            )
            return False
        checks["change_enumeration"] = True
        clean_diff, diff_message = diff_check(self.repo)
        run.changed_files = files
        run.working_tree_fingerprint = working_tree_fingerprint(self.repo, files)
        checks.update({"diff_check": clean_diff, "diff_check_output": diff_message, "changed_files": files})
        run.verification = checks
        self._save(run)
        if not all(value for key, value in checks.items() if key != "diff_check_output" and key != "changed_files"):
            self._block(run, "blocked_unexpected_repo_state", "repository changed unexpectedly during orchestration")
            return False
        if not files:
            self._block(run, "blocked_no_changes", "implementation produced no changed files")
            return False
        run.stage = "implementation_verified"
        self._save(run)
        return True

    def _implement(self, run: WorkflowRun, correction: bool = False) -> bool:
        run.stage = "correcting" if correction else "implementing"
        label = "Correction" if correction else "Implementation"

        prompt = _task_prompt(run)
        if correction:
            prompt += (
                "\n\nCorrect the implementation for this review finding. "
                "Make the smallest bounded change that addresses it; do not "
                "change policy or perform Git operations.\n"
                + (
                    run.implementation_review.summary
                    if run.implementation_review
                    else ""
                )
            )
            if run.sol_guidance:
                prompt += (
                    "\n\nSol High escalation guidance for this bounded "
                    "correction:\n"
                    + run.sol_guidance
                )

        self._arm_provider(run, run.stage, prompt)
        self._save(run)

        console.print(
            f"[cyan]Stage:[/cyan] {label} — Luna High"
        )
        execution = self.luna(prompt, self.repo)
        purpose = "correction" if correction else "implementation"
        _record_provider(
            run,
            purpose,
            "Luna High",
            execution,
        )
        self._save(run)

        if execution.returncode != 0:
            self._block_provider(
                run,
                "Luna High",
                purpose,
                execution,
            )
            return False

        self._clear_provider(run)
        run.stage = (
            "correction_completed"
            if correction
            else "implementation_completed"
        )
        self._save(run)
        return self._verify(run)

    def _escalate_to_sol(
        self,
        run: WorkflowRun,
        finding_key: str,
        escalation_round: int,
    ) -> bool:
        run.stage = "sol_escalating"
        prompt = _sol_prompt(
            run,
            self.repo,
            finding_key,
            escalation_round,
        )
        self._arm_provider(run, run.stage, prompt)
        self._save(run)

        console.print(
            "[cyan]Stage:[/cyan] Escalation diagnosis — Sol High"
        )
        execution = self.sol(prompt, self.repo)
        _record_provider(
            run,
            "escalation guidance",
            "Sol High",
            execution,
        )
        self._save(run)

        if execution.returncode != 0:
            self._block_provider(
                run,
                "Sol High",
                "escalation guidance",
                execution,
            )
            return False

        try:
            parsed = _parse_sol_response(execution)
        except Exception:
            retried = self._retry_structured_once(
                run,
                "Sol High",
                "escalation guidance",
                self.sol,
                _parse_sol_response,
            )
            if retried is None:
                return False
            parsed = retried

        kind, guidance = parsed
        self._clear_provider(run)
        run.sol_guidance = guidance
        self._save(run)

        if kind == "POLICY_AMBIGUITY":
            self._policy_stop(
                run,
                "Sol High escalation identified policy ambiguity: "
                + guidance,
            )
            return False

        run.stage = "sol_guidance_ready"
        self._save(run)
        return True

    def _review_and_correct(self, run: WorkflowRun) -> WorkflowRun:
        result = run.implementation_review if run.stage == "implementation_reviewed" else self._review(run, "implementation")
        if result is None:
            return run
        if result.category == "PASS":
            run.stage = "awaiting_commit_approval"
            self._save(run)
            return self._approval_gates(run)
        if result.category == "POLICY_AMBIGUITY":
            return self._policy_stop(run, result.summary)

        if run.correction_cycles >= MAX_TOTAL_CORRECTIONS:
            return self._block(
                run,
                "blocked_correction_budget",
                f"implementation still failing after {MAX_TOTAL_CORRECTIONS} total corrections",
            )

        finding_key = _finding_key(result)
        streak = _current_finding_streak(run, result)

        # New defect: one ordinary bounded Luna correction.
        if streak == 1:
            run.sol_guidance = None
            run.correction_cycles += 1
            run.stage = "correction_pending"
            self._save(run)
            if not self._implement(run, correction=True):
                return run
            return self._review_and_correct(run)

        # Same defect survived. Escalate to Sol twice before involving a human.
        if streak <= 1 + MAX_SOL_ESCALATIONS_PER_FINDING:
            escalation_round = streak - 1
            if not self._escalate_to_sol(run, finding_key, escalation_round):
                return run
            return self._run_from(run)

        return self._block(
            run,
            "blocked_repeated_finding",
            f"finding {finding_key} still fails after two Sol-guided corrections",
        )


    def _approval_gates(self, run: WorkflowRun) -> WorkflowRun:
        self._resume_guard(run)
        if run.stage in {"awaiting_commit_approval", "commit_declined"}:
            _print_run_report(run)
            console.print("\n[cyan]Commit approval gate[/cyan]")
            console.print("Changed files: " + ", ".join(run.changed_files))
            if run.implementation_review:
                console.print("Review: " + run.implementation_review.summary)
            if not self.approval("Commit these changes?"):
                run.stage = "commit_declined"
                self._save(run)
                console.print("Commit not approved; no Git commit was made.")
                return run
            self._resume_guard(run)
            stageable = _stageable_changed_files(self.repo, run.changed_files)
            if stageable:
                add = _git(self.repo, "add", "-A", "--", *stageable, check=False)
                _record_git(run, "stage changed files", add)
                if add.returncode != 0:
                    return self._block(run, "blocked_git_failure", add.stderr.strip() or "git add failed")
            message = f"Implement task {run.task_ref}"
            commit = _git(self.repo, "commit", "-m", message, check=False)
            _record_git(run, "commit", commit)
            if commit.returncode != 0:
                return self._block(run, "blocked_git_failure", commit.stderr.strip() or "git commit failed")
            run.commit_hash = git_text(self.repo, "rev-parse", "HEAD")
            run.commit_message = message
            run.stage = "awaiting_push_approval"
            self._save(run)
        if run.stage in {"awaiting_push_approval", "push_declined"}:
            console.print("\n[cyan]Push approval gate[/cyan]")
            if not self.approval("Push this commit to origin?"):
                run.stage = "push_declined"
                self._save(run)
                console.print("Push not approved; no Git push was made.")
                return run
            self._resume_guard(run)
            push = _git(self.repo, "push", "origin", run.repo.branch, check=False)
            _record_git(run, "push", push)
            if push.returncode != 0:
                return self._block(run, "blocked_git_failure", push.stderr.strip() or "git push failed")
            run.stage = "pushed_awaiting_merge"
            self._save(run)
            console.print("Pushed. Merge remains a separate manual/future gate.")
            _print_run_report(run)
        return run

    def new_run(self, task_ref: str) -> WorkflowRun:
        state = repo_state(self.repo)
        relative, _, specification = resolve_task(self.repo, task_ref)
        run = WorkflowRun(
            run_id=uuid.uuid4().hex[:12],
            created_at=datetime.now(timezone.utc).isoformat(),
            task_ref=task_ref,
            task_file=relative,
            task_sha256=hashlib.sha256(specification.encode()).hexdigest(),
            specification=specification,
            repo=state,
        )
        self._save(run)
        if not state.clean:
            return self._block(run, "blocked_dirty_repo", "jobs repo must be clean before starting")
        return self._run_from(run)

    def _run_from(self, run: WorkflowRun) -> WorkflowRun:
        if run.stage == "created":
            result = self._review(run, "specification")
            if result is None:
                return run
            if result.category == "POLICY_AMBIGUITY":
                return self._policy_stop(run, result.summary)
            if result.category != "PASS":
                return self._block(run, "blocked_spec_review", f"specification review failed: {result.summary}")
        if run.stage == "spec_review_passed":
            if not self._implement(run):
                return run
        if run.stage == "implementation_completed" or run.stage == "correction_completed":
            if not self._verify(run):
                return run
        if run.stage == "sol_guidance_ready":
            if run.correction_cycles >= MAX_TOTAL_CORRECTIONS:
                return self._block(
                    run,
                    "blocked_correction_budget",
                    f"implementation still failing after {MAX_TOTAL_CORRECTIONS} total corrections",
                )
            run.correction_cycles += 1
            run.stage = "correction_pending"
            self._save(run)
        if run.stage == "correction_pending":
            if not self._implement(run, correction=True):
                return run
        if run.stage == "implementation_verified":
            return self._review_and_correct(run)
        if run.stage == "implementation_reviewed":
            return self._review_and_correct(run)
        if run.stage in {"awaiting_commit_approval", "commit_declined", "awaiting_push_approval", "push_declined"}:
            return self._approval_gates(run)
        return run

    def _resume_guard(self, run: WorkflowRun) -> None:
        current = repo_state(self.repo)
        if current.repo != run.repo.repo:
            raise ControllerError("resume refused: configured repository differs from saved run")
        if current.branch != run.repo.branch or current.origin != run.repo.origin:
            raise ControllerError("resume refused: branch or origin no longer matches saved snapshot")
        if run.commit_hash:
            if current.head != run.commit_hash or not current.clean:
                raise ControllerError("resume refused: committed repository state no longer matches saved run")
        elif current.head != run.repo.head:
            raise ControllerError("resume refused: HEAD no longer matches saved snapshot")
        elif run.working_tree_fingerprint and working_tree_fingerprint(self.repo) != run.working_tree_fingerprint:
            raise ControllerError("resume refused: working tree no longer matches saved run")
        elif run.stage in {"created", "spec_reviewing", "spec_review_passed"} and not current.clean:
            raise ControllerError("resume refused: repository is dirty before implementation")

    def resume(self, run_id: str) -> WorkflowRun:
        run = load_run(run_id, self.runs_dir)
        self._resume_guard(run)

        if (
            run.stage in _PROVIDER_RESUMABLE_BLOCKS
            and run.provider_resume_stage
            and run.provider_resume_prompt
        ):
            return self._resume_blocked_provider(run)

        if (
            run.stage in {"blocked_after_correction", "blocked_after_escalation"}
            and run.correction_cycles < MAX_TOTAL_CORRECTIONS
            and run.implementation_review is not None
            and run.implementation_review.category not in {"PASS", "POLICY_AMBIGUITY"}
        ):
            run.stage = "implementation_reviewed"
            run.last_error = None
            self._save(run)
            return self._review_and_correct(run)
        if run.stage.startswith("blocked") or run.stage == "pushed_awaiting_merge":
            console.print(f"Stage: {run.stage}")
            return run
        if run.stage in {"spec_reviewing", "implementing", "correcting", "reviewing", "terra_resolving", "sol_escalating"}:
            return self._recover_provider_stage(run)
        return self._run_from(run)

    def _recover_provider_stage(self, run: WorkflowRun) -> WorkflowRun:
        """Consume a provider result already persisted before a process crash."""

        if not run.provider_runs:
            return self._block(run, "blocked_interrupted_provider", "provider stage was interrupted before output was recorded")
        record = run.provider_runs[-1]
        expected_provider_run = {
            "terra_resolving": ("policy clarification", "Terra High"),
            "sol_escalating": ("escalation guidance", "Sol High"),
            "spec_reviewing": ("specification", "Sonnet 5 High"),
            "reviewing": ("implementation", "Sonnet 5 High"),
            "implementing": ("implementation", "Luna High"),
            "correcting": ("correction", "Luna High"),
        }[run.stage]
        if (record.purpose, record.provider) != expected_provider_run:
            return self._block(
                run,
                "blocked_interrupted_provider",
                "provider stage was interrupted before matching output was recorded",
            )
        execution = ProviderExecution(
            command=record.command,
            returncode=record.returncode,
            stdout=record.stdout,
            stderr=record.stderr,
            duration_seconds=record.duration_seconds,
            failure_kind=record.failure_kind,
        )
        if execution.returncode != 0:
            return self._block_provider(
                run,
                record.provider,
                record.purpose,
                execution,
            )
        if run.stage == "terra_resolving":
            run.terra_resolution = execution.stdout or execution.stderr
            return self._block(run, "blocked_policy_ambiguity", "policy ambiguity requires human approval")
        if run.stage == "sol_escalating":
            try:
                kind, guidance = _parse_sol_response(execution)
            except Exception as exc:
                return self._block_provider_output(
                    run,
                    "Sol High",
                    "escalation guidance",
                    str(exc),
                )
            run.sol_guidance = guidance
            self._save(run)
            if kind == "POLICY_AMBIGUITY":
                return self._policy_stop(
                    run,
                    "Sol High escalation identified policy ambiguity: " + guidance,
                )
            run.stage = "sol_guidance_ready"
            self._save(run)
            return self._run_from(run)
        if run.stage == "spec_reviewing":
            try:
                result = parse_sonnet_review(execution)
            except Exception as exc:
                return self._block_provider_output(
                    run,
                    "Sonnet 5 High",
                    "specification",
                    str(exc),
                )
            run.spec_review = result
            run.stage = "spec_review_passed" if result.category == "PASS" else "spec_review_failed"
            self._save(run)
            if result.category == "POLICY_AMBIGUITY":
                return self._policy_stop(run, result.summary)
            if result.category != "PASS":
                return self._block(run, "blocked_spec_review", f"specification review failed: {result.summary}")
            return self._run_from(run)
        if run.stage == "reviewing":
            try:
                result = parse_sonnet_review(execution)
            except Exception as exc:
                return self._block_provider_output(
                    run,
                    "Sonnet 5 High",
                    "implementation",
                    str(exc),
                )
            run.implementation_review = result
            run.stage = "implementation_reviewed"
            self._save(run)
            return self._review_and_correct(run)
        if run.stage == "correcting":
            run.stage = "correction_completed"
            self._save(run)
            if execution.returncode != 0:
                return self._block(run, "blocked_provider_failure", "Luna failed during correction")
            if not self._verify(run):
                return run
            return self._review_and_correct(run)
        run.stage = "implementation_completed"
        self._save(run)
        if execution.returncode != 0:
            return self._block(run, "blocked_provider_failure", "Luna failed during implementation")
        if not self._verify(run):
            return run
        return self._review_and_correct(run)


def _handle_error(action: Callable[[], object]) -> None:
    try:
        action()
    except (ControllerError, OSError, subprocess.SubprocessError) as exc:
        console.print(f"[red]BLOCKED:[/red] {exc}")
        raise typer.Exit(1) from exc


@app.command("run")
def run_task(
    task_ref: str = typer.Argument(..., help="Task identifier resolved as tasks/<task-ref>-*.md."),
    repo: Path | None = typer.Option(None, "--repo", help="Override the jobs repository path."),
) -> None:
    """Run orchestration through review, correction, and human approval gates."""

    def action() -> None:
        result = Controller(configured_repo(repo)).new_run(task_ref)
        console.print(f"Run {result.run_id}: {result.stage}")
    _handle_error(action)


@app.command()
def resume(
    run_id: str = typer.Argument(..., help="Saved run id."),
    repo: Path | None = typer.Option(None, "--repo", help="Override the jobs repository path."),
) -> None:
    """Safely continue a saved run without repeating completed provider stages."""

    def action() -> None:
        saved = load_run(run_id)
        result = Controller(configured_repo(repo) if repo else Path(saved.repo.repo)).resume(run_id)
        console.print(f"Run {result.run_id}: {result.stage}")
    _handle_error(action)


@app.command("approve-policy")
def approve_policy_command(
    run_id: str = typer.Argument(..., help="Saved run awaiting human policy approval."),
    decision: str | None = typer.Option(
        None,
        "--decision",
        help="Exact approved policy text. If omitted, use Terra's quoted proposal.",
    ),
    repo: Path | None = typer.Option(None, "--repo", help="Override the jobs repository path."),
) -> None:
    """Record a human-approved Terra clarification without invoking providers."""

    def action() -> None:
        saved = load_run(run_id)
        if saved.stage != "blocked_policy_ambiguity":
            raise ControllerError("run is not awaiting policy approval")

        approved = decision or _terra_proposed_approval_text(saved.terra_resolution or "")
        if not approved:
            raise ControllerError(
                "could not extract Terra's proposed approval text; use --decision"
            )

        console.print("\n[cyan]Terra recommendation[/cyan]")
        console.print(saved.terra_resolution or "(none)")
        console.print("\n[cyan]Policy text to approve[/cyan]")
        console.print(approved)

        if not typer.confirm(
            "Approve this policy decision and resume orchestration?",
            default=False,
        ):
            console.print("Policy decision not approved; run remains blocked.")
            return

        controller = Controller(
            configured_repo(repo) if repo else Path(saved.repo.repo)
        )

        # Persist the human decision before any provider work resumes.
        result = controller.approve_policy(run_id, approved)
        console.print(
            f"Recorded {result.policy_decisions[-1].decision_id}. "
            "Resuming orchestration."
        )
        console.print(
            "Policy approval does not approve any future commit, push, or merge."
        )

        result = controller.resume(run_id)
        console.print(f"Run {result.run_id}: {result.stage}")

    _handle_error(action)


@app.command()
def report(
    run_id: str = typer.Argument(..., help="Saved run id."),
) -> None:
    """Show a concise orchestration audit and timing report."""

    def action() -> None:
        _print_run_report(load_run(run_id))

    _handle_error(action)


@app.command()
def status(
    run_id: str | None = typer.Argument(None, help="Run id; omit to list recent runs."),
) -> None:
    """Inspect one saved run or list recent run stages."""

    def action() -> None:
        if run_id:
            console.print(load_run(run_id).model_dump_json(indent=2))
            return
        paths = sorted(RUNS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]
        table = Table("Run", "Task", "Stage", "Corrections", "Policies")
        for path in paths:
            try:
                run = WorkflowRun.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                table.add_row(path.stem, "—", "INVALID", "—", "—")
                continue
            table.add_row(
                run.run_id,
                run.task_ref,
                run.stage,
                str(run.correction_cycles),
                str(len(run.policy_decisions)),
            )
        console.print(table)
    _handle_error(action)


if __name__ == "__main__":
    app()
