"""Gate 4.3 Phase 1 baseline parity locks for the provider-extraction seam.

These tests pin the exact runtime baseline that the Gate 4.3 adapter
extraction must preserve: byte-for-byte compatibility argv, wrapper
contracts, supervision constants, retry policy, evidence precedence,
single-pass normalization, and the doctor probe seam. They exercise only
the current code paths with fakes and fixtures; no live provider, network,
credential, or operator configuration state is touched.
"""

import errno
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import adapters
import orchestrator
import providers
import test_orchestrator
from models import (
    ADVERSARIAL_REVIEW_ROUTE,
    ESCALATION_EXECUTIVE_ROUTE,
    IMPLEMENTATION_ROUTE,
    OPERATION_ROLES,
    POLICY_AUTHORITY_ROUTE,
    ROUTE_IDENTITIES,
    RepoState,
    resolve_correction_policy,
)
from providers import ProviderAttempt, ProviderExecution


EXPECTED_SONNET_SCHEMA_JSON = (
    '{"type":"object","properties":{"status":{"type":"string",'
    '"enum":["PASS","FAIL"]},"category":{"type":"string","enum":'
    '["PASS","IMPLEMENTATION_DEFECT","POLICY_AMBIGUITY","SCOPE_VIOLATION"]},'
    '"finding_key":{"type":"string","minLength":1,"maxLength":120},'
    '"summary":{"type":"string"}},"required":["status","category",'
    '"finding_key","summary"],"additionalProperties":false}'
)

EXPECTED_LUNA_GIT_PROHIBITIONS = (
    "You have workspace-write only for bounded implementation edits. "
    "Do not commit, push, create or switch branches, merge, rebase, reset, "
    "or modify any Git metadata (.git). The controller alone has Git authority."
)


class GoldenCompatibilityCommandTests(unittest.TestCase):
    """Byte-for-byte argv locks for the four compatibility routes."""

    def test_sonnet_review_argv_is_byte_exact(self) -> None:
        prompt = "review the specification exactly as written"
        self.assertEqual(
            providers.build_sonnet_command(prompt),
            [
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
                EXPECTED_SONNET_SCHEMA_JSON,
                "--",
                prompt,
            ],
        )

    def test_sonnet_schema_json_is_minified_and_stable(self) -> None:
        self.assertEqual(
            providers.SONNET_REVIEW_SCHEMA_JSON,
            EXPECTED_SONNET_SCHEMA_JSON,
        )
        self.assertEqual(
            providers.SONNET_REVIEW_SCHEMA_JSON,
            json.dumps(
                providers.SONNET_REVIEW_SCHEMA, separators=(",", ":")
            ),
        )
        self.assertNotIn(" ", providers.SONNET_REVIEW_SCHEMA_JSON)

    def test_terra_advisory_argv_is_byte_exact(self) -> None:
        prompt = "clarify the ambiguous policy requirement"
        self.assertEqual(
            providers.build_terra_command(prompt),
            [
                "codex",
                "exec",
                "--model",
                "gpt-5.6-terra",
                "--sandbox",
                "read-only",
                "--",
                prompt,
            ],
        )

    def test_sol_escalation_argv_is_byte_exact(self) -> None:
        prompt = "escalate the repeated finding decision"
        self.assertEqual(
            providers.build_sol_command(prompt),
            [
                "codex",
                "exec",
                "--model",
                "gpt-5.6-sol",
                "--sandbox",
                "read-only",
                "--",
                prompt,
            ],
        )

    def test_luna_writer_argv_is_byte_exact(self) -> None:
        prompt = "implement the bounded change"
        self.assertEqual(
            providers.build_luna_command(prompt),
            [
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
                EXPECTED_LUNA_GIT_PROHIBITIONS + "\n\n" + prompt,
            ],
        )

    def test_luna_git_prohibitions_constant_is_exact(self) -> None:
        self.assertEqual(
            providers.LUNA_GIT_PROHIBITIONS,
            EXPECTED_LUNA_GIT_PROHIBITIONS,
        )

    def test_dash_prefixed_prompts_remain_single_data_arguments(self) -> None:
        hostile = "--rm -rf / --output-format text --model evil"
        for builder in (
            providers.build_sonnet_command,
            providers.build_terra_command,
            providers.build_sol_command,
            providers.build_luna_command,
        ):
            with self.subTest(builder=builder.__name__):
                argv = builder(hostile)
                separator = argv.index("--")
                self.assertEqual(separator, len(argv) - 2)
                luna_prompt = (
                    EXPECTED_LUNA_GIT_PROHIBITIONS + "\n\n" + hostile
                )
                if builder is providers.build_luna_command:
                    self.assertEqual(argv[-1], luna_prompt)
                else:
                    self.assertEqual(argv[-1], hostile)

    def test_every_argv_ends_with_end_of_options_then_prompt(self) -> None:
        for argv in (
            providers.build_sonnet_command("p"),
            providers.build_terra_command("p"),
            providers.build_sol_command("p"),
            providers.build_luna_command("p"),
        ):
            with self.subTest(argv=argv[:2]):
                self.assertEqual(argv[-2], "--")
                self.assertEqual(argv.count("--"), 1)


