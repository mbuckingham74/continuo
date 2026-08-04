"""Code-owned provider adapters behind the Gate 4.3 compatibility seam.

The two adapters, `claude_cli` and `codex_cli`, expose exactly the three
closed operations approved by Gate 3.5: `probe_local`, `build_attempt`, and
`execute_attempt`. One `execute_attempt` call performs exactly one physical
attempt through the shared controller-owned one-attempt executor and returns
one normalized `ProviderAttempt`. The controller-owned compatibility runner
retains the baseline same-provider transport retry loop. Adapters never
retry, sleep, probe before launch, select alternatives, or mutate plans.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

import providers
from configuration import canonical_sha256
from models import (
    OPERATION_ROLES,
    ROUTE_IDENTITIES,
    ProviderAccountProfile,
    ProviderRouteProfile,
)


ADAPTER_CONTRACT_ID = "continuo.provider-adapter.v1"

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
SONNET_REVIEW_SCHEMA_JSON = "".join(
    (
        '{"type":"object","properties":{"status":{"type":"string",',
        '"enum":["PASS","FAIL"]},"category":{"type":"string","enum":',
        '["PASS","IMPLEMENTATION_DEFECT","POLICY_AMBIGUITY",',
        '"SCOPE_VIOLATION"]},"finding_key":{"type":"string",',
        '"minLength":1,"maxLength":120},"summary":{"type":"string"}},',
        '"required":["status","category","finding_key","summary"],',
        '"additionalProperties":false}',
    )
)

LUNA_GIT_PROHIBITIONS = (
    "You have workspace-write only for bounded implementation edits. "
    "Do not commit, push, create or switch branches, merge, rebase, reset, "
    "or modify any Git metadata (.git). The controller alone has Git authority."
)


class AdapterContractError(RuntimeError):
    """A closed adapter-contract rule was violated; no fallback exists."""


_COMPATIBILITY_MODELS: Mapping[str, str] = MappingProxyType(
    {route.route_id: route.model_id for route in ROUTE_IDENTITIES.values()}
)


@dataclass(frozen=True)
class AdapterDescriptor:
    provider_adapter_schema_version: int
    provider_adapter_id: str
    adapter_contract_id: str
    transport_kind: str
    command_builder_ids: tuple[str, ...]
    failure_classifier_id: str
    local_probe_id: str
    supports_process_group_supervision: bool
    supports_partial_output_capture: bool
    descriptor_sha256: str


def _descriptor_payload(
    *,
    provider_adapter_id: str,
    command_builder_ids: tuple[str, ...],
    failure_classifier_id: str,
    local_probe_id: str,
    supports_process_group_supervision: bool = True,
    supports_partial_output_capture: bool = True,
) -> dict[str, object]:
    return {
        "provider_adapter_schema_version": 1,
        "provider_adapter_id": provider_adapter_id,
        "adapter_contract_id": ADAPTER_CONTRACT_ID,
        "transport_kind": "local_process",
        "command_builder_ids": list(command_builder_ids),
        "failure_classifier_id": failure_classifier_id,
        "local_probe_id": local_probe_id,
        "supports_process_group_supervision": supports_process_group_supervision,
        "supports_partial_output_capture": supports_partial_output_capture,
    }


def build_adapter_descriptor(
    *,
    provider_adapter_id: str,
    command_builder_ids: tuple[str, ...],
    failure_classifier_id: str,
    local_probe_id: str,
) -> AdapterDescriptor:
    payload = _descriptor_payload(
        provider_adapter_id=provider_adapter_id,
        command_builder_ids=command_builder_ids,
        failure_classifier_id=failure_classifier_id,
        local_probe_id=local_probe_id,
    )
    fields = dict(payload)
    fields["command_builder_ids"] = tuple(payload["command_builder_ids"])
    return AdapterDescriptor(
        **fields,  # type: ignore[arg-type]
        descriptor_sha256=canonical_sha256(payload),
    )


def validate_descriptor(descriptor: AdapterDescriptor) -> None:
    if descriptor.provider_adapter_schema_version != 1:
        raise AdapterContractError(
            "adapter descriptor schema version is unsupported"
        )
    if descriptor.adapter_contract_id != ADAPTER_CONTRACT_ID:
        raise AdapterContractError("adapter descriptor contract is unsupported")
    if descriptor.transport_kind != "local_process":
        raise AdapterContractError("adapter descriptor transport is unsupported")
    if not descriptor.provider_adapter_id:
        raise AdapterContractError("adapter descriptor lacks an adapter id")
    if not descriptor.command_builder_ids:
        raise AdapterContractError("adapter descriptor lacks command builders")
    if not isinstance(descriptor.command_builder_ids, tuple):
        raise AdapterContractError(
            "adapter descriptor command builders must be immutable"
        )
    if not descriptor.failure_classifier_id:
        raise AdapterContractError("adapter descriptor lacks a failure classifier")
    if not descriptor.local_probe_id:
        raise AdapterContractError("adapter descriptor lacks a local probe")
    payload = _descriptor_payload(
        provider_adapter_id=descriptor.provider_adapter_id,
        command_builder_ids=descriptor.command_builder_ids,
        failure_classifier_id=descriptor.failure_classifier_id,
        local_probe_id=descriptor.local_probe_id,
        supports_process_group_supervision=(
            descriptor.supports_process_group_supervision
        ),
        supports_partial_output_capture=descriptor.supports_partial_output_capture,
    )
    if canonical_sha256(payload) != descriptor.descriptor_sha256:
        raise AdapterContractError("adapter descriptor hash is incoherent")


@dataclass(frozen=True)
class LocalProbeResult:
    provider_adapter_id: str
    available: bool
    executable_path: str | None
    authentication_status: str
    reason_code: str


@dataclass(frozen=True)
class AdapterAttemptRequest:
    operation_id: str
    route_profile: ProviderRouteProfile
    provider_account_profile: ProviderAccountProfile
    prompt: str
    working_directory: Path
    capability: str


@dataclass(frozen=True)
class SupervisionPolicy:
    deadline_seconds: float
    term_grace_seconds: float
    poll_interval_seconds: float
    heartbeat_seconds: float


@dataclass(frozen=True)
class CommandAudit:
    provider_adapter_id: str
    command_builder_policy_id: str
    route_id: str
    route_profile_sha256: str
    provider_account_profile_id: str
    provider_account_profile_sha256: str
    model_id: str
    operation_id: str
    output_contract_id: str
    capability_profile_id: str
    prompt_sha256: str
    effort_mode: str
    effort_id: str | None
    effort_enforcement_policy_id: str | None


@dataclass(frozen=True)
class AttemptPlan:
    command: tuple[str, ...]
    working_directory: Path
    display_label: str
    capability: str
    output_contract_id: str
    supervision: SupervisionPolicy
    prompt_sha256: str
    audit: CommandAudit


@dataclass(frozen=True)
class RawAttemptResult:
    completed: subprocess.CompletedProcess[str]
    supervisor_kind: str | None
    os_evidence: providers.ProviderFailureEvidence | None


def run_single_attempt(plan: AttemptPlan) -> RawAttemptResult:
    """Perform exactly one supervised launch of the plan's exact argv."""

    supervised = providers._supervise_process(
        list(plan.command),
        plan.working_directory,
        label=plan.display_label,
        interactive=sys.stderr.isatty(),
        deadline_seconds=plan.supervision.deadline_seconds,
        term_grace_seconds=plan.supervision.term_grace_seconds,
        poll_interval_seconds=plan.supervision.poll_interval_seconds,
        heartbeat_seconds=plan.supervision.heartbeat_seconds,
    )
    return RawAttemptResult(supervised.completed, supervised.failure_kind, None)


