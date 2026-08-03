"""Persistent data models used by the orchestration controller."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CURRENT_RUN_SCHEMA_VERSION = 9
OLDEST_MIGRATABLE_RUN_SCHEMA_VERSION = 1


OrchestrationRole = Literal[
    "implementation",
    "adversarial_review",
    "escalation_executive",
    "policy_authority",
]
ProviderOperation = Literal[
    "implementation_write",
    "correction_write",
    "specification_review",
    "implementation_review",
    "escalation_guidance",
    "policy_clarification",
]
ProviderAdapterId = Literal["codex_cli", "claude_cli"]


class ProviderRouteIdentity(BaseModel):
    """Stable control identity plus presentation metadata for one route."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    role_id: OrchestrationRole
    provider_adapter_id: ProviderAdapterId
    route_id: str = Field(min_length=1, max_length=120)
    model_id: str = Field(min_length=1, max_length=240)
    display_name: str = Field(min_length=1, max_length=120)


IMPLEMENTATION_ROUTE = ProviderRouteIdentity(
    role_id="implementation",
    provider_adapter_id="codex_cli",
    route_id="builtin.implementation.v1",
    model_id="gpt-5.6-luna",
    display_name="Luna High",
)
ADVERSARIAL_REVIEW_ROUTE = ProviderRouteIdentity(
    role_id="adversarial_review",
    provider_adapter_id="claude_cli",
    route_id="builtin.adversarial_review.v1",
    model_id="sonnet",
    display_name="Sonnet 5 High",
)
ESCALATION_EXECUTIVE_ROUTE = ProviderRouteIdentity(
    role_id="escalation_executive",
    provider_adapter_id="codex_cli",
    route_id="builtin.escalation_executive.v1",
    model_id="gpt-5.6-sol",
    display_name="Sol High",
)
POLICY_AUTHORITY_ROUTE = ProviderRouteIdentity(
    role_id="policy_authority",
    provider_adapter_id="codex_cli",
    route_id="builtin.policy_authority.v1",
    model_id="gpt-5.6-terra",
    display_name="Terra High",
)

ROUTE_IDENTITIES: Mapping[OrchestrationRole, ProviderRouteIdentity] = (
    MappingProxyType(
        {
            route.role_id: route
            for route in (
                IMPLEMENTATION_ROUTE,
                ADVERSARIAL_REVIEW_ROUTE,
                ESCALATION_EXECUTIVE_ROUTE,
                POLICY_AUTHORITY_ROUTE,
            )
        }
    )
)

OPERATION_ROLES: Mapping[ProviderOperation, OrchestrationRole] = MappingProxyType(
    {
        "implementation_write": "implementation",
        "correction_write": "implementation",
        "specification_review": "adversarial_review",
        "implementation_review": "adversarial_review",
        "escalation_guidance": "escalation_executive",
        "policy_clarification": "policy_authority",
    }
)


ReviewStatus = Literal["PASS", "FAIL"]
ReviewCategory = Literal[
    "PASS",
    "IMPLEMENTATION_DEFECT",
    "POLICY_AMBIGUITY",
    "SCOPE_VIOLATION",
]


class ReviewResult(BaseModel):
    """The small, deliberately closed result vocabulary used by Sonnet."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: ReviewStatus
    category: ReviewCategory
    finding_key: str | None = None
    summary: str


ReviewOperation = Literal["specification_review", "implementation_review"]
ReviewUnreadableReason = Literal[
    "invalid_review_envelope",
    "invalid_review_schema",
    "invalid_review_semantics",
    "unreadable_legacy_review",
]


class ReviewRecord(BaseModel):
    """One immutable parsed review, linked to its exact raw physical attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    recorded_at: str
    operation_id: ReviewOperation
    result: ReviewResult
    provider_record_index: int = Field(ge=0)


