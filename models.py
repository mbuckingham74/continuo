"""Persistent data models used by the orchestration controller."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class ModelRoute:
    role: str
    cli: str
    model: str


TERRA = ModelRoute("architecture/policy", "codex", "gpt-5.6-terra")
SOL = ModelRoute("escalation-executive", "codex", "gpt-5.6-sol")
LUNA = ModelRoute("implementation", "codex", "gpt-5.6-luna")
SONNET = ModelRoute("adversarial-review", "claude", "sonnet")


ReviewStatus = Literal["PASS", "FAIL"]
ReviewCategory = Literal[
    "PASS",
    "IMPLEMENTATION_DEFECT",
    "POLICY_AMBIGUITY",
    "SCOPE_VIOLATION",
]


class ReviewResult(BaseModel):
    """The small, deliberately closed result vocabulary used by Sonnet."""

    model_config = ConfigDict(extra="forbid")

    status: ReviewStatus
    category: ReviewCategory
    finding_key: str | None = None
    summary: str


class RepoState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    branch: str
    head: str
    clean: bool
    origin: str


ProviderFailureKind = Literal[
    "quota",
    "billing",
    "auth",
    "rate_limit",
    "unavailable",
    "timeout",
    "interrupted",
    "configuration",
    "provider_error",
]

ProviderFailureSource = Literal[
    "provider_native",
    "os_error",
    "supervisor",
    "stderr",
    "stdout_tail",
    "returncode",
]

ProviderCapability = Literal["read_only", "workspace_write"]
WriterAttemptStage = Literal["implementing", "correcting"]
WriterAttemptPurpose = Literal["implementation", "correction"]
WriterRecoveryAction = Literal["retry_restored", "adopt_current"]


class WriterAttemptState(BaseModel):
    """Durable write-ahead repository evidence for one writer invocation."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: str = Field(min_length=1, max_length=64)
    stage: WriterAttemptStage
    purpose: WriterAttemptPurpose
    pre_fingerprint: str = Field(min_length=64, max_length=64)
    pre_changed_files: list[str] = Field(default_factory=list)
    post_fingerprint: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    post_changed_files: list[str] | None = None
    inspection_error: str | None = Field(default=None, max_length=1000)
    provider_record_index: int | None = Field(default=None, ge=0)


class WriterRecoveryDecision(BaseModel):
    """An explicit operator choice for an uncertain writer outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: str = Field(min_length=1, max_length=64)
    decided_at: str
    action: WriterRecoveryAction
    note: str = Field(min_length=1, max_length=1000)
    writer_attempt_id: str = Field(min_length=1, max_length=64)
    stage: WriterAttemptStage
    purpose: WriterAttemptPurpose
    pre_fingerprint: str = Field(min_length=64, max_length=64)
    saved_post_fingerprint: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    observed_fingerprint: str = Field(min_length=64, max_length=64)
    observed_changed_files: list[str] = Field(default_factory=list)


class ProviderRecord(BaseModel):
    provider: str
    purpose: str
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float | None = Field(default=None, ge=0)
    failure_kind: ProviderFailureKind | None = None
    failure_source: ProviderFailureSource | None = None
    failure_code: str | None = Field(default=None, max_length=120)
    capability: ProviderCapability | None = None
    repository_fingerprint_before: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    repository_fingerprint_after: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    retry_scheduled: bool = False


class GitRecord(BaseModel):
    operation: str
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class PolicyDecision(BaseModel):
    """Immutable human-approved policy clarification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: str
    approved_at: str
    approved_by: Literal["human"] = "human"
    source_provider: str = "Terra High"
    trigger_finding_key: str | None = None
    trigger_summary: str
    recommendation: str
    approved_text: str


class WorkflowRun(BaseModel):
    """Everything needed to audit and safely resume one run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 6
    run_id: str
    created_at: str
    updated_at: str | None = None
    task_ref: str
    task_file: str
    task_sha256: str
    specification: str
    repo: RepoState
    stage: str = "created"
    correction_cycles: int = Field(default=0, ge=0, le=12)
    spec_review: ReviewResult | None = None
    implementation_review: ReviewResult | None = None
    terra_resolution: str | None = None
    sol_guidance: str | None = None
    policy_decisions: list[PolicyDecision] = Field(default_factory=list)
    provider_resume_stage: str | None = None
    provider_resume_prompt: str | None = None
    active_writer_attempt: WriterAttemptState | None = None
    writer_recovery_decisions: list[WriterRecoveryDecision] = Field(
        default_factory=list
    )
    changed_files: list[str] = Field(default_factory=list)
    working_tree_fingerprint: str | None = None
    verification: dict[str, object] = Field(default_factory=dict)
    provider_runs: list[ProviderRecord] = Field(default_factory=list)
    git_operations: list[GitRecord] = Field(default_factory=list)
    commit_hash: str | None = None
    commit_message: str | None = None
    last_error: str | None = None


if __name__ == "__main__":
    for route in (TERRA, SOL, LUNA, SONNET):
        print(f"{route.role}: {route.cli} -> {route.model}")
