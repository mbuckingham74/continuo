"""Gate 4.3 adapter-contract tests for the strict code-owned registry.

These tests lock the adapter protocol itself: descriptor/registry
strictness, closed operation surface, request validation, `build_attempt`
purity and bounded redacted audit, exactly-one-executor-call per
`execute_attempt`, and the controller-owned compatibility runner consuming
already-normalized attempts. They use fakes, fixtures, and fake sleepers
only; no live provider, credential, network, or subprocess is touched.
"""

import errno
import hashlib
import inspect
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import adapters
import providers
from configuration import (
    ACCOUNT_PROFILE_CATALOG,
    ROUTE_PROFILE_CATALOG,
    canonical_sha256,
    compatibility_selection,
)
from models import (
    ADVERSARIAL_REVIEW_ROUTE,
    ESCALATION_EXECUTIVE_ROUTE,
    IMPLEMENTATION_ROUTE,
    POLICY_AUTHORITY_ROUTE,
    EffortPolicy,
)


def compatibility_request(
    operation_id: str,
    prompt: str,
    repo: Path,
    *,
    route=None,
    account=None,
    capability=None,
):
    role = providers.OPERATION_ROLES[operation_id]
    route = route or ROUTE_PROFILE_CATALOG[
        compatibility_selection(role).route_id
    ]
    account = account or ACCOUNT_PROFILE_CATALOG[
        compatibility_selection(role).provider_account_profile_id
    ]
    capability = capability or (
        "workspace_write" if role == "implementation" else "read_only"
    )
    return adapters.AdapterAttemptRequest(
        operation_id=operation_id,
        route_profile=route,
        provider_account_profile=account,
        prompt=prompt,
        working_directory=repo,
        capability=capability,
    )