class UnreadableReviewRecord(BaseModel):
    """One immutable bounded marker for an unreadable legacy review attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    recorded_at: str
    operation_id: ReviewOperation
    provider_record_index: int = Field(ge=0)
    reason_code: ReviewUnreadableReason


class RepoState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

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
TargetReleaseReason = Literal["published", "operator_released"]
RunMigrationDisposition = Literal[
    "resume_eligibility_deferred",
    "resume_blocked",
    "inspection_only",
]
LegacyRunStructuralClass = Literal[
    "V1",
    "V2",
    "V3",
    "V4",
    "V5",
    "V6-base",
    "V6-supervisor",
    "V6-provenance",
    "V6-writer",
    "V6-owner",
    "V6-current",
]
RunStructuralClass = Literal[
    "V1",
    "V2",
    "V3",
    "V4",
    "V5",
    "V6-base",
    "V6-supervisor",
    "V6-provenance",
    "V6-writer",
    "V6-owner",
    "V6-current",
    "V7",
    "V8",
]


class RunMigrationAudit(BaseModel):
    """Immutable provenance for one explicit historical-record migration."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    migration_id: str = Field(min_length=1, max_length=64)
    migrated_at: str
    source_schema_version: int = Field(ge=1, lt=7)
    target_schema_version: Literal[7] = 7
    source_structural_class: LegacyRunStructuralClass
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    applied_steps: tuple[str, ...] = Field(min_length=1, strict=False)
    reason_codes: tuple[str, ...] = Field(default_factory=tuple, strict=False)
    disposition: RunMigrationDisposition


class IdentityMigrationAudit(BaseModel):
    """Immutable provenance for the explicit stable-identity migration."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    migration_id: str = Field(min_length=1, max_length=64)
    migrated_at: str
    source_schema_version: int = Field(ge=1, lt=8)
    target_schema_version: Literal[8] = 8
    source_structural_class: RunStructuralClass
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    applied_steps: tuple[str, ...] = Field(min_length=1, strict=False)
    reason_codes: tuple[str, ...] = Field(default_factory=tuple, strict=False)
    disposition: RunMigrationDisposition


class ReviewMigrationAudit(BaseModel):
    """Immutable provenance for the explicit parsed-review backfill."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    migration_id: str = Field(min_length=1, max_length=64)
    migrated_at: str
    source_schema_version: int = Field(ge=1, lt=9)
    target_schema_version: Literal[9] = 9
    source_structural_class: RunStructuralClass
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    applied_steps: tuple[str, ...] = Field(min_length=1, strict=False)
    reason_codes: tuple[str, ...] = Field(default_factory=tuple, strict=False)
    parsed_count: int = Field(ge=0)
    unreadable_count: int = Field(ge=0)
    disposition: RunMigrationDisposition


class TargetOwnership(BaseModel):
    """Durable audit of one run's ownership of a target checkout."""

    model_config = ConfigDict(extra="forbid", strict=True)

    target_key: str = Field(min_length=64, max_length=64)
    canonical_repo: str = Field(min_length=1, max_length=4096)
    device: int = Field(ge=0)
    inode: int = Field(ge=0)
    acquired_at: str
    released_at: str | None = None
    release_reason: TargetReleaseReason | None = None
    release_note: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_release(self) -> "TargetOwnership":
        released = self.released_at is not None
        if released != (self.release_reason is not None):
            raise ValueError("target release timestamp and reason must appear together")
        if self.release_reason == "operator_released" and self.release_note is None:
            raise ValueError("operator target release requires a note")
        if self.release_reason != "operator_released" and self.release_note is not None:
            raise ValueError("target release note is only valid for operator release")
        return self