def normalize_attempt(
    plan: AttemptPlan,
    raw: RawAttemptResult,
    native_extractor: Callable[
        [subprocess.CompletedProcess[str]],
        providers.ProviderFailureEvidence | None,
    ]
    | None,
) -> providers.ProviderAttempt:
    """Apply the exact evidence precedence once and return one attempt."""

    evidence: providers.ProviderFailureEvidence | None
    if raw.supervisor_kind is not None:
        evidence = providers.ProviderFailureEvidence(
            raw.supervisor_kind, "supervisor", raw.supervisor_kind
        )
    elif raw.os_evidence is not None:
        evidence = raw.os_evidence
    elif native_extractor is not None:
        evidence = native_extractor(raw.completed)
    else:
        evidence = None
    if evidence is None:
        evidence = providers.normalize_provider_failure(
            raw.completed.returncode,
            raw.completed.stdout,
            raw.completed.stderr,
        )
    return providers.ProviderAttempt(
        command=list(plan.command),
        returncode=raw.completed.returncode,
        stdout=raw.completed.stdout,
        stderr=raw.completed.stderr,
        failure_kind=evidence.kind if evidence is not None else None,
        failure_source=evidence.source if evidence is not None else None,
        failure_code=evidence.code if evidence is not None else None,
    )


