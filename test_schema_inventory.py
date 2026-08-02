"""Read-only provenance checks for the Gate 2.1 run-schema inventory."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import unittest


FIXTURES = Path(__file__).parent / "test_fixtures" / "run_schemas"
MANIFEST = FIXTURES / "manifest.json"
BASELINE = "e0f1ad9b99a127be8885f1bc2fada24aba0dc0ad"

SCHEMA_COMMITS = {
    1: "aa2120cb12cbad134cefccaed04a7f5a4de1dc47",
    2: "b7b90deffe460c3d91cd9c57062ea627346e86e6",
    3: "20d011a355c6f637529c25897d5e9631211a809e",
    4: "2aeb003ee623fb3ee4425523d987ae1a73f1fc14",
    5: "37411184c6fedd12511a72f0b82a52003d793b50",
    6: "f7ac5ecd697d7b9df0f97fffaafc1de2cc8fc169",
}

SCHEMA_6_COMMITS = {
    "V6-supervisor": "fdcfa930e5570e9b667d0005a01c21a3551c5bbf",
    "V6-provenance": "4a3262eda14e41c60a21b7b3d3d152dffe48a286",
    "V6-writer": "b7ca3cab3d0fef780c773ffd67903af3bd568270",
    "V6-owner": "ec7fc5828c5e6877a2b59f2f191ff05fd1396021",
    "V6-current": "04a34ae90fd50b4b2da3d4400c6735be6bd0e11b",
}

MATRIX_ROWS = [
    "H1",
    "H2",
    "H3",
    "V1",
    "V2",
    "V3",
    "V4",
    "V5",
    "V6A",
    "V6B",
    "V6C",
    "V6D",
    "V6E",
    "V6F",
    "I1",
    "I2",
    "I3",
    "I4",
    "U1",
    "U2",
    "U3",
    "P1",
    "P2",
    "A1",
    "B1",
    "B2",
    "B3",
]


class InventoryEnvelopeError(ValueError):
    """Test-only rejection for an unsupported fixture envelope."""


def _reject_constant(value: str) -> None:
    raise InventoryEnvelopeError(f"unsupported numeric token: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InventoryEnvelopeError("duplicate object key")
        result[key] = value
    return result


def strict_object(payload: bytes) -> dict[str, object]:
    """Decode only standard UTF-8 JSON objects with unique keys."""

    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryEnvelopeError("invalid JSON envelope") from exc
    if not isinstance(value, dict):
        raise InventoryEnvelopeError("top level must be an object")
    return value


def classify_declared_version(payload: bytes) -> str:
    """Test the approved header treatment without loading a runtime model."""

    try:
        value = strict_object(payload)
    except InventoryEnvelopeError:
        return "unsupported"
    version = value.get("schema_version")
    if type(version) is not int or version <= 0:
        return "unsupported"
    if version in range(1, 6):
        return "migrate"
    if version == 6:
        return "compatible"
    return "unsupported"


def bounded_diagnostic(code: str, field_path: str | None = None) -> str:
    """Represent only the metadata that inventory output may disclose."""

    return code if field_path is None else f"{code}:{field_path}"


class HistoricalSchemaInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest_bytes = MANIFEST.read_bytes()
        cls.manifest = strict_object(cls.manifest_bytes)
        cls.declared = cls.manifest["declared_schemas"]
        cls.generations = cls.manifest["schema_6_generations"]

    def fixture_bytes(self, entry: dict[str, object]) -> bytes:
        return (FIXTURES / str(entry["fixture"])).read_bytes()

    def fixture_object(self, entry: dict[str, object]) -> dict[str, object]:
        return strict_object(self.fixture_bytes(entry))

    def test_manifest_records_exact_history_and_scope(self) -> None:
        self.assertEqual(self.manifest["inventory_version"], 1)
        self.assertEqual(self.manifest["baseline_commit"], BASELINE)
        self.assertEqual(
            {
                entry["schema_version"]: entry["source_commit"]
                for entry in self.declared
            },
            SCHEMA_COMMITS,
        )
        self.assertEqual(
            {entry["class_id"]: entry["source_commit"] for entry in self.generations},
            SCHEMA_6_COMMITS,
        )
        scope = self.manifest["scope_boundaries"]
        self.assertFalse(scope["runtime_changes"])
        self.assertFalse(scope["private_run_access"])
        self.assertFalse(scope["jobs_access"])
        self.assertFalse(scope["provider_access"])
        self.assertFalse(scope["git_subprocess_in_fixture_tests"])
        self.assertEqual(
            scope["compatibility_identifiers_unchanged"],
            ["jobs-orchestrator", "JOBS_REPO", "src/jobs_orchestrator"],
        )

    def test_fixture_checksums_and_synthetic_provenance(self) -> None:
        entries = [*self.declared, *self.generations]
        fixture_entries = [entry for entry in entries if "fixture" in entry]
        self.assertEqual(len(fixture_entries), 7)
        for entry in fixture_entries:
            payload = self.fixture_bytes(entry)
            self.assertEqual(hashlib.sha256(payload).hexdigest(), entry["sha256"])
            self.assertNotIn(b"/Users/", payload)
            self.assertNotIn(b"Documents/my-apps", payload)
            self.assertNotIn(b"github.com/mbuckingham74", payload)
            self.assertIn(b"/fixture/repo", payload)
            self.assertIn(b"example.invalid", payload)
        provenance = self.manifest["provenance"]
        self.assertIn("No record content came from runs/", provenance)
        self.assertIn("Jobs", provenance)

    def test_declared_schema_structural_transitions_are_explicit(self) -> None:
        fixtures = {
            entry["schema_version"]: self.fixture_object(entry)
            for entry in self.declared
        }
        for version, fixture in fixtures.items():
            self.assertEqual(fixture["schema_version"], version)
            self.assertEqual(classify_declared_version(self.fixture_bytes(self.declared[version - 1])), "migrate" if version < 6 else "compatible")

        self.assertNotIn("sol_guidance", fixtures[1])
        self.assertIn("sol_guidance", fixtures[2])
        self.assertNotIn("finding_key", fixtures[2]["implementation_review"])
        self.assertIn("finding_key", fixtures[3]["implementation_review"])
        self.assertNotIn("policy_decisions", fixtures[3])
        self.assertIn("policy_decisions", fixtures[4])
        self.assertNotIn("updated_at", fixtures[4])
        self.assertIn("updated_at", fixtures[5])
        self.assertNotIn("duration_seconds", fixtures[4]["provider_runs"][0])
        self.assertIn("duration_seconds", fixtures[5]["provider_runs"][0])
        self.assertNotIn("provider_resume_stage", fixtures[5])
        self.assertIn("provider_resume_stage", fixtures[6])
        self.assertNotIn("failure_kind", fixtures[5]["provider_runs"][0])
        self.assertIn("failure_kind", fixtures[6]["provider_runs"][0])

        self.assertEqual(
            [entry["treatment"] for entry in self.declared[:5]],
            ["migrate"] * 5,
        )
        self.assertTrue(
            all(
                entry["disposition"] == "resume_eligibility_deferred"
                for entry in self.declared
            )
        )

    def test_schema_six_generations_preserve_absence_and_block_unsafe_state(self) -> None:
        base = self.fixture_object(self.declared[-1])
        current_entry = next(
            entry for entry in self.generations if entry["class_id"] == "V6-current"
        )
        current = self.fixture_object(current_entry)

        for failure_kind in ("timeout", "interrupted"):
            derived = copy.deepcopy(base)
            derived["provider_runs"][0]["failure_kind"] = failure_kind
            self.assertEqual(derived["provider_runs"][0]["failure_kind"], failure_kind)

        without_provenance = copy.deepcopy(base["provider_runs"][0])
        self.assertNotIn("failure_source", without_provenance)
        with_provenance = copy.deepcopy(without_provenance)
        with_provenance["failure_source"] = "provider_native"
        with_provenance["failure_code"] = "fixture_native_code"
        self.assertEqual(with_provenance["failure_source"], "provider_native")
        self.assertNotIn("failure_source", without_provenance)

        active = current["active_writer_attempt"]
        self.assertEqual(active["provider_record_index"], 1)
        self.assertEqual(current["provider_runs"][1]["capability"], "workspace_write")
        incoherent = copy.deepcopy(current)
        incoherent["active_writer_attempt"]["provider_record_index"] = 99
        self.assertGreaterEqual(
            incoherent["active_writer_attempt"]["provider_record_index"],
            len(incoherent["provider_runs"]),
        )

        legacy_owner = copy.deepcopy(current)
        legacy_owner.pop("target_ownership")
        self.assertNotIn("target_ownership", legacy_owner)
        contradictory_owner = copy.deepcopy(current)
        contradictory_owner["target_ownership"]["released_at"] = (
            "2026-08-02T00:02:00+00:00"
        )
        self.assertIsNone(contradictory_owner["target_ownership"]["release_reason"])

        generation_treatments = {
            entry["class_id"]: (entry["treatment"], entry["disposition"])
            for entry in self.generations
        }
        self.assertEqual(
            generation_treatments["V6-supervisor"],
            ("compatible_when_valid", "resume_blocked"),
        )
        self.assertEqual(
            generation_treatments["V6-writer"],
            ("compatible_or_archive", "resume_blocked_when_incoherent"),
        )
        self.assertEqual(
            generation_treatments["V6-owner"],
            ("compatible_or_archive", "inspection_only_when_incoherent"),
        )

    def test_invalid_versions_are_never_coerced_or_guessed(self) -> None:
        base = self.fixture_object(self.declared[-1])
        invalid_versions = [None, True, False, "6", 6.0, 0, -1, 7, 999]
        for version in invalid_versions:
            candidate = copy.deepcopy(base)
            candidate["schema_version"] = version
            payload = json.dumps(candidate, allow_nan=False).encode()
            self.assertEqual(classify_declared_version(payload), "unsupported")

        missing = copy.deepcopy(base)
        missing.pop("schema_version")
        self.assertEqual(
            classify_declared_version(json.dumps(missing).encode()),
            "unsupported",
        )

    def test_invalid_json_envelopes_are_rejected_before_model_validation(self) -> None:
        unsupported = [
            b'{"schema_version": 6',
            b'{"schema_version": 6, "schema_version": 5}',
            b"[]",
            b'"fixture"',
            b"\xff",
            b'{"schema_version": NaN}',
            b'{"schema_version": Infinity}',
        ]
        for payload in unsupported:
            with self.assertRaises(InventoryEnvelopeError):
                strict_object(payload)
            self.assertEqual(classify_declared_version(payload), "unsupported")

    def test_manifest_covers_every_approved_matrix_row_and_invalid_class(self) -> None:
        self.assertEqual(self.manifest["matrix_rows"], MATRIX_ROWS)
        self.assertEqual(len(set(self.manifest["matrix_rows"])), len(MATRIX_ROWS))
        invalid = {
            entry["class_id"]: entry for entry in self.manifest["invalid_classes"]
        }
        self.assertEqual(
            set(invalid),
            {
                "known-version-invalid",
                "unversioned-or-invalid-version",
                "future-or-unknown-version",
                "invalid-json-envelope",
            },
        )
        self.assertEqual(
            invalid["unversioned-or-invalid-version"]["treatment"],
            "unsupported",
        )
        self.assertIn(
            "duplicate-key",
            invalid["invalid-json-envelope"]["derivations"],
        )
        self.assertIn(
            "writer-link-out-of-range",
            invalid["known-version-invalid"]["derivations"],
        )

    def test_derived_cases_are_in_memory_repeatable_and_source_immutable(self) -> None:
        entry = self.declared[-1]
        source_before = self.fixture_bytes(entry)
        source_hash = hashlib.sha256(source_before).hexdigest()

        first = self.fixture_object(entry)
        second = self.fixture_object(entry)
        first["provider_runs"][0]["failure_kind"] = "timeout"
        second["provider_runs"][0]["failure_kind"] = "timeout"
        self.assertEqual(first, second)

        with self.assertRaisesRegex(RuntimeError, "synthetic derivation failure"):
            interrupted = self.fixture_object(entry)
            interrupted["provider_runs"][0]["failure_kind"] = "interrupted"
            raise RuntimeError("synthetic derivation failure")

        source_after = self.fixture_bytes(entry)
        self.assertEqual(source_after, source_before)
        self.assertEqual(hashlib.sha256(source_after).hexdigest(), source_hash)

    def test_bounded_diagnostics_do_not_disclose_fixture_content(self) -> None:
        secret_like_values = [
            "secret-looking prompt",
            "HTTP 503 provider prose",
            "diff --git a/private b/private",
            "/private/operator/path",
        ]
        diagnostics = [
            bounded_diagnostic("unsupported_schema", "schema_version"),
            bounded_diagnostic("invalid_field", "provider_runs.0.failure_kind"),
            bounded_diagnostic("invalid_json"),
        ]
        rendered = "\n".join(diagnostics)
        for value in secret_like_values:
            self.assertNotIn(value, rendered)
        self.assertEqual(
            diagnostics,
            [
                "unsupported_schema:schema_version",
                "invalid_field:provider_runs.0.failure_kind",
                "invalid_json",
            ],
        )


if __name__ == "__main__":
    unittest.main()