class WriterAttemptState(BaseModel):
    """Durable write-ahead repository evidence for one writer invocation."""

    model_config = ConfigDict(extra="forbid", strict=True)

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

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

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
    model_config = ConfigDict(extra="forbid", strict=True)

    identity: ProviderRouteIdentity
    operation_id: ProviderOperation
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
    model_config = ConfigDict(extra="forbid", strict=True)

    operation: str
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class PolicyDecision(BaseModel):
    """Immutable human-approved policy clarification."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    decision_id: str
    approved_at: str
    approved_by: Literal["human"] = "human"
    source_role_id: Literal["policy_authority"] = "policy_authority"
    source_route_id: str = POLICY_AUTHORITY_ROUTE.route_id
    source_provider_record_index: int | None = Field(default=None, ge=0)
    source_link_reason: Literal["legacy_source_attempt_unlinked"] | None = None
    trigger_finding_key: str | None = None
    trigger_summary: str
    recommendation: str
    approved_text: str

    @model_validator(mode="after")
    def validate_source_link(self) -> "PolicyDecision":
        if (self.source_provider_record_index is None) == (
            self.source_link_reason is None
        ):
            raise ValueError(
                "policy source requires either a record link or a legacy reason"
            )
        return self


class WorkflowRun(BaseModel):
    """Everything needed to audit and safely resume one run."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[9] = CURRENT_RUN_SCHEMA_VERSION
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
    review_records: list[ReviewRecord] = Field(default_factory=list)
    unreadable_review_records: list[UnreadableReviewRecord] = Field(
        default_factory=list
    )
    terra_resolution: str | None = None
    sol_guidance: str | None = None
    policy_decisions: list[PolicyDecision] = Field(default_factory=list)
    provider_resume_stage: str | None = None
    provider_resume_prompt: str | None = None
    provider_resume_identity: ProviderRouteIdentity | None = None
    provider_resume_operation_id: ProviderOperation | None = None
    migration_audit: RunMigrationAudit | None = None
    identity_migration_audit: IdentityMigrationAudit | None = None
    review_migration_audit: ReviewMigrationAudit | None = None
    target_ownership: TargetOwnership | None = None
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

    @model_validator(mode="after")
    def validate_provider_identities(self) -> "WorkflowRun":
        legacy_audit = self.migration_audit
        identity_audit = self.identity_migration_audit
        review_audit = self.review_migration_audit
        if legacy_audit is not None and identity_audit is None:
            raise ValueError("schema-8 migration audit lacks identity audit")
        if identity_audit is not None:
            if not identity_audit.applied_steps or (
                identity_audit.applied_steps[-1] != "7_to_8"
            ):
                raise ValueError("identity migration steps are incoherent")
            if identity_audit.source_schema_version <= 6:
                if legacy_audit is None:
                    raise ValueError("identity migration lacks schema-7 audit")
                if (
                    legacy_audit.migration_id != identity_audit.migration_id
                    or legacy_audit.migrated_at != identity_audit.migrated_at
                    or legacy_audit.source_schema_version
                    != identity_audit.source_schema_version
                    or legacy_audit.source_structural_class
                    != identity_audit.source_structural_class
                    or legacy_audit.source_sha256
                    != identity_audit.source_sha256
                    or legacy_audit.applied_steps
                    != identity_audit.applied_steps[:-1]
                ):
                    raise ValueError("migration audit lineage is incoherent")
            if (
                legacy_audit is not None
                and legacy_audit.disposition != identity_audit.disposition
            ):
                raise ValueError("migration audit disposition is incoherent")

        if review_audit is not None:
            if not review_audit.applied_steps or (
                review_audit.applied_steps[-1] != "8_to_9"
            ):
                raise ValueError("review migration steps are incoherent")
            if identity_audit is None:
                if (
                    review_audit.source_schema_version < 8
                    or review_audit.applied_steps != ("8_to_9",)
                ):
                    raise ValueError("review migration lineage is incoherent")
            else:
                if review_audit.source_schema_version <= 7:
                    if (
                        identity_audit.migration_id != review_audit.migration_id
                        or identity_audit.migrated_at != review_audit.migrated_at
                        or identity_audit.source_schema_version
                        != review_audit.source_schema_version
                        or identity_audit.source_structural_class
                        != review_audit.source_structural_class
                        or identity_audit.source_sha256
                        != review_audit.source_sha256
                        or identity_audit.applied_steps
                        != review_audit.applied_steps[:-1]
                    ):
                        raise ValueError("review migration lineage is incoherent")
                if identity_audit.disposition != review_audit.disposition:
                    raise ValueError(
                        "review migration audit disposition is incoherent"
                    )
            if review_audit.parsed_count != len(self.review_records):
                raise ValueError("review migration audit parsed count is incoherent")
            if review_audit.unreadable_count != len(
                self.unreadable_review_records
            ):
                raise ValueError(
                    "review migration audit unreadable count is incoherent"
                )

        pending = (
            self.provider_resume_stage,
            self.provider_resume_prompt,
            self.provider_resume_identity,
            self.provider_resume_operation_id,
        )
        if any(item is not None for item in pending) and not all(
            item is not None for item in pending
        ):
            raise ValueError("provider resume state must be complete")

        migrated = (
            self.migration_audit is not None
            or self.identity_migration_audit is not None
            or self.review_migration_audit is not None
        )
        for record in self.provider_runs:
            expected_role = OPERATION_ROLES[record.operation_id]
            if record.identity.role_id != expected_role:
                raise ValueError("provider operation does not match role")
            catalog = ROUTE_IDENTITIES[record.identity.role_id]
            if (
                record.identity.provider_adapter_id
                != catalog.provider_adapter_id
                or record.identity.route_id != catalog.route_id
                or record.identity.model_id != catalog.model_id
            ):
                raise ValueError("provider route identity is not recognized")
            expected_capability: ProviderCapability = (
                "workspace_write"
                if record.identity.role_id == "implementation"
                else "read_only"
            )
            if record.capability is None:
                if not migrated:
                    raise ValueError("ordinary provider record lacks capability")
            elif record.capability != expected_capability:
                raise ValueError("provider capability does not match role")

        if self.provider_resume_identity is not None:
            operation = self.provider_resume_operation_id
            if operation is None or (
                self.provider_resume_identity.role_id != OPERATION_ROLES[operation]
            ):
                raise ValueError("provider resume operation does not match role")
            catalog = ROUTE_IDENTITIES[self.provider_resume_identity.role_id]
            if (
                self.provider_resume_identity.provider_adapter_id
                != catalog.provider_adapter_id
                or self.provider_resume_identity.route_id != catalog.route_id
                or self.provider_resume_identity.model_id != catalog.model_id
            ):
                raise ValueError("provider resume route is not recognized")
            expected_pending = {
                "implementing": "implementation_write",
                "correcting": "correction_write",
                "spec_reviewing": "specification_review",
                "reviewing": "implementation_review",
                "sol_escalating": "escalation_guidance",
                "terra_resolving": "policy_clarification",
            }.get(self.provider_resume_stage or "")
            if expected_pending != operation:
                raise ValueError("provider resume stage does not match operation")

        active = self.active_writer_attempt
        if active is not None and active.provider_record_index is not None:
            if active.provider_record_index >= len(self.provider_runs):
                raise ValueError("writer provider record index is out of range")
            record = self.provider_runs[active.provider_record_index]
            expected_operation: ProviderOperation = (
                "correction_write"
                if active.purpose == "correction"
                else "implementation_write"
            )
            if (
                record.identity.role_id != "implementation"
                or record.identity.route_id != IMPLEMENTATION_ROUTE.route_id
                or record.operation_id != expected_operation
                or record.capability != "workspace_write"
                or record.repository_fingerprint_before
                != active.pre_fingerprint
                or (
                    active.post_fingerprint is not None
                    and record.repository_fingerprint_after
                    != active.post_fingerprint
                )
            ):
                raise ValueError("writer provider record link is incoherent")

        for decision in self.policy_decisions:
            index = decision.source_provider_record_index
            if index is None:
                if not migrated:
                    raise ValueError("ordinary policy decision lacks source link")
                continue
            if index >= len(self.provider_runs):
                raise ValueError("policy source record index is out of range")
            record = self.provider_runs[index]
            if (
                record.identity.role_id != "policy_authority"
                or record.identity.route_id != decision.source_route_id
                or record.operation_id != "policy_clarification"
                or record.returncode != 0
                or record.failure_kind is not None
            ):
                raise ValueError("policy source record link is incoherent")

        index_owners: dict[int, str] = {}
        for review in [*self.review_records, *self.unreadable_review_records]:
            index = review.provider_record_index
            if index in index_owners:
                raise ValueError("review provider record index is duplicated")
            index_owners[index] = review.operation_id
            if index >= len(self.provider_runs):
                raise ValueError("review provider record index is out of range")
            record = self.provider_runs[index]
            if (
                record.identity.role_id != "adversarial_review"
                or record.operation_id != review.operation_id
                or record.returncode != 0
                or record.failure_kind is not None
            ):
                raise ValueError("review provider record link is incoherent")

        for field, operation_id in (
            (self.spec_review, "specification_review"),
            (self.implementation_review, "implementation_review"),
        ):
            parsed = [
                review.result
                for review in self.review_records
                if review.operation_id == operation_id
            ]
            expected = parsed[-1] if parsed else None
            if field == expected:
                continue
            if not migrated:
                raise ValueError("review current field contradicts parsed history")
            reasons = (
                set(review_audit.reason_codes)
                if review_audit is not None
                else set()
            )
            if field is None and expected is not None:
                allowed = {"resume_review_field_absent"}
            elif field is not None and expected is None:
                allowed = {"current_review_unreadable"}
            else:
                allowed = {"current_review_unreadable", "resume_review_field_absent"}
            if not (reasons & allowed):
                raise ValueError(
                    "review current field contradicts migrated history"
                )
        return self


if __name__ == "__main__":
    for role, route in ROUTE_IDENTITIES.items():
        print(
            f"{role}: {route.provider_adapter_id} -> {route.model_id} "
            f"({route.route_id})"
        )
