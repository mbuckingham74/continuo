"""Deterministic adversarial coverage for the Gate 2.2-2.3 migration contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import typer

import orchestrator
import run_migrations
from models import (
    CURRENT_RUN_SCHEMA_VERSION,
    RepoState,
    WorkflowRun,
    resolve_correction_policy,
)


FIXTURES = Path(__file__).parent / "test_fixtures" / "run_schemas"


def fixture_bytes(version: str | int) -> bytes:
    return (FIXTURES / f"schema_v{version}.json").read_bytes()


def fixture_object(version: str | int) -> dict[str, object]:
    return json.loads(fixture_bytes(version))


def encoded(value: dict[str, object]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def migrated(source: bytes) -> WorkflowRun:
    classification = run_migrations.classify_run_bytes(source)
    return run_migrations.migrate_classification(
        classification,
        migration_id="migration-test-01",
        migrated_at="2026-08-02T12:00:00+00:00",
    ).run


def schema7_bytes() -> bytes:
    source = fixture_bytes("6_current")
    classification = run_migrations.classify_run_bytes(source)
    context = run_migrations.MigrationContext(
        migration_id="historical-v7-fixture",
        migrated_at="2026-08-02T11:00:00+00:00",
        source_schema_version=6,
        source_structural_class=classification.structural_class,
        source_sha256=classification.source_sha256,
        disposition=classification.disposition,
        applied_steps=("6_to_7",),
        reason_codes=(),
    )
    payload, _ = run_migrations.MIGRATION_REGISTRY[(6, 7)](
        fixture_object("6_current"),
        context,
    )
    return encoded(payload)


def schema8_bytes() -> bytes:
    source = schema7_bytes()
    classification = run_migrations.classify_run_bytes(source)
    context = run_migrations.MigrationContext(
        migration_id="historical-v8-fixture",
        migrated_at="2026-08-02T11:30:00+00:00",
        source_schema_version=classification.schema_version,
        source_structural_class=classification.structural_class,
        source_sha256=classification.source_sha256,
        disposition=classification.disposition,
        applied_steps=("6_to_7", "7_to_8"),
        reason_codes=(),
    )
    payload, _ = run_migrations.MIGRATION_REGISTRY[(7, 8)](
        classification.payload,
        context,
    )
    return encoded(payload)


def schema8_object() -> dict[str, object]:
    return json.loads(schema8_bytes())


def schema9_bytes() -> bytes:
    source = schema8_bytes()
    classification = run_migrations.classify_run_bytes(source)
    context = run_migrations.MigrationContext(
        migration_id="historical-v9-fixture",
        migrated_at="2026-08-02T11:45:00+00:00",
        source_schema_version=classification.schema_version,
        source_structural_class=classification.structural_class,
        source_sha256=classification.source_sha256,
        disposition=classification.disposition,
        applied_steps=("6_to_7", "7_to_8", "8_to_9"),
        reason_codes=(),
    )
    payload, _ = run_migrations.MIGRATION_REGISTRY[(8, 9)](
        classification.payload,
        context,
    )
    return encoded(payload)


def schema7_object() -> dict[str, object]:
    return json.loads(schema7_bytes())


def migration_source(version: int) -> bytes:
    if version == 7:
        return schema7_bytes()
    if version == 8:
        return schema8_bytes()
    if version == 9:
        return schema9_bytes()
    if version == 6:
        return fixture_bytes("6_base")
    return fixture_bytes(version)


def review_stdout(
    status: str,
    category: str,
    summary: str,
    finding_key: str | None = None,
) -> str:
    if finding_key is None:
        finding_key = "PASS" if category == "PASS" else "test-finding"
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "structured_output": {
                "status": status,
                "category": category,
                "finding_key": finding_key,
                "summary": summary,
            },
        }
    )


def v8_review_record(
    operation: str,
    stdout: str,
    *,
    returncode: int = 0,
    failure_kind: str | None = None,
) -> dict[str, object]:
    return {
        "identity": {
            "role_id": "adversarial_review",
            "provider_adapter_id": "claude_cli",
            "route_id": "builtin.adversarial_review.v1",
            "model_id": "sonnet",
            "display_name": "Sonnet 5 High",
        },
        "operation_id": operation,
        "command": ["claude", "fixture"],
        "returncode": returncode,
        "stdout": stdout,
        "stderr": "",
        "duration_seconds": 1.0,
        "failure_kind": failure_kind,
        "failure_source": None,
        "failure_code": None,
        "capability": "read_only",
        "repository_fingerprint_before": None,
        "repository_fingerprint_after": None,
        "retry_scheduled": False,
    }


def v8_ordinary_payload(
    records: list[dict[str, object]] | None = None,
    *,
    spec_review: dict[str, object] | None = None,
    implementation_review: dict[str, object] | None = None,
    stage: str = "created",
    run_id: str = "v8-review-backfill",
) -> dict[str, object]:
    payload = schema8_object()
    payload["run_id"] = run_id
    payload["migration_audit"] = None
    payload["identity_migration_audit"] = None
    payload["stage"] = stage
    payload["active_writer_attempt"] = None
    payload["policy_decisions"] = []
    payload["provider_resume_stage"] = None
    payload["provider_resume_prompt"] = None
    payload["provider_resume_identity"] = None
    payload["provider_resume_operation_id"] = None
    payload["provider_runs"] = records if records is not None else []
    payload["spec_review"] = spec_review
    payload["implementation_review"] = implementation_review
    return payload


def pass_payload(summary: str = "fixture spec pass.") -> dict[str, object]:
    return {
        "status": "PASS",
        "category": "PASS",
        "finding_key": "PASS",
        "summary": summary,
    }


def fail_payload(
    summary: str,
    finding_key: str,
) -> dict[str, object]:
    return {
        "status": "FAIL",
        "category": "IMPLEMENTATION_DEFECT",
        "finding_key": finding_key,
        "summary": summary,
    }


class MigrationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.runs = Path(self.temp.name) / "runs"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_run(self, source: bytes, run_id: str | None = None) -> tuple[str, Path]:
        classification = run_migrations.classify_run_bytes(source)
        resolved = run_id or classification.run_id
        if resolved is None:
            raise AssertionError("test fixture requires an explicit run id")
        self.runs.mkdir(mode=0o700, exist_ok=True)
        path = self.runs / f"{resolved}.json"
        path.write_bytes(source)
        path.chmod(0o600)
        return resolved, path

    def approve(self, run_id: str) -> WorkflowRun | None:
        return orchestrator.migrate_run_record(
            run_id,
            self.runs,
            approval=lambda _: True,
            now=lambda: "2026-08-02T12:00:00+00:00",
            migration_id=lambda: "migration-test-01",
        )

    def test_c1_c2_current_constant_and_noncurrent_writes_fail_closed(self) -> None:
        current = WorkflowRun(
            run_id="current-schema-eight",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="current",
            task_file="tasks/current.md",
            task_sha256="0" * 64,
            specification="Current schema fixture.",
            repo=RepoState(
                repo="/fixture/repo",
                branch="main",
                head="1" * 40,
                clean=True,
                origin="https://example.invalid/repo.git",
            ),
            resolved_correction_policy=resolve_correction_policy(),
        )
        self.assertEqual(current.schema_version, 10)
        self.assertIsNone(current.migration_audit)
        self.assertIsNone(current.identity_migration_audit)
        self.assertIsNone(current.review_migration_audit)
        self.assertIsNone(current.policy_migration_audit)
        self.assertEqual(current.review_records, [])
        self.assertEqual(current.unreadable_review_records, [])

        for value in (1, 6, 7, 8, 9, 11, True, "10", 10.0):
            candidate = current.model_copy(deep=True)
            candidate.__dict__["schema_version"] = value
            with self.subTest(value=value):
                with self.assertRaisesRegex(orchestrator.ControllerError, "not current"):
                    orchestrator.persist(candidate, self.runs)
                self.assertFalse(self.runs.exists())

        candidate = current.model_copy(deep=True)
        candidate.__dict__["schema_version"] = 6
        unchanged_update = candidate.updated_at
        with patch.object(
            orchestrator,
            "persist",
            side_effect=AssertionError("persist called"),
        ):
            with self.assertRaisesRegex(orchestrator.ControllerError, "not current"):
                orchestrator.Controller(Path("/not-inspected"), self.runs)._save(
                    candidate
                )
        self.assertEqual(candidate.updated_at, unchanged_update)

    def test_c3_registry_is_exact_adjacent_closed_and_ordered(self) -> None:
        expected = [(version, version + 1) for version in range(1, 10)]
        self.assertEqual(sorted(run_migrations.MIGRATION_REGISTRY), expected)
        self.assertTrue(all(callable(step) for step in run_migrations.MIGRATION_REGISTRY.values()))
        for version in range(1, 10):
            self.assertEqual(
                run_migrations.migration_steps(version),
                tuple(f"{item}_to_{item + 1}" for item in range(version, 10)),
            )
        for invalid in (0, 10, 11):
            self.assertEqual(run_migrations.migration_steps(invalid), ())

    def test_h1_h2_all_committed_fixtures_classify_and_preserve_exact_values(self) -> None:
        expected_classes = ["V1", "V2", "V3", "V4", "V5", "V6-base", "V6-current"]
        names: list[str | int] = [1, 2, 3, 4, 5, "6_base", "6_current"]
        for name, expected_class in zip(names, expected_classes, strict=True):
            source = fixture_bytes(name)
            before = bytes(source)
            first = run_migrations.classify_run_bytes(source)
            second = run_migrations.classify_run_bytes(source)
            with self.subTest(name=name):
                self.assertEqual(first, second)
                self.assertEqual(first.treatment, "migrate")
                self.assertEqual(first.structural_class, expected_class)
                self.assertEqual(first.source_sha256, hashlib.sha256(source).hexdigest())
                self.assertEqual(source, before)

        source = fixture_bytes("6_current")
        original = fixture_object("6_current")
        result = migrated(source)
        dumped = result.model_dump(mode="json")
        transformed = {
            "schema_version",
            "provider_runs",
            "policy_decisions",
            "provider_resume_stage",
            "provider_resume_prompt",
            "migration_audit",
        }
        for key, value in original.items():
            if key not in transformed:
                self.assertEqual(dumped[key], value)
        audit = result.migration_audit
        self.assertIsNotNone(audit)
        assert audit is not None
        self.assertEqual(audit.source_structural_class, "V6-current")
        self.assertEqual(audit.source_sha256, hashlib.sha256(source).hexdigest())
        self.assertEqual(audit.disposition, "resume_eligibility_deferred")
        self.assertEqual(
            result.identity_migration_audit.applied_steps,
            ("6_to_7", "7_to_8"),
        )

    def test_v1_through_v5_adjacent_transforms_preserve_history_without_inference(self) -> None:
        for version in range(1, 6):
            source = fixture_object(version)
            run = migrated(encoded(source))
            audit = run.migration_audit
            assert audit is not None
            self.assertEqual(
                audit.applied_steps,
                tuple(f"{item}_to_{item + 1}" for item in range(version, 7)),
            )
            self.assertEqual(run.stage, source["stage"])
            self.assertEqual(run.correction_cycles, source["correction_cycles"])
            self.assertEqual(run.specification, source["specification"])
            self.assertEqual(run.sol_guidance, source.get("sol_guidance"))
            self.assertEqual(
                [decision.approved_text for decision in run.policy_decisions],
                [
                    decision["approved_text"]
                    for decision in source.get("policy_decisions", [])
                ],
            )
            for index, provider in enumerate(source.get("provider_runs", [])):
                self.assertEqual(run.provider_runs[index].duration_seconds, provider.get("duration_seconds"))
                self.assertIsNone(run.provider_runs[index].capability)
                self.assertIsNone(run.provider_runs[index].failure_source)

        v3 = fixture_object(3)
        self.assertEqual(
            migrated(encoded(v3)).implementation_review.finding_key,
            v3["implementation_review"]["finding_key"],
        )
        v3["implementation_review"]["finding_key"] = None
        self.assertIsNone(migrated(encoded(v3)).implementation_review.finding_key)

        v4 = fixture_object(4)
        second = copy.deepcopy(v4["policy_decisions"][0])
        second["decision_id"] = "fixture-policy-02"
        second["approved_text"] = "Second exact synthetic policy."
        v4["policy_decisions"].append(second)
        self.assertEqual(
            [item.approved_text for item in migrated(encoded(v4)).policy_decisions],
            [item["approved_text"] for item in v4["policy_decisions"]],
        )

    def test_v6a_v6b_provenance_absence_and_supervisor_blocks(self) -> None:
        base = fixture_object("6_base")
        run = migrated(encoded(base))
        audit = run.migration_audit
        assert audit is not None
        self.assertEqual(audit.source_structural_class, "V6-base")
        self.assertEqual(run.provider_runs[0].failure_kind, "quota")
        self.assertIn("missing_failure_provenance", audit.reason_codes)
        self.assertIn("missing_capability_audit", audit.reason_codes)
        self.assertEqual(audit.disposition, "resume_eligibility_deferred")

        provenance = copy.deepcopy(base)
        provenance["provider_runs"][0]["failure_source"] = "returncode"
        provenance["provider_runs"][0]["failure_code"] = "fixture-code"
        classification = run_migrations.classify_run_bytes(encoded(provenance))
        self.assertEqual(classification.structural_class, "V6-provenance")
        self.assertEqual(migrated(encoded(provenance)).provider_runs[0].failure_code, "fixture-code")

        for failure in ("timeout", "interrupted"):
            supervisor = copy.deepcopy(base)
            supervisor["provider_runs"][0]["failure_kind"] = failure
            classification = run_migrations.classify_run_bytes(encoded(supervisor))
            with self.subTest(failure=failure):
                self.assertEqual(classification.structural_class, "V6-supervisor")
                self.assertEqual(classification.disposition, "resume_blocked")
                self.assertEqual(migrated(encoded(supervisor)).migration_audit.disposition, "resume_blocked")

    def test_v6c_v6d_writer_links_round_trip_or_archive(self) -> None:
        writer = fixture_object("6_current")
        writer.pop("target_ownership")
        classification = run_migrations.classify_run_bytes(encoded(writer))
        self.assertEqual(classification.structural_class, "V6-writer")
        run = migrated(encoded(writer))
        self.assertEqual(
            run.active_writer_attempt.model_dump(mode="json"),
            writer["active_writer_attempt"],
        )
        self.assertEqual(
            [item.model_dump(mode="json") for item in run.writer_recovery_decisions],
            writer["writer_recovery_decisions"],
        )
        self.assertEqual(run.migration_audit.disposition, "resume_eligibility_deferred")

        invalids = []
        missing = copy.deepcopy(writer)
        missing["active_writer_attempt"] = None
        invalids.append(missing)
        out_of_range = copy.deepcopy(writer)
        out_of_range["active_writer_attempt"]["provider_record_index"] = 99
        invalids.append(out_of_range)
        mismatch = copy.deepcopy(writer)
        mismatch["provider_runs"][1]["purpose"] = "correction"
        invalids.append(mismatch)
        fingerprint = copy.deepcopy(writer)
        fingerprint["provider_runs"][1]["repository_fingerprint_after"] = "f" * 64
        invalids.append(fingerprint)
        for payload in invalids:
            classified = run_migrations.classify_run_bytes(encoded(payload))
            with self.subTest(reason=classified.field_path):
                self.assertEqual(classified.treatment, "archive")
                self.assertEqual(classified.record_state, "ARCHIVE_ONLY")

    def test_v6e_v6f_ownership_round_trips_or_archives_without_database_access(self) -> None:
        owner = fixture_object("6_base")
        owner["target_ownership"] = fixture_object("6_current")["target_ownership"]
        classification = run_migrations.classify_run_bytes(encoded(owner))
        self.assertEqual(classification.structural_class, "V6-owner")
        with patch.object(orchestrator.sqlite3, "connect", side_effect=AssertionError("database opened")):
            run = migrated(encoded(owner))
        self.assertEqual(run.target_ownership.model_dump(mode="json"), owner["target_ownership"])

        released = copy.deepcopy(owner)
        released["target_ownership"].update(
            released_at="2026-08-02T13:00:00+00:00",
            release_reason="operator_released",
            release_note="Synthetic release.",
        )
        self.assertEqual(
            migrated(encoded(released)).target_ownership.release_note,
            "Synthetic release.",
        )
        contradictory = copy.deepcopy(released)
        contradictory["target_ownership"]["release_note"] = None
        self.assertEqual(run_migrations.classify_run_bytes(encoded(contradictory)).treatment, "archive")
        identity = copy.deepcopy(owner)
        identity["target_ownership"]["canonical_repo"] = "/fixture/other"
        classified = run_migrations.classify_run_bytes(encoded(identity))
        self.assertEqual((classified.treatment, classified.reason_code), ("archive", "ownership_evidence_incoherent"))

    def test_v8g_writer_and_ownership_incoherence_archives_before_migration(self) -> None:
        base = schema8_object()
        classification = run_migrations.classify_run_bytes(encoded(base))
        self.assertEqual(classification.structural_class, "V8")
        self.assertEqual(classification.treatment, "migrate")

        invalids = []
        missing = copy.deepcopy(base)
        missing["active_writer_attempt"] = None
        invalids.append(("active_writer_attempt", missing))
        out_of_range = copy.deepcopy(base)
        out_of_range["active_writer_attempt"]["provider_record_index"] = 99
        invalids.append(("provider_record_index", out_of_range))
        wrong_operation = copy.deepcopy(base)
        wrong_operation["provider_runs"][1]["operation_id"] = "correction_write"
        invalids.append(("provider_record_index", wrong_operation))
        wrong_role = copy.deepcopy(base)
        wrong_role["provider_runs"][1]["identity"]["role_id"] = "adversarial_review"
        invalids.append(("provider_record_index", wrong_role))
        wrong_route = copy.deepcopy(base)
        wrong_route["provider_runs"][1]["identity"]["route_id"] = "builtin.other.v1"
        invalids.append(("provider_record_index", wrong_route))
        wrong_capability = copy.deepcopy(base)
        wrong_capability["provider_runs"][1]["capability"] = "read_only"
        invalids.append(("provider_record_index", wrong_capability))
        fingerprint = copy.deepcopy(base)
        fingerprint["provider_runs"][1]["repository_fingerprint_after"] = "f" * 64
        invalids.append(("repository_fingerprint_after", fingerprint))

        for field, payload in invalids:
            classified = run_migrations.classify_run_bytes(encoded(payload))
            with self.subTest(field=field):
                self.assertEqual(classified.treatment, "archive")
                self.assertEqual(classified.record_state, "ARCHIVE_ONLY")
                self.assertEqual(classified.reason_code, "writer_evidence_incoherent")
                self.assertEqual(classified.field_path, field)

        owner_mismatch = copy.deepcopy(base)
        owner_mismatch["target_ownership"]["canonical_repo"] = "/fixture/other"
        classified = run_migrations.classify_run_bytes(encoded(owner_mismatch))
        self.assertEqual(
            (classified.treatment, classified.reason_code),
            ("archive", "ownership_evidence_incoherent"),
        )

    def test_identity_m2_all_six_legacy_pairs_map_exactly(self) -> None:
        payload = schema7_object()
        payload["stage"] = "created"
        payload["active_writer_attempt"] = None
        payload["policy_decisions"] = []
        pairs = [
            ("Luna High", "implementation", "workspace_write"),
            ("Luna High", "correction", "workspace_write"),
            ("Sonnet 5 High", "specification", "read_only"),
            ("Sonnet 5 High", "implementation", "read_only"),
            ("Sol High", "escalation guidance", "read_only"),
            ("Terra High", "policy clarification", "read_only"),
        ]
        payload["provider_runs"] = [
            {
                "provider": provider,
                "purpose": purpose,
                "command": ["adversarial", "Luna High", "sonnet"],
                "returncode": 0,
                "capability": capability,
            }
            for provider, purpose, capability in pairs
        ]

        run = migrated(encoded(payload))

        self.assertEqual(
            [record.identity.role_id for record in run.provider_runs],
            [
                "implementation",
                "implementation",
                "adversarial_review",
                "adversarial_review",
                "escalation_executive",
                "policy_authority",
            ],
        )
        self.assertEqual(
            [record.operation_id for record in run.provider_runs],
            [
                "implementation_write",
                "correction_write",
                "specification_review",
                "implementation_review",
                "escalation_guidance",
                "policy_clarification",
            ],
        )
        self.assertEqual(
            [record.identity.provider_adapter_id for record in run.provider_runs],
            [
                "codex_cli",
                "codex_cli",
                "claude_cli",
                "claude_cli",
                "codex_cli",
                "codex_cli",
            ],
        )
        for record in run.model_dump(mode="json")["provider_runs"]:
            self.assertNotIn("provider", record)
            self.assertNotIn("purpose", record)
            self.assertEqual(
                record["command"],
                ["adversarial", "Luna High", "sonnet"],
            )

    def test_identity_m4_m5_v7_requires_approval_and_preserves_prior_audit(self) -> None:
        ordinary = schema7_object()
        ordinary["migration_audit"] = None
        ordinary_source = encoded(ordinary)
        run_id, path = self.write_run(ordinary_source)
        with orchestrator.console.capture():
            declined = orchestrator.migrate_run_record(
                run_id,
                self.runs,
                approval=lambda _: False,
            )
        self.assertIsNone(declined)
        self.assertEqual(path.read_bytes(), ordinary_source)

        migrated_ordinary = self.approve(run_id)
        self.assertIsNotNone(migrated_ordinary)
        self.assertIsNone(migrated_ordinary.migration_audit)
        self.assertEqual(
            migrated_ordinary.identity_migration_audit.source_structural_class,
            "V7",
        )
        self.assertEqual(
            migrated_ordinary.identity_migration_audit.applied_steps,
            ("7_to_8",),
        )
        self.assertEqual(
            migrated_ordinary.identity_migration_audit.disposition,
            "resume_eligibility_deferred",
        )

        prior = schema7_object()
        prior["run_id"] = "v7-prior-audit"
        original_audit = copy.deepcopy(prior["migration_audit"])
        migrated_prior = migrated(encoded(prior))
        self.assertEqual(
            migrated_prior.migration_audit.model_dump(mode="json"),
            original_audit,
        )
        self.assertIsNotNone(migrated_prior.identity_migration_audit)

    def test_identity_m6_policy_link_is_exact_or_explicitly_absent(self) -> None:
        linked = schema7_object()
        linked["stage"] = "created"
        linked["active_writer_attempt"] = None
        linked["provider_runs"].append(
            {
                "provider": "Terra High",
                "purpose": "policy clarification",
                "command": ["codex", "unrelated-model-text"],
                "returncode": 0,
                "stdout": "Recommendation names Sol High.",
                "capability": "read_only",
            }
        )
        linked_run = migrated(encoded(linked))
        decision = linked_run.policy_decisions[0]
        self.assertEqual(decision.source_role_id, "policy_authority")
        self.assertEqual(
            decision.source_route_id,
            "builtin.policy_authority.v1",
        )
        self.assertEqual(
            decision.source_provider_record_index,
            len(linked["provider_runs"]) - 1,
        )
        self.assertIsNone(decision.source_link_reason)

        absent = schema7_object()
        absent["stage"] = "created"
        absent["active_writer_attempt"] = None
        absent_run = migrated(encoded(absent))
        absent_decision = absent_run.policy_decisions[0]
        self.assertIsNone(absent_decision.source_provider_record_index)
        self.assertEqual(
            absent_decision.source_link_reason,
            "legacy_source_attempt_unlinked",
        )
        self.assertIn(
            "legacy_policy_source_attempt_unlinked",
            absent_run.identity_migration_audit.reason_codes,
        )

        terra_record = {
            "provider": "Terra High",
            "purpose": "policy clarification",
            "command": ["codex", "unrelated-model-text"],
            "returncode": 0,
            "stdout": "Recommendation names Sol High.",
            "capability": "read_only",
        }
        paired = schema7_object()
        paired["stage"] = "created"
        paired["active_writer_attempt"] = None
        paired["policy_decisions"].append(
            {
                "decision_id": "policy-02",
                "approved_at": "2026-08-02T00:00:00+00:00",
                "approved_by": "human",
                "trigger_summary": "Second ambiguity.",
                "recommendation": "Second recommendation.",
                "approved_text": "Second human text.",
            }
        )
        paired["provider_runs"].append(copy.deepcopy(terra_record))
        paired["provider_runs"].append(copy.deepcopy(terra_record))
        paired_run = migrated(encoded(paired))
        self.assertEqual(
            [
                (decision.source_provider_record_index, decision.source_link_reason)
                for decision in paired_run.policy_decisions
            ],
            [
                (len(paired["provider_runs"]) - 2, None),
                (len(paired["provider_runs"]) - 1, None),
            ],
        )

        mismatch = schema7_object()
        mismatch["stage"] = "created"
        mismatch["active_writer_attempt"] = None
        mismatch["policy_decisions"].append(
            {
                "decision_id": "policy-02",
                "approved_at": "2026-08-02T00:00:00+00:00",
                "approved_by": "human",
                "trigger_summary": "Second ambiguity.",
                "recommendation": "Second recommendation.",
                "approved_text": "Second human text.",
            }
        )
        mismatch["provider_runs"].append(copy.deepcopy(terra_record))
        mismatch_run = migrated(encoded(mismatch))
        for decision in mismatch_run.policy_decisions:
            self.assertIsNone(decision.source_provider_record_index)
            self.assertEqual(
                decision.source_link_reason,
                "legacy_source_attempt_unlinked",
            )

    def test_identity_m7_pending_resume_maps_without_provider_invocation(self) -> None:
        payload = schema7_object()
        payload["active_writer_attempt"] = None
        payload["stage"] = "sol_escalating"
        payload["provider_resume_stage"] = "sol_escalating"
        payload["provider_resume_prompt"] = "saved Sol prompt"

        with patch.object(
            orchestrator,
            "execute_sol_escalation",
            side_effect=AssertionError("provider invoked"),
        ):
            run = migrated(encoded(payload))

        self.assertEqual(
            run.provider_resume_identity.role_id,
            "escalation_executive",
        )
        self.assertEqual(
            run.provider_resume_identity.route_id,
            "builtin.escalation_executive.v1",
        )
        self.assertEqual(
            run.provider_resume_operation_id,
            "escalation_guidance",
        )

    def test_identity_m8_m9_unknown_or_contradictory_history_archives(self) -> None:
        variants: list[tuple[dict[str, object], str]] = []
        unknown = schema7_object()
        unknown["provider_runs"][0]["provider"] = "renamed reviewer"
        variants.append((unknown, "legacy_provider_identity_unmappable"))
        purpose = schema7_object()
        purpose["provider_runs"][0]["purpose"] = "correction"
        variants.append((purpose, "legacy_provider_identity_unmappable"))
        policy = schema7_object()
        policy["policy_decisions"][0]["source_provider"] = "Sol High"
        variants.append((policy, "legacy_policy_source_unmappable"))
        pending = schema7_object()
        pending["provider_resume_stage"] = "unknown_stage"
        pending["provider_resume_prompt"] = "saved"
        variants.append((pending, "legacy_provider_resume_unmappable"))
        capability = schema7_object()
        capability["provider_runs"][0]["capability"] = "workspace_write"
        variants.append((capability, "legacy_provider_capability_incoherent"))
        writer = schema7_object()
        writer["active_writer_attempt"]["provider_record_index"] = 0
        variants.append((writer, "writer_evidence_incoherent"))

        for payload, reason in variants:
            with self.subTest(reason=reason):
                classification = run_migrations.classify_run_bytes(encoded(payload))
                self.assertEqual(classification.treatment, "archive")
                self.assertEqual(classification.record_state, "ARCHIVE_ONLY")
                self.assertEqual(classification.reason_code, reason)

    def test_identity_m10_raw_command_is_preserved_not_interpreted(self) -> None:
        payload = schema7_object()
        payload["stage"] = "created"
        payload["active_writer_attempt"] = None
        command = [
            "claude",
            "--model",
            "gpt-5.6-terra",
            "Luna High / correction / unknown provider",
        ]
        payload["provider_runs"][0]["command"] = command

        run = migrated(encoded(payload))

        self.assertEqual(run.provider_runs[0].command, command)
        self.assertEqual(
            run.provider_runs[0].identity.role_id,
            "adversarial_review",
        )
        self.assertEqual(
            run.provider_runs[0].operation_id,
            "specification_review",
        )

    def test_identity_migrated_v8_classifies_resume_blocked_from_identity_audit(self) -> None:
        migrated_run = migrated(encoded(schema7_object()))
        audit = migrated_run.identity_migration_audit
        assert audit is not None
        classification = run_migrations.classify_run_bytes(
            migrated_run.model_dump_json().encode()
        )
        self.assertEqual(classification.treatment, "current")
        self.assertEqual(classification.record_state, "RESUME_BLOCKED")
        self.assertEqual(classification.disposition, audit.disposition)
        self.assertEqual(classification.reason_code, audit.disposition)
        self.assertEqual(
            classification.structural_class,
            audit.source_structural_class,
        )

    def test_identity_v8_legacy_audit_without_identity_audit_archives(self) -> None:
        payload = schema8_object()
        payload["identity_migration_audit"] = None
        classification = run_migrations.classify_run_bytes(encoded(payload))
        self.assertEqual(classification.treatment, "archive")
        self.assertEqual(classification.record_state, "ARCHIVE_ONLY")
        self.assertEqual(classification.reason_code, "archive_only")

    def test_e1_e2_e3_strict_envelope_and_version_dispatch(self) -> None:
        base = fixture_object("6_current")
        invalid_versions = [None, True, "8", 8.0, 8.5, 0, -1]
        for value in invalid_versions:
            payload = copy.deepcopy(base)
            payload["schema_version"] = value
            classified = run_migrations.classify_run_bytes(encoded(payload))
            with self.subTest(value=value):
                self.assertEqual(classified.treatment, "unsupported")
                self.assertEqual(classified.reason_code, "invalid_schema_version")
        missing = copy.deepcopy(base)
        missing.pop("schema_version")
        self.assertEqual(run_migrations.classify_run_bytes(encoded(missing)).reason_code, "invalid_schema_version")
        for version in (11, 99):
            payload = copy.deepcopy(base)
            payload["schema_version"] = version
            classified = run_migrations.classify_run_bytes(encoded(payload))
            self.assertEqual((classified.treatment, classified.reason_code), ("unsupported", "future_schema"))

        envelopes = [
            b"{",
            b'{"schema_version":6',
            b'{"schema_version":6,"schema_version":5}',
            b"[]",
            b"1",
            b"\xff",
            b'{"schema_version":NaN}',
            b'{"schema_version":Infinity}',
        ]
        for source in envelopes:
            classified = run_migrations.classify_run_bytes(source)
            with self.subTest(source=source[:20]):
                self.assertEqual(classified.treatment, "unsupported")
                self.assertEqual(classified.reason_code, "invalid_envelope")

    def test_e4_known_invalid_records_archive_only_and_never_default_or_drop(self) -> None:
        base = fixture_object(5)
        variants = []
        missing = copy.deepcopy(base)
        missing.pop("specification")
        variants.append(missing)
        extra = copy.deepcopy(base)
        extra["unexpected"] = "must not be dropped"
        variants.append(extra)
        nested = copy.deepcopy(base)
        nested["provider_runs"][0]["unexpected"] = True
        variants.append(nested)
        enum = copy.deepcopy(base)
        enum["implementation_review"]["category"] = "UNKNOWN"
        variants.append(enum)
        invalid_type = copy.deepcopy(base)
        invalid_type["correction_cycles"] = "3"
        variants.append(invalid_type)
        bound = copy.deepcopy(base)
        bound["correction_cycles"] = 99
        variants.append(bound)
        for payload in variants:
            classified = run_migrations.classify_run_bytes(encoded(payload))
            self.assertEqual((classified.treatment, classified.record_state), ("archive", "ARCHIVE_ONLY"))
            self.assertIsNotNone(classified.field_path)

        unidentified = copy.deepcopy(extra)
        unidentified.pop("run_id")
        classified = run_migrations.classify_run_bytes(encoded(unidentified))
        self.assertEqual((classified.treatment, classified.record_state), ("unsupported", "UNSUPPORTED"))

    def test_m1_m2_m3_m4_m5_explicit_action_is_bounded_atomic_and_idempotent(self) -> None:
        source = fixture_bytes(1)
        run_id, path = self.write_run(source)
        before = path.stat()
        with orchestrator.console.capture() as capture:
            result = orchestrator.migrate_run_record(run_id, self.runs, approval=lambda _: False)
        output = capture.get()
        self.assertIsNone(result)
        self.assertEqual(path.read_bytes(), source)
        after = path.stat()
        self.assertEqual((after.st_ino, after.st_mtime_ns, stat.S_IMODE(after.st_mode)), (before.st_ino, before.st_mtime_ns, 0o600))
        for expected in (run_id, "Source schema: 1", "Structural class: V1", hashlib.sha256(source).hexdigest(), "1_to_2", "resume_eligibility_deferred"):
            self.assertIn(expected, output)
        self.assertNotIn("Synthetic schema 1", output)

        replacements: list[tuple[Path, Path]] = []
        real_replace = os.replace

        def observe_replace(source_path: Path, target_path: Path) -> None:
            candidate = json.loads(Path(source_path).read_text())
            self.assertEqual(candidate["schema_version"], 10)
            replacements.append((Path(source_path), Path(target_path)))
            real_replace(source_path, target_path)

        with patch.object(orchestrator.os, "replace", side_effect=observe_replace):
            run = self.approve(run_id)
        self.assertIsNotNone(run)
        self.assertEqual(len(replacements), 1)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        persisted = json.loads(path.read_text())
        self.assertEqual(persisted["schema_version"], 10)
        self.assertEqual(persisted["migration_audit"]["source_sha256"], hashlib.sha256(source).hexdigest())

        stable = path.stat()
        stable_bytes = path.read_bytes()
        with orchestrator.console.capture() as capture:
            again = self.approve(run_id)
        self.assertIsNotNone(again)
        self.assertIn("already current", capture.get())
        self.assertEqual(path.read_bytes(), stable_bytes)
        self.assertEqual(path.stat().st_mtime_ns, stable.st_mtime_ns)

        invalid_id, invalid_path = self.write_run(b"not-json", "invalid")
        with patch.object(orchestrator.tempfile, "mkstemp", side_effect=AssertionError("temp created")):
            with self.assertRaisesRegex(orchestrator.ControllerError, "invalid_envelope"):
                self.approve(invalid_id)
        self.assertEqual(invalid_path.read_bytes(), b"not-json")

        archive = fixture_object(5)
        archive["run_id"] = "archive-command"
        archive["unexpected"] = "must remain private"
        archive_source = encoded(archive)
        _, archive_path = self.write_run(archive_source, "archive-command")
        with patch.object(
            orchestrator.tempfile,
            "mkstemp",
            side_effect=AssertionError("temp created"),
        ):
            with self.assertRaisesRegex(orchestrator.ControllerError, "archive_only"):
                self.approve("archive-command")
        self.assertEqual(archive_path.read_bytes(), archive_source)

    def test_a1_a2_transform_validation_and_replace_failures_preserve_source(self) -> None:
        for edge in list(run_migrations.MIGRATION_REGISTRY):
            source = migration_source(edge[0])
            run_id, path = self.write_run(source)
            original = run_migrations.MIGRATION_REGISTRY[edge]

            def fail(*_: object) -> object:
                raise run_migrations.MigrationError("injected_step_failure")

            run_migrations.MIGRATION_REGISTRY[edge] = fail  # type: ignore[assignment]
            try:
                with self.assertRaisesRegex(orchestrator.ControllerError, "injected_step_failure"):
                    self.approve(run_id)
                self.assertEqual(path.read_bytes(), source)
            finally:
                run_migrations.MIGRATION_REGISTRY[edge] = original
                path.unlink()

        for edge in list(run_migrations.MIGRATION_REGISTRY):
            source = migration_source(edge[0])
            run_id, path = self.write_run(source)
            original = run_migrations.MIGRATION_REGISTRY[edge]

            def fail_after(
                payload: dict[str, object],
                context: run_migrations.MigrationContext,
                transform: run_migrations.MigrationStep = original,
            ) -> object:
                transform(payload, context)
                raise run_migrations.MigrationError("injected_after_step_failure")

            run_migrations.MIGRATION_REGISTRY[edge] = fail_after  # type: ignore[assignment]
            try:
                with self.assertRaisesRegex(
                    orchestrator.ControllerError,
                    "injected_after_step_failure",
                ):
                    self.approve(run_id)
                self.assertEqual(path.read_bytes(), source)
            finally:
                run_migrations.MIGRATION_REGISTRY[edge] = original
                path.unlink()

        source = fixture_bytes("6_base")
        run_id, path = self.write_run(source)
        original_final = run_migrations.MIGRATION_REGISTRY[(6, 7)]

        def invalidate_final(
            payload: dict[str, object],
            context: run_migrations.MigrationContext,
        ) -> tuple[dict[str, object], list[str]]:
            result, reasons = original_final(payload, context)
            result.pop("run_id")
            return result, reasons

        run_migrations.MIGRATION_REGISTRY[(6, 7)] = invalidate_final  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(
                orchestrator.ControllerError,
                "migration_step_failed:run_id",
            ):
                self.approve(run_id)
            self.assertEqual(path.read_bytes(), source)
        finally:
            run_migrations.MIGRATION_REGISTRY[(6, 7)] = original_final
            path.unlink()

        source = fixture_bytes(5)
        run_id, path = self.write_run(source)
        with patch.object(orchestrator.os, "replace", side_effect=OSError("synthetic replace")):
            with self.assertRaisesRegex(orchestrator.ControllerError, "atomic migration persistence failed"):
                self.approve(run_id)
        self.assertEqual(path.read_bytes(), source)
        self.assertEqual(list(self.runs.glob(".run-*.tmp")), [])

    def test_a3_source_compare_and_swap_detects_byte_or_identity_change(self) -> None:
        source = fixture_bytes(5)
        run_id, path = self.write_run(source)
        real_migrate = orchestrator.migrate_classification

        def change_bytes(*args: object, **kwargs: object) -> object:
            result = real_migrate(*args, **kwargs)
            path.write_bytes(source + b" ")
            path.chmod(0o600)
            return result

        with patch.object(orchestrator, "migrate_classification", side_effect=change_bytes):
            with self.assertRaisesRegex(orchestrator.ControllerError, "source_changed"):
                self.approve(run_id)
        self.assertEqual(path.read_bytes(), source + b" ")
        self.assertEqual(list(self.runs.glob(".run-*.tmp")), [])

    def test_a4_two_concurrent_migrations_have_one_winner_and_one_audit(self) -> None:
        source = fixture_bytes(5)
        run_id, path = self.write_run(source)
        barrier = threading.Barrier(2)
        real_write = orchestrator._write_migrated_run
        results: list[str] = []
        lock = threading.Lock()

        def synchronized_write(*args: object, **kwargs: object) -> object:
            barrier.wait(timeout=5)
            return real_write(*args, **kwargs)

        def worker(label: str) -> None:
            try:
                self.approve(run_id)
            except orchestrator.ControllerError as exc:
                outcome = str(exc)
            else:
                outcome = "success"
            with lock:
                results.append(f"{label}:{outcome}")

        with patch.object(orchestrator, "_write_migrated_run", side_effect=synchronized_write):
            threads = [threading.Thread(target=worker, args=(label,)) for label in ("a", "b")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sum(item.endswith(":success") for item in results), 1)
        self.assertEqual(sum("source_changed" in item for item in results), 1)
        persisted = json.loads(path.read_text())
        self.assertEqual(persisted["schema_version"], 10)
        self.assertEqual(persisted["migration_audit"]["migration_id"], "migration-test-01")

    def test_a5_a6_crash_boundaries_leave_one_retryable_or_complete_record(self) -> None:
        source = fixture_bytes(5)
        run_id, path = self.write_run(source)
        with patch.object(orchestrator.os, "replace", side_effect=SystemExit("before replace")):
            with self.assertRaises(SystemExit):
                self.approve(run_id)
        self.assertEqual(path.read_bytes(), source)
        self.assertEqual(list(self.runs.glob(".run-*.tmp")), [])
        self.approve(run_id)
        self.assertEqual(json.loads(path.read_text())["schema_version"], 10)

        second_source = fixture_bytes(4)
        second_id, second_path = self.write_run(second_source)
        real_print = orchestrator.console.print

        def crash_after_replace(*values: object, **kwargs: object) -> None:
            if values and str(values[0]).startswith("Migrated run"):
                raise SystemExit("after replace")
            real_print(*values, **kwargs)

        with patch.object(orchestrator.console, "print", side_effect=crash_after_replace):
            with self.assertRaises(SystemExit):
                self.approve(second_id)
        complete = second_path.read_bytes()
        self.assertEqual(json.loads(complete)["schema_version"], 10)
        self.approve(second_id)
        self.assertEqual(second_path.read_bytes(), complete)

    def test_r1_r2_read_surfaces_distinguish_states_and_redact_noncurrent(self) -> None:
        current = WorkflowRun(
            run_id="current",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="task-current",
            task_file="tasks/current.md",
            task_sha256="a" * 64,
            specification="CURRENT-SECRET-SPECIFICATION",
            repo=RepoState(repo="/fixture/repo", branch="main", head="b" * 40, clean=True, origin="https://example.invalid/repo.git"),
            resolved_correction_policy=resolve_correction_policy(),
        )
        orchestrator.persist(current, self.runs)
        historical = fixture_object(5)
        historical["run_id"] = "historical"
        historical["specification"] = "HISTORICAL-SECRET-SPECIFICATION"
        self.write_run(encoded(historical), "historical")
        blocked = fixture_object("6_base")
        blocked["run_id"] = "blocked"
        blocked["provider_runs"][0]["failure_kind"] = "timeout"
        self.write_run(encoded(blocked), "blocked")
        archive = copy.deepcopy(historical)
        archive["run_id"] = "archive"
        archive["unexpected"] = "ARCHIVE-SECRET"
        self.write_run(encoded(archive), "archive")
        future = copy.deepcopy(historical)
        future["run_id"] = "future"
        future["schema_version"] = 11
        self.write_run(encoded(future), "future")
        self.write_run(b"CORRUPT-SECRET", "corrupt")

        with patch.object(orchestrator, "RUNS", self.runs), orchestrator.console.capture() as capture:
            orchestrator.status(None)
        listing = capture.get()
        for state in ("CURRENT", "MIGRATION_REQUIRED", "RESUME_BLOCKED", "ARCHIVE_ONLY", "UNSUPPORTED"):
            self.assertIn(state, listing)
        for secret in ("CURRENT-SECRET", "HISTORICAL-SECRET", "ARCHIVE-SECRET", "CORRUPT-SECRET"):
            self.assertNotIn(secret, listing)

        with patch.object(orchestrator, "RUNS", self.runs), orchestrator.console.capture() as capture:
            orchestrator.status("current")
        self.assertIn("CURRENT-SECRET-SPECIFICATION", capture.get())
        with patch.object(orchestrator, "RUNS", self.runs), orchestrator.console.capture() as capture:
            orchestrator.status("historical")
        bounded = capture.get()
        self.assertIn("MIGRATION_REQUIRED", bounded)
        self.assertNotIn("HISTORICAL-SECRET-SPECIFICATION", bounded)

    def test_r3_migrated_record_refuses_report_and_all_controller_mutations(self) -> None:
        source = fixture_object("6_base")
        source["run_id"] = "migrated"
        _, path = self.write_run(encoded(source), "migrated")
        controller = orchestrator.Controller(Path("/not-inspected"), self.runs)
        with patch.object(
            orchestrator,
            "TargetCoordinator",
            side_effect=AssertionError("coordinator created"),
        ):
            with self.assertRaisesRegex(
                orchestrator.ControllerError,
                "migration_required",
            ):
                controller.resume("migrated")
        self.approve("migrated")
        saved = orchestrator.load_run("migrated", self.runs)
        self.assertIsNotNone(saved.migration_audit)

        with patch.object(orchestrator, "RUNS", self.runs), orchestrator.console.capture():
            with self.assertRaises(typer.Exit):
                orchestrator.report("migrated")

        with patch.object(orchestrator, "TargetCoordinator", side_effect=AssertionError("coordinator created")):
            for action in (
                lambda: controller.resume("migrated"),
                lambda: controller.approve_policy("migrated", "decision"),
                lambda: controller.recover_writer("migrated", "retry_restored", "note"),
                lambda: controller.release_target("migrated", "note"),
            ):
                with self.subTest(action=action):
                    with self.assertRaisesRegex(orchestrator.ControllerError, "migrated record disposition"):
                        action()
        self.assertEqual(json.loads(path.read_text())["schema_version"], 10)

    def test_p1_p2_p3_storage_safety_redaction_and_text_independence(self) -> None:
        source = fixture_object(5)
        source["specification"] = "SECRET provider timeout /Users/private diff"
        source["provider_runs"][0]["stdout"] = "quota exceeded timeout auth"
        run_id, path = self.write_run(encoded(source))
        self.runs.chmod(0o755)
        path.chmod(0o644)
        with orchestrator.console.capture() as capture:
            result = self.approve(run_id)
        self.assertIsNotNone(result)
        self.assertEqual(stat.S_IMODE(self.runs.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertNotIn("SECRET", capture.get())
        self.assertIsNone(result.provider_runs[0].failure_kind)

        outside = Path(self.temp.name) / "outside.json"
        outside.write_bytes(fixture_bytes(5))
        outside.chmod(0o600)
        link = self.runs / "linked.json"
        link.symlink_to(outside)
        with self.assertRaisesRegex(orchestrator.ControllerError, "symlink"):
            self.approve("linked")
        self.assertEqual(outside.read_bytes(), fixture_bytes(5))
        with self.assertRaisesRegex(orchestrator.ControllerError, "safe identifier"):
            self.approve("../escape")

    def test_b1_b2_b3_scope_and_compatibility_identifiers_remain(self) -> None:
        source = Path(orchestrator.__file__).read_text(encoding="utf-8")
        migration_source = Path(run_migrations.__file__).read_text(encoding="utf-8")
        combined = source + migration_source
        for excluded in ("bulk-migrate", "--force", "downgrade-run", "archive-run", "purge-run"):
            self.assertNotIn(excluded, combined)
        self.assertIn('os.environ.get("JOBS_REPO"', source)
        self.assertTrue((Path(__file__).parent / "src" / "jobs_orchestrator" / "__init__.py").is_file())
        compatibility_source = (
            Path(__file__).parent / "src" / "jobs_orchestrator" / "__init__.py"
        ).read_text(encoding="utf-8")
        self.assertIn('metadata.distribution("jobs-orchestrator")', compatibility_source)
        self.assertIn('"direct_url.json"', compatibility_source)
        self.assertEqual(
            [key for key in run_migrations.MIGRATION_REGISTRY],
            [
                (1, 2),
                (2, 3),
                (3, 4),
                (4, 5),
                (5, 6),
                (6, 7),
                (7, 8),
                (8, 9),
                (9, 10),
            ],
        )
        command_names = {
            command.name or command.callback.__name__.replace("_", "-")
            for command in orchestrator.app.registered_commands
        }
        self.assertIn("migrate-run", command_names)


class PersistedCorrectionPolicyMigrationTests(unittest.TestCase):
    def test_g25_m1_m2_v9_is_exact_and_gains_only_null_policy_audit(self) -> None:
        source = schema9_bytes()
        classification = run_migrations.classify_run_bytes(source)
        self.assertEqual(classification.schema_version, 9)
        self.assertEqual(classification.structural_class, "V9")
        self.assertEqual(classification.treatment, "migrate")
        self.assertEqual(run_migrations.migration_steps(9), ("9_to_10",))

        run = migrated(source)
        self.assertEqual(run.schema_version, 10)
        self.assertIsNone(run.resolved_correction_policy)
        audit = run.policy_migration_audit
        self.assertIsNotNone(audit)
        assert audit is not None
        self.assertEqual(audit.source_schema_version, 9)
        self.assertEqual(audit.source_structural_class, "V9")
        self.assertEqual(audit.source_sha256, hashlib.sha256(source).hexdigest())
        self.assertEqual(audit.applied_steps, ("9_to_10",))
        self.assertEqual(audit.reason_codes, ("missing_resolved_correction_policy",))
        serialized = run.model_dump_json().encode()
        current = run_migrations.classify_run_bytes(serialized)
        self.assertEqual((current.treatment, current.record_state), ("current", "RESUME_BLOCKED"))
        with self.assertRaisesRegex(orchestrator.ControllerError, "run execution refused"):
            orchestrator.Controller._require_executable(run)

    def test_g25_m3_historical_lineage_ends_in_policy_absence_without_inference(self) -> None:
        for version in range(1, 10):
            source = migration_source(version)
            with self.subTest(version=version):
                run = migrated(source)
                audit = run.policy_migration_audit
                self.assertIsNone(run.resolved_correction_policy)
                self.assertIsNotNone(audit)
                assert audit is not None
                self.assertEqual(audit.source_schema_version, version)
                self.assertEqual(
                    audit.applied_steps,
                    tuple(f"{item}_to_{item + 1}" for item in range(version, 10)),
                )
                self.assertIn("missing_resolved_correction_policy", audit.reason_codes)


class ReviewBackfillMigrationTests(unittest.TestCase):
    """Gate 2.4 M-matrix: exact V8 model, one adjacent 8_to_9 step, audit."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.runs = Path(self.temp.name) / "runs"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_run(self, source: bytes, run_id: str | None = None) -> tuple[str, Path]:
        classification = run_migrations.classify_run_bytes(source)
        resolved = run_id or classification.run_id
        if resolved is None:
            raise AssertionError("test fixture requires an explicit run id")
        self.runs.mkdir(mode=0o700, exist_ok=True)
        path = self.runs / f"{resolved}.json"
        path.write_bytes(source)
        path.chmod(0o600)
        return resolved, path

    def approve(self, run_id: str) -> WorkflowRun | None:
        return orchestrator.migrate_run_record(
            run_id,
            self.runs,
            approval=lambda _: True,
            now=lambda: "2026-08-02T12:00:00+00:00",
            migration_id=lambda: "migration-review-01",
        )

    def test_m1_registry_steps_remain_literal_and_v8_model_is_exact(self) -> None:
        registry_source = Path(run_migrations.__file__).read_text(encoding="utf-8")
        self.assertIn('"target_schema_version": 8', registry_source)
        self.assertIn("result[\"schema_version\"] = 8", registry_source)
        self.assertIn('"target_schema_version": 7', registry_source)
        for name in ("_step_6_to_7", "_step_7_to_8"):
            start = registry_source.index(f"def {name}")
            following = registry_source.index("def ", start + 1)
            self.assertNotIn(
                "CURRENT_RUN_SCHEMA_VERSION",
                registry_source[start:following],
            )
        self.assertEqual(len(run_migrations._HISTORICAL_MODELS), 9)
        self.assertEqual(run_migrations._HISTORICAL_MODELS[8].__name__, "_RunV8")

        historical = run_migrations._HISTORICAL_MODELS[8]
        fields = historical.model_fields
        self.assertIn("identity_migration_audit", fields)
        self.assertIn("provider_resume_identity", fields)
        self.assertIn("provider_resume_operation_id", fields)
        self.assertNotIn("review_records", fields)
        self.assertNotIn("review_migration_audit", fields)
        self.assertNotIn("unreadable_review_records", fields)

        historical_v9 = run_migrations._HISTORICAL_MODELS[9]
        self.assertEqual(historical_v9.__name__, "_RunV9")
        self.assertIn("review_records", historical_v9.model_fields)
        self.assertIn("review_migration_audit", historical_v9.model_fields)
        self.assertNotIn("resolved_correction_policy", historical_v9.model_fields)

        payload = schema8_object()
        self.assertEqual(
            run_migrations.classify_run_bytes(encoded(payload)).structural_class,
            "V8",
        )
        for version in (1, 2, 3, 4, 5):
            run_migrations._validate_historical(
                fixture_object(version),
                version,
            )
        run_migrations._validate_historical(fixture_object("6_base"), 6)
        run_migrations._validate_historical(schema7_object(), 7)
        run_migrations._validate_historical(payload, 8)

    def test_m2_v8_ordinary_record_backfills_parsed_records_only(self) -> None:
        spec_stdout = review_stdout("PASS", "PASS", "fixture spec pass.")
        impl_stdout = review_stdout(
            "FAIL",
            "IMPLEMENTATION_DEFECT",
            "fixture defect",
            "fixture-defect",
        )
        records = [
            v8_review_record("specification_review", spec_stdout),
            v8_review_record("implementation_review", impl_stdout),
            v8_review_record(
                "implementation_review",
                "truncated attempt",
                failure_kind="quota",
            ),
            v8_review_record(
                "implementation_review",
                impl_stdout,
                returncode=1,
            ),
        ]
        source = encoded(
            v8_ordinary_payload(
                records,
                spec_review=pass_payload(),
                implementation_review=fail_payload("fixture defect", "fixture-defect"),
            )
        )
        run_id, path = self.write_run(source)
        before = path.read_bytes()
        run = self.approve(run_id)
        self.assertIsNotNone(run)
        self.assertEqual(path.read_bytes() == before, False)

        self.assertEqual(run.schema_version, 10)
        self.assertIsNone(run.identity_migration_audit)
        self.assertIsNone(run.migration_audit)
        audit = run.review_migration_audit
        self.assertIsNotNone(audit)
        self.assertEqual(
            [item.provider_record_index for item in run.review_records],
            [0, 1],
        )
        self.assertEqual(run.unreadable_review_records, [])
        self.assertEqual(audit.parsed_count, 2)
        self.assertEqual(audit.unreadable_count, 0)
        self.assertEqual(audit.applied_steps, ("8_to_9",))
        self.assertEqual(audit.source_structural_class, "V8")
        self.assertEqual(audit.source_schema_version, 8)
        self.assertEqual(audit.source_sha256, hashlib.sha256(source).hexdigest())
        self.assertEqual(audit.disposition, "resume_eligibility_deferred")
        self.assertEqual(audit.reason_codes, ())
        self.assertEqual(
            run.spec_review.model_dump(mode="json"),
            pass_payload(),
        )
        self.assertEqual(
            run.implementation_review.model_dump(mode="json"),
            fail_payload("fixture defect", "fixture-defect"),
        )
        self.assertEqual(run.provider_runs[2].failure_kind, "quota")
        self.assertEqual(run.provider_runs[3].returncode, 1)

        with self.assertRaisesRegex(
            orchestrator.ControllerError,
            "migrated record disposition is resume_eligibility_deferred",
        ):
            orchestrator.Controller._require_executable(run)

    def test_m3_v1_through_v8_chain_preserves_prior_audits_and_appends_review(self) -> None:
        for version in range(1, 7):
            source = migration_source(version)
            run = migrated(source)
            with self.subTest(version=version):
                self.assertEqual(run.schema_version, 10)
                self.assertIsNotNone(run.migration_audit)
                self.assertIsNotNone(run.identity_migration_audit)
                audit = run.review_migration_audit
                self.assertIsNotNone(audit)
                self.assertEqual(audit.applied_steps[-1], "8_to_9")
                self.assertEqual(
                    audit.applied_steps,
                    tuple(
                        f"{item}_to_{item + 1}"
                        for item in range(version, 9)
                    ),
                )
                self.assertEqual(
                    run.identity_migration_audit.applied_steps,
                    audit.applied_steps[:-1],
                )
                self.assertEqual(
                    audit.source_schema_version,
                    version,
                )
                self.assertEqual(
                    audit.parsed_count,
                    len(run.review_records),
                )
                self.assertEqual(
                    audit.unreadable_count,
                    len(run.unreadable_review_records),
                )
                self.assertEqual(
                    run.migration_audit.applied_steps,
                    tuple(
                        f"{item}_to_{item + 1}"
                        for item in range(version, 7)
                    ),
                )

        migrated_v8 = migrated(schema8_bytes())
        self.assertEqual(
            migrated_v8.identity_migration_audit.applied_steps,
            ("6_to_7", "7_to_8"),
        )
        self.assertEqual(
            migrated_v8.review_migration_audit.applied_steps,
            ("8_to_9",),
        )

    def test_m4_v8_with_identity_audit_preserves_audit_and_appends_review_audit(self) -> None:
        payload = schema8_object()
        payload["run_id"] = "v8-carried-identity"
        original_identity = copy.deepcopy(payload["identity_migration_audit"])
        original_legacy = copy.deepcopy(payload["migration_audit"])
        source = encoded(payload)
        run = migrated(source)

        self.assertEqual(
            run.identity_migration_audit.model_dump(mode="json"),
            original_identity,
        )
        self.assertEqual(
            run.migration_audit.model_dump(mode="json"),
            original_legacy,
        )
        audit = run.review_migration_audit
        self.assertEqual(audit.source_schema_version, 8)
        self.assertEqual(audit.applied_steps, ("8_to_9",))
        self.assertEqual(audit.parsed_count, len(run.review_records))
        self.assertEqual(audit.unreadable_count, len(run.unreadable_review_records))
        self.assertNotEqual(audit.migration_id, original_identity["migration_id"])
        with self.assertRaisesRegex(
            orchestrator.ControllerError,
            "migrated record disposition",
        ):
            orchestrator.Controller._require_executable(run)
        self.assertEqual(run.identity_migration_audit.disposition, audit.disposition)

    def test_m5_current_v9_source_reports_already_current_without_rewrite(self) -> None:
        current = WorkflowRun(
            run_id="v9-current-m5",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="current",
            task_file="tasks/current.md",
            task_sha256="0" * 64,
            specification="Current schema fixture.",
            repo=RepoState(
                repo="/fixture/repo",
                branch="main",
                head="1" * 40,
                clean=True,
                origin="https://example.invalid/repo.git",
            ),
            resolved_correction_policy=resolve_correction_policy(),
        )
        source = current.model_dump_json().encode()
        run_id, path = self.write_run(source)
        before = path.stat()
        with orchestrator.console.capture() as capture:
            again = self.approve(run_id)
        self.assertIsNotNone(again)
        self.assertEqual(path.read_bytes(), source)
        after = path.stat()
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
        self.assertIn("already current", capture.get())

    def test_m6_unreadable_legacy_reviews_get_bounded_markers(self) -> None:
        records = [
            v8_review_record("specification_review", "truncated stdout"),
            v8_review_record("implementation_review", "{not valid json"),
            v8_review_record(
                "implementation_review",
                review_stdout(
                    "FAIL",
                    "IMPLEMENTATION_DEFECT",
                    "broken semantics",
                )
                .replace('"summary"', '"unexpected"')
                .replace('"broken semantics"', '"removed"'),
            ),
            v8_review_record("implementation_review", review_stdout("PASS", "PASS", "ok")),
        ]
        source = encoded(
            v8_ordinary_payload(records, spec_review=pass_payload())
        )
        run = migrated(source)
        self.assertEqual(len(run.unreadable_review_records), 3)
        self.assertEqual(
            [item.provider_record_index for item in run.unreadable_review_records],
            [0, 1, 2],
        )
        self.assertEqual(
            [item.reason_code for item in run.unreadable_review_records],
            [
                "invalid_review_envelope",
                "invalid_review_envelope",
                "invalid_review_schema",
            ],
        )
        self.assertEqual(
            [item.provider_record_index for item in run.review_records],
            [3],
        )
        self.assertEqual(
            run.review_migration_audit.parsed_count,
            1,
        )
        self.assertEqual(
            run.review_migration_audit.unreadable_count,
            3,
        )
        self.assertIn("current_review_unreadable", run.review_migration_audit.reason_codes)
        self.assertIsNone(run.implementation_review)
        classification = run_migrations.classify_run_bytes(
            run.model_dump_json().encode()
        )
        self.assertEqual(classification.record_state, "RESUME_BLOCKED")

    def test_m7_legacy_retry_pairs_and_failed_attempts_backfill_once(self) -> None:
        first = v8_review_record(
            "implementation_review",
            "{invalid first attempt",
        )
        first["retry_scheduled"] = True
        second = v8_review_record(
            "implementation_review",
            review_stdout("FAIL", "IMPLEMENTATION_DEFECT", "retried", "retry-key"),
        )
        failed = v8_review_record(
            "implementation_review",
            "partial transport output",
            failure_kind="unavailable",
        )
        source = encoded(
            v8_ordinary_payload(
                [first, second, failed],
                implementation_review=fail_payload("retried", "retry-key"),
            )
        )
        run = migrated(source)
        self.assertEqual(
            [item.provider_record_index for item in run.review_records],
            [1],
        )
        self.assertEqual(
            [item.provider_record_index for item in run.unreadable_review_records],
            [0],
        )
        self.assertEqual(
            run.unreadable_review_records[0].reason_code,
            "invalid_review_envelope",
        )
        self.assertEqual(
            run.review_migration_audit.parsed_count,
            1,
        )
        self.assertEqual(
            run.review_migration_audit.unreadable_count,
            1,
        )
        self.assertEqual(
            run.implementation_review.finding_key,
            "retry-key",
        )

    def test_m8_preserved_field_mismatch_records_bounded_reason_and_stays_blocked(self) -> None:
        parsed = review_stdout("PASS", "PASS", "actual pass")
        records = [v8_review_record("implementation_review", parsed)]
        field_absent = v8_ordinary_payload(
            records,
            implementation_review=None,
        )
        absent_run = migrated(encoded(field_absent))
        self.assertIn(
            "resume_review_field_absent",
            absent_run.review_migration_audit.reason_codes,
        )
        self.assertIsNone(absent_run.implementation_review)

        stale_field = v8_ordinary_payload(
            records,
            implementation_review=fail_payload("stale failure", "stale-key"),
        )
        stale_run = migrated(encoded(stale_field))
        self.assertIn(
            "current_review_unreadable",
            stale_run.review_migration_audit.reason_codes,
        )
        self.assertEqual(
            stale_run.implementation_review.finding_key,
            "stale-key",
        )
        for run in (absent_run, stale_run):
            classification = run_migrations.classify_run_bytes(
                run.model_dump_json().encode()
            )
            self.assertEqual(classification.record_state, "RESUME_BLOCKED")
            with self.assertRaisesRegex(
                orchestrator.ControllerError,
                "migrated record disposition",
            ):
                orchestrator.Controller._require_executable(run)

    def test_m9_backfill_incoherence_and_count_tampering_fail_closed(self) -> None:
        records = [
            v8_review_record(
                "implementation_write",
                review_stdout("PASS", "PASS", "not a review"),
            ),
        ]
        incoherent = v8_ordinary_payload(records)
        source = encoded(incoherent)
        run_id, path = self.write_run(source)
        with self.assertRaisesRegex(
            orchestrator.ControllerError,
            "migration_step_failed",
        ):
            self.approve(run_id)
        self.assertEqual(path.read_bytes(), source)
        self.assertEqual(len(run_migrations.classify_run_bytes(source).payload["provider_runs"]), 1)

        clean = v8_ordinary_payload(
            [
                v8_review_record(
                    "implementation_review",
                    review_stdout("PASS", "PASS", "ok"),
                )
            ],
            implementation_review=pass_payload(),
            run_id="v8-count-tamper",
        )
        clean_source = encoded(clean)
        clean_id, clean_path = self.write_run(clean_source, "v8-count-tamper")
        original = run_migrations.MIGRATION_REGISTRY[(8, 9)]

        def corrupt_counts(
            payload: dict[str, object],
            context: run_migrations.MigrationContext,
        ) -> tuple[dict[str, object], list[str]]:
            result, reasons = original(payload, context)
            result["review_migration_audit"]["parsed_count"] = 99
            return result, reasons

        run_migrations.MIGRATION_REGISTRY[(8, 9)] = corrupt_counts  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(
                orchestrator.ControllerError,
                "migration_step_failed",
            ):
                self.approve(clean_id)
        finally:
            run_migrations.MIGRATION_REGISTRY[(8, 9)] = original
        self.assertEqual(clean_path.read_bytes(), clean_source)

    def test_migrated_v9_refuses_execution_and_preserves_raw_review_stdout(self) -> None:
        impl_stdout = review_stdout(
            "FAIL",
            "IMPLEMENTATION_DEFECT",
            "preserved defect",
            "preserved-key",
        )
        source = encoded(
            v8_ordinary_payload(
                [
                    v8_review_record("specification_review", review_stdout("PASS", "PASS", "ok")),
                    v8_review_record("implementation_review", impl_stdout),
                ],
                spec_review=pass_payload(),
                implementation_review=fail_payload("preserved defect", "preserved-key"),
            )
        )
        run = migrated(source)
        self.assertEqual(run.provider_runs[1].stdout, impl_stdout)
        self.assertEqual(run.review_records[1].result.finding_key, "preserved-key")
        classification = run_migrations.classify_run_bytes(
            run.model_dump_json().encode()
        )
        self.assertEqual(classification.treatment, "current")
        self.assertEqual(classification.record_state, "RESUME_BLOCKED")
        self.assertEqual(
            classification.structural_class,
            run.review_migration_audit.source_structural_class,
        )
        with self.assertRaisesRegex(
            orchestrator.ControllerError,
            "run execution refused",
        ):
            orchestrator.Controller._require_executable(run)


if __name__ == "__main__":
    unittest.main()
