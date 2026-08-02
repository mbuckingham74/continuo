"""Provider command construction and subprocess boundaries.

The controller owns workflow decisions. These functions only build/run provider
commands and parse Sonnet's closed structured response.
"""

from __future__ import annotations

import json
import subprocess
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
        "summary": {"type": "string"},
    },
    "required": ["status", "category", "summary"],
    "additionalProperties": False,
}
SONNET_REVIEW_SCHEMA_JSON = json.dumps(SONNET_REVIEW_SCHEMA, separators=(",", ":"))

LUNA_GIT_PROHIBITIONS = (
    "You have workspace-write only for bounded implementation edits. "
    "Do not commit, push, create or switch branches, merge, rebase, reset, "
    "or modify any Git metadata (.git). The controller alone has Git authority."
)


@dataclass(frozen=True)
class ProviderExecution:
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""


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


def _run(command: list[str], repo: Path, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> ProviderExecution:
    result = runner(
        command,
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return ProviderExecution(
        command=command,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


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
    return result


def run_sonnet_review(prompt: str, repo: Path = DEFAULT_REPO) -> ReviewResult:
    return parse_sonnet_review(execute_sonnet_review(prompt, repo))


def execute_terra_resolution(prompt: str, repo: Path = DEFAULT_REPO) -> ProviderExecution:
    return _run(build_terra_command(prompt), repo)


def execute_luna_implementation(prompt: str, repo: Path = DEFAULT_REPO) -> ProviderExecution:
    return _run(build_luna_command(prompt), repo)