class AdapterRegistryStrictnessTests(unittest.TestCase):
    """Code-owned descriptors are validated exactly and uniquely."""

    def test_default_registry_is_the_closed_two_adapter_set(self) -> None:
        self.assertEqual(
            adapters.DEFAULT_REGISTRY.adapter_ids(),
            ("claude_cli", "codex_cli"),
        )
        for adapter_id in ("claude_cli", "codex_cli"):
            adapter = adapters.get_adapter(adapter_id)
            self.assertEqual(adapter.descriptor.provider_adapter_id, adapter_id)

    def test_descriptor_contract_facts_are_exact(self) -> None:
        expected = {
            "claude_cli": (
                "claude-cli.compatibility-builder.v1",
                "claude-cli.failure-classifier.v1",
                "claude-cli.local-probe.v1",
            ),
            "codex_cli": (
                "codex-cli.compatibility-builder.v1",
                "codex-cli.failure-classifier.v1",
                "codex-cli.local-probe.v1",
            ),
        }
        for adapter_id, (builder, classifier, probe) in expected.items():
            with self.subTest(adapter_id=adapter_id):
                descriptor = adapters.get_adapter(adapter_id).descriptor
                self.assertEqual(descriptor.provider_adapter_schema_version, 1)
                self.assertEqual(
                    descriptor.adapter_contract_id,
                    adapters.ADAPTER_CONTRACT_ID,
                )
                self.assertEqual(descriptor.transport_kind, "local_process")
                self.assertEqual(descriptor.command_builder_ids, (builder,))
                self.assertEqual(descriptor.failure_classifier_id, classifier)
                self.assertEqual(descriptor.local_probe_id, probe)
                self.assertTrue(
                    descriptor.supports_process_group_supervision
                )
                self.assertTrue(descriptor.supports_partial_output_capture)
                adapters.validate_descriptor(descriptor)

    def test_descriptor_hashes_are_deterministic_and_incoherent_hashes_fail(self) -> None:
        first = adapters.build_adapter_descriptor(
            provider_adapter_id="claude_cli",
            command_builder_ids=("claude-cli.compatibility-builder.v1",),
            failure_classifier_id="claude-cli.failure-classifier.v1",
            local_probe_id="claude-cli.local-probe.v1",
        )
        second = adapters.build_adapter_descriptor(
            provider_adapter_id="claude_cli",
            command_builder_ids=("claude-cli.compatibility-builder.v1",),
            failure_classifier_id="claude-cli.failure-classifier.v1",
            local_probe_id="claude-cli.local-probe.v1",
        )
        self.assertEqual(first.descriptor_sha256, second.descriptor_sha256)

        approved_hashes = {
            "claude_cli": (
                "44622fa2aba64ce65498770958f5787e8ea6ffb01fa71db224c026c2e4daf439"
            ),
            "codex_cli": (
                "cc41f7abaea29d2e6af01327f1f01959b48b132e771617e2bc3ef171388eb371"
            ),
        }
        for adapter_id in ("claude_cli", "codex_cli"):
            with self.subTest(adapter_id=adapter_id):
                self.assertEqual(
                    adapters.get_adapter(adapter_id).descriptor.descriptor_sha256,
                    approved_hashes[adapter_id],
                )

        tampered = adapters.AdapterDescriptor(
            provider_adapter_schema_version=first.provider_adapter_schema_version,
            provider_adapter_id=first.provider_adapter_id,
            adapter_contract_id=first.adapter_contract_id,
            transport_kind=first.transport_kind,
            command_builder_ids=first.command_builder_ids,
            failure_classifier_id=first.failure_classifier_id,
            local_probe_id=first.local_probe_id,
            supports_process_group_supervision=first.supports_process_group_supervision,
            supports_partial_output_capture=first.supports_partial_output_capture,
            descriptor_sha256=first.descriptor_sha256,
        )
        tampered = adapters.AdapterDescriptor(
            **{**tampered.__dict__, "command_builder_ids": ("evil.builder.v1",)}
        )
        with self.assertRaises(adapters.AdapterContractError):
            adapters.validate_descriptor(tampered)

    def test_descriptor_command_builder_ids_is_an_immutable_tuple(self) -> None:
        descriptor = adapters.get_adapter("claude_cli").descriptor
        self.assertIsInstance(descriptor.command_builder_ids, tuple)
        self.assertEqual(
            descriptor.command_builder_ids,
            ("claude-cli.compatibility-builder.v1",),
        )
        with self.assertRaises(AttributeError):
            descriptor.command_builder_ids.append("evil.builder.v1")
        with self.assertRaises(AttributeError):
            descriptor.command_builder_ids = descriptor.command_builder_ids + (
                "evil.builder.v1",
            )

    def test_descriptor_validation_rejects_mutable_command_builder_ids(self) -> None:
        descriptor = adapters.get_adapter("codex_cli").descriptor
        with self.assertRaises(adapters.AdapterContractError):
            adapters.validate_descriptor(
                adapters.AdapterDescriptor(
                    **{
                        **descriptor.__dict__,
                        "command_builder_ids": list(descriptor.command_builder_ids),
                    }
                )
            )

    def test_support_boolean_mutation_fails_descriptor_coherence(self) -> None:
        for field in (
            "supports_process_group_supervision",
            "supports_partial_output_capture",
        ):
            with self.subTest(field=field):
                descriptor = adapters.get_adapter("claude_cli").descriptor
                altered = adapters.AdapterDescriptor(
                    **{
                        **descriptor.__dict__,
                        field: not getattr(descriptor, field),
                    }
                )
                with self.assertRaises(adapters.AdapterContractError):
                    adapters.validate_descriptor(altered)

    def test_descriptor_validation_rejects_unsupported_facts(self) -> None:
        base = dict(
            provider_adapter_id="claude_cli",
            command_builder_ids=("claude-cli.compatibility-builder.v1",),
            failure_classifier_id="claude-cli.failure-classifier.v1",
            local_probe_id="claude-cli.local-probe.v1",
        )
        for mutation in (
            {"provider_adapter_schema_version": 2},
            {"adapter_contract_id": "continuo.other.v1"},
            {"transport_kind": "remote"},
            {"provider_adapter_id": ""},
            {"command_builder_ids": ()},
            {"failure_classifier_id": ""},
            {"local_probe_id": ""},
        ):
            with self.subTest(mutation=mutation):
                descriptor = adapters.build_adapter_descriptor(**base)
                altered = adapters.AdapterDescriptor(
                    **{
                        **descriptor.__dict__,
                        **mutation,
                        "descriptor_sha256": descriptor.descriptor_sha256,
                    }
                )
                with self.assertRaises(adapters.AdapterContractError):
                    adapters.validate_descriptor(altered)

    def test_registry_rejects_duplicate_and_descriptorless_registrations(self) -> None:
        with self.assertRaises(adapters.AdapterContractError):
            adapters.AdapterRegistry(
                (adapters.ClaudeCliAdapter(), adapters.ClaudeCliAdapter())
            )
        with self.assertRaises(adapters.AdapterContractError):
            adapters.AdapterRegistry((object(),))
        with self.assertRaises(adapters.AdapterContractError):
            adapters.AdapterRegistry(
                (
                    adapters.ClaudeCliAdapter(),
                    type(
                        "ForgeAdapter",
                        (),
                        {"descriptor": "not-a-descriptor"},
                    )(),
                )
            )

    def test_registry_lookup_is_exact_and_never_selects_an_alternate(self) -> None:
        with self.assertRaises(adapters.AdapterContractError):
            adapters.get_adapter("claude")
        with self.assertRaises(adapters.AdapterContractError):
            adapters.get_adapter("codex")
        with self.assertRaises(adapters.AdapterContractError):
            adapters.get_adapter("openai_cli")
        with self.assertRaises(adapters.AdapterContractError):
            adapters.AdapterRegistry(()).get("claude_cli")

    def test_registry_is_not_configurable_from_configuration_catalogs(self) -> None:
        adapters.DEFAULT_REGISTRY.get("claude_cli")
        adapters.DEFAULT_REGISTRY.get("codex_cli")
        self.assertEqual(len(adapters.DEFAULT_REGISTRY), 2)
        self.assertNotIn("provider_adapter_id", dir(adapters.DEFAULT_REGISTRY))

    def test_adapter_exposes_exactly_the_three_closed_operations(self) -> None:
        for adapter in (
            adapters.ClaudeCliAdapter(),
            adapters.CodexCliAdapter(),
        ):
            with self.subTest(adapter=adapter.descriptor.provider_adapter_id):
                public = {
                    name
                    for name, value in inspect.getmembers(adapter)
                    if not name.startswith("_")
                    and callable(value)
                    and not inspect.ismethoddescriptor(value)
                }
                self.assertEqual(public, {"probe_local", "build_attempt", "execute_attempt"})

    def test_probe_local_is_local_fixed_token_discovery_only(self) -> None:
        with patch.object(
            adapters.shutil, "which", return_value="/fixture/claude"
        ) as which, patch.object(
            adapters.subprocess, "run", side_effect=AssertionError("probe spawned")
        ):
            result = adapters.get_adapter("claude_cli").probe_local()
        which.assert_called_once_with("claude")
        self.assertEqual(result.executable_path, "/fixture/claude")
        self.assertTrue(result.available)
        self.assertEqual(result.authentication_status, "unknown")

        with patch.object(adapters.shutil, "which", return_value=None):
            missing = adapters.get_adapter("codex_cli").probe_local()
        self.assertFalse(missing.available)
        self.assertEqual(missing.reason_code, "executable_missing")


