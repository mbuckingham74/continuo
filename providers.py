"""Provider command construction and subprocess boundaries.

The controller owns workflow decisions. These functions only build/run provider
commands and parse Sonnet's closed structured response.
"""

from __future__ import annotations

import errno
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from models import (
    ProviderFailureKind,
    ProviderFailureSource,
    ReviewResult,
)


DEFAULT_REPO = Path.home() / "Documents/my-apps/jobs"
READ_ONLY_PROVIDER_DEADLINE_SECONDS = 30.0 * 60.0
WRITE_PROVIDER_DEADLINE_SECONDS = 60.0 * 60.0
PROVIDER_TERM_GRACE_SECONDS = 5.0
PROVIDER_POLL_INTERVAL_SECONDS = 0.2
PROVIDER_HEARTBEAT_SECONDS = 5.0
PROVIDER_TIMEOUT_RETURN_CODE = 124
PROVIDER_INTERRUPTED_RETURN_CODE = 130
PROVIDER_STDOUT_TAIL_BYTES = 8 * 1024

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
    failure_kind: ProviderFailureKind | None = None
    failure_source: ProviderFailureSource | None = None
    failure_code: str | None = None
    retry_scheduled: bool = False


@dataclass(frozen=True)
class ProviderExecution:
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float | None = None
    failure_kind: ProviderFailureKind | None = None
    failure_source: ProviderFailureSource | None = None
    failure_code: str | None = None
    attempts: tuple[ProviderAttempt, ...] = ()


@dataclass(frozen=True)
class ProviderFailureEvidence:
    kind: ProviderFailureKind
    source: ProviderFailureSource
    code: str | None = None


@dataclass(frozen=True)
class _SupervisedResult:
    completed: subprocess.CompletedProcess[str]
    failure_kind: ProviderFailureKind | None = None


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
    """Compatibility wrapper returning only the normalized failure kind."""

    evidence = normalize_provider_failure(returncode, stdout, stderr)
    return evidence.kind if evidence is not None else None


_HTTP_STATUS_LINE = re.compile(
    r"^(?:error:\s*)?(?:http(?:\s+status)?|status(?:\s+code)?)"
    r"\s*[:=]?\s*(?P<status>\d{3})\b",
    re.IGNORECASE,
)

_DIAGNOSTIC_PREFIXES: tuple[
    tuple[ProviderFailureKind, tuple[str, ...]], ...
] = (
    (
        "quota",
        (
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
        ),
    ),
    (
        "billing",
        (
            "payment required",
            "billing account",
            "billing issue",
        ),
    ),
    (
        "auth",
        (
            "unauthorized",
            "authentication failed",
            "authentication error",
            "invalid api key",
            "invalid_api_key",
            "not authenticated",
        ),
    ),
    (
        "rate_limit",
        (
            "rate limit",
            "rate_limit",
            "too many requests",
        ),
    ),
    (
        "configuration",
        (
            "command not found",
            "no such file or directory",
            "model not found",
            "unknown model",
            "invalid model",
        ),
    ),
    (
        "unavailable",
        (
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
            "capacity unavailable",
        ),
    ),
)


def _kind_for_http_status(status: int) -> ProviderFailureKind | None:
    if status == 402:
        return "billing"
    if status in {401, 403}:
        return "auth"
    if status == 429:
        return "rate_limit"
    if status in {500, 502, 503, 504}:
        return "unavailable"
    return None


def _classify_diagnostic_text(
    text: str,
    source: ProviderFailureSource,
) -> ProviderFailureEvidence | None:
    for raw_line in text.splitlines():
        line = raw_line.strip().lower()
        if not line:
            continue
        status_match = _HTTP_STATUS_LINE.match(line)
        if status_match:
            status = int(status_match.group("status"))
            kind = _kind_for_http_status(status)
            if kind is not None:
                return ProviderFailureEvidence(kind, source, str(status))
        if line.startswith("error:"):
            line = line.removeprefix("error:").lstrip()
        for kind, prefixes in _DIAGNOSTIC_PREFIXES:
            if any(line.startswith(prefix) for prefix in prefixes):
                return ProviderFailureEvidence(kind, source)
    return None


def _stdout_tail(stdout: str) -> str:
    encoded = stdout.encode("utf-8", errors="replace")
    return encoded[-PROVIDER_STDOUT_TAIL_BYTES:].decode(
        "utf-8", errors="replace"
    )


def normalize_provider_failure(
    returncode: int,
    stdout: str = "",
    stderr: str = "",
    *,
    allow_stdout_tail: bool = False,
) -> ProviderFailureEvidence | None:
    """Classify only trusted, bounded transport evidence."""

    if returncode == 0:
        return None
    evidence = _classify_diagnostic_text(stderr, "stderr")
    if evidence is not None:
        return evidence
    if allow_stdout_tail:
        evidence = _classify_diagnostic_text(
            _stdout_tail(stdout),
            "stdout_tail",
        )
        if evidence is not None:
            return evidence
    return ProviderFailureEvidence(
        "provider_error",
        "returncode",
        str(returncode),
    )


