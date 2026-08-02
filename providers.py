"""Provider command construction and subprocess boundaries.

The controller owns workflow decisions. These functions only build/run provider
commands and parse Sonnet's closed structured response.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from models import ReviewResult


DEFAULT_REPO = Path.home() / "Documents/my-apps/jobs"

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


def _run(
    command: list[str],
    repo: Path,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
) -> ProviderExecution:
    """Run one provider with bounded same-provider outage retries only."""

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
                process = subprocess.Popen(
                    command,
                    cwd=repo,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                next_heartbeat = 5.0

                while True:
                    try:
                        stdout, stderr = process.communicate(timeout=0.2)
                        break
                    except subprocess.TimeoutExpired:
                        elapsed = time.monotonic() - started
                        if elapsed >= next_heartbeat:
                            print(
                                f"… {label} still running ({elapsed:0.0f}s)",
                                file=sys.stderr,
                                flush=True,
                            )
                            next_heartbeat += 5.0

                result = subprocess.CompletedProcess(
                    command,
                    process.returncode,
                    stdout,
                    stderr,
                )
        except OSError as exc:
            result = subprocess.CompletedProcess(
                command,
                127,
                "",
                f"{type(exc).__name__}: {exc}",
            )

        elapsed = time.monotonic() - started
        failure_kind = classify_provider_failure(
            result.returncode,
            result.stdout,
            result.stderr,
        )

        # Only true provider/network unavailability is retried automatically.
        # Quota, billing, auth, rate-limit, configuration, and unknown errors
        # stop immediately.
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
    return _run(build_sonnet_command(prompt), repo)


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
    return _run(build_terra_command(prompt), repo)


def execute_sol_escalation(prompt: str, repo: Path = DEFAULT_REPO) -> ProviderExecution:
    return _run(build_sol_command(prompt), repo)


def execute_luna_implementation(prompt: str, repo: Path = DEFAULT_REPO) -> ProviderExecution:
    return _run(build_luna_command(prompt), repo)