def classify_claude_native_failure(
    result: subprocess.CompletedProcess[str],
) -> providers.ProviderFailureEvidence | None:
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
    kind = (
        providers._kind_for_http_status(status)
        if isinstance(status, int)
        else None
    )
    return providers.ProviderFailureEvidence(
        kind or "provider_error",
        "provider_native",
        code,
    )


def normalize_sonnet_execution(
    execution: providers.ProviderExecution,
) -> providers.ProviderExecution:
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
    return providers.normalize_provider_execution(execution)


class ProviderAdapter:
    """One strict code-owned compatibility adapter."""

    descriptor: AdapterDescriptor
    executable: str
    output_contract_id: str
    effort_omission_policy_id: str
    capability_profiles: Mapping[str, str]

    def probe_local(self) -> LocalProbeResult:
        discovered = shutil.which(self.executable)
        if discovered is None:
            return LocalProbeResult(
                provider_adapter_id=self.descriptor.provider_adapter_id,
                available=False,
                executable_path=None,
                authentication_status="unknown",
                reason_code="executable_missing",
            )
        return LocalProbeResult(
            provider_adapter_id=self.descriptor.provider_adapter_id,
            available=True,
            executable_path=discovered,
            authentication_status="unknown",
            reason_code="executable_available",
        )

    def build_attempt(self, request: AdapterAttemptRequest) -> AttemptPlan:
        self._validate_request(request)
        argv = self._build_argv(request)
        if any("\x00" in part for part in argv):
            raise AdapterContractError("adapter argv contains a NUL byte")
        provider_prompt = argv[-1]
        prompt_sha256 = hashlib.sha256(
            provider_prompt.encode("utf-8")
        ).hexdigest()
        supervision = SupervisionPolicy(
            deadline_seconds=(
                providers.WRITE_PROVIDER_DEADLINE_SECONDS
                if request.capability == "workspace_write"
                else providers.READ_ONLY_PROVIDER_DEADLINE_SECONDS
            ),
            term_grace_seconds=providers.PROVIDER_TERM_GRACE_SECONDS,
            poll_interval_seconds=providers.PROVIDER_POLL_INTERVAL_SECONDS,
            heartbeat_seconds=providers.PROVIDER_HEARTBEAT_SECONDS,
        )
        audit = CommandAudit(
            provider_adapter_id=self.descriptor.provider_adapter_id,
            command_builder_policy_id=request.route_profile.command_builder_policy_id,
            route_id=request.route_profile.route_id,
            route_profile_sha256=request.route_profile.route_profile_sha256,
            provider_account_profile_id=(
                request.provider_account_profile.provider_account_profile_id
            ),
            provider_account_profile_sha256=(
                request.provider_account_profile.provider_account_profile_sha256
            ),
            model_id=request.route_profile.model_id,
            operation_id=request.operation_id,
            output_contract_id=request.route_profile.output_contract_id,
            capability_profile_id=request.route_profile.capability_profile_id,
            prompt_sha256=prompt_sha256,
            effort_mode=request.route_profile.effort.mode,
            effort_id=request.route_profile.effort.effort_id,
            effort_enforcement_policy_id=(
                request.route_profile.effort.enforcement_policy_id
            ),
        )
        return AttemptPlan(
            command=tuple(argv),
            working_directory=request.working_directory,
            display_label=ROUTE_IDENTITIES[
                request.route_profile.role_id
            ].display_name,
            capability=request.capability,
            output_contract_id=request.route_profile.output_contract_id,
            supervision=supervision,
            prompt_sha256=prompt_sha256,
            audit=audit,
        )

    def execute_attempt(self, plan: AttemptPlan) -> providers.ProviderAttempt:
        try:
            raw = run_single_attempt(plan)
        except OSError as exc:
            raw = RawAttemptResult(
                subprocess.CompletedProcess(
                    list(plan.command),
                    127,
                    "",
                    f"{type(exc).__name__}: {exc}",
                ),
                None,
                providers._os_failure_evidence(exc),
            )
        return normalize_attempt(plan, raw, self._native_evidence_extractor())

    def _native_evidence_extractor(
        self,
    ) -> Callable[
        [subprocess.CompletedProcess[str]],
        providers.ProviderFailureEvidence | None,
    ] | None:
        return None

    def _validate_request(self, request: AdapterAttemptRequest) -> None:
        route = request.route_profile
        account = request.provider_account_profile
        descriptor = self.descriptor
        if request.operation_id not in OPERATION_ROLES:
            raise AdapterContractError(
                "adapter request carries an unknown operation"
            )
        if OPERATION_ROLES[request.operation_id] != route.role_id:
            raise AdapterContractError(
                "adapter request operation does not match the saved route role"
            )
        if route.provider_adapter_id != descriptor.provider_adapter_id:
            raise AdapterContractError(
                "saved route adapter does not match the selected adapter"
            )
        if route.command_builder_policy_id not in descriptor.command_builder_ids:
            raise AdapterContractError(
                "saved command builder policy is not registered by this adapter"
            )
        installed_model = _COMPATIBILITY_MODELS.get(route.route_id)
        if installed_model is None or route.model_id != installed_model:
            raise AdapterContractError(
                "saved route model is not the installed compatibility model"
            )
        if account.provider_adapter_id != descriptor.provider_adapter_id:
            raise AdapterContractError(
                "saved account does not use the selected adapter"
            )
        effort = route.effort
        if (
            effort.mode != "provider_default"
            or effort.effort_id is not None
            or effort.enforcement_policy_id != self.effort_omission_policy_id
        ):
            raise AdapterContractError(
                "saved effort policy is not the provider-default omission policy"
            )
        if request.capability not in self.capability_profiles:
            raise AdapterContractError(
                "adapter request capability is not a coarse compatibility capability"
            )
        if route.capability_profile_id != self.capability_profiles[request.capability]:
            raise AdapterContractError(
                "saved capability profile does not match the installed compatibility record"
            )
        if route.output_contract_id != self.output_contract_id:
            raise AdapterContractError(
                "saved output contract does not match the installed compatibility record"
            )
        if "\x00" in request.prompt or "\x00" in os.fspath(request.working_directory):
            raise AdapterContractError("adapter request contains a NUL byte")

    def _build_argv(self, request: AdapterAttemptRequest) -> list[str]:
        raise NotImplementedError


