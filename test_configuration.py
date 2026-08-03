"""Deterministic adversarial coverage for the Gate 4.2 configuration contract."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from pydantic import ValidationError

import orchestrator
import run_migrations
import configuration as configuration_module
from configuration import (
    ACCOUNT_PROFILE_CATALOG,
    MAX_SOURCE_BYTES,
    ROUTE_PROFILE_CATALOG,
    ConfigurationError,
    builtin_project_configuration,
    canonical_json_bytes,
    canonical_sha256,
    compatibility_selection,
    decode_configuration,
    resolve_configuration,
    validate_saved_configuration,
)
from models import RepoState, RunOverrides, WorkflowRun, resolve_correction_policy
from providers import ProviderExecution
from test_run_migrations import schema12_bytes


ROLES = (
    "implementation",
    "adversarial_review",
    "escalation_executive",
    "policy_authority",
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _make_repo(root: Path) -> Path:
    repo = root / "target"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Continuo Test")
    _git(repo, "remote", "add", "origin", "https://example.invalid/repo.git")
    (repo / "tasks").mkdir()
    (repo / "tasks" / "009-example.md").write_text("Implement the fixture.\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture")
    return repo


def _private_dir(path: Path) -> None:
    path.mkdir()
    path.chmod(0o700)


def _private_source(path: Path, payload: object, *, yaml_format: bool = False) -> None:
    text = (
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        if yaml_format
        else json.dumps(payload, ensure_ascii=False)
    )
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


class ConfigurationParserTests(unittest.TestCase):
    def test_g42_01_json_yaml_equivalence_and_canonical_order(self) -> None:
        logical = {
            "user_defaults_schema_version": 2,
            "role_bindings": {
                "implementation": {
                    "route_id": "builtin.implementation.v1",
                    "provider_account_profile_id": (
                        "builtin.codex-cli.local-session.v1"
                    ),
                }
            },
        }
        json_value = decode_configuration(
            json.dumps(logical, separators=(",", ":")).encode()
        )
        yaml_value = decode_configuration(
            ("# comment\n" + yaml.safe_dump(logical, sort_keys=False)).encode()
        )
        self.assertEqual(json_value, yaml_value)
        self.assertEqual(canonical_json_bytes(json_value), canonical_json_bytes(yaml_value))
        self.assertEqual(canonical_sha256(json_value), canonical_sha256(yaml_value))
        self.assertTrue(canonical_json_bytes(json_value).endswith(b"\n"))

    def test_g42_02_03_strict_syntax_alias_null_depth_size_and_utf8(self) -> None:
        invalid = (
            b'{"a":1,"a":2}',
            b"a: &shared {b: 1}\nc: *shared\n",
            b"a: !unsafe value\n",
            b"a: null\n",
            b'{"a":NaN}',
            b"\xff",
        )
        for source in invalid:
            with self.subTest(source=source[:20]):
                with self.assertRaises(ConfigurationError):
                    decode_configuration(source)
        nested: object = "leaf"
        for _ in range(34):
            nested = {"next": nested}
        with self.assertRaisesRegex(ConfigurationError, "configuration_invalid_syntax"):
            decode_configuration(json.dumps(nested).encode())
        with self.assertRaisesRegex(ConfigurationError, "configuration_too_large"):
            decode_configuration(b"{" + b" " * MAX_SOURCE_BYTES + b"}")

    def test_g42_04_closed_input_models_reject_authority_fields_and_coercion(self) -> None:
        selection = compatibility_selection("implementation").model_dump(mode="json")
        for extra in (
            "provider_adapter_id",
            "model_id",
            "effort",
            "endpoint",
            "command",
            "capability",
            "credential",
            "prompt",
        ):
            payload = {
                "run_overrides_schema_version": 2,
                "role_bindings": {"implementation": {**selection, extra: "x"}},
            }
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                RunOverrides.model_validate(payload, strict=True)
        with self.assertRaises(ValidationError):
            RunOverrides.model_validate(
                {"run_overrides_schema_version": "2", "role_bindings": {}},
                strict=True,
            )


class ConfigurationResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "controller"
        self.target_key = "a" * 64
        self.repo = "/private/tmp/continuo-target"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _install_project(self, payload: dict[str, object] | None = None) -> Path:
        _private_dir(self.root)
        projects = self.root / "projects"
        _private_dir(projects)
        target = projects / self.target_key
        _private_dir(target)
        project = payload or builtin_project_configuration(
            self.target_key,
            self.repo,
        ).model_dump(mode="json")
        path = target / "project-configuration.yaml"
        _private_source(path, project, yaml_format=True)
        return path

    def test_g42_10_11_12_seed_catalog_is_exact_hash_coherent_and_honest(self) -> None:
        self.assertEqual(set(ROUTE_PROFILE_CATALOG), {
            "builtin.implementation.v1",
            "builtin.adversarial_review.v1",
            "builtin.escalation_executive.v1",
            "builtin.policy_authority.v1",
        })
        self.assertEqual(set(ACCOUNT_PROFILE_CATALOG), {
            "builtin.codex-cli.local-session.v1",
            "builtin.claude-cli.local-session.v1",
        })
        for route in ROUTE_PROFILE_CATALOG.values():
            self.assertEqual(route.effort.mode, "provider_default")
            self.assertIsNone(route.effort.effort_id)
            self.assertEqual(
                route.route_profile_sha256,
                canonical_sha256(
                    route.model_dump(mode="json", exclude={"route_profile_sha256"})
                ),
            )
        resolved = resolve_configuration(
            target_key=self.target_key,
            canonical_repo=self.repo,
            controller_root=self.root,
        ).configuration
        self.assertNotIn("High", resolved.model_dump_json())

    def test_g42_05_06_08_complete_permitted_binding_fails_closed(self) -> None:
        project = builtin_project_configuration(
            self.target_key,
            self.repo,
        ).model_dump(mode="json")
        del project["role_bindings"]["policy_authority"]
        self._install_project(project)
        with self.assertRaisesRegex(ConfigurationError, "configuration_binding_mismatch"):
            resolve_configuration(
                target_key=self.target_key,
                canonical_repo=self.repo,
                controller_root=self.root,
            )

        self.temp.cleanup()
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "controller"
        override = RunOverrides(
            role_bindings={
                "implementation": {
                    "route_id": "builtin.adversarial_review.v1",
                    "provider_account_profile_id": (
                        "builtin.claude-cli.local-session.v1"
                    ),
                }
            }
        )
        with self.assertRaisesRegex(ConfigurationError, "configuration_binding_unpermitted"):
            resolve_configuration(
                target_key=self.target_key,
                canonical_repo=self.repo,
                controller_root=self.root,
                run_overrides=override,
            )

    def test_g42_07_precedence_records_override_user_and_project_sources(self) -> None:
        _private_dir(self.root)
        selection = compatibility_selection("implementation").model_dump(mode="json")
        _private_source(
            self.root / "user-defaults.yaml",
            {
                "user_defaults_schema_version": 2,
                "role_bindings": {"implementation": selection},
            },
            yaml_format=True,
        )
        user = resolve_configuration(
            target_key=self.target_key,
            canonical_repo=self.repo,
            controller_root=self.root,
        ).configuration
        self.assertEqual(
            user.role_bindings["implementation"].selection_source,
            "user_default",
        )
        override = resolve_configuration(
            target_key=self.target_key,
            canonical_repo=self.repo,
            controller_root=self.root,
            run_overrides=RunOverrides(role_bindings={"implementation": selection}),
        ).configuration
        self.assertEqual(
            override.role_bindings["implementation"].selection_source,
            "run_override",
        )
        self.assertNotEqual(user.configuration_sha256, override.configuration_sha256)

    def test_g42_13_16_17_private_topology_never_repairs_or_falls_back(self) -> None:
        _private_dir(self.root)
        self.root.chmod(0o755)
        before = self.root.stat()
        with self.assertRaisesRegex(ConfigurationError, "configuration_storage_unsafe"):
            resolve_configuration(
                target_key=self.target_key,
                canonical_repo=self.repo,
                controller_root=self.root,
            )
        self.assertEqual(self.root.stat().st_mode, before.st_mode)

        self.root.chmod(0o700)
        projects = self.root / "projects"
        _private_dir(projects)
        target = projects / self.target_key
        _private_dir(target)
        with self.assertRaisesRegex(ConfigurationError, "configuration_missing"):
            resolve_configuration(
                target_key=self.target_key,
                canonical_repo=self.repo,
                controller_root=self.root,
            )
        self.assertEqual(list(target.iterdir()), [])

    def test_g42_13_descriptor_read_detects_hardlink_and_replacement(self) -> None:
        _private_dir(self.root)
        defaults = self.root / "user-defaults.yaml"
        payload = {"user_defaults_schema_version": 2, "role_bindings": {}}
        _private_source(defaults, payload)
        linked = self.base / "linked-defaults.yaml"
        os.link(defaults, linked)
        with self.assertRaisesRegex(ConfigurationError, "configuration_storage_unsafe"):
            resolve_configuration(
                target_key=self.target_key,
                canonical_repo=self.repo,
                controller_root=self.root,
            )
        linked.unlink()

        replacement = self.base / "replacement.yaml"
        _private_source(replacement, payload)
        real_read = configuration_module.os.read
        replaced = False

        def replace_during_read(descriptor: int, size: int) -> bytes:
            nonlocal replaced
            if not replaced:
                replaced = True
                os.replace(replacement, defaults)
            return real_read(descriptor, size)

        with patch.object(configuration_module.os, "read", replace_during_read):
            with self.assertRaisesRegex(ConfigurationError, "configuration_source_changed"):
                resolve_configuration(
                    target_key=self.target_key,
                    canonical_repo=self.repo,
                    controller_root=self.root,
                )

    def test_g42_15_19_builtin_is_target_bound_and_project_mismatch_rejects(self) -> None:
        result = resolve_configuration(
            target_key=self.target_key,
            canonical_repo=self.repo,
            controller_root=self.root,
        )
        self.assertEqual(
            result.configuration.project_source.source_kind,
            "builtin_compatibility",
        )
        self.assertEqual(result.project.target_binding.target_key, self.target_key)

        mismatch = builtin_project_configuration(
            self.target_key,
            self.repo,
        ).model_dump(mode="json")
        mismatch["target_binding"]["canonical_repo"] = "/different"
        self._install_project(mismatch)
        with self.assertRaisesRegex(ConfigurationError, "configuration_binding_mismatch"):
            resolve_configuration(
                target_key=self.target_key,
                canonical_repo=self.repo,
                controller_root=self.root,
            )

    def test_g42_30_31_private_semantic_hash_controls_resume(self) -> None:
        path = self._install_project()
        result = resolve_configuration(
            target_key=self.target_key,
            canonical_repo=self.repo,
            controller_root=self.root,
        )
        project = result.project.model_dump(mode="json")
        _private_source(path, project, yaml_format=False)
        validate_saved_configuration(
            result.configuration,
            target_key=self.target_key,
            canonical_repo=self.repo,
            controller_root=self.root,
        )
        project["profile_id"] = "not-registered"
        _private_source(path, project)
        with self.assertRaisesRegex(ConfigurationError, "configuration_source_changed"):
            validate_saved_configuration(
                result.configuration,
                target_key=self.target_key,
                canonical_repo=self.repo,
                controller_root=self.root,
            )

    def test_g42_32_33_builtin_install_blocks_but_defaults_are_frozen(self) -> None:
        _private_dir(self.root)
        selection = compatibility_selection("implementation").model_dump(mode="json")
        defaults = self.root / "user-defaults.yaml"
        _private_source(
            defaults,
            {
                "user_defaults_schema_version": 2,
                "role_bindings": {"implementation": selection},
            },
        )
        result = resolve_configuration(
            target_key=self.target_key,
            canonical_repo=self.repo,
            controller_root=self.root,
        )
        defaults.write_text("secret-looking changed text")
        defaults.chmod(0o644)
        validate_saved_configuration(
            result.configuration,
            target_key=self.target_key,
            canonical_repo=self.repo,
            controller_root=self.root,
        )
        projects = self.root / "projects"
        _private_dir(projects)
        target = projects / self.target_key
        _private_dir(target)
        with self.assertRaisesRegex(ConfigurationError, "configuration_source_changed"):
            validate_saved_configuration(
                result.configuration,
                target_key=self.target_key,
                canonical_repo=self.repo,
                controller_root=self.root,
            )


class ConfigurationLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.repo = _make_repo(self.base)
        self.runs = self.base / "runs"
        self.root = self.base / "controller"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_g42_21_configuration_failure_precedes_all_controller_side_effects(self) -> None:
        _private_dir(self.root)
        self.root.chmod(0o755)
        calls: list[str] = []

        def provider(*_: object) -> ProviderExecution:
            calls.append("provider")
            raise AssertionError("provider called")

        controller = orchestrator.Controller(
            self.repo,
            self.runs,
            sonnet=provider,
            terra=provider,
            sol=provider,
            luna=provider,
            controller_root=self.root,
        )
        before = orchestrator.repo_state(self.repo)
        with self.assertRaisesRegex(orchestrator.ControllerError, "configuration_storage_unsafe"):
            controller.new_run("009")
        self.assertEqual(calls, [])
        self.assertFalse(self.runs.exists())
        self.assertEqual(orchestrator.repo_state(self.repo), before)

    def test_g42_22_dirty_run_persists_complete_schema13_without_provider(self) -> None:
        (self.repo / "dirty.txt").write_text("dirty")
        calls: list[str] = []

        def provider(*_: object) -> ProviderExecution:
            calls.append("provider")
            raise AssertionError("provider called")

        run = orchestrator.Controller(
            self.repo,
            self.runs,
            sonnet=provider,
            terra=provider,
            sol=provider,
            luna=provider,
            controller_root=self.root,
        ).new_run("009")
        self.assertEqual(run.schema_version, 13)
        self.assertIsNotNone(run.resolved_configuration)
        self.assertEqual(run.stage, "blocked_dirty_repo")
        self.assertEqual(calls, [])
        saved = orchestrator.load_run(run.run_id, self.runs)
        self.assertEqual(
            saved.resolved_configuration.configuration_sha256,
            run.resolved_configuration.configuration_sha256,
        )

    def test_g42_14_source_change_before_initial_persist_leaves_no_state(self) -> None:
        identity = orchestrator.target_identity(self.repo)
        _private_dir(self.root)
        projects = self.root / "projects"
        _private_dir(projects)
        target = projects / identity.target_key
        _private_dir(target)
        source = target / "project-configuration.yaml"
        project = builtin_project_configuration(
            identity.target_key,
            identity.canonical_repo,
        ).model_dump(mode="json")
        _private_source(source, project)
        real_revalidate = orchestrator.revalidate_sources

        def change_then_revalidate(result) -> None:
            changed = copy.deepcopy(project)
            changed["profile_id"] = "unregistered-profile"
            _private_source(source, changed)
            real_revalidate(result)

        controller = orchestrator.Controller(
            self.repo,
            self.runs,
            sonnet=lambda *_: (_ for _ in ()).throw(AssertionError("provider called")),
            controller_root=self.root,
        )
        with patch.object(orchestrator, "revalidate_sources", change_then_revalidate):
            with self.assertRaises(orchestrator.ControllerError):
                controller.new_run("009")
        self.assertFalse(self.runs.exists())
        self.assertFalse(any(self.base.rglob("*.sqlite3")))

    def test_g42_24_hash_policy_and_missing_configuration_reject(self) -> None:
        identity = orchestrator.target_identity(self.repo)
        configuration = resolve_configuration(
            target_key=identity.target_key,
            canonical_repo=identity.canonical_repo,
            controller_root=self.root,
        ).configuration
        values = {
            "run_id": "configuration-model",
            "created_at": "2026-08-03T00:00:00+00:00",
            "task_ref": "009",
            "task_file": "tasks/009-example.md",
            "task_sha256": "a" * 64,
            "specification": "fixture",
            "repo": RepoState(
                repo=str(self.repo),
                branch="main",
                head="b" * 40,
                clean=True,
                origin="https://example.invalid/repo.git",
            ),
            "resolved_correction_policy": resolve_correction_policy(),
        }
        with self.assertRaisesRegex(ValidationError, "resolved_configuration"):
            WorkflowRun(**values)
        tampered = configuration.model_dump(mode="json")
        tampered["configuration_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValidationError, "configuration hash"):
            WorkflowRun(**values, resolved_configuration=tampered)

    def test_g42_25_26_schema12_migrates_to_audited_null_and_refuses(self) -> None:
        source = schema12_bytes()
        classification = run_migrations.classify_run_bytes(source)
        self.assertEqual(
            (classification.schema_version, classification.structural_class),
            (12, "V12"),
        )
        self.assertEqual(classification.treatment, "migrate")
        result = run_migrations.migrate_classification(
            classification,
            migration_id="g42-schema12",
            migrated_at="2026-08-03T00:00:00+00:00",
        )
        self.assertEqual(result.applied_steps, ("12_to_13",))
        self.assertIsNone(result.run.resolved_configuration)
        audit = result.run.configuration_migration_audit
        self.assertIsNotNone(audit)
        self.assertEqual(audit.reason_codes, ("missing_resolved_configuration",))
        with self.assertRaisesRegex(orchestrator.ControllerError, "execution refused"):
            orchestrator.Controller._require_executable(result.run)

    def test_g42_35_provider_guard_rejects_saved_binding_before_helper(self) -> None:
        identity = orchestrator.target_identity(self.repo)
        configuration = resolve_configuration(
            target_key=identity.target_key,
            canonical_repo=identity.canonical_repo,
            controller_root=self.root,
        ).configuration
        run = WorkflowRun(
            run_id="provider-guard",
            created_at="2026-08-03T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="a" * 64,
            specification="fixture",
            repo=orchestrator.repo_state(self.repo),
            resolved_correction_policy=resolve_correction_policy(),
            resolved_configuration=configuration,
        )
        bindings = dict(configuration.role_bindings)
        bindings["implementation"] = bindings["adversarial_review"]
        run.__dict__["resolved_configuration"] = configuration.model_copy(
            update={"role_bindings": bindings}
        )
        calls: list[str] = []

        def provider(*_: object) -> ProviderExecution:
            calls.append("provider")
            raise AssertionError("provider called")

        controller = orchestrator.Controller(
            self.repo,
            self.runs,
            luna=provider,
            controller_root=self.root,
        )
        with self.assertRaisesRegex(orchestrator.ControllerError, "configuration_binding_mismatch"):
            controller._provider_for(run, orchestrator.IMPLEMENTATION_ROUTE)
        self.assertEqual(calls, [])

    def test_g42_36_38_dry_run_and_doctor_are_v2_deterministic_and_read_only(self) -> None:
        first = orchestrator.build_dry_run_plan(
            self.repo,
            "009",
            self.runs,
            controller_root=self.root,
        )
        second = orchestrator.build_dry_run_plan(
            self.repo,
            "009",
            self.runs,
            controller_root=self.root,
        )
        self.assertEqual(first, second)
        self.assertEqual(first["plan_version"], "continuo.run-plan.v2")
        self.assertEqual(first["configuration"]["profile_id"], "continuo.jobs-compat.v1")
        self.assertFalse(self.root.exists())
        self.assertFalse(self.runs.exists())
        doctor = orchestrator.doctor_report(
            self.repo,
            self.runs,
            controller_root=self.root,
        )
        self.assertEqual(doctor["doctor_version"], "continuo.doctor.v2")
        check = next(item for item in doctor["checks"] if item["id"] == "configuration")
        self.assertEqual(check["code"], "configuration_builtin_compatibility")
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
