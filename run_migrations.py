"""Strict historical run classification and pure adjacent migrations."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from models import (
    ADVERSARIAL_REVIEW_ROUTE,
    CURRENT_RUN_SCHEMA_VERSION,
    ESCALATION_EXECUTIVE_ROUTE,
    GitRecord,
    IdentityMigrationAudit,
    IMPLEMENTATION_ROUTE,
    OLDEST_MIGRATABLE_RUN_SCHEMA_VERSION,
    POLICY_AUTHORITY_ROUTE,
    PolicyDecision,
    ProviderCapability,
    ProviderFailureKind,
    ProviderFailureSource,
    ProviderOperation,
    ProviderRecord,
    ProviderRouteIdentity,
    RepoState,
    ReviewCategory,
    ReviewStatus,
    RunMigrationDisposition,
    RunStructuralClass,
    TargetOwnership,
    WorkflowRun,
    WriterAttemptState,
    WriterRecoveryDecision,
)
from providers import (
    ProviderExecution,
    parse_sonnet_review,
)


RecordTreatment = Literal["current", "migrate", "archive", "unsupported"]
RecordState = Literal[
    "CURRENT",
    "MIGRATION_REQUIRED",
    "RESUME_BLOCKED",
    "ARCHIVE_ONLY",
    "UNSUPPORTED",
]


class MigrationError(RuntimeError):
    """A bounded migration failure safe to show without source content."""

    def __init__(self, reason_code: str, field_path: str | None = None) -> None:
        self.reason_code = reason_code
        self.field_path = field_path
        detail = reason_code if field_path is None else f"{reason_code}:{field_path}"
        super().__init__(detail)


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _ReviewV1(_ClosedModel):
    status: ReviewStatus
    category: ReviewCategory
    summary: str


class _ReviewV3(_ReviewV1):
    finding_key: str | None = None


class _ProviderV1(_ClosedModel):
    provider: str
    purpose: str
    command: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class _ProviderV5(_ProviderV1):
    duration_seconds: float | None = Field(default=None, ge=0)


class _ProviderV6(_ProviderV5):
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


class _PolicyDecisionV4(_ClosedModel):
    decision_id: str
    approved_at: str
    approved_by: Literal["human"] = "human"
    source_provider: str = "Terra High"
    trigger_finding_key: str | None = None
    trigger_summary: str
    recommendation: str
    approved_text: str


class _RunMigrationAuditV7(_ClosedModel):
    migration_id: str = Field(min_length=1, max_length=64)
    migrated_at: str
    source_schema_version: int = Field(ge=1, lt=7)
    target_schema_version: Literal[7] = 7
    source_structural_class: Literal[
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
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    applied_steps: tuple[str, ...] = Field(min_length=1, strict=False)
    reason_codes: tuple[str, ...] = Field(default_factory=tuple, strict=False)
    disposition: RunMigrationDisposition


class _RunV1(_ClosedModel):
    schema_version: Literal[1] = 1
    run_id: str
    created_at: str
    task_ref: str
    task_file: str
    task_sha256: str
    specification: str
    repo: RepoState
    stage: str = "created"
    correction_cycles: int = Field(default=0, ge=0, le=1)
    spec_review: _ReviewV1 | None = None
    implementation_review: _ReviewV1 | None = None
    terra_resolution: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    working_tree_fingerprint: str | None = None
    verification: dict[str, object] = Field(default_factory=dict)
    provider_runs: list[_ProviderV1] = Field(default_factory=list)
    git_operations: list[GitRecord] = Field(default_factory=list)
    commit_hash: str | None = None
    commit_message: str | None = None
    last_error: str | None = None


class _RunV2(_RunV1):
    schema_version: Literal[2] = 2
    correction_cycles: int = Field(default=0, ge=0, le=3)
    sol_guidance: str | None = None


class _RunV3(_RunV2):
    schema_version: Literal[3] = 3
    correction_cycles: int = Field(default=0, ge=0, le=12)
    spec_review: _ReviewV3 | None = None
    implementation_review: _ReviewV3 | None = None


class _RunV4(_RunV3):
    schema_version: Literal[4] = 4
    policy_decisions: list[_PolicyDecisionV4] = Field(default_factory=list)


class _RunV5(_RunV4):
    schema_version: Literal[5] = 5
    updated_at: str | None = None
    provider_runs: list[_ProviderV5] = Field(default_factory=list)


class _RunV6(_RunV5):
    schema_version: Literal[6] = 6
    provider_resume_stage: str | None = None
    provider_resume_prompt: str | None = None
    target_ownership: TargetOwnership | None = None
    active_writer_attempt: WriterAttemptState | None = None
    writer_recovery_decisions: list[WriterRecoveryDecision] = Field(
        default_factory=list
    )
    provider_runs: list[_ProviderV6] = Field(default_factory=list)


class _RunV7(_RunV6):
    schema_version: Literal[7] = 7
    migration_audit: _RunMigrationAuditV7 | None = None


class _RunV8(_RunV7):
    """The exact historical schema-8 shape before parsed-review backfill."""

    schema_version: Literal[8] = 8
    provider_runs: list[ProviderRecord] = Field(default_factory=list)
    policy_decisions: list[PolicyDecision] = Field(default_factory=list)
    provider_resume_identity: ProviderRouteIdentity | None = None
    provider_resume_operation_id: ProviderOperation | None = None
    identity_migration_audit: IdentityMigrationAudit | None = None

    @model_validator(mode="after")
    def validate_identity_audit_presence(self) -> "_RunV8":
        if self.migration_audit is not None and self.identity_migration_audit is None:
            raise ValueError("schema-8 migration audit lacks identity audit")
        return self


_HISTORICAL_MODELS: dict[int, type[BaseModel]] = {
    1: _RunV1,
    2: _RunV2,
    3: _RunV3,
    4: _RunV4,
    5: _RunV5,
    6: _RunV6,
    7: _RunV7,
    8: _RunV8,
}


@dataclass(frozen=True)
class RecordClassification:
    treatment: RecordTreatment
    record_state: RecordState
    source_sha256: str
    schema_version: int | None
    structural_class: RunStructuralClass | None
    disposition: RunMigrationDisposition | None
    reason_code: str
    field_path: str | None
    run_id: str | None
    task_ref: str | None
    stage: str | None
    payload: dict[str, Any] | None
    current_run: WorkflowRun | None = None


@dataclass(frozen=True)
class MigrationResult:
    run: WorkflowRun
    source_sha256: str
    source_schema_version: int
    source_structural_class: RunStructuralClass
    applied_steps: tuple[str, ...]


@dataclass(frozen=True)
class MigrationContext:
    migration_id: str
    migrated_at: str
    source_schema_version: int
    source_structural_class: RunStructuralClass
    source_sha256: str
    disposition: RunMigrationDisposition
    applied_steps: tuple[str, ...]
    reason_codes: tuple[str, ...]


def _reject_constant(value: str) -> None:
    raise MigrationError("invalid_envelope", "numeric_token")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MigrationError("invalid_envelope", "duplicate_key")
        result[key] = value
    return result


def strict_json_object(source: bytes) -> dict[str, Any]:
    try:
        text = source.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except MigrationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationError("invalid_envelope") from exc
    if not isinstance(value, dict):
        raise MigrationError("invalid_envelope", "top_level")
    return value


def _bounded_identity(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 128:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        return None
    return value


def _bounded_field_path(error: ValidationError) -> str | None:
    details = error.errors(include_url=False, include_context=False, include_input=False)
    if not details:
        return None
    segments = []
    for segment in details[0].get("loc", ()):
        rendered = str(segment)
        if re.fullmatch(r"[A-Za-z0-9_-]+", rendered):
            segments.append(rendered)
    return ".".join(segments)[:240] or None


def _classify_invalid_known(
    *,
    source_sha256: str,
    schema_version: int,
    payload: dict[str, Any],
    reason_code: str,
    field_path: str | None = None,
) -> RecordClassification:
    run_id = _bounded_identity(payload.get("run_id"))
    treatment: RecordTreatment = "archive" if run_id is not None else "unsupported"
    state: RecordState = "ARCHIVE_ONLY" if treatment == "archive" else "UNSUPPORTED"
    return RecordClassification(
        treatment=treatment,
        record_state=state,
        source_sha256=source_sha256,
        schema_version=schema_version,
        structural_class=None,
        disposition="inspection_only" if treatment == "archive" else None,
        reason_code=reason_code,
        field_path=field_path,
        run_id=run_id,
        task_ref=_bounded_identity(payload.get("task_ref")),
        stage=_bounded_identity(payload.get("stage")),
        payload=payload,
    )


def _v6_structural_class(payload: dict[str, Any]) -> RunStructuralClass:
    providers = payload.get("provider_runs")
    provider_dicts = providers if isinstance(providers, list) else []
    current_top_level = {
        "target_ownership",
        "active_writer_attempt",
        "writer_recovery_decisions",
    }.issubset(payload)
    current_providers = all(
        isinstance(record, dict)
        and {
            "failure_source",
            "failure_code",
            "capability",
            "repository_fingerprint_before",
            "repository_fingerprint_after",
        }.issubset(record)
        for record in provider_dicts
    )
    if current_top_level and current_providers:
        return "V6-current"
    if "target_ownership" in payload:
        return "V6-owner"
    if (
        "active_writer_attempt" in payload
        or "writer_recovery_decisions" in payload
        or any(
            isinstance(record, dict)
            and (
                "capability" in record
                or "repository_fingerprint_before" in record
                or "repository_fingerprint_after" in record
            )
            for record in provider_dicts
        )
    ):
        return "V6-writer"
    if any(
        isinstance(record, dict)
        and ("failure_source" in record or "failure_code" in record)
        for record in provider_dicts
    ):
        return "V6-provenance"
    if any(
        isinstance(record, dict)
        and record.get("failure_kind") in {"timeout", "interrupted"}
        for record in provider_dicts
    ):
        return "V6-supervisor"
    return "V6-base"


def _v6_coherence_reason(run: _RunV6) -> tuple[str, str | None] | None:
    ownership = run.target_ownership
    if ownership is not None:
        if ownership.canonical_repo != run.repo.repo:
            return "ownership_evidence_incoherent", "target_ownership.canonical_repo"
    writer_stages = {
        "implementing",
        "correcting",
        "blocked_writer_retry_required",
        "blocked_writer_partial_changes",
        "blocked_writer_state_unknown",
    }
    active = run.active_writer_attempt
    if run.stage in writer_stages and active is None:
        return "writer_evidence_incoherent", "active_writer_attempt"
    if active is None:
        return None
    if run.stage in {"implementing", "correcting"} and active.stage != run.stage:
        return "writer_evidence_incoherent", "active_writer_attempt.stage"
    index = active.provider_record_index
    if index is None:
        if active.post_fingerprint is not None:
            return "writer_evidence_incoherent", "provider_record_index"
        return None
    if index >= len(run.provider_runs):
        return "writer_evidence_incoherent", "provider_record_index"
    record = run.provider_runs[index]
    if (
        record.provider != "Luna High"
        or record.purpose != active.purpose
        or record.capability != "workspace_write"
    ):
        return "writer_evidence_incoherent", "provider_record_index"
    if record.repository_fingerprint_before != active.pre_fingerprint:
        return "writer_evidence_incoherent", "repository_fingerprint_before"
    if (
        active.post_fingerprint is not None
        and record.repository_fingerprint_after != active.post_fingerprint
    ):
        return "writer_evidence_incoherent", "repository_fingerprint_after"
    return None


_LEGACY_PROVIDER_IDENTITIES = {
    ("Luna High", "implementation"): (
        IMPLEMENTATION_ROUTE,
        "implementation_write",
    ),
    ("Luna High", "correction"): (
        IMPLEMENTATION_ROUTE,
        "correction_write",
    ),
    ("Sonnet 5 High", "specification"): (
        ADVERSARIAL_REVIEW_ROUTE,
        "specification_review",
    ),
    ("Sonnet 5 High", "implementation"): (
        ADVERSARIAL_REVIEW_ROUTE,
        "implementation_review",
    ),
    ("Sol High", "escalation guidance"): (
        ESCALATION_EXECUTIVE_ROUTE,
        "escalation_guidance",
    ),
    ("Terra High", "policy clarification"): (
        POLICY_AUTHORITY_ROUTE,
        "policy_clarification",
    ),
}

_LEGACY_RESUME_IDENTITIES = {
    "implementing": (IMPLEMENTATION_ROUTE, "implementation_write"),
    "correcting": (IMPLEMENTATION_ROUTE, "correction_write"),
    "spec_reviewing": (ADVERSARIAL_REVIEW_ROUTE, "specification_review"),
    "reviewing": (ADVERSARIAL_REVIEW_ROUTE, "implementation_review"),
    "sol_escalating": (ESCALATION_EXECUTIVE_ROUTE, "escalation_guidance"),
    "terra_resolving": (POLICY_AUTHORITY_ROUTE, "policy_clarification"),
}


def _legacy_identity_reason(
    payload: dict[str, Any],
) -> tuple[str, str | None] | None:
    providers = payload.get("provider_runs", [])
    if isinstance(providers, list):
        for index, record in enumerate(providers):
            if not isinstance(record, dict):
                continue
            mapped = _LEGACY_PROVIDER_IDENTITIES.get(
                (record.get("provider"), record.get("purpose"))
            )
            if mapped is None:
                return (
                    "legacy_provider_identity_unmappable",
                    f"provider_runs.{index}",
                )
            identity, _ = mapped
            capability = record.get("capability")
            expected = (
                "workspace_write"
                if identity.role_id == "implementation"
                else "read_only"
            )
            if capability is not None and capability != expected:
                return (
                    "legacy_provider_capability_incoherent",
                    f"provider_runs.{index}.capability",
                )

    decisions = payload.get("policy_decisions", [])
    if isinstance(decisions, list):
        for index, decision in enumerate(decisions):
            if (
                isinstance(decision, dict)
                and decision.get("source_provider", "Terra High") != "Terra High"
            ):
                return (
                    "legacy_policy_source_unmappable",
                    f"policy_decisions.{index}.source_provider",
                )

    if "provider_resume_stage" in payload or "provider_resume_prompt" in payload:
        stage = payload.get("provider_resume_stage")
        prompt = payload.get("provider_resume_prompt")
        if (stage is None) != (prompt is None):
            return (
                "legacy_provider_resume_incoherent",
                "provider_resume_stage",
            )
        if stage is not None and stage not in _LEGACY_RESUME_IDENTITIES:
            return (
                "legacy_provider_resume_unmappable",
                "provider_resume_stage",
            )
    return None


def _migration_disposition(
    payload: dict[str, Any],
    schema_version: int,
) -> RunMigrationDisposition:
    if schema_version == 7:
        audit = payload.get("migration_audit")
        if isinstance(audit, dict) and audit.get("disposition") in {
            "resume_eligibility_deferred",
            "resume_blocked",
            "inspection_only",
        }:
            return audit["disposition"]
    if schema_version == 8:
        audit = payload.get("identity_migration_audit")
        if isinstance(audit, dict) and audit.get("disposition") in {
            "resume_eligibility_deferred",
            "resume_blocked",
            "inspection_only",
        }:
            return audit["disposition"]
    if schema_version < 6:
        return "resume_eligibility_deferred"
    blocked_stages = {
        "blocked_provider_timeout",
        "blocked_provider_interrupted",
        "blocked_writer_state_unknown",
        "blocked_repeated_finding",
        "blocked_correction_budget",
    }
    providers = payload.get("provider_runs")
    supervisor_stop = isinstance(providers, list) and any(
        isinstance(record, dict)
        and record.get("failure_kind") in {"timeout", "interrupted"}
        for record in providers
    )
    return (
        "resume_blocked"
        if payload.get("stage") in blocked_stages or supervisor_stop
        else "resume_eligibility_deferred"
    )


def classify_run_bytes(source: bytes) -> RecordClassification:
    source_sha256 = hashlib.sha256(source).hexdigest()
    try:
        payload = strict_json_object(source)
    except MigrationError as exc:
        return RecordClassification(
            treatment="unsupported",
            record_state="UNSUPPORTED",
            source_sha256=source_sha256,
            schema_version=None,
            structural_class=None,
            disposition=None,
            reason_code=exc.reason_code,
            field_path=exc.field_path,
            run_id=None,
            task_ref=None,
            stage=None,
            payload=None,
        )

    version = payload.get("schema_version")
    if type(version) is not int or version <= 0:
        return RecordClassification(
            treatment="unsupported",
            record_state="UNSUPPORTED",
            source_sha256=source_sha256,
            schema_version=None,
            structural_class=None,
            disposition=None,
            reason_code="invalid_schema_version",
            field_path="schema_version",
            run_id=_bounded_identity(payload.get("run_id")),
            task_ref=_bounded_identity(payload.get("task_ref")),
            stage=_bounded_identity(payload.get("stage")),
            payload=payload,
        )
    if version > CURRENT_RUN_SCHEMA_VERSION:
        return RecordClassification(
            treatment="unsupported",
            record_state="UNSUPPORTED",
            source_sha256=source_sha256,
            schema_version=version,
            structural_class=None,
            disposition=None,
            reason_code="future_schema",
            field_path="schema_version",
            run_id=_bounded_identity(payload.get("run_id")),
            task_ref=_bounded_identity(payload.get("task_ref")),
            stage=_bounded_identity(payload.get("stage")),
            payload=payload,
        )
    if version < OLDEST_MIGRATABLE_RUN_SCHEMA_VERSION:
        return _classify_invalid_known(
            source_sha256=source_sha256,
            schema_version=version,
            payload=payload,
            reason_code="unknown_schema",
            field_path="schema_version",
        )

    if version == CURRENT_RUN_SCHEMA_VERSION:
        try:
            run = WorkflowRun.model_validate(payload)
        except ValidationError as exc:
            return _classify_invalid_known(
                source_sha256=source_sha256,
                schema_version=version,
                payload=payload,
                reason_code="archive_only",
                field_path=_bounded_field_path(exc),
            )
        if run.review_migration_audit is not None:
            state: RecordState = "RESUME_BLOCKED"
            disposition = run.review_migration_audit.disposition
            structural_class = run.review_migration_audit.source_structural_class
        elif run.identity_migration_audit is not None:
            state = "RESUME_BLOCKED"
            disposition = run.identity_migration_audit.disposition
            structural_class = (
                run.identity_migration_audit.source_structural_class
            )
        elif run.migration_audit is not None:
            # Unreachable for schema 9: WorkflowRun validation requires the
            # identity audit whenever a legacy audit is present. Kept as an
            # explicit total branch so a migrated record can never be
            # classified as current.
            state = "RESUME_BLOCKED"
            disposition = run.migration_audit.disposition
            structural_class = run.migration_audit.source_structural_class
        else:
            state = "CURRENT"
            disposition = None
            structural_class = None
        return RecordClassification(
            treatment="current",
            record_state=state,
            source_sha256=source_sha256,
            schema_version=version,
            structural_class=structural_class,
            disposition=disposition,
            reason_code=(
                "current"
                if disposition is None
                else disposition
            ),
            field_path=None,
            run_id=run.run_id,
            task_ref=run.task_ref,
            stage=run.stage,
            payload=payload,
            current_run=run,
        )

    model = _HISTORICAL_MODELS.get(version)
    if model is None:
        return RecordClassification(
            treatment="unsupported",
            record_state="UNSUPPORTED",
            source_sha256=source_sha256,
            schema_version=version,
            structural_class=None,
            disposition=None,
            reason_code="unknown_schema",
            field_path="schema_version",
            run_id=_bounded_identity(payload.get("run_id")),
            task_ref=_bounded_identity(payload.get("task_ref")),
            stage=_bounded_identity(payload.get("stage")),
            payload=payload,
        )
    try:
        historical = model.model_validate(payload)
    except ValidationError as exc:
        return _classify_invalid_known(
            source_sha256=source_sha256,
            schema_version=version,
            payload=payload,
            reason_code="archive_only",
            field_path=_bounded_field_path(exc),
        )

    structural_class: RunStructuralClass = (
        _v6_structural_class(payload) if version == 6 else f"V{version}"  # type: ignore[assignment]
    )
    if version in {6, 7}:
        coherence = _v6_coherence_reason(historical)  # type: ignore[arg-type]
        if coherence is not None:
            reason, field_path = coherence
            return RecordClassification(
                treatment="archive",
                record_state="ARCHIVE_ONLY",
                source_sha256=source_sha256,
                schema_version=version,
                structural_class=structural_class,
                disposition="inspection_only",
                reason_code=reason,
                field_path=field_path,
                run_id=_bounded_identity(payload.get("run_id")),
                task_ref=_bounded_identity(payload.get("task_ref")),
                stage=_bounded_identity(payload.get("stage")),
                payload=payload,
            )

    identity_reason = (
        None if version >= 8 else _legacy_identity_reason(payload)
    )
    if identity_reason is not None:
        reason, field_path = identity_reason
        return RecordClassification(
            treatment="archive",
            record_state="ARCHIVE_ONLY",
            source_sha256=source_sha256,
            schema_version=version,
            structural_class=structural_class,
            disposition="inspection_only",
            reason_code=reason,
            field_path=field_path,
            run_id=_bounded_identity(payload.get("run_id")),
            task_ref=_bounded_identity(payload.get("task_ref")),
            stage=_bounded_identity(payload.get("stage")),
            payload=payload,
        )

    disposition = _migration_disposition(payload, version)
    return RecordClassification(
        treatment="migrate",
        record_state=(
            "RESUME_BLOCKED"
            if disposition == "resume_blocked"
            else "MIGRATION_REQUIRED"
        ),
        source_sha256=source_sha256,
        schema_version=version,
        structural_class=structural_class,
        disposition=disposition,
        reason_code="migration_required",
        field_path=None,
        run_id=_bounded_identity(payload.get("run_id")),
        task_ref=_bounded_identity(payload.get("task_ref")),
        stage=_bounded_identity(payload.get("stage")),
        payload=payload,
    )


def _validate_historical(payload: dict[str, Any], version: int) -> None:
    model = _HISTORICAL_MODELS[version]
    try:
        model.model_validate(payload)
    except ValidationError as exc:
        raise MigrationError(
            "migration_step_failed",
            _bounded_field_path(exc),
        ) from exc


def _step_1_to_2(
    payload: dict[str, Any], _: MigrationContext
) -> tuple[dict[str, Any], list[str]]:
    result = copy.deepcopy(payload)
    result["schema_version"] = 2
    result.setdefault("sol_guidance", None)
    return result, ["missing_sol_guidance"]


def _step_2_to_3(
    payload: dict[str, Any], _: MigrationContext
) -> tuple[dict[str, Any], list[str]]:
    result = copy.deepcopy(payload)
    result["schema_version"] = 3
    for key in ("spec_review", "implementation_review"):
        review = result.get(key)
        if isinstance(review, dict):
            review.setdefault("finding_key", None)
    return result, ["missing_finding_identity"]


def _step_3_to_4(
    payload: dict[str, Any], _: MigrationContext
) -> tuple[dict[str, Any], list[str]]:
    result = copy.deepcopy(payload)
    result["schema_version"] = 4
    result.setdefault("policy_decisions", [])
    return result, ["missing_policy_decision_audit"]


def _step_4_to_5(
    payload: dict[str, Any], _: MigrationContext
) -> tuple[dict[str, Any], list[str]]:
    result = copy.deepcopy(payload)
    result["schema_version"] = 5
    result.setdefault("updated_at", None)
    for record in result.get("provider_runs", []):
        if isinstance(record, dict):
            record.setdefault("duration_seconds", None)
    return result, ["missing_timing_audit"]


def _step_5_to_6(
    payload: dict[str, Any], _: MigrationContext
) -> tuple[dict[str, Any], list[str]]:
    result = copy.deepcopy(payload)
    result["schema_version"] = 6
    result.setdefault("provider_resume_stage", None)
    result.setdefault("provider_resume_prompt", None)
    result.setdefault("target_ownership", None)
    result.setdefault("active_writer_attempt", None)
    result.setdefault("writer_recovery_decisions", [])
    for record in result.get("provider_runs", []):
        if isinstance(record, dict):
            record.setdefault("failure_kind", None)
            record.setdefault("failure_source", None)
            record.setdefault("failure_code", None)
            record.setdefault("capability", None)
            record.setdefault("repository_fingerprint_before", None)
            record.setdefault("repository_fingerprint_after", None)
            record.setdefault("retry_scheduled", False)
    return result, [
        "missing_failure_provenance",
        "missing_capability_audit",
        "missing_writer_audit",
        "missing_ownership_audit",
        "missing_retry_audit",
    ]


def _step_6_to_7(
    payload: dict[str, Any], context: MigrationContext
) -> tuple[dict[str, Any], list[str]]:
    result = copy.deepcopy(payload)
    result["schema_version"] = 7
    result["migration_audit"] = {
        "migration_id": context.migration_id,
        "migrated_at": context.migrated_at,
        "source_schema_version": context.source_schema_version,
        "target_schema_version": 7,
        "source_structural_class": context.source_structural_class,
        "source_sha256": context.source_sha256,
        "applied_steps": list(context.applied_steps),
        "reason_codes": sorted(set(context.reason_codes)),
        "disposition": context.disposition,
    }
    return result, []


def _step_7_to_8(
    payload: dict[str, Any], context: MigrationContext
) -> tuple[dict[str, Any], list[str]]:
    result = copy.deepcopy(payload)
    reasons: list[str] = []

    providers = result.get("provider_runs", [])
    for record in providers:
        if not isinstance(record, dict):
            raise MigrationError("migration_step_failed", "provider_runs")
        mapped = _LEGACY_PROVIDER_IDENTITIES.get(
            (record.pop("provider", None), record.pop("purpose", None))
        )
        if mapped is None:
            raise MigrationError(
                "legacy_provider_identity_unmappable",
                "provider_runs",
            )
        identity, operation_id = mapped
        record["identity"] = identity.model_dump(mode="json")
        record["operation_id"] = operation_id
    if providers:
        reasons.append("legacy_display_identity_mapped")

    successful_policy_records = [
        index
        for index, record in enumerate(providers)
        if isinstance(record, dict)
        and record.get("identity", {}).get("role_id") == "policy_authority"
        and record.get("operation_id") == "policy_clarification"
        and record.get("returncode") == 0
        and record.get("failure_kind") is None
    ]
    policy_decisions = result.get("policy_decisions", [])
    paired = (
        len(successful_policy_records) == len(policy_decisions) > 0
    )
    for index, decision in enumerate(policy_decisions):
        if not isinstance(decision, dict):
            raise MigrationError("migration_step_failed", "policy_decisions")
        if decision.pop("source_provider", "Terra High") != "Terra High":
            raise MigrationError(
                "legacy_policy_source_unmappable",
                "policy_decisions.source_provider",
            )
        decision["source_role_id"] = "policy_authority"
        decision["source_route_id"] = POLICY_AUTHORITY_ROUTE.route_id
        if paired:
            decision["source_provider_record_index"] = successful_policy_records[index]
            decision["source_link_reason"] = None
        else:
            decision["source_provider_record_index"] = None
            decision["source_link_reason"] = "legacy_source_attempt_unlinked"
            reasons.append("legacy_policy_source_attempt_unlinked")
    if policy_decisions:
        reasons.append("legacy_policy_source_mapped")

    stage = result.get("provider_resume_stage")
    prompt = result.get("provider_resume_prompt")
    if stage is None and prompt is None:
        result["provider_resume_identity"] = None
        result["provider_resume_operation_id"] = None
    else:
        mapped_resume = _LEGACY_RESUME_IDENTITIES.get(stage)
        if mapped_resume is None or prompt is None:
            raise MigrationError(
                "legacy_provider_resume_unmappable",
                "provider_resume_stage",
            )
        identity, operation_id = mapped_resume
        result["provider_resume_identity"] = identity.model_dump(mode="json")
        result["provider_resume_operation_id"] = operation_id
        reasons.append("legacy_provider_resume_identity_mapped")

    result["schema_version"] = 8
    result["identity_migration_audit"] = {
        "migration_id": context.migration_id,
        "migrated_at": context.migrated_at,
        "source_schema_version": context.source_schema_version,
        "target_schema_version": 8,
        "source_structural_class": context.source_structural_class,
        "source_sha256": context.source_sha256,
        "applied_steps": list(context.applied_steps),
        "reason_codes": sorted(set((*context.reason_codes, *reasons))),
        "disposition": context.disposition,
    }
    return result, reasons


_REVIEW_OPERATION_IDS = ("specification_review", "implementation_review")


def _review_operation_for(record: dict[str, Any]) -> str | None:
    identity = record.get("identity")
    operation = record.get("operation_id")
    if not isinstance(identity, dict) or identity.get("role_id") != "adversarial_review":
        return None
    if operation not in _REVIEW_OPERATION_IDS:
        return None
    return operation


def _unreadable_review_reason(exc: Exception) -> str:
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_review_envelope"
    if isinstance(exc, ValidationError):
        return "invalid_review_schema"
    if isinstance(exc, RuntimeError):
        return "unreadable_legacy_review"
    message = str(exc)
    if any(
        marker in message
        for marker in ("envelope", "structured_output", "must be an object")
    ):
        return "invalid_review_envelope"
    return "invalid_review_semantics"


def _step_8_to_9(
    payload: dict[str, Any], context: MigrationContext
) -> tuple[dict[str, Any], list[str]]:
    result = copy.deepcopy(payload)
    reasons: list[str] = []
    review_records: list[dict[str, Any]] = []
    unreadable_records: list[dict[str, Any]] = []

    providers = result.get("provider_runs", [])
    for index, record in enumerate(providers):
        if not isinstance(record, dict):
            raise MigrationError("migration_step_failed", "provider_runs")
        operation = _review_operation_for(record)
        if operation is None:
            continue
        if record.get("returncode") != 0 or record.get("failure_kind") is not None:
            continue
        execution = ProviderExecution(
            command=record.get("command", []),
            returncode=0,
            stdout=record.get("stdout", ""),
            stderr=record.get("stderr", ""),
        )
        try:
            parsed = parse_sonnet_review(execution)
        except Exception as exc:
            unreadable_records.append(
                {
                    "recorded_at": context.migrated_at,
                    "operation_id": operation,
                    "provider_record_index": index,
                    "reason_code": _unreadable_review_reason(exc),
                }
            )
        else:
            review_records.append(
                {
                    "recorded_at": context.migrated_at,
                    "operation_id": operation,
                    "result": parsed.model_dump(mode="json"),
                    "provider_record_index": index,
                }
            )

    for key, operation in (
        ("spec_review", "specification_review"),
        ("implementation_review", "implementation_review"),
    ):
        preserved = result.get(key)
        last_parsed = next(
            (
                item["result"]
                for item in reversed(review_records)
                if item["operation_id"] == operation
            ),
            None,
        )
        if preserved is None and last_parsed is not None:
            reasons.append("resume_review_field_absent")
        elif preserved is not None and (
            last_parsed is None or preserved != last_parsed
        ):
            reasons.append("current_review_unreadable")

    result["schema_version"] = 9
    result["review_records"] = review_records
    result["unreadable_review_records"] = unreadable_records
    result["review_migration_audit"] = {
        "migration_id": context.migration_id,
        "migrated_at": context.migrated_at,
        "source_schema_version": context.source_schema_version,
        "target_schema_version": 9,
        "source_structural_class": context.source_structural_class,
        "source_sha256": context.source_sha256,
        "applied_steps": list(context.applied_steps),
        "reason_codes": sorted(set((*context.reason_codes, *reasons))),
        "parsed_count": len(review_records),
        "unreadable_count": len(unreadable_records),
        "disposition": context.disposition,
    }
    return result, reasons


MigrationStep = Callable[
    [dict[str, Any], MigrationContext],
    tuple[dict[str, Any], list[str]],
]
MIGRATION_REGISTRY: dict[tuple[int, int], MigrationStep] = {
    (1, 2): _step_1_to_2,
    (2, 3): _step_2_to_3,
    (3, 4): _step_3_to_4,
    (4, 5): _step_4_to_5,
    (5, 6): _step_5_to_6,
    (6, 7): _step_6_to_7,
    (7, 8): _step_7_to_8,
    (8, 9): _step_8_to_9,
}


def migration_steps(source_version: int) -> tuple[str, ...]:
    if not (
        OLDEST_MIGRATABLE_RUN_SCHEMA_VERSION
        <= source_version
        < CURRENT_RUN_SCHEMA_VERSION
    ):
        return ()
    return tuple(
        f"{version}_to_{version + 1}"
        for version in range(source_version, CURRENT_RUN_SCHEMA_VERSION)
    )


def migrate_classification(
    classification: RecordClassification,
    *,
    migration_id: str,
    migrated_at: str,
) -> MigrationResult:
    if (
        classification.treatment != "migrate"
        or classification.schema_version is None
        or classification.structural_class is None
        or classification.disposition is None
        or classification.payload is None
    ):
        raise MigrationError(classification.reason_code, classification.field_path)

    payload = copy.deepcopy(classification.payload)
    source_version = classification.schema_version
    reason_codes: list[str] = []
    applied_steps: list[str] = []
    if source_version == 6:
        missing_reason_map = {
            "failure_source": "missing_failure_provenance",
            "capability": "missing_capability_audit",
            "repository_fingerprint_before": "missing_writer_audit",
        }
        source_providers = classification.payload.get("provider_runs", [])
        for key, reason in missing_reason_map.items():
            if any(
                isinstance(record, dict) and key not in record
                for record in source_providers
            ):
                reason_codes.append(reason)
        if "target_ownership" not in classification.payload:
            reason_codes.append("missing_ownership_audit")

        # Materialize typed absence for current validation while the audit
        # preserves which schema-6 facts were not present in the source.
        payload.setdefault("target_ownership", None)
        payload.setdefault("active_writer_attempt", None)
        payload.setdefault("writer_recovery_decisions", [])
        payload.setdefault("provider_resume_stage", None)
        payload.setdefault("provider_resume_prompt", None)
        for record in payload.get("provider_runs", []):
            if isinstance(record, dict):
                for key in (
                    "failure_kind",
                    "failure_source",
                    "failure_code",
                    "capability",
                    "repository_fingerprint_before",
                    "repository_fingerprint_after",
                ):
                    record.setdefault(key, None)
                record.setdefault("retry_scheduled", False)
    version = source_version
    while version < CURRENT_RUN_SCHEMA_VERSION:
        step = MIGRATION_REGISTRY.get((version, version + 1))
        if step is None:
            raise MigrationError("migration_registry_invalid")
        next_step = f"{version}_to_{version + 1}"
        context = MigrationContext(
            migration_id=migration_id,
            migrated_at=migrated_at,
            source_schema_version=source_version,
            source_structural_class=classification.structural_class,
            source_sha256=classification.source_sha256,
            disposition=classification.disposition,
            applied_steps=tuple([*applied_steps, next_step]),
            reason_codes=tuple(reason_codes),
        )
        payload, reasons = step(payload, context)
        version += 1
        if version <= 8:
            _validate_historical(payload, version)
        reason_codes.extend(reasons)
        applied_steps.append(next_step)

    if version != CURRENT_RUN_SCHEMA_VERSION:
        raise MigrationError("migration_registry_invalid")
    try:
        run = WorkflowRun.model_validate(payload)
    except ValidationError as exc:
        raise MigrationError(
            "migration_step_failed",
            _bounded_field_path(exc),
        ) from exc
    identity_audit = run.identity_migration_audit
    if source_version < 8:
        if (
            identity_audit is None
            or identity_audit.source_schema_version != source_version
            or identity_audit.target_schema_version != 8
            or identity_audit.source_structural_class
            != classification.structural_class
            or identity_audit.source_sha256 != classification.source_sha256
            or identity_audit.applied_steps != tuple(applied_steps[:-1])
            or identity_audit.disposition != classification.disposition
        ):
            raise MigrationError("identity_migration_audit_invalid")

    review_audit = run.review_migration_audit
    if (
        review_audit is None
        or review_audit.source_schema_version != source_version
        or review_audit.target_schema_version != 9
        or review_audit.source_structural_class
        != classification.structural_class
        or review_audit.source_sha256 != classification.source_sha256
        or review_audit.applied_steps != tuple(applied_steps)
        or review_audit.disposition != classification.disposition
        or review_audit.parsed_count != len(run.review_records)
        or review_audit.unreadable_count != len(run.unreadable_review_records)
    ):
        raise MigrationError("review_migration_audit_invalid")

    if source_version <= 6:
        audit = run.migration_audit
        if (
            audit is None
            or audit.source_schema_version != source_version
            or audit.target_schema_version != 7
            or audit.source_structural_class != classification.structural_class
            or audit.source_sha256 != classification.source_sha256
            or audit.applied_steps != tuple(applied_steps[:-2])
            or audit.disposition != classification.disposition
        ):
            raise MigrationError("migration_audit_invalid")
    elif source_version == 7:
        original_audit = classification.payload.get("migration_audit")
        migrated_audit = (
            run.migration_audit.model_dump(mode="json")
            if run.migration_audit is not None
            else None
        )
        if migrated_audit != original_audit:
            raise MigrationError("migration_audit_invalid")
    elif source_version == 8:
        for key in ("migration_audit", "identity_migration_audit"):
            original = classification.payload.get(key)
            migrated_value = (
                getattr(run, key).model_dump(mode="json")
                if getattr(run, key) is not None
                else None
            )
            if migrated_value != original:
                raise MigrationError(
                    "identity_migration_audit_invalid"
                    if key == "identity_migration_audit"
                    else "migration_audit_invalid"
                )
    return MigrationResult(
        run=run,
        source_sha256=classification.source_sha256,
        source_schema_version=source_version,
        source_structural_class=classification.structural_class,
        applied_steps=tuple(applied_steps),
    )
