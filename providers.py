"""Provider command construction and subprocess boundaries.

The controller owns workflow decisions. These functions only build/run provider
commands and parse Sonnet's closed structured response.
"""

from __future__ import annotations

import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from models import ReviewResult


DEFAULT_REPO = Path.home() / "Documents/my-apps/jobs"
READ_ONLY_PROVIDER_DEADLINE_SECONDS = 30.0 * 60.0
WRITE_PROVIDER_DEADLINE_SECONDS = 60.0 * 60.0
PROVIDER_TERM_GRACE_SECONDS = 5.0
PROVIDER_POLL_INTERVAL_SECONDS = 0.2
PROVIDER_HEARTBEAT_SECONDS = 5.0
PROVIDER_TIMEOUT_RETURN_CODE = 124
PROVIDER_INTERRUPTED_RETURN_CODE = 130

SONNET_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["PASS", "FAIL"]},
        "category": {
            "type": "string",
            "enum": [
                "PASS",
                "IMPLEMENTATION_DEFECT",
                "POLICY_AMBIGUITY",
                "SCOPE_VIOLATION",
            ],
        },
        "finding_key": {"type": "string", "minLength": 1, "maxLength": 120},
        "summary": {"type": "string"},
    },
    "required": ["status", "category", "finding_key", "summary"],
    "additionalProperties": False,
}
SONNET_REVIEW_SCHEMA_JSON = json.dumps(SONNET_REVIEW_SCHEMA, separators=(",", ":"))

LUNA_GIT_PROHIBITIONS = (
    "You have workspace-write only for bounded implementation edits. "
    "Do not commit, push, create or switch branches, merge, rebase, reset, "
    "or modify any Git metadata (.git). The controller alone has Git authority."
)


@dataclass(frozen=True)
class ProviderAttempt:
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float | None = None
    failure_kind: str | None = None
    retry_scheduled: bool = False


@dataclass(frozen=True)
class ProviderExecution:
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float | None = None
    failure_kind: str | None = None
    attempts: tuple[ProviderAttempt, ...] = ()


@dataclass(frozen=True)
class _SupervisedResult:
    completed: subprocess.CompletedProcess[str]
    failure_kind: str | None = None


def build_sonnet_command(prompt: str) -> list[str]:
    """Build a fresh, read-only, structured Sonnet invocation."""

    return [
        "claude",
        "-p",
        "--model",
        "sonnet",
        "--permission-mode",
        "plan",
        "--tools",
        "Read,Glob,Grep",
        "--output-format",
        "json",
        "--json-schema",
        SONNET_REVIEW_SCHEMA_JSON,
        "--",
        prompt,
    ]


def build_terra_command(prompt: str) -> list[str]:
    return [
        "codex",
        "exec",
        "--model",
        "gpt-5.6-terra",
        "--sandbox",
        "read-only",
        "--",
        prompt,
    ]


def build_sol_command(prompt: str) -> list[str]:
    """Build a read-only Sol High escalation invocation."""

    return [
        "codex",
        "exec",
        "--model",
        "gpt-5.6-sol",
        "--sandbox",
        "read-only",
        "--",
        prompt,
    ]


def build_luna_command(prompt: str) -> list[str]:
    bounded_prompt = f"{LUNA_GIT_PROHIBITIONS}\n\n{prompt}"
    return [
        "codex",
        "exec",
        "--model",
        "gpt-5.6-luna",
        "--sandbox",
        "workspace-write",
        "--config",
        "approval_policy=never",
        "--config",
        "sandbox_workspace_write.network_access=false",
        "--",
        bounded_prompt,
    ]


def _provider_label(command: list[str]) -> str:
    if "gpt-5.6-luna" in command:
        return "Luna High"
    if "gpt-5.6-sol" in command:
        return "Sol High"
    if "gpt-5.6-terra" in command:
        return "Terra High"
    if command and Path(command[0]).name == "claude":
        return "Sonnet 5 High"
    return Path(command[0]).name if command else "provider"