class RouteCatalogParityTests(unittest.TestCase):
    """Closed route/operation facts that adapters must consume unchanged."""

    def test_route_identities_are_exact(self) -> None:
        expected = {
            "implementation": ("codex_cli", "builtin.implementation.v1", "gpt-5.6-luna", "Luna High"),
            "adversarial_review": ("claude_cli", "builtin.adversarial_review.v1", "sonnet", "Sonnet 5 High"),
            "escalation_executive": ("codex_cli", "builtin.escalation_executive.v1", "gpt-5.6-sol", "Sol High"),
            "policy_authority": ("codex_cli", "builtin.policy_authority.v1", "gpt-5.6-terra", "Terra High"),
        }
        self.assertEqual(set(ROUTE_IDENTITIES), set(expected))
        for role_id, facts in expected.items():
            route = ROUTE_IDENTITIES[role_id]
            self.assertEqual(
                (
                    route.provider_adapter_id,
                    route.route_id,
                    route.model_id,
                    route.display_name,
                ),
                facts,
            )

    def test_operation_roles_are_closed_and_exact(self) -> None:
        self.assertEqual(
            dict(OPERATION_ROLES),
            {
                "implementation_write": "implementation",
                "correction_write": "implementation",
                "specification_review": "adversarial_review",
                "implementation_review": "adversarial_review",
                "escalation_guidance": "escalation_executive",
                "policy_clarification": "policy_authority",
            },
        )

    def test_argv_model_tokens_come_from_route_catalog(self) -> None:
        self.assertEqual(
            providers.build_sonnet_command("p")[3],
            ADVERSARIAL_REVIEW_ROUTE.model_id,
        )
        self.assertEqual(
            providers.build_terra_command("p")[3],
            POLICY_AUTHORITY_ROUTE.model_id,
        )
        self.assertEqual(
            providers.build_sol_command("p")[3],
            ESCALATION_EXECUTIVE_ROUTE.model_id,
        )
        self.assertEqual(
            providers.build_luna_command("p")[3],
            IMPLEMENTATION_ROUTE.model_id,
        )

    def test_adapter_ids_form_a_closed_two_element_set(self) -> None:
        self.assertEqual(
            {route.provider_adapter_id for route in ROUTE_IDENTITIES.values()},
            {"claude_cli", "codex_cli"},
        )