def _os_failure_evidence(exc: OSError) -> ProviderFailureEvidence:
    configuration_errnos = {
        errno.EACCES,
        errno.ENOENT,
        errno.ENOEXEC,
        errno.EPERM,
    }
    kind: ProviderFailureKind = (
        "configuration"
        if exc.errno in configuration_errnos
        else "provider_error"
    )
    code = type(exc).__name__
    if exc.errno is not None:
        code += f":{exc.errno}"
    return ProviderFailureEvidence(kind, "os_error", code[:120])


def classify_claude_native_failure(
    result: subprocess.CompletedProcess[str],
) -> ProviderFailureEvidence | None:
    """Read only Claude's structured result discriminator/error fields."""

    try:
        envelope = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(envelope, dict):
        return None
    if envelope.get("type") != "result" or envelope.get("is_error") is not True:
        return None

    subtype = envelope.get("subtype")
    code = (
        subtype
        if isinstance(subtype, str) and 0 < len(subtype) <= 120
        else None
    )
    status = envelope.get("api_error_status")
    if isinstance(status, str) and status.isdigit():
        status = int(status)
    kind = _kind_for_http_status(status) if isinstance(status, int) else None
    return ProviderFailureEvidence(
        kind or "provider_error",
        "provider_native",
        code,
    )


def normalize_sonnet_execution(
    execution: ProviderExecution,
) -> ProviderExecution:
    """Normalize a Sonnet result before persistence or controller routing."""

    if execution.failure_source in {"supervisor", "os_error"}:
        return execution
    completed = subprocess.CompletedProcess(
        execution.command,
        execution.returncode,
        execution.stdout,
        execution.stderr,
    )
    evidence = classify_claude_native_failure(completed)
    if evidence is not None:
        return replace(
            execution,
            failure_kind=evidence.kind,
            failure_source=evidence.source,
            failure_code=evidence.code,
        )
    return normalize_provider_execution(execution)


def normalize_provider_execution(
    execution: ProviderExecution,
) -> ProviderExecution:
    if execution.failure_kind is not None:
        return execution
    evidence = normalize_provider_failure(
        execution.returncode,
        execution.stdout,
        execution.stderr,
    )
    if evidence is None:
        return execution
    return replace(
        execution,
        failure_kind=evidence.kind,
        failure_source=evidence.source,
        failure_code=evidence.code,
    )


def execution_failed(execution: ProviderExecution) -> bool:
    return execution.failure_kind is not None or execution.returncode != 0


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
    native_classifier: Callable[
        [subprocess.CompletedProcess[str]],
        ProviderFailureEvidence | None,
    ]
    | None = None,
    allow_stdout_tail: bool = False,
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

        terminal_failure_kind: ProviderFailureKind | None = None
        direct_evidence: ProviderFailureEvidence | None = None
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
            direct_evidence = _os_failure_evidence(exc)

        elapsed = time.monotonic() - started
        evidence = direct_evidence
        if terminal_failure_kind is not None:
            evidence = ProviderFailureEvidence(
                terminal_failure_kind,
                "supervisor",
                terminal_failure_kind,
            )
        elif native_classifier is not None:
            evidence = native_classifier(result) or evidence
        if evidence is None:
            evidence = normalize_provider_failure(
                result.returncode,
                result.stdout,
                result.stderr,
                allow_stdout_tail=allow_stdout_tail,
            )
        failure_kind = evidence.kind if evidence is not None else None

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
                failure_source=(
                    evidence.source if evidence is not None else None
                ),
                failure_code=(
                    evidence.code if evidence is not None else None
                ),
                retry_scheduled=retry_scheduled,
            )
        )

        if interactive:
            print("\r\033[2K", end="", file=sys.stderr)

        mark = (
            "✓"
            if result.returncode == 0 and failure_kind is None
            else "✗"
        )
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
            failure_source=(
                evidence.source if evidence is not None else None
            ),
            failure_code=(
                evidence.code if evidence is not None else None
            ),
            attempts=tuple(attempts),
        )

    raise RuntimeError("unreachable provider retry state")


def execute_sonnet_review(prompt: str, repo: Path = DEFAULT_REPO) -> ProviderExecution:
    return _run(
        build_sonnet_command(prompt),
        repo,
        deadline_seconds=READ_ONLY_PROVIDER_DEADLINE_SECONDS,
        native_classifier=classify_claude_native_failure,
    )


def parse_sonnet_review(execution: ProviderExecution) -> ReviewResult:
    execution = normalize_sonnet_execution(execution)
    if execution_failed(execution):
        raise RuntimeError(execution.stderr or "Sonnet review failed")
    envelope = json.loads(execution.stdout)
    if not isinstance(envelope, dict):
        raise ValueError("Claude result envelope must be an object")
    if (
        envelope.get("type") != "result"
        or envelope.get("subtype") != "success"
        or envelope.get("is_error") is not False
    ):
        raise ValueError("Claude result envelope is not a recognized success")
    if "structured_output" not in envelope:
        raise ValueError("Claude success envelope lacks structured_output")
    structured = envelope["structured_output"]
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