def classify_provider_failure(
    returncode: int,
    stdout: str = "",
    stderr: str = "",
) -> str | None:
    """Conservatively classify provider/transport failures."""

    if returncode == 0:
        return None

    text = f"{stderr}\n{stdout}".lower()

    quota_patterns = (
        "quota exceeded",
        "insufficient_quota",
        "usage limit",
        "usage cap",
        "you've hit your limit",
        "you have hit your limit",
        "you've hit your usage limit",
        "you have hit your usage limit",
        "insufficient credits",
        "out of credits",
        "credit balance",
        "spending limit",
        "weekly limit",
        "monthly limit",
    )
    if any(pattern in text for pattern in quota_patterns):
        return "quota"

    if (
        "payment required" in text
        or "billing account" in text
        or "billing issue" in text
        or re.search(r"\b402\b", text)
    ):
        return "billing"

    auth_patterns = (
        "unauthorized",
        "authentication failed",
        "authentication error",
        "invalid api key",
        "invalid_api_key",
        "not authenticated",
        "permission denied",
    )
    if any(pattern in text for pattern in auth_patterns) or re.search(
        r"\b(401|403)\b", text
    ):
        return "auth"

    if (
        "rate limit" in text
        or "rate_limit" in text
        or "too many requests" in text
        or re.search(r"\b429\b", text)
    ):
        return "rate_limit"

    configuration_patterns = (
        "command not found",
        "no such file or directory",
        "model not found",
        "unknown model",
        "invalid model",
    )
    if any(pattern in text for pattern in configuration_patterns):
        return "configuration"

    unavailable_patterns = (
        "service unavailable",
        "temporarily unavailable",
        "bad gateway",
        "gateway timeout",
        "connection refused",
        "connection reset",
        "connection error",
        "network error",
        "network unavailable",
        "timed out",
        "timeout",
        "internal server error",
        "overloaded",
        "capacity",
    )
    if any(pattern in text for pattern in unavailable_patterns) or re.search(
        r"\b(500|502|503|504)\b", text
    ):
        return "unavailable"

    return "provider_error"


def _validate_supervision_timing(
    deadline_seconds: float,
    term_grace_seconds: float,
    poll_interval_seconds: float,
    heartbeat_seconds: float,
) -> None:
    if not math.isfinite(deadline_seconds) or deadline_seconds <= 0:
        raise ValueError("provider deadline must be positive and finite")
    if not math.isfinite(term_grace_seconds) or term_grace_seconds <= 0:
        raise ValueError("provider termination grace must be positive and finite")
    if not math.isfinite(poll_interval_seconds) or poll_interval_seconds <= 0:
        raise ValueError("provider poll interval must be positive and finite")
    if not math.isfinite(heartbeat_seconds) or heartbeat_seconds <= 0:
        raise ValueError("provider heartbeat interval must be positive and finite")


def _output_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _latest_output(current: str | bytes | None, previous: str) -> str:
    value = _output_text(current)
    return value if value else previous


def _signal_process_group(
    process: subprocess.Popen[str],
    *,
    force: bool,
) -> None:
    try:
        if os.name == "posix":
            os.killpg(
                process.pid,
                signal.SIGKILL if force else signal.SIGTERM,
            )
            return
        if os.name == "nt":
            if force:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
            elif process.poll() is None:
                process.send_signal(signal.CTRL_BREAK_EVENT)
            return
        if process.poll() is None:
            process.kill() if force else process.terminate()
    except (OSError, subprocess.SubprocessError):
        if process.poll() is None:
            try:
                process.kill() if force else process.terminate()
            except OSError:
                pass