class ExecutionWrapperContractTests(unittest.TestCase):
    """Each execute_* wrapper selects the exact baseline run policy."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.captured: list[tuple[tuple, dict]] = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    def capture(self, command, repo, **kwargs):
        self.captured.append(((command, repo), kwargs))
        return ProviderExecution(command, 0, "ok", "")

    def run_wrapper(self, wrapper, prompt):
        with patch.object(providers, "_run", side_effect=self.capture):
            execution = wrapper(prompt, self.repo)
        self.assertEqual(len(self.captured), 1)
        (command, repo), kwargs = self.captured[0]
        return command, repo, kwargs, execution

    def test_sonnet_wrapper_uses_read_only_review_policy(self) -> None:
        command, repo, kwargs, _ = self.run_wrapper(
            providers.execute_sonnet_review, "review prompt"
        )
        self.assertEqual(command, providers.build_sonnet_command("review prompt"))
        self.assertEqual(repo, self.repo)
        self.assertEqual(kwargs["capability"], "read_only")
        self.assertEqual(
            kwargs["display_name"], ADVERSARIAL_REVIEW_ROUTE.display_name
        )
        self.assertEqual(
            kwargs["deadline_seconds"],
            providers.READ_ONLY_PROVIDER_DEADLINE_SECONDS,
        )
        self.assertIs(
            kwargs["native_classifier"],
            providers.classify_claude_native_failure,
        )

    def test_terra_wrapper_uses_read_only_policy_without_native_classifier(self) -> None:
        command, _, kwargs, _ = self.run_wrapper(
            providers.execute_terra_resolution, "terra prompt"
        )
        self.assertEqual(command, providers.build_terra_command("terra prompt"))
        self.assertEqual(kwargs["capability"], "read_only")
        self.assertEqual(
            kwargs["display_name"], POLICY_AUTHORITY_ROUTE.display_name
        )
        self.assertEqual(
            kwargs["deadline_seconds"],
            providers.READ_ONLY_PROVIDER_DEADLINE_SECONDS,
        )
        self.assertNotIn("native_classifier", kwargs)

    def test_sol_wrapper_uses_read_only_policy_without_native_classifier(self) -> None:
        command, _, kwargs, _ = self.run_wrapper(
            providers.execute_sol_escalation, "sol prompt"
        )
        self.assertEqual(command, providers.build_sol_command("sol prompt"))
        self.assertEqual(kwargs["capability"], "read_only")
        self.assertEqual(
            kwargs["display_name"], ESCALATION_EXECUTIVE_ROUTE.display_name
        )
        self.assertEqual(
            kwargs["deadline_seconds"],
            providers.READ_ONLY_PROVIDER_DEADLINE_SECONDS,
        )
        self.assertNotIn("native_classifier", kwargs)

    def test_luna_wrapper_uses_workspace_write_policy(self) -> None:
        command, _, kwargs, _ = self.run_wrapper(
            providers.execute_luna_implementation, "writer prompt"
        )
        self.assertEqual(command, providers.build_luna_command("writer prompt"))
        self.assertEqual(kwargs["capability"], "workspace_write")
        self.assertEqual(
            kwargs["display_name"], IMPLEMENTATION_ROUTE.display_name
        )
        self.assertEqual(
            kwargs["deadline_seconds"],
            providers.WRITE_PROVIDER_DEADLINE_SECONDS,
        )
        self.assertNotIn("native_classifier", kwargs)

    def test_default_repository_is_baseline_jobs_checkout(self) -> None:
        with patch.object(providers, "_run", side_effect=self.capture):
            providers.execute_sonnet_review("review prompt")
        (_, repo), _ = self.captured[0]
        self.assertEqual(repo, providers.DEFAULT_REPO)


class SupervisionConstantTests(unittest.TestCase):
    """Supervision policy facts are pinned to the baseline values."""

    def test_deadlines_grace_poll_heartbeat_and_codes_are_exact(self) -> None:
        self.assertEqual(providers.READ_ONLY_PROVIDER_DEADLINE_SECONDS, 1800.0)
        self.assertEqual(providers.WRITE_PROVIDER_DEADLINE_SECONDS, 3600.0)
        self.assertEqual(providers.PROVIDER_TERM_GRACE_SECONDS, 5.0)
        self.assertEqual(providers.PROVIDER_POLL_INTERVAL_SECONDS, 0.2)
        self.assertEqual(providers.PROVIDER_HEARTBEAT_SECONDS, 5.0)
        self.assertEqual(providers.PROVIDER_TIMEOUT_RETURN_CODE, 124)
        self.assertEqual(providers.PROVIDER_INTERRUPTED_RETURN_CODE, 130)
        self.assertEqual(providers.PROVIDER_STDOUT_TAIL_BYTES, 8 * 1024)


class ClaudeNativeEnvelopeBoundaryTests(unittest.TestCase):
    """Claude native evidence is recognized only under the exact rule."""

    def completed(self, stdout, returncode=1, stderr=""):
        return subprocess.CompletedProcess(
            ["claude"], returncode, stdout, stderr
        )

    def envelope(self, **overrides):
        payload = {"type": "result", "is_error": True}
        payload.update(overrides)
        return json.dumps(payload)

    def test_only_result_envelopes_with_strict_is_error_true_classify(self) -> None:
        evidence = providers.classify_claude_native_failure(
            self.completed(self.envelope())
        )
        self.assertEqual(evidence.kind, "provider_error")
        self.assertEqual(evidence.source, "provider_native")

        for stdout in (
            self.envelope(is_error="true"),
            self.envelope(is_error=1),
            self.envelope(is_error=False),
            self.envelope(type="progress"),
            json.dumps([{"type": "result", "is_error": True}]),
            "not-json",
            "",
        ):
            with self.subTest(stdout=stdout[:40]):
                self.assertIsNone(
                    providers.classify_claude_native_failure(
                        self.completed(stdout)
                    )
                )

    def test_api_error_status_maps_to_exact_kinds(self) -> None:
        expected = {
            401: "auth",
            402: "billing",
            403: "auth",
            429: "rate_limit",
            500: "unavailable",
            502: "unavailable",
            503: "unavailable",
            504: "unavailable",
        }
        for status, kind in expected.items():
            with self.subTest(status=status):
                evidence = providers.classify_claude_native_failure(
                    self.completed(self.envelope(api_error_status=status))
                )
                self.assertEqual(evidence.kind, kind)

        string_status = providers.classify_claude_native_failure(
            self.completed(self.envelope(api_error_status="503"))
        )
        self.assertEqual(string_status.kind, "unavailable")

        unknown_status = providers.classify_claude_native_failure(
            self.completed(self.envelope(api_error_status=418))
        )
        self.assertEqual(unknown_status.kind, "provider_error")

    def test_subtype_is_bounded_failure_code(self) -> None:
        bounded = providers.classify_claude_native_failure(
            self.completed(self.envelope(subtype="error_during_execution"))
        )
        self.assertEqual(bounded.code, "error_during_execution")

        oversized = providers.classify_claude_native_failure(
            self.completed(self.envelope(subtype="x" * 121))
        )
        self.assertIsNone(oversized.code)

    def test_native_error_classifies_exit_zero_results(self) -> None:
        execution = ProviderExecution(
            ["claude"], 0, self.envelope(subtype="error_max_turns"), ""
        )
        normalized = providers.normalize_sonnet_execution(execution)
        self.assertEqual(normalized.failure_kind, "provider_error")
        self.assertEqual(normalized.failure_source, "provider_native")
        self.assertTrue(providers.execution_failed(normalized))

    def test_exit_zero_with_failure_prose_stays_successful(self) -> None:
        success = ProviderExecution(
            ["claude"],
            0,
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "quota exceeded; HTTP 503; authentication failed",
                    "structured_output": {
                        "status": "PASS",
                        "category": "PASS",
                        "finding_key": "PASS",
                        "summary": "ok",
                    },
                }
            ),
            "HTTP 401 Unauthorized",
        )
        normalized = providers.normalize_sonnet_execution(success)
        self.assertIsNone(normalized.failure_kind)
        self.assertFalse(providers.execution_failed(normalized))

    def test_supervisor_and_os_evidence_short_circuit_native_reparse(self) -> None:
        for source in ("supervisor", "os_error"):
            with self.subTest(source=source):
                execution = ProviderExecution(
                    ["claude"],
                    providers.PROVIDER_TIMEOUT_RETURN_CODE,
                    self.envelope(api_error_status=503),
                    "",
                    failure_kind="timeout",
                    failure_source=source,
                    failure_code="timeout",
                )
                normalized = providers.normalize_sonnet_execution(execution)
                self.assertEqual(normalized.failure_kind, "timeout")
                self.assertEqual(normalized.failure_source, source)

    def test_native_evidence_wins_over_conflicting_stderr(self) -> None:
        execution = ProviderExecution(
            ["claude"],
            1,
            self.envelope(api_error_status=503),
            "HTTP 401 Unauthorized",
        )
        normalized = providers.normalize_sonnet_execution(execution)
        self.assertEqual(normalized.failure_kind, "unavailable")
        self.assertEqual(normalized.failure_source, "provider_native")


class OsLaunchEvidenceParityTests(unittest.TestCase):
    """OS launch failures synthesize the exact baseline attempt."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_with_launch_error(self, exc):
        def runner(command, **kwargs):
            raise exc

        return providers._run(
            ["missing-provider"],
            self.repo,
            capability="read_only",
            runner=runner,
            sleeper=lambda seconds: None,
        )

    def test_configuration_errnos_map_to_configuration(self) -> None:
        for exc in (
            OSError(errno.EACCES, "Permission denied"),
            OSError(errno.ENOENT, "No such file or directory"),
            OSError(errno.ENOEXEC, "Exec format error"),
            OSError(errno.EPERM, "Operation not permitted"),
        ):
            with self.subTest(errno=exc.errno):
                execution = self.run_with_launch_error(exc)
                self.assertEqual(execution.failure_kind, "configuration")
                self.assertEqual(execution.failure_source, "os_error")
                self.assertEqual(
                    execution.failure_code,
                    f"{type(exc).__name__}:{exc.errno}",
                )

    def test_other_errnos_map_to_provider_error(self) -> None:
        execution = self.run_with_launch_error(
            OSError(errno.EIO, "I/O error")
        )
        self.assertEqual(execution.failure_kind, "provider_error")
        self.assertEqual(execution.failure_source, "os_error")
        self.assertEqual(execution.failure_code, f"OSError:{errno.EIO}")

    def test_launch_failure_is_one_synthesized_127_attempt_without_retry(self) -> None:
        execution = self.run_with_launch_error(
            FileNotFoundError(errno.ENOENT, "No such file or directory")
        )
        self.assertEqual(execution.returncode, 127)
        self.assertEqual(execution.stdout, "")
        self.assertIn("FileNotFoundError", execution.stderr)
        self.assertEqual(len(execution.attempts), 1)
        self.assertFalse(execution.attempts[0].retry_scheduled)
        self.assertEqual(execution.attempts[0].capability, "read_only")