class ClaudeCliAdapter(ProviderAdapter):
    descriptor = build_adapter_descriptor(
        provider_adapter_id="claude_cli",
        command_builder_ids=("claude-cli.compatibility-builder.v1",),
        failure_classifier_id="claude-cli.failure-classifier.v1",
        local_probe_id="claude-cli.local-probe.v1",
    )
    executable = "claude"
    output_contract_id = "claude-cli.compatibility-output.v1"
    effort_omission_policy_id = "claude-cli.effort-omission.v1"
    capability_profiles = MappingProxyType(
        {"read_only": "continuo.read-only.v1"}
    )

    def _build_argv(self, request: AdapterAttemptRequest) -> list[str]:
        return [
            "claude",
            "-p",
            "--model",
            request.route_profile.model_id,
            "--permission-mode",
            "plan",
            "--tools",
            "Read,Glob,Grep",
            "--output-format",
            "json",
            "--json-schema",
            SONNET_REVIEW_SCHEMA_JSON,
            "--",
            request.prompt,
        ]

    def _native_evidence_extractor(self):
        return classify_claude_native_failure


class CodexCliAdapter(ProviderAdapter):
    descriptor = build_adapter_descriptor(
        provider_adapter_id="codex_cli",
        command_builder_ids=("codex-cli.compatibility-builder.v1",),
        failure_classifier_id="codex-cli.failure-classifier.v1",
        local_probe_id="codex-cli.local-probe.v1",
    )
    executable = "codex"
    output_contract_id = "codex-cli.compatibility-output.v1"
    effort_omission_policy_id = "codex-cli.effort-omission.v1"
    capability_profiles = MappingProxyType(
        {
            "read_only": "continuo.read-only.v1",
            "workspace_write": "continuo.workspace-write.v1",
        }
    )

    def _build_argv(self, request: AdapterAttemptRequest) -> list[str]:
        if request.route_profile.role_id == "implementation":
            bounded_prompt = f"{LUNA_GIT_PROHIBITIONS}\n\n{request.prompt}"
            return [
                "codex",
                "exec",
                "--model",
                request.route_profile.model_id,
                "--sandbox",
                "workspace-write",
                "--config",
                "approval_policy=never",
                "--config",
                "sandbox_workspace_write.network_access=false",
                "--",
                bounded_prompt,
            ]
        return [
            "codex",
            "exec",
            "--model",
            request.route_profile.model_id,
            "--sandbox",
            "read-only",
            "--",
            request.prompt,
        ]