class AdapterRequestValidationTests(unittest.TestCase):
    """build_attempt rejects every incoherent request before spawn."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "jobs"
        self.repo.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def build(self, operation_id, prompt="prompt", **kwargs):
        request = compatibility_request(operation_id, prompt, self.repo, **kwargs)
        adapter = adapters.get_adapter(request.route_profile.provider_adapter_id)
        return adapter, adapter.build_attempt(request)

    def test_all_four_operations_build_valid_plans(self) -> None:
        for operation_id in (
            "specification_review",
            "implementation_review",
            "policy_clarification",
            "escalation_guidance",
            "implementation_write",
            "correction_write",
        ):
            with self.subTest(operation_id=operation_id):
                _, plan = self.build(operation_id)
                self.assertEqual(plan.capability, request_capability(operation_id))

    def test_operation_role_mismatch_is_rejected(self) -> None:
        route = ROUTE_PROFILE_CATALOG[IMPLEMENTATION_ROUTE.route_id]
        account = ACCOUNT_PROFILE_CATALOG[compatibility_selection("implementation").provider_account_profile_id]
        with self.assertRaises(adapters.AdapterContractError):
            self.build("specification_review", route=route, account=account)

    def test_route_adapter_mismatch_is_rejected(self) -> None:
        claude_route = ROUTE_PROFILE_CATALOG[ADVERSARIAL_REVIEW_ROUTE.route_id]
        codex_account = ACCOUNT_PROFILE_CATALOG[compatibility_selection("escalation_executive").provider_account_profile_id]
        with self.assertRaises(adapters.AdapterContractError):
            self.build("specification_review", route=claude_route, account=codex_account)

    def test_account_adapter_mismatch_is_rejected(self) -> None:
        codex_account = ACCOUNT_PROFILE_CATALOG[compatibility_selection("escalation_executive").provider_account_profile_id]
        with self.assertRaises(adapters.AdapterContractError):
            self.build("specification_review", account=codex_account)

    def test_unknown_operation_is_rejected(self) -> None:
        request = compatibility_request("specification_review", "p", self.repo)
        request = adapters.AdapterAttemptRequest(
            operation_id="invented_operation",
            route_profile=request.route_profile,
            provider_account_profile=request.provider_account_profile,
            prompt=request.prompt,
            working_directory=request.working_directory,
            capability=request.capability,
        )
        with self.assertRaises(adapters.AdapterContractError):
            adapters.get_adapter("claude_cli").build_attempt(request)

    def test_unregistered_command_builder_policy_is_rejected(self) -> None:
        route = ROUTE_PROFILE_CATALOG[ADVERSARIAL_REVIEW_ROUTE.route_id]
        route = route.model_copy(
            update={"command_builder_policy_id": "evil.builder.v1"}
        )
        route = route.model_copy(
            update={"route_profile_sha256": canonical_sha256(route)}
        )
        with self.assertRaises(adapters.AdapterContractError):
            self.build("specification_review", route=route)

    def test_wrong_model_is_rejected(self) -> None:
        route = ROUTE_PROFILE_CATALOG[ADVERSARIAL_REVIEW_ROUTE.route_id]
        route = route.model_copy(update={"model_id": "gpt-5.6-luna"})
        route = route.model_copy(
            update={"route_profile_sha256": canonical_sha256(route)}
        )
        with self.assertRaises(adapters.AdapterContractError):
            self.build("specification_review", route=route)

    def test_explicit_or_unsupported_effort_is_rejected(self) -> None:
        route = ROUTE_PROFILE_CATALOG[ADVERSARIAL_REVIEW_ROUTE.route_id]
        route = route.model_copy(
            update={
                "effort": EffortPolicy(
                    mode="explicit",
                    effort_id="effort.v1",
                    enforcement_policy_id="evil.effort.v1",
                )
            }
        )
        route = route.model_copy(
            update={"route_profile_sha256": canonical_sha256(route)}
        )
        with self.assertRaises(adapters.AdapterContractError):
            self.build("specification_review", route=route)

    def test_capability_not_in_adapter_profiles_is_rejected(self) -> None:
        claude_route = ROUTE_PROFILE_CATALOG[ADVERSARIAL_REVIEW_ROUTE.route_id]
        claude_account = ACCOUNT_PROFILE_CATALOG[
            compatibility_selection("adversarial_review").provider_account_profile_id
        ]
        request = adapters.AdapterAttemptRequest(
            operation_id="specification_review",
            route_profile=claude_route,
            provider_account_profile=claude_account,
            prompt="p",
            working_directory=self.repo,
            capability="workspace_write",
        )
        with self.assertRaises(adapters.AdapterContractError):
            adapters.get_adapter("claude_cli").build_attempt(request)

    def test_capability_profile_id_mismatch_is_rejected(self) -> None:
        route = ROUTE_PROFILE_CATALOG[ADVERSARIAL_REVIEW_ROUTE.route_id]
        route = route.model_copy(
            update={"capability_profile_id": "continuo.workspace-write.v1"}
        )
        route = route.model_copy(
            update={"route_profile_sha256": canonical_sha256(route)}
        )
        with self.assertRaises(adapters.AdapterContractError):
            self.build("specification_review", route=route)

    def test_output_contract_mismatch_is_rejected(self) -> None:
        route = ROUTE_PROFILE_CATALOG[ADVERSARIAL_REVIEW_ROUTE.route_id]
        route = route.model_copy(
            update={"output_contract_id": "evil.output.v1"}
        )
        route = route.model_copy(
            update={"route_profile_sha256": canonical_sha256(route)}
        )
        with self.assertRaises(adapters.AdapterContractError):
            self.build("specification_review", route=route)

    def test_nul_in_prompt_or_working_directory_is_rejected(self) -> None:
        with self.assertRaises(adapters.AdapterContractError):
            self.build("specification_review", prompt="bad\x00prompt")
        request = compatibility_request(
            "specification_review",
            "prompt",
            Path(str(self.repo) + "\x00"),
        )
        with self.assertRaises(adapters.AdapterContractError):
            adapters.get_adapter("claude_cli").build_attempt(request)


class BuildAttemptPurityAndAuditTests(unittest.TestCase):
    """build_attempt is pure and its audit view is bounded."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "jobs"
        self.repo.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_build_attempt_probes_nothing_and_spawns_nothing(self) -> None:
        with patch.object(
            adapters.shutil, "which", side_effect=AssertionError("probe called")
        ), patch.object(
            adapters.subprocess, "run", side_effect=AssertionError("process spawned")
        ), patch.object(
            adapters.subprocess, "Popen", side_effect=AssertionError("process spawned")
        ), patch.object(
            adapters.time, "sleep", side_effect=AssertionError("slept")
        ), patch.object(
            adapters.ProviderAdapter, "probe_local",
            side_effect=AssertionError("probe called"),
        ):
            for operation_id in (
                "specification_review",
                "escalation_guidance",
                "implementation_write",
            ):
                with self.subTest(operation_id=operation_id):
                    self.build(operation_id)

    def build(self, operation_id, prompt="prompt text"):
        request = compatibility_request(operation_id, prompt, self.repo)
        adapter = adapters.get_adapter(request.route_profile.provider_adapter_id)
        return adapter, request, adapter.build_attempt(request)

    def test_plan_is_immutable_and_carries_exact_facts(self) -> None:
        for operation_id, identity in (
            ("specification_review", ADVERSARIAL_REVIEW_ROUTE),
            ("implementation_review", ADVERSARIAL_REVIEW_ROUTE),
            ("policy_clarification", POLICY_AUTHORITY_ROUTE),
            ("escalation_guidance", ESCALATION_EXECUTIVE_ROUTE),
            ("implementation_write", IMPLEMENTATION_ROUTE),
            ("correction_write", IMPLEMENTATION_ROUTE),
        ):
            with self.subTest(operation_id=operation_id):
                _, request, plan = self.build(operation_id)
                self.assertEqual(plan.working_directory, self.repo)
                self.assertEqual(plan.display_label, identity.display_name)
                self.assertEqual(
                    plan.capability, request_capability(operation_id)
                )
                self.assertEqual(
                    plan.output_contract_id,
                    request.route_profile.output_contract_id,
                )
                self.assertEqual(
                    plan.supervision.deadline_seconds,
                    (
                        providers.WRITE_PROVIDER_DEADLINE_SECONDS
                        if request_capability(operation_id) == "workspace_write"
                        else providers.READ_ONLY_PROVIDER_DEADLINE_SECONDS
                    ),
                )
                self.assertEqual(
                    plan.supervision.term_grace_seconds,
                    providers.PROVIDER_TERM_GRACE_SECONDS,
                )
                self.assertEqual(
                    plan.supervision.poll_interval_seconds,
                    providers.PROVIDER_POLL_INTERVAL_SECONDS,
                )
                self.assertEqual(
                    plan.supervision.heartbeat_seconds,
                    providers.PROVIDER_HEARTBEAT_SECONDS,
                )
                self.assertEqual(
                    plan.audit.route_id, request.route_profile.route_id
                )
                self.assertEqual(
                    plan.audit.model_id, request.route_profile.model_id
                )
                self.assertEqual(
                    plan.audit.operation_id, operation_id
                )
                self.assertEqual(
                    plan.audit.provider_account_profile_id,
                    request.provider_account_profile.provider_account_profile_id,
                )
                self.assertEqual(
                    plan.audit.effort_mode, "provider_default"
                )
                self.assertIsNone(plan.audit.effort_id)
                self.assertEqual(
                    plan.audit.effort_enforcement_policy_id,
                    request.route_profile.effort.enforcement_policy_id,
                )

    def test_prompt_hash_covers_the_exact_provider_facing_prompt(self) -> None:
        for operation_id in ("specification_review", "implementation_write"):
            with self.subTest(operation_id=operation_id):
                _, _, plan = self.build(operation_id, "final prompt")
                provider_prompt = plan.command[-1]
                expected = hashlib.sha256(
                    provider_prompt.encode("utf-8")
                ).hexdigest()
                self.assertEqual(plan.prompt_sha256, expected)
                self.assertEqual(plan.audit.prompt_sha256, expected)
                self.assertTrue(plan.audit.command_builder_policy_id)
                self.assertTrue(plan.audit.route_profile_sha256)
                self.assertTrue(plan.audit.provider_account_profile_sha256)
                self.assertTrue(plan.audit.output_contract_id)
                self.assertTrue(plan.audit.capability_profile_id)

    def test_audit_view_contains_no_raw_prompt_environment_or_output(self) -> None:
        _, _, plan = self.build("implementation_write", "TOP SECRET PROMPT")
        audit_json = json.dumps(plan.audit.__dict__)
        self.assertNotIn("TOP SECRET PROMPT", audit_json)
        self.assertNotIn(adapters.LUNA_GIT_PROHIBITIONS, audit_json)
        for secret_name in ("PATH", "TOKEN", "API_KEY", "HOME"):
            self.assertNotIn(secret_name, audit_json)

    def test_luna_prompt_prefix_and_sandbox_flags_are_exact(self) -> None:
        _, _, plan = self.build("implementation_write", "write it")
        self.assertEqual(
            plan.command[-1],
            adapters.LUNA_GIT_PROHIBITIONS + "\n\n" + "write it",
        )
        self.assertEqual(plan.command[:6], (
            "codex", "exec", "--model", "gpt-5.6-luna",
            "--sandbox", "workspace-write",
        ))
        self.assertEqual(plan.command[-2], "--")

    def test_nul_rejection_is_contractual_and_deterministic(self) -> None:
        with self.assertRaises(adapters.AdapterContractError):
            self.build("implementation_write", "bad\x00prompt")