class TransportRetryPolicyBoundaryTests(unittest.TestCase):
    """The closed retry loop admits only read-only unavailable retries."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_every_non_unavailable_kind_stops_after_one_attempt(self) -> None:
        terminal = (
            ("quota exceeded", "quota"),
            ("HTTP 402 Payment Required", "billing"),
            ("HTTP 401 Unauthorized", "auth"),
            ("status code: 429 Too Many Requests", "rate_limit"),
            ("no such file or directory", "configuration"),
            ("unclassified transport failure", "provider_error"),
        )
        for stderr, kind in terminal:
            with self.subTest(kind=kind):
                calls = 0
                sleeps: list[float] = []

                def runner(command, **kwargs):
                    nonlocal calls
                    calls += 1
                    return subprocess.CompletedProcess(command, 1, "", stderr)

                execution = providers._run(
                    ["claude", "-p"],
                    self.repo,
                    capability="read_only",
                    runner=runner,
                    sleeper=sleeps.append,
                )
                self.assertEqual(calls, 1)
                self.assertEqual(sleeps, [])
                self.assertEqual(execution.failure_kind, kind)
                self.assertEqual(len(execution.attempts), 1)
                self.assertFalse(execution.attempts[0].retry_scheduled)

    def test_supervisor_terminal_kinds_stop_after_one_attempt(self) -> None:
        for kind, returncode in (("timeout", 124), ("interrupted", 130)):
            with self.subTest(kind=kind):
                sleeps: list[float] = []
                supervised = providers._SupervisedResult(
                    subprocess.CompletedProcess(
                        ["claude", "-p"], returncode, "partial", "stopped"
                    ),
                    kind,
                )
                with patch.object(
                    providers, "_supervise_process", return_value=supervised
                ) as supervisor:
                    execution = providers._run(
                        ["claude", "-p"],
                        self.repo,
                        capability="read_only",
                        sleeper=sleeps.append,
                    )
                supervisor.assert_called_once()
                self.assertEqual(sleeps, [])
                self.assertEqual(execution.failure_kind, kind)
                self.assertEqual(execution.failure_source, "supervisor")
                self.assertEqual(len(execution.attempts), 1)
                self.assertFalse(execution.attempts[0].retry_scheduled)

    def test_three_unavailable_attempts_are_the_read_only_ceiling(self) -> None:
        command = ["claude", "-p"]
        results = iter(
            [
                subprocess.CompletedProcess(
                    command, 1, "", "HTTP 503 Service Unavailable"
                )
                for _ in range(3)
            ]
        )
        commands: list[list[str]] = []
        sleeps: list[float] = []

        def runner(candidate, **kwargs):
            commands.append(candidate)
            return next(results)

        execution = providers._run(
            command,
            self.repo,
            capability="read_only",
            runner=runner,
            sleeper=sleeps.append,
        )

        self.assertEqual(len(commands), 3)
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
        self.assertEqual(
            execution.duration_seconds,
            sum(attempt.duration_seconds or 0.0 for attempt in execution.attempts),
        )

    def test_workspace_write_unavailable_is_single_shot(self) -> None:
        calls = 0
        sleeps: list[float] = []

        def runner(command, **kwargs):
            nonlocal calls
            calls += 1
            return subprocess.CompletedProcess(
                command, 1, "", "HTTP 503 Service Unavailable"
            )

        execution = providers._run(
            ["codex", "exec", "--model", "gpt-5.6-luna"],
            self.repo,
            capability="workspace_write",
            runner=runner,
            sleeper=sleeps.append,
        )

        self.assertEqual(calls, 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(execution.failure_kind, "unavailable")
        self.assertEqual(len(execution.attempts), 1)
        self.assertFalse(execution.attempts[0].retry_scheduled)


class SinglePassNormalizationTests(unittest.TestCase):
    """_record_provider records already-normalized evidence once."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def blank_run(self, run_id):
        return test_orchestrator._workflow_run(
            run_id=run_id,
            created_at="2026-08-04T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="0" * 64,
            specification="Baseline normalization parity.",
            resolved_correction_policy=resolve_correction_policy(),
            repo=RepoState(
                repo=str(self.repo),
                branch="main",
                head="0" * 40,
                clean=True,
                origin="https://example.invalid/repo.git",
            ),
        )

    def test_recorded_failure_is_not_reclassified_from_stderr(self) -> None:
        run = self.blank_run("single-pass-reclassify")
        execution = ProviderExecution(
            command=["claude"],
            returncode=1,
            stdout="",
            stderr="HTTP 401 Unauthorized",
            duration_seconds=1.0,
            failure_kind="provider_error",
            failure_source="returncode",
            failure_code="1",
            capability="read_only",
        )

        recorded = orchestrator._record_provider(
            run,
            "implementation_review",
            ADVERSARIAL_REVIEW_ROUTE,
            execution,
            capability="read_only",
        )

        self.assertEqual(recorded.failure_kind, "provider_error")
        self.assertEqual(run.provider_runs[-1].failure_kind, "provider_error")
        self.assertEqual(
            run.provider_runs[-1].failure_source, "returncode"
        )

    def test_supervisor_evidence_is_not_reparsed_through_native_envelope(self) -> None:
        run = self.blank_run("single-pass-supervisor")
        native_stdout = json.dumps(
            {"type": "result", "is_error": True, "api_error_status": 503}
        )
        execution = ProviderExecution(
            command=["claude"],
            returncode=providers.PROVIDER_TIMEOUT_RETURN_CODE,
            stdout=native_stdout,
            stderr="[continuo] deadline of 1800s exceeded",
            duration_seconds=1800.0,
            failure_kind="timeout",
            failure_source="supervisor",
            failure_code="timeout",
            capability="read_only",
        )

        recorded = orchestrator._record_provider(
            run,
            "specification_review",
            ADVERSARIAL_REVIEW_ROUTE,
            execution,
            capability="read_only",
        )

        self.assertEqual(recorded.failure_kind, "timeout")
        self.assertEqual(recorded.failure_source, "supervisor")
        self.assertEqual(run.provider_runs[-1].failure_kind, "timeout")

    def test_attempt_tuples_receive_contiguous_ordinals_and_coherence_checks(self) -> None:
        run = self.blank_run("single-pass-attempts")
        command = ["claude", "-p"]
        attempts = (
            ProviderAttempt(
                command=command,
                returncode=1,
                stdout="",
                stderr="HTTP 503 Service Unavailable",
                duration_seconds=0.5,
                failure_kind="unavailable",
                failure_source="stderr",
                failure_code="503",
                capability="read_only",
                retry_scheduled=True,
            ),
            ProviderAttempt(
                command=command,
                returncode=0,
                stdout="ok",
                stderr="",
                duration_seconds=0.5,
                capability="read_only",
                retry_scheduled=False,
            ),
        )
        execution = ProviderExecution(
            command=command,
            returncode=0,
            stdout="ok",
            stderr="",
            duration_seconds=1.0,
            capability="read_only",
            attempts=attempts,
        )

        orchestrator._record_provider(
            run,
            "implementation_review",
            ADVERSARIAL_REVIEW_ROUTE,
            execution,
            capability="read_only",
        )

        records = run.provider_runs[-2:]
        self.assertEqual(
            [record.physical_attempt_ordinal for record in records],
            [1, 2],
        )
        self.assertEqual(
            len({record.logical_invocation_id for record in records}), 1
        )
        self.assertEqual(
            [record.retry_scheduled for record in records], [True, False]
        )

    def test_incoherent_retry_bits_are_rejected(self) -> None:
        run = self.blank_run("single-pass-incoherent")
        attempt = ProviderAttempt(
            command=["claude"],
            returncode=1,
            stdout="",
            stderr="HTTP 503 Service Unavailable",
            failure_kind="unavailable",
            failure_source="stderr",
            failure_code="503",
            capability="read_only",
            retry_scheduled=True,
        )
        execution = ProviderExecution(
            command=["claude"],
            returncode=1,
            stdout="",
            stderr="HTTP 503 Service Unavailable",
            capability="read_only",
            attempts=(attempt,),
        )

        with self.assertRaises(orchestrator.ControllerError):
            orchestrator._record_provider(
                run,
                "implementation_review",
                ADVERSARIAL_REVIEW_ROUTE,
                execution,
                capability="read_only",
            )
        self.assertEqual(run.provider_runs, [])