class AdapterRegistry:
    """Exact code-owned adapter lookup; configuration registers nothing."""

    def __init__(
        self,
        adapters,
        *,
        validate: bool = True,
    ) -> None:
        by_id: dict[str, ProviderAdapter] = {}
        for adapter in adapters:
            descriptor = getattr(adapter, "descriptor", None)
            if not isinstance(descriptor, AdapterDescriptor):
                raise AdapterContractError(
                    "adapter registration requires a code-owned descriptor"
                )
            adapter_id = descriptor.provider_adapter_id
            if adapter_id in by_id:
                raise AdapterContractError(
                    f"duplicate provider adapter registration: {adapter_id}"
                )
            by_id[adapter_id] = adapter
        if validate:
            for adapter in by_id.values():
                validate_descriptor(adapter.descriptor)
        self._adapters = MappingProxyType(by_id)

    def get(self, adapter_id: str) -> ProviderAdapter:
        adapter = self._adapters.get(adapter_id)
        if adapter is None:
            raise AdapterContractError(
                f"provider adapter is not registered: {adapter_id}"
            )
        return adapter

    def __contains__(self, adapter_id: object) -> bool:
        return adapter_id in self._adapters

    def __len__(self) -> int:
        return len(self._adapters)

    def adapter_ids(self) -> tuple[str, ...]:
        return tuple(self._adapters)


DEFAULT_REGISTRY = AdapterRegistry((ClaudeCliAdapter(), CodexCliAdapter()))


def get_adapter(
    adapter_id: str,
    registry: AdapterRegistry | None = None,
) -> ProviderAdapter:
    return (registry or DEFAULT_REGISTRY).get(adapter_id)


def run_compatibility(
    request: AdapterAttemptRequest,
    *,
    execute: Callable[[AttemptPlan], providers.ProviderAttempt] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> providers.ProviderExecution:
    """Controller-owned compatibility runner for the baseline retry loop."""

    adapter = get_adapter(request.route_profile.provider_adapter_id)
    plan = adapter.build_attempt(request)
    attempt_factory = execute if execute is not None else adapter.execute_attempt

    def execute_once() -> providers.ProviderAttempt:
        return attempt_factory(plan)

    return providers.run_attempt_loop(
        label=plan.display_label,
        capability=request.capability,
        execute_attempt_once=execute_once,
        sleeper=sleeper,
    )