def _terminate_process_group(
    process: subprocess.Popen[str],
    term_grace_seconds: float,
    stdout: str,
    stderr: str,
) -> tuple[str, str, bool]:
    _signal_process_group(process, force=False)
    try:
        final_stdout, final_stderr = process.communicate(
            timeout=term_grace_seconds
        )
        return (
            _latest_output(final_stdout, stdout),
            _latest_output(final_stderr, stderr),
            False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _latest_output(exc.output, stdout)
        stderr = _latest_output(exc.stderr, stderr)

    _signal_process_group(process, force=True)
    try:
        final_stdout, final_stderr = process.communicate(
            timeout=term_grace_seconds
        )
        return (
            _latest_output(final_stdout, stdout),
            _latest_output(final_stderr, stderr),
            True,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _latest_output(exc.output, stdout)
        stderr = _latest_output(exc.stderr, stderr)

    for pipe in (process.stdout, process.stderr):
        if pipe is not None:
            pipe.close()
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=term_grace_seconds)
    except subprocess.TimeoutExpired:
        pass
    return stdout, stderr, True


def _termination_diagnostic(
    reason: str,
    deadline_seconds: float,
    term_grace_seconds: float,
    forced: bool,
) -> str:
    if reason == "timeout":
        trigger = f"deadline of {deadline_seconds:g}s exceeded"
    else:
        trigger = "operator interruption received"
    cleanup = (
        "process group required forced termination"
        if forced
        else "process group exited during TERM grace"
    )
    return (
        f"[continuo] {trigger}; {cleanup} "
        f"({term_grace_seconds:g}s grace)"
    )


def _append_diagnostic(stderr: str, diagnostic: str) -> str:
    separator = "" if not stderr or stderr.endswith("\n") else "\n"
    return f"{stderr}{separator}{diagnostic}\n"


def _supervise_process(
    command: list[str],
    repo: Path,
    *,
    label: str,
    interactive: bool,
    deadline_seconds: float,
    term_grace_seconds: float,
    poll_interval_seconds: float,
    heartbeat_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
) -> _SupervisedResult:
    popen_options: dict[str, object] = {}
    if os.name == "posix":
        popen_options["start_new_session"] = True
    elif os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    process = subprocess.Popen(
        command,
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **popen_options,
    )
    started = monotonic()
    next_heartbeat = heartbeat_seconds
    stdout = ""
    stderr = ""

    try:
        while True:
            elapsed = monotonic() - started
            remaining = deadline_seconds - elapsed
            communicate_timeout = min(
                poll_interval_seconds,
                max(0.0, remaining),
            )
            try:
                final_stdout, final_stderr = process.communicate(
                    timeout=communicate_timeout
                )
                return _SupervisedResult(
                    subprocess.CompletedProcess(
                        command,
                        process.returncode,
                        final_stdout,
                        final_stderr,
                    )
                )
            except subprocess.TimeoutExpired as exc:
                stdout = _latest_output(exc.output, stdout)
                stderr = _latest_output(exc.stderr, stderr)

            elapsed = monotonic() - started
            if elapsed >= deadline_seconds:
                stdout, stderr, forced = _terminate_process_group(
                    process,
                    term_grace_seconds,
                    stdout,
                    stderr,
                )
                stderr = _append_diagnostic(
                    stderr,
                    _termination_diagnostic(
                        "timeout",
                        deadline_seconds,
                        term_grace_seconds,
                        forced,
                    ),
                )
                return _SupervisedResult(
                    subprocess.CompletedProcess(
                        command,
                        PROVIDER_TIMEOUT_RETURN_CODE,
                        stdout,
                        stderr,
                    ),
                    "timeout",
                )

            if elapsed >= next_heartbeat:
                print(
                    f"… {label} still running ({elapsed:0.0f}s)",
                    file=sys.stderr,
                    flush=True,
                )
                next_heartbeat += heartbeat_seconds
    except KeyboardInterrupt:
        stdout, stderr, forced = _terminate_process_group(
            process,
            term_grace_seconds,
            stdout,
            stderr,
        )
        stderr = _append_diagnostic(
            stderr,
            _termination_diagnostic(
                "interrupted",
                deadline_seconds,
                term_grace_seconds,
                forced,
            ),
        )
        if interactive:
            print("\r\033[2K", end="", file=sys.stderr)
        return _SupervisedResult(
            subprocess.CompletedProcess(
                command,
                PROVIDER_INTERRUPTED_RETURN_CODE,
                stdout,
                stderr,
            ),
            "interrupted",
        )
    except BaseException:
        _terminate_process_group(
            process,
            term_grace_seconds,
            stdout,
            stderr,
        )
        raise


def _run(
    command: list[str],
    repo: Path,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
    deadline_seconds: float = READ_ONLY_PROVIDER_DEADLINE_SECONDS,
    term_grace_seconds: float = PROVIDER_TERM_GRACE_SECONDS,
    poll_interval_seconds: float = PROVIDER_POLL_INTERVAL_SECONDS,
    heartbeat_seconds: float = PROVIDER_HEARTBEAT_SECONDS,
) -> ProviderExecution:
    """Run one provider with bounded same-provider outage retries only."""

    _validate_supervision_timing(
        deadline_seconds,
        term_grace_seconds,
        poll_interval_seconds,
        heartbeat_seconds,
    )
    label = _provider_label(command)
    attempts: list[ProviderAttempt] = []
    retry_delays = (5.0, 15.0)
    max_attempts = 1 + len(retry_delays)

    for attempt_number in range(1, max_attempts + 1):
        started = time.monotonic()
        interactive = sys.stderr.isatty()

        retry_suffix = (
            ""
            if attempt_number == 1
            else f" (same-provider retry {attempt_number - 1}/{len(retry_delays)})"
        )
        print(
            f"▶ {label} started{retry_suffix}",
            file=sys.stderr,
            flush=True,
        )

        terminal_failure_kind: str | None = None
        try:
            if runner is not subprocess.run:
                result = runner(
                    command,
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            else:
                supervised = _supervise_process(
                    command,
                    repo,
                    label=label,
                    interactive=interactive,
                    deadline_seconds=deadline_seconds,
                    term_grace_seconds=term_grace_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                )
                result = supervised.completed
                terminal_failure_kind = supervised.failure_kind
        except OSError as exc:
            result = subprocess.CompletedProcess(
                command,
                127,
                "",
                f"{type(exc).__name__}: {exc}",
            )

        elapsed = time.monotonic() - started
        failure_kind = (
            terminal_failure_kind
            or classify_provider_failure(
                result.returncode,
                result.stdout,
                result.stderr,
            )
        )

        # Only true provider/network unavailability is retried automatically.
        # Timeout, interruption, quota, billing, auth, rate-limit,
        # configuration, and unknown errors stop immediately.
        retry_scheduled = (
            failure_kind == "unavailable"
            and attempt_number < max_attempts
        )

        attempts.append(
            ProviderAttempt(
                command=command,
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_seconds=elapsed,
                failure_kind=failure_kind,
                retry_scheduled=retry_scheduled,
            )
        )

        if interactive:
            print("\r\033[2K", end="", file=sys.stderr)

        mark = "✓" if result.returncode == 0 else "✗"
        print(
            f"{mark} {label} finished in {elapsed:0.1f}s "
            f"(exit {result.returncode})",
            file=sys.stderr,
            flush=True,
        )

        if retry_scheduled:
            delay = retry_delays[attempt_number - 1]
            print(
                f"↻ {label} unavailable; retrying the same provider "
                f"in {delay:0.0f}s",
                file=sys.stderr,
                flush=True,
            )
            sleeper(delay)
            continue

        return ProviderExecution(
            command=command,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_seconds=sum(
                attempt.duration_seconds or 0.0
                for attempt in attempts
            ),
            failure_kind=failure_kind,
            attempts=tuple(attempts),
        )

    raise RuntimeError("unreachable provider retry state")


def execute_sonnet_review(prompt: str, repo: Path = DEFAULT_REPO) -> ProviderExecution:
    return _run(
        build_sonnet_command(prompt),
        repo,
        deadline_seconds=READ_ONLY_PROVIDER_DEADLINE_SECONDS,
    )


def parse_sonnet_review(execution: ProviderExecution) -> ReviewResult:
    if execution.returncode != 0:
        raise RuntimeError(execution.stderr or "Sonnet review failed")
    envelope = json.loads(execution.stdout)
    structured = envelope.get("structured_output", envelope)
    result = ReviewResult.model_validate(structured)
    if (result.status == "PASS") != (result.category == "PASS"):
        raise ValueError("Sonnet returned inconsistent status/category")
    if result.category == "PASS" and result.finding_key not in (None, "PASS"):
        raise ValueError("Sonnet PASS must use finding_key PASS")
    if result.category != "PASS" and result.finding_key == "PASS":
        raise ValueError("Sonnet failure cannot use finding_key PASS")
    return result


def run_sonnet_review(prompt: str, repo: Path = DEFAULT_REPO) -> ReviewResult:
    return parse_sonnet_review(execute_sonnet_review(prompt, repo))


def execute_terra_resolution(prompt: str, repo: Path = DEFAULT_REPO) -> ProviderExecution:
    return _run(
        build_terra_command(prompt),
        repo,
        deadline_seconds=READ_ONLY_PROVIDER_DEADLINE_SECONDS,
    )


def execute_sol_escalation(prompt: str, repo: Path = DEFAULT_REPO) -> ProviderExecution:
    return _run(
        build_sol_command(prompt),
        repo,
        deadline_seconds=READ_ONLY_PROVIDER_DEADLINE_SECONDS,
    )


def execute_luna_implementation(prompt: str, repo: Path = DEFAULT_REPO) -> ProviderExecution:
    return _run(
        build_luna_command(prompt),
        repo,
        deadline_seconds=WRITE_PROVIDER_DEADLINE_SECONDS,
    )