class ControllerProductionRoutingSeamTests(unittest.TestCase):
    """The Controller's default provider wiring reaches `adapters.run_compatibility`
    for real routes, and an injected fake never reaches it or a real process."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "jobs"
        self.repo.mkdir()
        self.runs = Path(self.temp.name) / "runs"
        self.controller_root = Path(self.temp.name) / "configuration"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def blank_run(self, run_id):
        return test_orchestrator._workflow_run(
            run_id=run_id,
            created_at="2026-08-04T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="0" * 64,
            specification="Routing seam parity.",
            resolved_correction_policy=resolve_correction_policy(),
            repo=RepoState(
                repo=str(self.repo),
                branch="main",
                head="0" * 40,
                clean=True,
                origin="https://example.invalid/repo.git",
            ),
        )

    def test_default_providers_route_through_adapters_run_compatibility(self) -> None:
        controller = orchestrator.Controller(
            self.repo, self.runs, controller_root=self.controller_root,
        )
        run = self.blank_run("routing-seam-default")
        calls: list[adapters.AdapterAttemptRequest] = []

        def fake_run_compatibility(request):
            calls.append(request)
            return ProviderExecution(["claude"], 0, "ok", "")

        with patch.object(
            orchestrator.adapters,
            "run_compatibility",
            side_effect=fake_run_compatibility,
        ), patch.object(
            orchestrator,
            "execute_sonnet_review",
            side_effect=AssertionError("legacy facade invoked in production routing"),
        ), patch.object(
            orchestrator.subprocess,
            "run",
            side_effect=AssertionError("a real process was spawned"),
        ):
            controller._invoke_route(
                run,
                ADVERSARIAL_REVIEW_ROUTE,
                "specification_review",
                "prompt text",
                "read_only",
            )

        self.assertEqual(len(calls), 1)
        request = calls[0]
        binding = run.resolved_configuration.role_bindings["adversarial_review"]
        self.assertEqual(request.operation_id, "specification_review")
        self.assertIs(request.route_profile, binding.route_profile)
        self.assertIs(
            request.provider_account_profile, binding.provider_account_profile
        )
        self.assertEqual(request.prompt, "prompt text")
        self.assertEqual(request.working_directory, self.repo)
        self.assertEqual(request.capability, "read_only")

    def test_injected_fake_provider_bypasses_adapters_and_never_spawns(self) -> None:
        controller = orchestrator.Controller(
            self.repo, self.runs, controller_root=self.controller_root,
        )
        run = self.blank_run("routing-seam-fake")
        fake_calls: list[tuple[str, Path]] = []

        def fake_provider(prompt, repo):
            fake_calls.append((prompt, repo))
            return ProviderExecution(["claude"], 0, "fake output", "")

        with patch.object(
            orchestrator.adapters,
            "run_compatibility",
            side_effect=AssertionError("adapter path reached by an injected fake"),
        ), patch.object(
            orchestrator.subprocess,
            "run",
            side_effect=AssertionError("a real process was spawned"),
        ):
            execution = controller._invoke_route(
                run,
                ADVERSARIAL_REVIEW_ROUTE,
                "specification_review",
                "prompt text",
                "read_only",
                fake_provider,
            )

        self.assertEqual(fake_calls, [("prompt text", self.repo)])
        self.assertEqual(execution.stdout, "fake output")


class DoctorProbeSeamTests(unittest.TestCase):
    """Doctor's provider checks probe only the fixed executable tokens."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "target"
        self.repo.mkdir()
        self.runs = Path(self.temp.name) / "runs"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_doctor_probes_fixed_tokens_without_processes_or_auth(self) -> None:
        probed: list[str] = []

        def fake_which(name):
            probed.append(name)
            return None

        with patch.object(orchestrator.shutil, "which", side_effect=fake_which), patch.object(
            orchestrator.subprocess, "run", side_effect=AssertionError("doctor spawned a process")
        ), patch.object(
            orchestrator, "execute_sonnet_review", side_effect=AssertionError("provider called")
        ), patch.object(
            orchestrator, "execute_terra_resolution", side_effect=AssertionError("provider called")
        ), patch.object(
            orchestrator, "execute_sol_escalation", side_effect=AssertionError("provider called")
        ), patch.object(
            orchestrator, "execute_luna_implementation", side_effect=AssertionError("provider called")
        ):
            report = orchestrator.doctor_report(self.repo, self.runs)

        self.assertEqual(report["doctor_version"], "continuo.doctor.v2")
        self.assertEqual(set(probed), {"git", "claude", "codex"})
        self.assertEqual(
            [check["id"] for check in report["checks"]],
            [
                "git_binary",
                "target_repository",
                "target_state",
                "configuration",
                "run_storage",
                "provider_binaries",
                "provider_auth",
                "route_capabilities",
            ],
        )
        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["provider_binaries"]["status"], "fail")
        self.assertEqual(
            checks["provider_binaries"]["code"], "provider_binary_missing"
        )
        self.assertEqual(checks["provider_auth"]["status"], "unknown")
        self.assertEqual(
            checks["provider_auth"]["code"], "auth_probe_unavailable"
        )
        self.assertEqual(checks["route_capabilities"]["status"], "fail")
        self.assertEqual(
            checks["route_capabilities"]["code"], "route_capabilities_invalid"
        )

    def test_doctor_passes_provider_checks_when_fixed_tokens_resolve(self) -> None:
        with patch.object(
            orchestrator.shutil, "which", return_value="/fixture/bin"
        ), patch.object(
            orchestrator,
            "read_only_repo_state",
            side_effect=orchestrator.ControllerError("target unavailable in test"),
        ), patch.object(
            orchestrator.subprocess, "run", side_effect=AssertionError("doctor spawned a process")
        ):
            report = orchestrator.doctor_report(self.repo, self.runs)

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["provider_binaries"]["status"], "pass")
        self.assertEqual(
            checks["provider_binaries"]["code"], "provider_binaries_available"
        )
        self.assertEqual(checks["route_capabilities"]["status"], "pass")
        self.assertEqual(checks["provider_auth"]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