class ExecuteAttemptBoundaryTests(unittest.TestCase):
    """execute_attempt performs exactly one physical attempt per call."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "jobs"
        self.repo.mkdir()
        request = compatibility_request(
            "specification_review", "prompt", self.repo
        )
        self.adapter = adapters.get_adapter("claude_cli")
        self.plan = self.adapter.build_attempt(request)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_one_executor_call_with_the_unchanged_plan(self) -> None:
        calls: list[adapters.AttemptPlan] = []

        def fake_executor(plan):
            calls.append(plan)
            return adapters.RawAttemptResult(
                subprocess.CompletedProcess(
                    list(plan.command), 0, "ok", ""
                ),
                None,
                None,
            )

        with patch.object(
            adapters, "run_single_attempt", side_effect=fake_executor
        ) as executor, patch.object(
            adapters.time, "sleep", side_effect=AssertionError("adapter slept")
        ), patch.object(
            self.adapter, "probe_local", side_effect=AssertionError("probe called")
        ):
            attempt = self.adapter.execute_attempt(self.plan)

        executor.assert_called_once()
        self.assertIs(calls[0], self.plan)
        self.assertEqual(attempt.returncode, 0)
        self.assertIsNone(attempt.failure_kind)
        self.assertEqual(attempt.command, list(self.plan.command))

    def test_supervisor_evidence_is_normalized_once(self) -> None:
        def fake_executor(plan):
            return adapters.RawAttemptResult(
                subprocess.CompletedProcess(
                    list(plan.command),
                    providers.PROVIDER_TIMEOUT_RETURN_CODE,
                    "partial",
                    "stopped",
                ),
                "timeout",
                None,
            )

        with patch.object(
            adapters, "run_single_attempt", side_effect=fake_executor
        ) as executor:
            attempt = self.adapter.execute_attempt(self.plan)

        executor.assert_called_once()
        self.assertEqual(attempt.failure_kind, "timeout")
        self.assertEqual(attempt.failure_source, "supervisor")
        self.assertEqual(attempt.returncode, 124)

    def test_os_launch_failure_synthesizes_one_127_attempt(self) -> None:
        with patch.object(
            adapters,
            "run_single_attempt",
            side_effect=FileNotFoundError(errno.ENOENT, "no such binary"),
        ) as executor, patch.object(
            adapters.time, "sleep", side_effect=AssertionError("adapter slept")
        ):
            attempt = self.adapter.execute_attempt(self.plan)

        executor.assert_called_once()
        self.assertEqual(attempt.returncode, 127)
        self.assertEqual(attempt.failure_kind, "configuration")
        self.assertEqual(attempt.failure_source, "os_error")
        self.assertEqual(attempt.failure_code, "FileNotFoundError:2")
        self.assertEqual(attempt.stdout, "")
        self.assertIn("FileNotFoundError", attempt.stderr)

    def test_claude_native_envelope_contributes_evidence(self) -> None:
        envelope = json.dumps(
            {"type": "result", "is_error": True, "api_error_status": 503}
        )

        def fake_executor(plan):
            return adapters.RawAttemptResult(
                subprocess.CompletedProcess(list(plan.command), 0, envelope, ""),
                None,
                None,
            )

        with patch.object(
            adapters, "run_single_attempt", side_effect=fake_executor
        ) as executor:
            attempt = self.adapter.execute_attempt(self.plan)

        executor.assert_called_once()
        self.assertEqual(attempt.failure_kind, "unavailable")
        self.assertEqual(attempt.failure_source, "provider_native")
        self.assertEqual(attempt.returncode, 0)


class CompatibilityRunnerTests(unittest.TestCase):
    """The controller-owned runner owns retry and never reclassifies."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "jobs"
        self.repo.mkdir()
        self.request = compatibility_request(
            "policy_clarification", "prompt", self.repo
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def attempt(self, plan, *, stderr="HTTP 503 Service Unavailable", kind="unavailable"):
        return providers.ProviderAttempt(
            command=list(plan.command),
            returncode=1 if kind is not None else 0,
            stdout="",
            stderr=stderr,
            failure_kind=kind,
            failure_source="stderr" if kind is not None else None,
            failure_code="503" if kind == "unavailable" else None,
        )

    def test_runner_reuses_the_immutable_plan_for_read_only_retries(self) -> None:
        delivered: list[adapters.AttemptPlan] = []
        sleeps: list[float] = []

        def fake_execute(plan):
            delivered.append(plan)
            return self.attempt(plan)

        execution = adapters.run_compatibility(
            self.request, execute=fake_execute, sleeper=sleeps.append
        )

        self.assertEqual(len(delivered), 3)
        self.assertTrue(all(plan is delivered[0] for plan in delivered))
        self.assertEqual(sleeps, [5.0, 15.0])
        self.assertEqual(
            [attempt.retry_scheduled for attempt in execution.attempts],
            [True, True, False],
        )
        self.assertEqual(
            [attempt.failure_kind for attempt in execution.attempts],
            ["unavailable", "unavailable", "unavailable"],
        )
        self.assertEqual(execution.failure_kind, "unavailable")
        self.assertEqual(execution.capability, "read_only")

    def test_runner_stops_immediately_on_terminal_kinds(self) -> None:
        for kind, stderr in (
            ("auth", "HTTP 401 Unauthorized"),
            ("quota", "quota exceeded"),
            ("provider_error", "unclassified transport failure"),
        ):
            with self.subTest(kind=kind):
                calls: list[adapters.AttemptPlan] = []
                sleeps: list[float] = []

                def fake_execute(plan, stderr=stderr, kind=kind):
                    calls.append(plan)
                    return self.attempt(plan, stderr=stderr, kind=kind)

                execution = adapters.run_compatibility(
                    self.request, execute=fake_execute, sleeper=sleeps.append
                )
                self.assertEqual(len(calls), 1)
                self.assertEqual(sleeps, [])
                self.assertEqual(execution.failure_kind, kind)
                self.assertEqual(len(execution.attempts), 1)
                self.assertFalse(execution.attempts[0].retry_scheduled)

    def test_writer_unavailable_is_single_shot_and_never_sleeps(self) -> None:
        writer_request = compatibility_request(
            "implementation_write", "prompt", self.repo
        )
        calls: list[adapters.AttemptPlan] = []
        sleeps: list[float] = []

        def fake_execute(plan):
            calls.append(plan)
            return self.attempt(plan)

        execution = adapters.run_compatibility(
            writer_request, execute=fake_execute, sleeper=sleeps.append
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(execution.failure_kind, "unavailable")
        self.assertEqual(execution.capability, "workspace_write")
        self.assertEqual(execution.attempts[0].retry_scheduled, False)

    def test_runner_accepts_an_already_normalized_success(self) -> None:
        calls: list[adapters.AttemptPlan] = []

        def fake_execute(plan):
            calls.append(plan)
            return providers.ProviderAttempt(
                command=list(plan.command),
                returncode=0,
                stdout="resolved",
                stderr="",
                capability=plan.capability,
            )

        execution = adapters.run_compatibility(
            self.request, execute=fake_execute, sleeper=lambda seconds: None
        )
        self.assertEqual(len(calls), 1)
        self.assertIsNone(execution.failure_kind)
        self.assertEqual(execution.returncode, 0)
        self.assertEqual(execution.stdout, "resolved")


def request_capability(operation_id: str) -> str:
    role = providers.OPERATION_ROLES[operation_id]
    return "workspace_write" if role == "implementation" else "read_only"


if __name__ == "__main__":
    unittest.main()
