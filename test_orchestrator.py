import _thread
import hashlib
import itertools
import json
import math
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import orchestrator
import providers
import run_migrations
import typer
from models import (
    ADVERSARIAL_REVIEW_ROUTE,
    IMPLEMENTATION_ROUTE,
    IdentityMigrationAudit,
    POLICY_AUTHORITY_ROUTE,
    PolicyMigrationAudit,
    ProviderRouteIdentity,
    RepoState,
    ReviewMigrationAudit,
    ReviewRecord,
    ReviewResult,
    ResolvedCorrectionPolicy,
    TargetOwnership,
    UnreadableReviewRecord,
    WorkflowRun,
    WriterAttemptState,
    WriterRecoveryDecision,
    resolve_correction_policy,
)
from providers import (
    ProviderAttempt,
    ProviderExecution,
    build_luna_command,
    build_sol_command,
    build_sonnet_command,
)


CLAUDE_FIXTURES = Path(__file__).parent / "test_fixtures/claude"
_TEST_PROVIDER_INVOCATIONS = itertools.count(1)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def review_execution(
    status: str,
    category: str,
    summary: str = "ok",
    finding_key: str | None = None,
) -> ProviderExecution:
    if finding_key is None:
        finding_key = "PASS" if category == "PASS" else "test-finding"
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "structured_output": {
            "status": status,
            "category": category,
            "finding_key": finding_key,
            "summary": summary,
        }
    }
    return ProviderExecution(["claude"], 0, json.dumps(payload), "")


def claude_fixture(name: str) -> tuple[dict[str, object], ProviderExecution]:
    fixture = json.loads(
        (CLAUDE_FIXTURES / f"{name}.json").read_text(encoding="utf-8")
    )
    expected = fixture["provenance"]["stdout_sha256"]
    actual = hashlib.sha256(fixture["stdout"].encode()).hexdigest()
    if actual != expected:
        raise AssertionError(f"fixture checksum mismatch: {name}")
    return fixture, ProviderExecution(
        ["claude"],
        fixture["returncode"],
        fixture["stdout"],
        fixture["stderr"],
    )


def provider_record(
    identity,
    operation_id,
    *,
    capability=None,
    **kwargs,
):
    if capability is None:
        capability = (
            "workspace_write"
            if identity.role_id == "implementation"
            else "read_only"
        )
    kwargs.setdefault(
        "logical_invocation_id",
        f"test-invocation-{next(_TEST_PROVIDER_INVOCATIONS)}",
    )
    kwargs.setdefault("physical_attempt_ordinal", 1)
    return orchestrator.ProviderRecord(
        identity=identity,
        operation_id=operation_id,
        capability=capability,
        **kwargs,
    )


def provider_resume(
    stage: str,
    prompt: str,
    identity,
    operation_id: str,
) -> dict[str, object]:
    return {
        "provider_resume_stage": stage,
        "provider_resume_prompt": prompt,
        "provider_resume_identity": identity,
        "provider_resume_operation_id": operation_id,
    }


class ControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "jobs"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.email", "tests@example.invalid")
        git(self.repo, "config", "user.name", "Controller Tests")
        git(self.repo, "remote", "add", "origin", "https://example.invalid/jobs.git")
        (self.repo / "tasks").mkdir()
        (self.repo / "tasks/009-example.md").write_text("Implement the example task.\n")
        (self.repo / "README.md").write_text("fixture\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "fixture")
        self.runs = Path(self.temp.name) / "runs"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def controller(
        self,
        sonnet,
        terra=None,
        sol=None,
        luna=None,
        approval=None,
        approval_actor=None,
        runs_dir=None,
    ):
        return orchestrator.Controller(
            self.repo,
            runs_dir or self.runs,
            sonnet=sonnet,
            terra=terra or (lambda prompt, repo: ProviderExecution(["codex"], 0, "resolved", "")),
            sol=sol or (lambda prompt, repo: ProviderExecution(["codex"], 0, "GUIDANCE: inspect the remaining defect", "")),
            luna=luna or self.luna,
            approval=approval,
            approval_actor=approval_actor,
        )

    def luna(self, prompt: str, repo: Path) -> ProviderExecution:
        (repo / "implementation.py").write_text("# implementation\n")
        return ProviderExecution(["codex"], 0, "implemented", "")

    def passing_sonnet(self, prompt: str, repo: Path) -> ProviderExecution:
        return review_execution("PASS", "PASS")

    def test_dirty_repo_blocks_start(self) -> None:
        (self.repo / "uncommitted.txt").write_text("dirty\n")
        controller = self.controller(self.passing_sonnet)

        run = controller.new_run("009")

        self.assertEqual(run.stage, "blocked_dirty_repo")
        self.assertEqual(list(self.runs.glob("*.json")) != [], True)

    def test_task_resolution_ambiguity_fails(self) -> None:
        (self.repo / "tasks/009-second.md").write_text("another task\n")

        with self.assertRaisesRegex(orchestrator.ControllerError, "ambiguous"):
            orchestrator.resolve_task(self.repo, "009")

    def test_special_paths_are_exact_in_enumeration_review_fingerprint_and_staging(self) -> None:
        paths = [
            "unicode-名称.txt",
            "space name.txt",
            'embedded-"quote".txt',
            "literal -> arrow.txt",
            'combined 名称 " -> value.txt',
        ]
        for index, relative in enumerate(paths):
            (self.repo / relative).write_text(f"special content {index}\n")

        self.assertEqual(orchestrator.changed_files(self.repo), sorted(paths))
        review_diff = orchestrator._implementation_diff(self.repo)
        for relative in paths:
            self.assertIn(f"--- untracked: {relative}", review_diff)

        before = orchestrator.working_tree_fingerprint(self.repo)
        (self.repo / paths[0]).write_text("changed bytes\n")
        after = orchestrator.working_tree_fingerprint(self.repo)
        self.assertNotEqual(before, after)

        git(self.repo, "add", "-A", "--", *paths)
        self.assertEqual(orchestrator.changed_files(self.repo), sorted(paths))

    def test_porcelain_parser_handles_target_source_order_and_malformed_records(self) -> None:
        raw = (
            "R  target -> 名称.txt\0source name.txt\0"
            "C  copy target.txt\0copy source.txt\0"
            '?? embedded-"quote".txt\0'
        ).encode()

        self.assertEqual(
            orchestrator._parse_porcelain_v1_z(raw),
            sorted(
                [
                    "target -> 名称.txt",
                    "source name.txt",
                    "copy target.txt",
                    "copy source.txt",
                    'embedded-"quote".txt',
                ]
            ),
        )

        malformed = (
            b"?",
            b"Z? path\0",
            b"??\tpath\0",
            b"?? \0",
            b"?? unterminated",
            b"R  target\0",
            b"C  target\0\0",
        )
        for payload in malformed:
            with self.subTest(payload=payload):
                with self.assertRaises(orchestrator.ControllerError):
                    orchestrator._parse_porcelain_v1_z(payload)

        with self.assertRaises(orchestrator.ControllerError):
            orchestrator._parse_porcelain_v1_z(b"?? invalid-\xff.txt\0")

    def test_git_bytes_failure_raises_controller_error(self) -> None:
        missing = Path(self.temp.name) / "not-a-repository"
        missing.mkdir()

        with self.assertRaisesRegex(orchestrator.ControllerError, "git status"):
            orchestrator._git_bytes(
                missing,
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            )

    def test_real_git_rename_and_copy_records_are_target_then_source(self) -> None:
        source = self.repo / "copy source.txt"
        old = self.repo / 'old "name".txt'
        source.write_text("copy content\n")
        old.write_text("rename content\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "add rename and copy fixtures")

        copied = self.repo / "copied -> 名称.txt"
        copied.write_bytes(source.read_bytes())
        source.write_text("copy content\nsource changed\n")
        target = self.repo / 'new -> 名称 "name".txt'
        old.rename(target)
        git(self.repo, "add", "-A")
        git(self.repo, "config", "status.renames", "copies")

        raw = orchestrator._git_bytes(
            self.repo,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ).stdout
        self.assertIn(
            b"C  " + copied.name.encode() + b"\0" + source.name.encode() + b"\0",
            raw,
        )
        self.assertIn(
            b"R  " + target.name.encode() + b"\0" + old.name.encode() + b"\0",
            raw,
        )
        self.assertEqual(
            orchestrator.changed_files(self.repo),
            sorted([source.name, copied.name, old.name, target.name]),
        )
        self.assertEqual(
            orchestrator._stageable_changed_files(
                self.repo,
                orchestrator.changed_files(self.repo),
            ),
            sorted([source.name, copied.name, target.name]),
        )

    def test_mixed_git_states_are_complete_deduplicated_and_stageable(self) -> None:
        tracked = {
            "unstaged.txt": "base\n",
            "staged.txt": "base\n",
            "mixed.txt": "base\n",
            "delete unstaged.txt": "base\n",
            "delete staged.txt": "base\n",
            "rename old.txt": "base\n",
        }
        for relative, content in tracked.items():
            (self.repo / relative).write_text(content)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "add mixed-state fixtures")

        (self.repo / "unstaged.txt").write_text("unstaged\n")
        (self.repo / "staged.txt").write_text("staged\n")
        git(self.repo, "add", "staged.txt")
        (self.repo / "mixed.txt").write_text("index\n")
        git(self.repo, "add", "mixed.txt")
        (self.repo / "mixed.txt").write_text("worktree\n")
        (self.repo / "delete unstaged.txt").unlink()
        (self.repo / "delete staged.txt").unlink()
        git(self.repo, "add", "-A", "--", "delete staged.txt")
        (self.repo / "untracked 名称.txt").write_text("untracked\n")
        (self.repo / "added staged.txt").write_text("added\n")
        git(self.repo, "add", "added staged.txt")
        (self.repo / "rename old.txt").rename(self.repo / "rename new -> value.txt")
        git(self.repo, "add", "-A", "--", "rename old.txt", "rename new -> value.txt")

        expected = sorted(
            [
                "unstaged.txt",
                "staged.txt",
                "mixed.txt",
                "delete unstaged.txt",
                "delete staged.txt",
                "untracked 名称.txt",
                "added staged.txt",
                "rename old.txt",
                "rename new -> value.txt",
            ]
        )
        self.assertEqual(orchestrator.changed_files(self.repo), expected)

        stageable = orchestrator._stageable_changed_files(self.repo, expected)
        self.assertEqual(
            stageable,
            [
                "added staged.txt",
                "delete unstaged.txt",
                "mixed.txt",
                "rename new -> value.txt",
                "staged.txt",
                "unstaged.txt",
                "untracked 名称.txt",
            ],
        )
        git(self.repo, "add", "-A", "--", *stageable)
        raw = orchestrator._git_bytes(
            self.repo,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ).stdout
        cursor = 0
        while cursor < len(raw):
            self.assertEqual(raw[cursor + 1 : cursor + 2], b" ")
            path_end = raw.index(b"\0", cursor + 3)
            status = raw[cursor : cursor + 2]
            cursor = path_end + 1
            if status[0] in b"RC" or status[1] in b"RC":
                cursor = raw.index(b"\0", cursor) + 1

    def test_deleted_path_identity_changes_legacy_fingerprint_and_blocks_resume(self) -> None:
        deleted = self.repo / 'deleted -> "名称".txt'
        deleted.write_text("delete me\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "add deletion fixture")
        snapshot = orchestrator.repo_state(self.repo)
        deleted.unlink()

        legacy = hashlib.sha256()
        legacy.update(
            orchestrator._git(
                self.repo,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ).stdout.encode()
        )
        for command in (
            ("diff", "--no-ext-diff", "--binary"),
            ("diff", "--cached", "--no-ext-diff", "--binary"),
        ):
            legacy.update(orchestrator._git(self.repo, *command).stdout.encode())

        corrected = orchestrator.working_tree_fingerprint(self.repo)
        self.assertNotEqual(legacy.hexdigest(), corrected)

        run = self.controller(self.passing_sonnet, approval=lambda prompt: False).new_run("009")
        run.repo = snapshot
        run.stage = "commit_declined"
        run.changed_files = ['"deleted -> \\345\\220\\215\\347\\247\\260.txt"']
        run.working_tree_fingerprint = legacy.hexdigest()
        orchestrator.persist(run, self.runs)

        with self.assertRaisesRegex(orchestrator.ControllerError, "working tree"):
            self.controller(self.passing_sonnet).resume(run.run_id)

    def test_special_path_round_trips_run_state_and_controller_staging(self) -> None:
        special = 'implementation 名称 " -> result.py'
        review_prompts = []

        def luna(prompt, repo):
            (repo / special).write_text("# implementation\n")
            return ProviderExecution(["codex"], 0, "implemented", "")

        def sonnet(prompt, repo):
            review_prompts.append(prompt)
            return review_execution("PASS", "PASS")

        approvals = iter([True, False])
        run = self.controller(
            sonnet,
            luna=luna,
            approval=lambda prompt: next(approvals),
        ).new_run("009")

        self.assertEqual(run.stage, "push_declined")
        self.assertEqual(run.changed_files, [special])
        self.assertIn(special, review_prompts[-1])
        self.assertEqual(
            orchestrator.load_run(run.run_id, self.runs).changed_files,
            [special],
        )
        stage = next(
            record
            for record in run.git_operations
            if record.operation == "stage changed files"
        )
        self.assertEqual(stage.returncode, 0)
        self.assertIn(special, stage.command)
        committed_names = subprocess.run(
            ["git", "-C", str(self.repo), "show", "--format=", "--name-only", "-z", "HEAD"],
            capture_output=True,
            check=True,
        ).stdout.split(b"\0")
        self.assertIn(special.encode(), committed_names)

    def test_resume_guard_detects_special_path_change_before_provider_or_staging(self) -> None:
        special = 'implementation 名称 " -> result.py'
        provider_calls = 0

        def luna(prompt, repo):
            nonlocal provider_calls
            provider_calls += 1
            (repo / special).write_text("# implementation\n")
            return ProviderExecution(["codex"], 0, "implemented", "")

        run = self.controller(
            self.passing_sonnet,
            luna=luna,
            approval=lambda prompt: False,
        ).new_run("009")
        calls_before_resume = provider_calls
        (self.repo / special).write_text("# changed after verification\n")

        with self.assertRaisesRegex(orchestrator.ControllerError, "working tree"):
            self.controller(
                self.passing_sonnet,
                luna=luna,
                approval=lambda prompt: True,
            ).resume(run.run_id)

        self.assertEqual(provider_calls, calls_before_resume)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), run.repo.head)
        self.assertEqual(run.git_operations, [])

    def test_already_staged_deletion_commits_without_fabricated_add_record(self) -> None:
        deleted = self.repo / 'already staged -> "名称".txt'
        deleted.write_text("delete me\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "add staged deletion fixture")
        snapshot = orchestrator.repo_state(self.repo)
        deleted.unlink()
        git(self.repo, "add", "-A", "--", deleted.name)

        run = WorkflowRun(
            run_id="staged-delete",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="0" * 64,
            specification="Delete the fixture.",
            resolved_correction_policy=resolve_correction_policy(),
            repo=snapshot,
            stage="awaiting_commit_approval",
            changed_files=[deleted.name],
            working_tree_fingerprint=orchestrator.working_tree_fingerprint(self.repo),
        )
        approvals = iter([True, False])

        result = self.controller(
            self.passing_sonnet,
            approval=lambda prompt: next(approvals),
        )._approval_gates(run)

        self.assertEqual(result.stage, "push_declined")
        self.assertFalse(deleted.exists())
        self.assertIsNotNone(result.commit_hash)
        self.assertNotIn(
            "stage changed files",
            [record.operation for record in result.git_operations],
        )

    def test_verification_crash_boundaries_resume_without_repeating_writer(self) -> None:
        luna_calls = 0
        special = "crash resume 名称.txt"

        def luna(prompt, repo):
            nonlocal luna_calls
            luna_calls += 1
            (repo / special).write_text("implementation\n")
            return ProviderExecution(["codex"], 0, "implemented", "")

        controller = self.controller(
            self.passing_sonnet,
            luna=luna,
            approval=lambda prompt: False,
        )
        completed = controller.new_run("009")
        calls_after_implementation = luna_calls

        before_save = completed.model_copy(deep=True)
        before_save.run_id = "before-verification-save"
        before_save.stage = "implementation_completed"
        before_save.changed_files = []
        before_save.working_tree_fingerprint = None
        before_save.target_ownership = None
        before_runs = self.runs / "before-verification-save"
        orchestrator.persist(before_save, before_runs)
        resumed_before = self.controller(
            self.passing_sonnet,
            luna=luna,
            approval=lambda prompt: False,
            runs_dir=before_runs,
        ).resume(before_save.run_id)
        self.assertEqual(resumed_before.stage, "commit_declined")
        self.assertEqual(luna_calls, calls_after_implementation)
        self.assertEqual(resumed_before.changed_files, [special])

        after_save = completed.model_copy(deep=True)
        after_save.run_id = "after-verification-save"
        after_save.stage = "implementation_verified"
        after_save.target_ownership = None
        after_runs = self.runs / "after-verification-save"
        orchestrator.persist(after_save, after_runs)
        resumed_after = self.controller(
            self.passing_sonnet,
            luna=luna,
            approval=lambda prompt: False,
            runs_dir=after_runs,
        ).resume(after_save.run_id)
        self.assertEqual(resumed_after.stage, "commit_declined")
        self.assertEqual(luna_calls, calls_after_implementation)

    def test_change_enumeration_failure_blocks_before_writer_invocation(self) -> None:
        sonnet_calls = 0

        def sonnet(prompt, repo):
            nonlocal sonnet_calls
            sonnet_calls += 1
            return review_execution("PASS", "PASS")

        with patch.object(
            orchestrator,
            "_git_bytes",
            side_effect=orchestrator.ControllerError("malformed porcelain fixture"),
        ):
            run = self.controller(sonnet, approval=lambda prompt: False).new_run("009")

        self.assertEqual(run.stage, "blocked_writer_state_unknown")
        self.assertEqual(sonnet_calls, 1)
        self.assertNotIn("change_enumeration", run.verification)
        self.assertIn("malformed porcelain fixture", run.last_error)
        self.assertEqual(
            [record.identity.display_name for record in run.provider_runs],
            ["Sonnet 5 High"],
        )
        self.assertEqual(run.git_operations, [])

    def test_staging_failure_is_audited_and_blocks_commit(self) -> None:
        original_git = orchestrator._git

        def fail_add(repo, *args, check=True):
            if args and args[0] == "add":
                return subprocess.CompletedProcess(
                    ["git", "-C", str(repo), *args],
                    128,
                    "",
                    "synthetic staging failure",
                )
            return original_git(repo, *args, check=check)

        with patch.object(orchestrator, "_git", side_effect=fail_add):
            run = self.controller(
                self.passing_sonnet,
                approval=lambda prompt: True,
            ).new_run("009")

        self.assertEqual(run.stage, "blocked_git_failure")
        self.assertIsNone(run.commit_hash)
        self.assertEqual(run.git_operations[-1].operation, "stage changed files")
        self.assertEqual(run.git_operations[-1].returncode, 128)

    def test_resume_snapshot_mismatch_blocks(self) -> None:
        run = self.controller(self.passing_sonnet, approval=lambda prompt: False).new_run("009")
        saved = orchestrator.load_run(run.run_id, self.runs)
        saved.repo = RepoState(
            repo=saved.repo.repo,
            branch="different-branch",
            head=saved.repo.head,
            clean=saved.repo.clean,
            origin=saved.repo.origin,
        )
        orchestrator.persist(saved, self.runs)

        with self.assertRaisesRegex(orchestrator.ControllerError, "branch or origin"):
            self.controller(self.passing_sonnet).resume(run.run_id)

    def test_pass_advances_to_commit_gate(self) -> None:
        approvals = iter([False])
        run = self.controller(
            self.passing_sonnet,
            approval=lambda prompt: next(approvals),
        ).new_run("009")

        self.assertEqual(run.stage, "commit_declined")
        self.assertEqual(run.correction_cycles, 0)
        self.assertEqual(run.implementation_review.category, "PASS")

    def test_g26_commit_decline_and_retry_append_immutable_records(self) -> None:
        first = self.controller(
            self.passing_sonnet,
            approval=lambda _: False,
            approval_actor=lambda: "local-os-uid:4242",
        ).new_run("009")
        self.assertEqual(first.stage, "commit_declined")
        self.assertEqual(len(first.approval_requests), 1)
        self.assertEqual(len(first.approval_decisions), 1)
        initial = first.approval_decisions[0]
        self.assertEqual(initial.decision, "declined")
        self.assertEqual(initial.decided_by, "local-os-uid:4242")
        self.assertEqual(
            initial.decided_fingerprint,
            first.approval_requests[0].requested_fingerprint,
        )

        retried = self.controller(
            self.passing_sonnet,
            approval=lambda _: False,
            approval_actor=lambda: "local-os-uid:4242",
        ).resume(first.run_id)
        self.assertEqual(retried.stage, "commit_declined")
        self.assertEqual(len(retried.approval_requests), 2)
        self.assertEqual(len(retried.approval_decisions), 2)
        self.assertEqual(retried.approval_decisions[0], initial)

    def test_g26_approved_commit_and_declined_push_are_audited(self) -> None:
        answers = iter([True, False])
        run = self.controller(
            self.passing_sonnet,
            approval=lambda _: next(answers),
            approval_actor=lambda: "local-os-uid:4242",
        ).new_run("009")
        self.assertEqual(run.stage, "push_declined")
        self.assertEqual([request.gate for request in run.approval_requests], ["commit", "push"])
        self.assertEqual(
            [(decision.gate, decision.decision) for decision in run.approval_decisions],
            [("commit", "approved"), ("push", "declined")],
        )
        self.assertNotEqual(
            run.approval_requests[0].requested_fingerprint,
            run.approval_requests[1].requested_fingerprint,
        )

    def test_policy_ambiguity_calls_terra_and_stops(self) -> None:
        terra_calls = []
        luna_calls = []

        def terra(prompt, repo):
            terra_calls.append(prompt)
            return ProviderExecution(["codex"], 0, "Human must decide title policy", "")

        def luna(prompt, repo):
            luna_calls.append(prompt)
            return self.luna(prompt, repo)

        def ambiguous(prompt, repo):
            return review_execution("FAIL", "POLICY_AMBIGUITY", "title family is unclear")

        run = self.controller(ambiguous, terra=terra, luna=luna).new_run("009")

        self.assertEqual(run.stage, "blocked_policy_ambiguity")
        self.assertEqual(len(terra_calls), 1)
        self.assertEqual(luna_calls, [])
        self.assertIn("Human must decide", run.terra_resolution)

    def test_new_findings_continue_without_sol_until_pass(self) -> None:
        review_calls = 0
        luna_calls = 0
        sol_calls = 0

        def sonnet(prompt, repo):
            nonlocal review_calls
            review_calls += 1
            if review_calls == 1:
                return review_execution("PASS", "PASS")
            if review_calls <= 4:
                index = review_calls - 1
                return review_execution(
                    "FAIL",
                    "IMPLEMENTATION_DEFECT",
                    f"distinct defect {index}",
                    f"defect-{index}",
                )
            return review_execution("PASS", "PASS")

        def luna(prompt, repo):
            nonlocal luna_calls
            luna_calls += 1
            (repo / f"implementation-{luna_calls}.py").write_text("# bounded change\n")
            return ProviderExecution(["codex"], 0, "implemented", "")

        def sol(prompt, repo):
            nonlocal sol_calls
            sol_calls += 1
            return ProviderExecution(["codex"], 0, "GUIDANCE: unexpected", "")

        run = self.controller(
            sonnet,
            sol=sol,
            luna=luna,
            approval=lambda prompt: False,
        ).new_run("009")

        self.assertEqual(run.stage, "commit_declined")
        self.assertEqual(run.correction_cycles, 3)
        self.assertEqual(luna_calls, 4)  # initial + A/B/C corrections
        self.assertEqual(sol_calls, 0)

    def test_same_finding_gets_two_sol_escalations_then_blocks(self) -> None:
        review_calls = 0
        luna_prompts = []
        sol_prompts = []

        def sonnet(prompt, repo):
            nonlocal review_calls
            review_calls += 1
            if review_calls == 1:
                return review_execution("PASS", "PASS")
            return review_execution(
                "FAIL",
                "IMPLEMENTATION_DEFECT",
                "same persistent defect",
                "persistent-defect",
            )

        def luna(prompt, repo):
            luna_prompts.append(prompt)
            (repo / f"implementation-{len(luna_prompts)}.py").write_text("# bounded change\n")
            return ProviderExecution(["codex"], 0, "implemented", "")

        def sol(prompt, repo):
            sol_prompts.append(prompt)
            return ProviderExecution(
                ["codex"],
                0,
                f"GUIDANCE: diagnostic round {len(sol_prompts)}",
                "",
            )

        run = self.controller(sonnet, sol=sol, luna=luna).new_run("009")

        self.assertEqual(run.stage, "blocked_repeated_finding")
        self.assertEqual(run.correction_cycles, 3)
        self.assertEqual(len(luna_prompts), 4)  # initial + ordinary + two Sol-guided
        self.assertEqual(len(sol_prompts), 2)
        self.assertIn("escalation round 1", sol_prompts[0])
        self.assertIn("escalation round 2", sol_prompts[1])
        self.assertIn("Sol High escalation guidance", luna_prompts[-1])

    def test_emergency_total_correction_budget_blocks(self) -> None:
        review_calls = 0
        luna_calls = 0
        sol_calls = 0

        def sonnet(prompt, repo):
            nonlocal review_calls
            review_calls += 1
            if review_calls == 1:
                return review_execution("PASS", "PASS")
            index = review_calls - 1
            return review_execution(
                "FAIL",
                "IMPLEMENTATION_DEFECT",
                f"new defect {index}",
                f"new-defect-{index}",
            )

        def luna(prompt, repo):
            nonlocal luna_calls
            luna_calls += 1
            (repo / f"implementation-{luna_calls}.py").write_text("# bounded change\n")
            return ProviderExecution(["codex"], 0, "implemented", "")

        def sol(prompt, repo):
            nonlocal sol_calls
            sol_calls += 1
            return ProviderExecution(["codex"], 0, "GUIDANCE: unexpected", "")

        run = self.controller(sonnet, sol=sol, luna=luna).new_run("009")

        self.assertEqual(run.stage, "blocked_correction_budget")
        self.assertEqual(run.correction_cycles, 12)
        self.assertEqual(sol_calls, 0)

    def test_legacy_one_correction_block_can_resume_under_new_policy(self) -> None:
        run = self.controller(
            self.passing_sonnet,
            approval=lambda prompt: False,
        ).new_run("009")

        run.stage = "blocked_after_correction"
        run.correction_cycles = 1
        result = ReviewResult(
            status="FAIL",
            category="IMPLEMENTATION_DEFECT",
            summary="legacy blocked finding",
        )
        run.implementation_review = result
        attempt = review_execution(
            "FAIL",
            "IMPLEMENTATION_DEFECT",
            "legacy blocked finding",
        )
        index = len(run.provider_runs)
        run.provider_runs.append(
            provider_record(
                ADVERSARIAL_REVIEW_ROUTE,
                "implementation_review",
                command=attempt.command,
                returncode=0,
                stdout=attempt.stdout,
            )
        )
        run.review_records.append(
            orchestrator.ReviewRecord(
                recorded_at="2026-08-02T00:00:00+00:00",
                operation_id="implementation_review",
                result=result,
                provider_record_index=index,
            )
        )
        run.last_error = "implementation review still fails after one correction"
        orchestrator.persist(run, self.runs)

        with patch.object(
            orchestrator,
            "resolve_correction_policy",
            side_effect=AssertionError("resume must use the saved policy"),
        ):
            resumed = self.controller(
                self.passing_sonnet,
                approval=lambda prompt: False,
            ).resume(run.run_id)

        self.assertEqual(resumed.stage, "commit_declined")
        self.assertEqual(resumed.correction_cycles, 2)

    def test_policy_approval_is_audited_and_propagated(self) -> None:
        luna_prompts = []

        run = self.controller(
            self.passing_sonnet,
            approval=lambda prompt: False,
        ).new_run("009")

        run.stage = "blocked_policy_ambiguity"
        review_result = ReviewResult(
            status="FAIL",
            category="IMPLEMENTATION_DEFECT",
            finding_key="remote-scope-ownership",
            summary="remote scope ownership remains unresolved",
        )
        run.implementation_review = review_result
        attempt = review_execution(
            "FAIL",
            "IMPLEMENTATION_DEFECT",
            "remote scope ownership remains unresolved",
            "remote-scope-ownership",
        )
        review_index = len(run.provider_runs)
        run.provider_runs.append(
            provider_record(
                ADVERSARIAL_REVIEW_ROUTE,
                "implementation_review",
                command=attempt.command,
                returncode=0,
                stdout=attempt.stdout,
            )
        )
        run.review_records.append(
            orchestrator.ReviewRecord(
                recorded_at="2026-08-02T00:00:00+00:00",
                operation_id="implementation_review",
                result=review_result,
                provider_record_index=review_index,
            )
        )
        run.sol_guidance = "Decision 3 versus Decision 4 ownership is ambiguous."
        run.terra_resolution = (
            "Analysis.\n\n"
            "Proposed approval text:\n\n"
            "> Remote-role scope is positive evidence for remote reality.\n"
        )
        policy_source_index = len(run.provider_runs)
        run.provider_runs.append(
            provider_record(
                POLICY_AUTHORITY_ROUTE,
                "policy_clarification",
                command=["codex"],
                returncode=0,
                stdout=run.terra_resolution,
            )
        )
        run.last_error = "policy ambiguity requires human approval"
        orchestrator.persist(run, self.runs)

        def luna(prompt, repo):
            luna_prompts.append(prompt)
            (repo / "policy-fix.py").write_text("# bounded change\n")
            return ProviderExecution(["codex"], 0, "implemented", "")

        controller = self.controller(
            self.passing_sonnet,
            luna=luna,
            approval=lambda prompt: False,
        )

        approved = controller.approve_policy(
            run.run_id,
            "Remote-role scope is positive evidence for remote reality.",
        )

        self.assertEqual(approved.stage, "correction_pending")
        self.assertEqual(approved.correction_cycles, 1)
        self.assertEqual(len(approved.policy_decisions), 1)

        decision = approved.policy_decisions[0]
        self.assertEqual(decision.decision_id, "policy-01")
        self.assertEqual(decision.approved_by, "human")
        self.assertEqual(decision.source_role_id, "policy_authority")
        self.assertEqual(
            decision.source_route_id,
            POLICY_AUTHORITY_ROUTE.route_id,
        )
        self.assertEqual(
            decision.source_provider_record_index,
            policy_source_index,
        )
        self.assertEqual(decision.trigger_finding_key, "remote-scope-ownership")
        self.assertIn("Decision 3 versus Decision 4", decision.trigger_summary)
        self.assertIn("Proposed approval text", decision.recommendation)

        resumed = controller.resume(run.run_id)

        self.assertEqual(resumed.stage, "commit_declined")
        self.assertEqual(len(luna_prompts), 1)
        self.assertIn(
            "Human-approved policy decisions (authoritative for this run)",
            luna_prompts[0],
        )
        self.assertIn(
            "Remote-role scope is positive evidence for remote reality.",
            luna_prompts[0],
        )

        extracted = orchestrator._terra_proposed_approval_text(
            run.terra_resolution
        )
        self.assertEqual(
            extracted,
            "Remote-role scope is positive evidence for remote reality.",
        )

    def test_provider_timing_and_run_report_are_derived_safely(self) -> None:
        run = self.controller(
            self.passing_sonnet,
            approval=lambda prompt: False,
        ).new_run("009")

        run.provider_runs = []
        run.created_at = "2026-08-01T12:00:00+00:00"
        run.updated_at = "2026-08-01T12:01:30+00:00"

        orchestrator._record_provider(
            run,
            "implementation_write",
            IMPLEMENTATION_ROUTE,
            ProviderExecution(
                command=["codex"],
                returncode=0,
                stdout="implemented",
                stderr="",
                duration_seconds=12.5,
            ),
            capability="workspace_write",
        )
        orchestrator._record_provider(
            run,
            "implementation_review",
            ADVERSARIAL_REVIEW_ROUTE,
            ProviderExecution(
                command=["claude"],
                returncode=0,
                stdout="legacy review output",
                stderr="",
            ),
            capability="read_only",
        )

        report = orchestrator._run_report(run)

        self.assertEqual(report["provider_logical_invocations_total"], 2)
        self.assertEqual(report["provider_physical_attempts_total"], 2)
        self.assertEqual(
            report["role_logical_invocation_counts"]["implementation"],
            1,
        )
        self.assertEqual(
            report["role_logical_invocation_counts"]["adversarial_review"],
            1,
        )
        self.assertAlmostEqual(
            report["role_physical_attempt_seconds"]["implementation"],
            12.5,
        )
        self.assertEqual(
            report["untimed_physical_attempt_counts"]["adversarial_review"],
            1,
        )
        self.assertEqual(report["successful_writer_invocations"], 1)
        self.assertEqual(report["wall_seconds"], 90.0)

        self.assertEqual(
            run.provider_runs[0].duration_seconds,
            12.5,
        )

    def test_g27_logical_invocations_and_physical_attempts_are_distinct(self) -> None:
        run = self.controller(
            self.passing_sonnet,
            approval=lambda prompt: False,
        ).new_run("009")
        run.provider_runs = []
        attempts = (
            ProviderAttempt(
                command=["claude", "same"],
                returncode=1,
                stdout="",
                stderr="unavailable",
                duration_seconds=1.0,
                failure_kind="unavailable",
                failure_source="stderr",
                failure_code="503",
                capability="read_only",
                retry_scheduled=True,
            ),
            ProviderAttempt(
                command=["claude", "same"],
                returncode=1,
                stdout="",
                stderr="unavailable",
                duration_seconds=2.0,
                failure_kind="unavailable",
                failure_source="stderr",
                failure_code="503",
                capability="read_only",
                retry_scheduled=True,
            ),
            ProviderAttempt(
                command=["claude", "same"],
                returncode=0,
                stdout="ok",
                stderr="",
                duration_seconds=3.0,
                capability="read_only",
                retry_scheduled=False,
            ),
        )
        execution = ProviderExecution(
            command=["claude", "same"],
            returncode=0,
            stdout="ok",
            stderr="",
            duration_seconds=6.0,
            capability="read_only",
            attempts=attempts,
        )
        orchestrator._record_provider(
            run,
            "implementation_review",
            ADVERSARIAL_REVIEW_ROUTE,
            execution,
            capability="read_only",
            logical_invocation_id="logical-review-01",
        )

        report = orchestrator._run_report(run)
        self.assertEqual(report["provider_logical_invocations_total"], 1)
        self.assertEqual(report["provider_physical_attempts_total"], 3)
        self.assertEqual(report["provider_retry_transitions_total"], 2)
        self.assertEqual(
            report["provider_physical_attempt_failure_counts"],
            {"unavailable": 2},
        )
        self.assertEqual(
            report["provider_physical_attempt_seconds_total"],
            6.0,
        )
        self.assertEqual(
            [record.logical_invocation_id for record in run.provider_runs],
            ["logical-review-01"] * 3,
        )
        self.assertEqual(
            [record.physical_attempt_ordinal for record in run.provider_runs],
            [1, 2, 3],
        )
        for obsolete in (
            "provider_calls_total",
            "role_counts",
            "provider_failure_counts",
            "provider_retry_attempts",
            "verification_runs",
        ):
            self.assertNotIn(obsolete, report)

        orchestrator._record_provider(
            run,
            "implementation_review",
            ADVERSARIAL_REVIEW_ROUTE,
            ProviderExecution(["claude", "same"], 0, "ok", ""),
            capability="read_only",
            logical_invocation_id="logical-review-02",
        )
        self.assertEqual(
            orchestrator._run_report(run)["provider_logical_invocations_total"],
            2,
        )

        with orchestrator.console.capture() as capture:
            orchestrator._print_run_report(run)
        rendered = capture.get()
        self.assertIn("Provider logical invocations: 2", rendered)
        self.assertIn("Provider physical attempts: 4", rendered)
        self.assertIn(
            "Successful writer invocations (not verification executions): 0",
            rendered,
        )
        self.assertNotIn("Provider calls:", rendered)
        self.assertNotIn("Verification runs:", rendered)

    def test_g27_invocation_group_validation_fails_closed(self) -> None:
        invocation_id = "logical-review-validation"
        valid = WorkflowRun(
            run_id="g27-validation",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="0" * 64,
            specification="Validate provider invocation grouping.",
            resolved_correction_policy=resolve_correction_policy(),
            repo=orchestrator.repo_state(self.repo),
            provider_runs=[
                provider_record(
                    ADVERSARIAL_REVIEW_ROUTE,
                    "implementation_review",
                    logical_invocation_id=invocation_id,
                    physical_attempt_ordinal=1,
                    command=["claude"],
                    returncode=1,
                    failure_kind="unavailable",
                    failure_source="stderr",
                    retry_scheduled=True,
                ),
                provider_record(
                    ADVERSARIAL_REVIEW_ROUTE,
                    "implementation_review",
                    logical_invocation_id=invocation_id,
                    physical_attempt_ordinal=2,
                    command=["claude"],
                    returncode=0,
                ),
            ],
        )
        payload = valid.model_dump(mode="json")

        cases = {
            "lacks invocation identity": lambda value: value["provider_runs"][0].update(
                logical_invocation_id=None
            ),
            "attempt ordinals": lambda value: value["provider_runs"][1].update(
                physical_attempt_ordinal=3
            ),
            "retry transition": lambda value: value["provider_runs"][0].update(
                retry_scheduled=False
            ),
            "attempts are incoherent": lambda value: value["provider_runs"][1].update(
                command=["different"]
            ),
        }
        for message, mutate in cases.items():
            candidate = json.loads(json.dumps(payload))
            mutate(candidate)
            with self.subTest(message=message), self.assertRaisesRegex(
                ValueError,
                message,
            ):
                WorkflowRun.model_validate(candidate)

        candidate = json.loads(json.dumps(payload))
        middle = json.loads(json.dumps(candidate["provider_runs"][1]))
        middle.update(
            logical_invocation_id="logical-review-other",
            physical_attempt_ordinal=1,
        )
        repeated = json.loads(json.dumps(candidate["provider_runs"][1]))
        repeated["physical_attempt_ordinal"] = 3
        candidate["provider_runs"].extend([middle, repeated])
        with self.assertRaisesRegex(ValueError, "not contiguous"):
            WorkflowRun.model_validate(candidate)

        candidate = json.loads(json.dumps(payload))
        candidate["provider_runs"][1].update(
            returncode=1,
            failure_kind="unavailable",
            failure_source="stderr",
            retry_scheduled=True,
        )
        with self.assertRaisesRegex(ValueError, "final attempt"):
            WorkflowRun.model_validate(candidate)

        candidate = json.loads(json.dumps(payload))
        candidate["provider_runs"][0].update(
            capability="workspace_write",
            identity=IMPLEMENTATION_ROUTE.model_dump(mode="json"),
            operation_id="implementation_write",
        )
        candidate["provider_runs"][1].update(
            capability="workspace_write",
            identity=IMPLEMENTATION_ROUTE.model_dump(mode="json"),
            operation_id="implementation_write",
        )
        with self.assertRaisesRegex(ValueError, "retry authority"):
            WorkflowRun.model_validate(candidate)


    def test_quota_stop_is_global_and_resume_retries_only_interrupted_stage(self) -> None:
        sonnet_calls = 0
        luna_calls = 0

        def sonnet(prompt, repo):
            nonlocal sonnet_calls
            sonnet_calls += 1
            if sonnet_calls == 1:
                return review_execution("PASS", "PASS")
            if sonnet_calls == 2:
                return ProviderExecution(
                    ["claude"],
                    1,
                    "",
                    "You've hit your usage limit; resets later.",
                )
            return review_execution("PASS", "PASS")

        def luna(prompt, repo):
            nonlocal luna_calls
            luna_calls += 1
            (repo / "implementation.py").write_text(
                "# implementation\n"
            )
            return ProviderExecution(
                ["codex"],
                0,
                "implemented",
                "",
            )

        controller = self.controller(
            sonnet,
            luna=luna,
            approval=lambda prompt: False,
        )
        run = controller.new_run("009")

        self.assertEqual(run.stage, "blocked_provider_quota")
        self.assertEqual(
            run.provider_resume_stage,
            "reviewing",
        )
        self.assertEqual(sonnet_calls, 2)
        self.assertEqual(luna_calls, 1)
        self.assertEqual(
            run.provider_runs[-1].failure_kind,
            "quota",
        )
        self.assertFalse(
            run.provider_runs[-1].retry_scheduled
        )

        resumed = controller.resume(run.run_id)

        self.assertEqual(resumed.stage, "commit_declined")
        self.assertEqual(sonnet_calls, 3)
        self.assertEqual(luna_calls, 1)
        self.assertIsNone(resumed.provider_resume_stage)
        self.assertIsNone(resumed.provider_resume_prompt)
        review_invocations = [
            record.logical_invocation_id
            for record in resumed.provider_runs
            if record.operation_id == "implementation_review"
        ]
        self.assertEqual(len(review_invocations), 2)
        self.assertEqual(len(set(review_invocations)), 2)


    def test_provider_unavailability_retries_only_same_provider(self) -> None:
        results = iter(
            [
                subprocess.CompletedProcess(
                    ["claude"],
                    503,
                    "",
                    "HTTP 503 Service Unavailable",
                ),
                subprocess.CompletedProcess(
                    ["claude"],
                    502,
                    "",
                    "HTTP 502 Bad Gateway",
                ),
                subprocess.CompletedProcess(
                    ["claude"],
                    0,
                    "ok",
                    "",
                ),
            ]
        )
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return next(results)

        execution = providers._run(
            ["claude"],
            self.repo,
            capability="read_only",
            runner=runner,
            sleeper=lambda seconds: None,
        )

        self.assertEqual(execution.returncode, 0)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(execution.attempts), 3)
        self.assertEqual(
            [
                attempt.failure_kind
                for attempt in execution.attempts
            ],
            ["unavailable", "unavailable", None],
        )
        self.assertEqual(
            [
                attempt.retry_scheduled
                for attempt in execution.attempts
            ],
            [True, True, False],
        )


    def test_provider_failure_classification_is_deterministic(self) -> None:
        cases = (
            ("You've hit your usage limit", "quota"),
            ("HTTP 402 Payment Required", "billing"),
            ("HTTP 401 Unauthorized", "auth"),
            ("status code: 429 Too Many Requests", "rate_limit"),
            ("HTTP 503 Service Unavailable", "unavailable"),
            ("unknown model requested", "configuration"),
        )

        for message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(
                    providers.classify_provider_failure(
                        1,
                        "",
                        message,
                    ),
                    expected,
                )


    def test_malformed_sonnet_output_gets_one_same_provider_retry(self) -> None:
        sonnet_calls = 0
        luna_calls = 0

        def sonnet(prompt, repo):
            nonlocal sonnet_calls
            sonnet_calls += 1
            if sonnet_calls == 1:
                return review_execution("PASS", "PASS")
            if sonnet_calls == 2:
                return ProviderExecution(
                    ["claude"],
                    0,
                    "not-json",
                    "",
                )
            return review_execution("PASS", "PASS")

        def luna(prompt, repo):
            nonlocal luna_calls
            luna_calls += 1
            (repo / "implementation.py").write_text(
                "# implementation\n"
            )
            return ProviderExecution(
                ["codex"],
                0,
                "implemented",
                "",
            )

        run = self.controller(
            sonnet,
            luna=luna,
            approval=lambda prompt: False,
        ).new_run("009")

        self.assertEqual(run.stage, "commit_declined")
        self.assertEqual(sonnet_calls, 3)
        self.assertEqual(luna_calls, 1)


    def test_commit_prompt_defaults_to_no(self) -> None:
        prompts = []

        def confirm(prompt, default):
            prompts.append((prompt, default))
            return False

        with patch.object(orchestrator.typer, "confirm", side_effect=confirm):
            run = self.controller(self.passing_sonnet).new_run("009")

        self.assertEqual(run.stage, "commit_declined")
        self.assertEqual(prompts, [("Commit these changes?", False)])

    def test_push_prompt_defaults_to_no(self) -> None:
        prompts = []

        def confirm(prompt, default):
            prompts.append((prompt, default))
            return len(prompts) == 1

        with patch.object(orchestrator.typer, "confirm", side_effect=confirm):
            run = self.controller(self.passing_sonnet).new_run("009")

        self.assertEqual(run.stage, "push_declined")
        self.assertEqual(prompts, [("Commit these changes?", False), ("Push this commit to origin?", False)])

    def test_blocked_after_escalation_with_new_finding_resumes_normally(self) -> None:
        luna_calls = 0
        sol_calls = 0

        run = self.controller(
            self.passing_sonnet,
            approval=lambda prompt: False,
        ).new_run("009")

        # Simulate prior QA history for a different defect.
        old = review_execution(
            "FAIL",
            "IMPLEMENTATION_DEFECT",
            "old attendance defect",
            "attendance-linking",
        )
        old_result = providers.parse_sonnet_review(old)
        old_index = len(run.provider_runs)
        run.provider_runs.append(
            provider_record(
                ADVERSARIAL_REVIEW_ROUTE,
                "implementation_review",
                command=old.command,
                returncode=old.returncode,
                stdout=old.stdout,
                stderr=old.stderr,
            )
        )
        run.review_records.append(
            orchestrator.ReviewRecord(
                recorded_at="2026-08-02T00:00:00+00:00",
                operation_id="implementation_review",
                result=old_result,
                provider_record_index=old_index,
            )
        )

        # Simulate the real T008-style hard stop: Sol path exhausted,
        # but final QA has uncovered a genuinely new defect.
        run.stage = "blocked_after_escalation"
        run.correction_cycles = 3
        new_result = ReviewResult(
            status="FAIL",
            category="IMPLEMENTATION_DEFECT",
            finding_key="geography-modal-alternatives",
            summary="new geography modal defect",
        )
        run.implementation_review = new_result
        new_index = len(run.provider_runs)
        run.provider_runs.append(
            provider_record(
                ADVERSARIAL_REVIEW_ROUTE,
                "implementation_review",
                command=old.command,
                returncode=0,
                stdout=old.stdout,
            )
        )
        run.review_records.append(
            orchestrator.ReviewRecord(
                recorded_at="2026-08-02T00:00:00+00:00",
                operation_id="implementation_review",
                result=new_result,
                provider_record_index=new_index,
            )
        )
        run.last_error = "implementation review still fails after Sol-guided final correction"
        orchestrator.persist(run, self.runs)

        def luna(prompt, repo):
            nonlocal luna_calls
            luna_calls += 1
            (repo / "new-finding-fix.py").write_text("# bounded change\n")
            return ProviderExecution(["codex"], 0, "implemented", "")

        def sol(prompt, repo):
            nonlocal sol_calls
            sol_calls += 1
            return ProviderExecution(["codex"], 0, "GUIDANCE: unexpected", "")

        resumed = self.controller(
            self.passing_sonnet,
            sol=sol,
            luna=luna,
            approval=lambda prompt: False,
        ).resume(run.run_id)

        self.assertEqual(resumed.stage, "commit_declined")
        self.assertEqual(resumed.correction_cycles, 4)
        self.assertEqual(luna_calls, 1)
        self.assertEqual(sol_calls, 0)

    def test_provider_commands_preserve_restrictions(self) -> None:
        sonnet = build_sonnet_command("review")
        self.assertIn("--permission-mode", sonnet)
        self.assertEqual(sonnet[sonnet.index("--permission-mode") + 1], "plan")
        self.assertEqual(sonnet[sonnet.index("--tools") + 1], "Read,Glob,Grep")
        self.assertNotIn("Write", sonnet)
        self.assertNotIn("Edit", sonnet)
        self.assertNotIn("Bash", sonnet)

        sol = build_sol_command("diagnose")
        self.assertIn("gpt-5.6-sol", sol)
        self.assertEqual(sol[sol.index("--sandbox") + 1], "read-only")

        luna = build_luna_command("implement")
        self.assertEqual(luna[luna.index("--sandbox") + 1], "workspace-write")
        luna_configs = [value for index, value in enumerate(luna) if index and luna[index - 1] == "--config"]
        self.assertIn("approval_policy=never", luna_configs)
        self.assertIn("sandbox_workspace_write.network_access=false", luna_configs)
        luna_prompt = luna[-1]
        for forbidden in ("commit", "push", "branch", "merge", ".git"):
            self.assertIn(forbidden, luna_prompt)

    def test_read_only_timeout_persists_distinct_nonresumable_block(self) -> None:
        calls = 0

        def timed_out_sonnet(prompt, repo):
            nonlocal calls
            calls += 1
            return providers._run(
                [
                    sys.executable,
                    "-c",
                    "import time; print('review partial', flush=True); time.sleep(60)",
                ],
                repo,
                capability="read_only",
                deadline_seconds=0.15,
                term_grace_seconds=0.15,
                poll_interval_seconds=0.02,
                heartbeat_seconds=60.0,
            )

        controller = self.controller(timed_out_sonnet)
        run = controller.new_run("009")

        self.assertEqual(run.stage, "blocked_provider_timeout")
        self.assertEqual(calls, 1)
        self.assertEqual(run.provider_runs[-1].failure_kind, "timeout")
        self.assertFalse(run.provider_runs[-1].retry_scheduled)
        self.assertIn("review partial", run.provider_runs[-1].stdout)

        resumed = controller.resume(run.run_id)
        self.assertEqual(resumed.stage, "blocked_provider_timeout")
        self.assertEqual(calls, 1)

    def test_writer_timeout_is_not_retried_or_resumed(self) -> None:
        luna_calls = 0

        def timed_out_luna(prompt, repo):
            nonlocal luna_calls
            luna_calls += 1
            return providers._run(
                [
                    sys.executable,
                    "-c",
                    "import time; print('writer partial', flush=True); time.sleep(60)",
                ],
                repo,
                capability="workspace_write",
                deadline_seconds=0.15,
                term_grace_seconds=0.15,
                poll_interval_seconds=0.02,
                heartbeat_seconds=60.0,
            )

        controller = self.controller(
            self.passing_sonnet,
            luna=timed_out_luna,
        )
        run = controller.new_run("009")

        self.assertEqual(run.stage, "blocked_writer_retry_required")
        self.assertEqual(luna_calls, 1)
        self.assertEqual(run.provider_runs[-1].failure_kind, "timeout")
        self.assertIn("writer partial", run.provider_runs[-1].stdout)

        resumed = controller.resume(run.run_id)
        self.assertEqual(resumed.stage, "blocked_writer_retry_required")
        self.assertEqual(luna_calls, 1)

    def test_timeout_record_recovery_reconstructs_block_without_provider(self) -> None:
        provider_calls = 0

        def unexpected_provider(prompt, repo):
            nonlocal provider_calls
            provider_calls += 1
            return review_execution("PASS", "PASS")

        run = WorkflowRun(
            run_id="timeout-recovery",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="0" * 64,
            specification="Test timeout recovery.",
            resolved_correction_policy=resolve_correction_policy(),
            repo=orchestrator.repo_state(self.repo),
            stage="spec_reviewing",
            provider_resume_stage="spec_reviewing",
            provider_resume_prompt="saved prompt",
            provider_resume_identity=ADVERSARIAL_REVIEW_ROUTE,
            provider_resume_operation_id="specification_review",
        )
        run.provider_runs.append(
            provider_record(
                ADVERSARIAL_REVIEW_ROUTE,
                "specification_review",
                command=["claude"],
                returncode=providers.PROVIDER_TIMEOUT_RETURN_CODE,
                stdout="partial",
                stderr="[continuo] deadline exceeded",
                duration_seconds=1.0,
                failure_kind="timeout",
                retry_scheduled=False,
            )
        )
        orchestrator.persist(run, self.runs)

        recovered = self.controller(unexpected_provider).resume(run.run_id)

        self.assertEqual(recovered.stage, "blocked_provider_timeout")
        self.assertEqual(provider_calls, 0)
        self.assertEqual(recovered.provider_runs[-1].failure_kind, "timeout")

    def test_interrupted_provider_persists_distinct_nonresumable_block(self) -> None:
        calls = 0

        def interrupted_sonnet(prompt, repo):
            nonlocal calls
            calls += 1
            return ProviderExecution(
                command=["claude"],
                returncode=providers.PROVIDER_INTERRUPTED_RETURN_CODE,
                stdout="partial",
                stderr="[continuo] operator interruption received",
                duration_seconds=0.5,
                failure_kind="interrupted",
            )

        controller = self.controller(interrupted_sonnet)
        run = controller.new_run("009")

        self.assertEqual(run.stage, "blocked_provider_interrupted")
        self.assertEqual(run.provider_runs[-1].failure_kind, "interrupted")
        self.assertEqual(calls, 1)

        resumed = controller.resume(run.run_id)
        self.assertEqual(resumed.stage, "blocked_provider_interrupted")
        self.assertEqual(calls, 1)

    def test_recorded_native_error_blocks_without_content_retry(self) -> None:
        calls = 0
        _, native_error = claude_fixture("provider_error")

        def sonnet(prompt, repo):
            nonlocal calls
            calls += 1
            return native_error

        run = self.controller(sonnet).new_run("009")

        self.assertEqual(run.stage, "blocked_provider_failure")
        self.assertEqual(calls, 1)
        self.assertEqual(len(run.provider_runs), 1)
        record = run.provider_runs[0]
        self.assertEqual(record.returncode, 1)
        self.assertEqual(record.failure_kind, "provider_error")
        self.assertEqual(record.failure_source, "provider_native")
        self.assertEqual(record.failure_code, "error_max_budget_usd")

    def test_fixture_content_retry_then_native_error_stops_at_two_calls(self) -> None:
        calls = 0
        _, malformed = claude_fixture("malformed_envelope")
        _, max_turns = claude_fixture("max_turns")

        def sonnet(prompt, repo):
            nonlocal calls
            calls += 1
            return malformed if calls == 1 else max_turns

        run = self.controller(sonnet).new_run("009")

        self.assertEqual(run.stage, "blocked_provider_failure")
        self.assertEqual(calls, 2)
        self.assertEqual(len(run.provider_runs), 2)
        self.assertIsNone(run.provider_runs[0].failure_kind)
        self.assertEqual(run.provider_runs[1].failure_source, "provider_native")
        self.assertEqual(run.provider_runs[1].failure_code, "error_max_turns")

    def test_fixture_content_retry_then_success_advances(self) -> None:
        calls = 0
        _, malformed = claude_fixture("malformed_envelope")
        _, success = claude_fixture("success")

        def sonnet(prompt, repo):
            nonlocal calls
            calls += 1
            return malformed if calls == 1 else success

        run = self.controller(
            sonnet,
            approval=lambda prompt: False,
        ).new_run("009")

        self.assertEqual(run.stage, "commit_declined")
        self.assertEqual(calls, 3)
        self.assertEqual(run.spec_review.status, "PASS")
        self.assertEqual(run.implementation_review.status, "PASS")
        specification_invocations = [
            record.logical_invocation_id
            for record in run.provider_runs
            if record.operation_id == "specification_review"
        ]
        self.assertEqual(len(specification_invocations), 2)
        self.assertEqual(len(set(specification_invocations)), 2)

    def test_native_failure_crash_recovery_uses_saved_evidence(self) -> None:
        provider_calls = 0
        _, native_error = claude_fixture("provider_error")

        def unexpected_provider(prompt, repo):
            nonlocal provider_calls
            provider_calls += 1
            return review_execution("PASS", "PASS")

        run = WorkflowRun(
            run_id="native-failure-recovery",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="0" * 64,
            specification="Test native failure recovery.",
            resolved_correction_policy=resolve_correction_policy(),
            repo=orchestrator.repo_state(self.repo),
            stage="spec_reviewing",
            provider_resume_stage="spec_reviewing",
            provider_resume_prompt="saved prompt",
            provider_resume_identity=ADVERSARIAL_REVIEW_ROUTE,
            provider_resume_operation_id="specification_review",
        )
        normalized = orchestrator._record_provider(
            run,
            "specification_review",
            ADVERSARIAL_REVIEW_ROUTE,
            native_error,
            capability="read_only",
        )
        self.assertTrue(providers.execution_failed(normalized))
        orchestrator.persist(run, self.runs)

        recovered = self.controller(unexpected_provider).resume(run.run_id)

        self.assertEqual(recovered.stage, "blocked_provider_failure")
        self.assertEqual(provider_calls, 0)
        self.assertEqual(
            recovered.provider_runs[-1].failure_source,
            "provider_native",
        )

        _, malformed = claude_fixture("malformed_envelope")
        malformed_run = WorkflowRun(
            run_id="malformed-content-recovery",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="0" * 64,
            specification="Test malformed content recovery.",
            resolved_correction_policy=resolve_correction_policy(),
            repo=orchestrator.repo_state(self.repo),
            stage="spec_reviewing",
            provider_resume_stage="spec_reviewing",
            provider_resume_prompt="saved prompt",
            provider_resume_identity=ADVERSARIAL_REVIEW_ROUTE,
            provider_resume_operation_id="specification_review",
        )
        orchestrator._record_provider(
            malformed_run,
            "specification_review",
            ADVERSARIAL_REVIEW_ROUTE,
            malformed,
            capability="read_only",
        )
        malformed_runs = self.runs / "malformed-content-recovery"
        orchestrator.persist(malformed_run, malformed_runs)

        recovered_malformed = self.controller(
            unexpected_provider,
            runs_dir=malformed_runs,
        ).resume(
            malformed_run.run_id
        )

        self.assertEqual(
            recovered_malformed.stage,
            "blocked_provider_output",
        )
        self.assertEqual(provider_calls, 0)

        _, success = claude_fixture("success")
        success_run = WorkflowRun(
            run_id="successful-content-recovery",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="0" * 64,
            specification="Test successful content recovery.",
            resolved_correction_policy=resolve_correction_policy(),
            repo=orchestrator.repo_state(self.repo),
            stage="spec_reviewing",
            provider_resume_stage="spec_reviewing",
            provider_resume_prompt="saved prompt",
            provider_resume_identity=ADVERSARIAL_REVIEW_ROUTE,
            provider_resume_operation_id="specification_review",
        )
        orchestrator._record_provider(
            success_run,
            "specification_review",
            ADVERSARIAL_REVIEW_ROUTE,
            success,
            capability="read_only",
        )
        success_runs = self.runs / "successful-content-recovery"
        orchestrator.persist(success_run, success_runs)

        recovered_success = self.controller(
            unexpected_provider,
            approval=lambda prompt: False,
            runs_dir=success_runs,
        ).resume(success_run.run_id)

        self.assertEqual(recovered_success.stage, "commit_declined")
        self.assertEqual(provider_calls, 1)
        self.assertEqual(
            [
                record.operation_id
                for record in recovered_success.provider_runs
                if record.identity.role_id == "adversarial_review"
            ],
            ["specification_review", "implementation_review"],
        )

    def test_legacy_failure_recovery_does_not_scan_model_stdout(self) -> None:
        run = WorkflowRun(
            run_id="legacy-model-prose",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="0" * 64,
            specification="Test conservative legacy recovery.",
            resolved_correction_policy=resolve_correction_policy(),
            repo=orchestrator.repo_state(self.repo),
            stage="implementing",
            provider_resume_stage="implementing",
            provider_resume_prompt="saved prompt",
            provider_resume_identity=IMPLEMENTATION_ROUTE,
            provider_resume_operation_id="implementation_write",
        )
        run.provider_runs.append(
            provider_record(
                IMPLEMENTATION_ROUTE,
                "implementation_write",
                command=["codex"],
                returncode=1,
                stdout=(
                    "Model transcript and diff:\n"
                    "HTTP 503 Service Unavailable\n"
                    "+ status code: 429\n"
                ),
                stderr="",
            )
        )
        orchestrator.persist(run, self.runs)

        recovered = self.controller(self.passing_sonnet).resume(run.run_id)

        self.assertEqual(recovered.stage, "blocked_writer_state_unknown")
        self.assertEqual(recovered.provider_runs[-1].failure_kind, None)
        self.assertNotIn("implementation.py", orchestrator.changed_files(self.repo))

    def test_writer_marker_precedes_call_and_success_records_exact_snapshots(self) -> None:
        observed: dict[str, object] = {}
        clean_fingerprint = orchestrator.working_tree_fingerprint(self.repo, [])

        def luna(prompt, repo):
            saved_path = next(self.runs.glob("*.json"))
            saved = orchestrator.load_run(saved_path.stem, self.runs)
            active = saved.active_writer_attempt
            self.assertIsNotNone(active)
            observed["stage"] = saved.stage
            observed["prompt"] = saved.provider_resume_prompt
            observed["attempt_id"] = active.attempt_id
            observed["pre_files"] = active.pre_changed_files
            observed["pre_fingerprint"] = active.pre_fingerprint
            (repo / "implementation.py").write_text("# implementation\n")
            return ProviderExecution(["codex"], 0, "implemented", "")

        run = self.controller(
            self.passing_sonnet,
            luna=luna,
            approval=lambda prompt: False,
        ).new_run("009")

        self.assertEqual(observed["stage"], "implementing")
        self.assertEqual(observed["pre_files"], [])
        self.assertEqual(observed["pre_fingerprint"], clean_fingerprint)
        self.assertTrue(observed["prompt"])
        self.assertEqual(run.stage, "commit_declined")
        self.assertIsNone(run.active_writer_attempt)
        writer = next(
            record
            for record in run.provider_runs
            if record.identity.role_id == "implementation"
        )
        self.assertEqual(writer.capability, "workspace_write")
        self.assertEqual(
            writer.repository_fingerprint_before,
            clean_fingerprint,
        )
        self.assertEqual(
            writer.repository_fingerprint_after,
            orchestrator.working_tree_fingerprint(self.repo),
        )
        self.assertNotEqual(
            writer.repository_fingerprint_before,
            writer.repository_fingerprint_after,
        )

    def test_correction_snapshot_describes_existing_reviewed_changes(self) -> None:
        sonnet_calls = 0
        luna_calls = 0
        correction_observation: dict[str, object] = {}

        def sonnet(prompt, repo):
            nonlocal sonnet_calls
            sonnet_calls += 1
            if sonnet_calls == 2:
                return review_execution(
                    "FAIL",
                    "IMPLEMENTATION_DEFECT",
                    "correct the fixture",
                    "fixture-defect",
                )
            return review_execution("PASS", "PASS")

        def luna(prompt, repo):
            nonlocal luna_calls
            luna_calls += 1
            if luna_calls == 1:
                (repo / "implementation.py").write_text("# first pass\n")
            else:
                saved_path = next(self.runs.glob("*.json"))
                saved = orchestrator.load_run(saved_path.stem, self.runs)
                active = saved.active_writer_attempt
                self.assertIsNotNone(active)
                correction_observation["stage"] = active.stage
                correction_observation["purpose"] = active.purpose
                correction_observation["files"] = active.pre_changed_files
                correction_observation["fingerprint"] = active.pre_fingerprint
                correction_observation["saved_fingerprint"] = (
                    saved.working_tree_fingerprint
                )
                (repo / "correction.py").write_text("# correction\n")
            return ProviderExecution(["codex"], 0, "implemented", "")

        run = self.controller(
            sonnet,
            luna=luna,
            approval=lambda prompt: False,
        ).new_run("009")

        self.assertEqual(run.stage, "commit_declined")
        self.assertEqual(correction_observation["stage"], "correcting")
        self.assertEqual(correction_observation["purpose"], "correction")
        self.assertEqual(correction_observation["files"], ["implementation.py"])
        self.assertEqual(
            correction_observation["fingerprint"],
            correction_observation["saved_fingerprint"],
        )

    def test_failed_writer_preserves_all_partial_path_states_without_cleanup(self) -> None:
        tracked = self.repo / 'tracked "名称".txt'
        staged = self.repo / "staged -> path.txt"
        deleted = self.repo / "deleted path.txt"
        rename_old = self.repo / "rename old.txt"
        rename_new = self.repo / 'rename new 名称 " -> path.txt'
        for path in (tracked, staged, deleted, rename_old):
            path.write_text("base\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "writer path fixtures")

        def luna(prompt, repo):
            tracked.write_text("modified\n")
            staged.write_text("staged modification\n")
            git(repo, "add", "--", staged.name)
            deleted.unlink()
            rename_old.rename(rename_new)
            git(repo, "add", "-A", "--", rename_old.name, rename_new.name)
            (repo / 'untracked 名称 " -> file.txt').write_text("new\n")
            return ProviderExecution(
                ["codex"],
                1,
                "partial output",
                "HTTP 401 Unauthorized",
            )

        controller = self.controller(self.passing_sonnet, luna=luna)
        run = controller.new_run("009")

        expected = sorted(
            [
                tracked.name,
                staged.name,
                deleted.name,
                rename_old.name,
                rename_new.name,
                'untracked 名称 " -> file.txt',
            ]
        )
        self.assertEqual(run.stage, "blocked_writer_partial_changes")
        self.assertEqual(run.active_writer_attempt.post_changed_files, expected)
        self.assertNotEqual(
            run.active_writer_attempt.pre_fingerprint,
            run.active_writer_attempt.post_fingerprint,
        )
        self.assertEqual(run.provider_runs[-1].failure_kind, "auth")
        self.assertEqual(run.provider_runs[-1].failure_source, "stderr")
        self.assertEqual(run.provider_runs[-1].failure_code, "401")
        self.assertEqual(run.provider_runs[-1].capability, "workspace_write")
        self.assertEqual(run.verification, {})
        self.assertEqual(run.git_operations, [])
        self.assertTrue(tracked.exists())
        self.assertTrue(staged.exists())
        self.assertFalse(deleted.exists())
        self.assertFalse(rename_old.exists())
        self.assertTrue(rename_new.exists())
        self.assertTrue((self.repo / 'untracked 名称 " -> file.txt').exists())
        worktrees = git(self.repo, "worktree", "list", "--porcelain")
        self.assertEqual(worktrees.count("worktree "), 1)

        before_resume = run.model_dump()
        resumed = controller.resume(run.run_id)
        self.assertEqual(resumed.stage, "blocked_writer_partial_changes")
        self.assertEqual(resumed.model_dump(), before_resume)

    def test_writer_post_inspection_failure_keeps_raw_attempt_and_unknown_state(self) -> None:
        real_changed_files = orchestrator.changed_files
        enumerations = 0

        def fragile_changed_files(repo):
            nonlocal enumerations
            enumerations += 1
            if enumerations == 2:
                raise orchestrator.ControllerError("synthetic post-state failure")
            return real_changed_files(repo)

        def luna(prompt, repo):
            (repo / "partial.py").write_text("# partial\n")
            return ProviderExecution(
                ["codex"],
                1,
                "raw writer output",
                "Error: service unavailable",
            )

        with patch.object(
            orchestrator,
            "changed_files",
            side_effect=fragile_changed_files,
        ):
            run = self.controller(self.passing_sonnet, luna=luna).new_run("009")

        self.assertEqual(run.stage, "blocked_writer_state_unknown")
        self.assertEqual(len(run.provider_runs), 2)
        self.assertEqual(run.provider_runs[-1].stdout, "raw writer output")
        self.assertEqual(run.provider_runs[-1].failure_kind, "unavailable")
        self.assertIsNone(run.provider_runs[-1].repository_fingerprint_after)
        self.assertIn(
            "synthetic post-state failure",
            run.active_writer_attempt.inspection_error,
        )

    def test_successful_noop_writer_uses_existing_no_changes_block(self) -> None:
        run = self.controller(
            self.passing_sonnet,
            luna=lambda prompt, repo: ProviderExecution(
                ["codex"], 0, "claimed success", ""
            ),
        ).new_run("009")

        self.assertEqual(run.stage, "blocked_no_changes")
        self.assertIsNone(run.active_writer_attempt)
        self.assertEqual(run.provider_runs[-1].capability, "workspace_write")
        self.assertEqual(
            run.provider_runs[-1].repository_fingerprint_before,
            run.provider_runs[-1].repository_fingerprint_after,
        )

    def test_retry_restored_requires_exact_state_and_audits_one_new_attempt(self) -> None:
        first_calls = 0

        def failed_luna(prompt, repo):
            nonlocal first_calls
            first_calls += 1
            return ProviderExecution(
                ["codex"], 1, "", "Error: service unavailable"
            )

        initial = self.controller(
            self.passing_sonnet,
            luna=failed_luna,
        ).new_run("009")
        self.assertEqual(initial.stage, "blocked_writer_retry_required")
        old_attempt_id = initial.active_writer_attempt.attempt_id
        saved_prompt = initial.provider_resume_prompt

        external = self.repo / "operator-change.txt"
        external.write_text("not restored\n")
        retry_calls = 0

        def retried_luna(prompt, repo):
            nonlocal retry_calls
            retry_calls += 1
            saved = orchestrator.load_run(initial.run_id, self.runs)
            self.assertNotEqual(
                saved.active_writer_attempt.attempt_id,
                old_attempt_id,
            )
            self.assertEqual(saved.provider_resume_prompt, saved_prompt)
            self.assertNotIn("HTTP 503", prompt)
            (repo / "implementation.py").write_text("# restored retry\n")
            return ProviderExecution(["codex"], 0, "implemented", "")

        controller = self.controller(
            self.passing_sonnet,
            luna=retried_luna,
            approval=lambda prompt: False,
        )
        with self.assertRaisesRegex(
            orchestrator.ControllerError,
            "exact saved pre-attempt state",
        ):
            controller.recover_writer(
                initial.run_id,
                "retry_restored",
                "HTTP 503: retry only after exact restore",
            )
        refused = orchestrator.load_run(initial.run_id, self.runs)
        self.assertEqual(refused.stage, "blocked_writer_retry_required")
        self.assertEqual(refused.writer_recovery_decisions, [])
        self.assertEqual(retry_calls, 0)

        external.unlink()
        recovered = controller.recover_writer(
            initial.run_id,
            "retry_restored",
            "HTTP 503: retry only after exact restore",
        )

        self.assertEqual(recovered.stage, "commit_declined")
        self.assertEqual(first_calls, 1)
        self.assertEqual(retry_calls, 1)
        self.assertEqual(len(recovered.writer_recovery_decisions), 1)
        decision = recovered.writer_recovery_decisions[0]
        self.assertEqual(decision.action, "retry_restored")
        self.assertEqual(decision.writer_attempt_id, old_attempt_id)
        self.assertEqual(
            decision.note,
            "HTTP 503: retry only after exact restore",
        )
        writer_records = [
            record
            for record in recovered.provider_runs
            if record.identity.role_id == "implementation"
        ]
        self.assertEqual(len(writer_records), 2)
        self.assertEqual(
            [record.capability for record in writer_records],
            ["workspace_write", "workspace_write"],
        )

    def test_adopt_current_audits_reconciled_state_without_writer_success(self) -> None:
        writer_calls = 0

        def failed_luna(prompt, repo):
            nonlocal writer_calls
            writer_calls += 1
            (repo / "partial.py").write_text("# first partial\n")
            return ProviderExecution(["codex"], 1, "partial", "failure")

        initial = self.controller(
            self.passing_sonnet,
            luna=failed_luna,
        ).new_run("009")
        self.assertEqual(initial.stage, "blocked_writer_partial_changes")
        saved_post = initial.active_writer_attempt.post_fingerprint
        provider_count = len(initial.provider_runs)

        (self.repo / "partial.py").write_text("# operator reconciled\n")
        (self.repo / "reconciled.py").write_text("# chosen state\n")

        def unexpected_luna(prompt, repo):
            raise AssertionError("adopt-current must not invoke Luna")

        recovered = self.controller(
            self.passing_sonnet,
            luna=unexpected_luna,
            approval=lambda prompt: False,
        ).recover_writer(
            initial.run_id,
            "adopt_current",
            "Adopt reconciled diff; prompt says quota exceeded",
        )

        self.assertEqual(recovered.stage, "commit_declined")
        self.assertEqual(writer_calls, 1)
        self.assertEqual(len(recovered.provider_runs), provider_count + 1)
        self.assertEqual(
            recovered.provider_runs[-1].identity.role_id,
            "adversarial_review",
        )
        self.assertIsNone(recovered.active_writer_attempt)
        decision = recovered.writer_recovery_decisions[-1]
        self.assertEqual(decision.action, "adopt_current")
        self.assertEqual(decision.saved_post_fingerprint, saved_post)
        self.assertNotEqual(decision.observed_fingerprint, saved_post)
        self.assertEqual(
            decision.observed_changed_files,
            ["partial.py", "reconciled.py"],
        )
        report = orchestrator._run_report(recovered)
        self.assertEqual(report["writer_recovery_decisions"], 1)
        self.assertEqual(
            report["provider_physical_attempts_total"],
            provider_count + 1,
        )

    def test_adopt_current_refuses_noop_unknown_and_identity_mismatch(self) -> None:
        def unchanged_failure(prompt, repo):
            return ProviderExecution(["codex"], 1, "", "failure")

        run = self.controller(
            self.passing_sonnet,
            luna=unchanged_failure,
        ).new_run("009")
        controller = self.controller(self.passing_sonnet)

        with self.assertRaisesRegex(
            orchestrator.ControllerError,
            "trustworthy changes",
        ):
            controller.recover_writer(run.run_id, "adopt_current", "no-op")

        saved = orchestrator.load_run(run.run_id, self.runs)
        saved.stage = "blocked_writer_state_unknown"
        saved.active_writer_attempt.inspection_error = "prior inspection failed"
        orchestrator.persist(saved, self.runs)
        git(self.repo, "remote", "set-url", "origin", "https://example.invalid/other.git")

        with self.assertRaisesRegex(
            orchestrator.ControllerError,
            "branch, HEAD, or origin changed",
        ):
            controller.recover_writer(
                run.run_id,
                "adopt_current",
                "identity mismatch",
            )

        refused = orchestrator.load_run(run.run_id, self.runs)
        self.assertEqual(refused.stage, "blocked_writer_state_unknown")
        self.assertIsNotNone(refused.active_writer_attempt)
        self.assertEqual(refused.writer_recovery_decisions, [])

    def test_writer_crash_recovery_never_reinvokes_and_consumes_saved_success(self) -> None:
        pre_files = orchestrator.changed_files(self.repo)
        pre_fingerprint = orchestrator.working_tree_fingerprint(
            self.repo,
            pre_files,
        )
        provider_calls = 0

        def unexpected_luna(prompt, repo):
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("writer crash recovery must not invoke Luna")

        def interrupted_run(run_id):
            return WorkflowRun(
                run_id=run_id,
                created_at="2026-08-02T00:00:00+00:00",
                task_ref="009",
                task_file="tasks/009-example.md",
                task_sha256="0" * 64,
                specification="Crash recovery fixture.",
                resolved_correction_policy=resolve_correction_policy(),
                repo=orchestrator.repo_state(self.repo),
                stage="implementing",
                provider_resume_stage="implementing",
                provider_resume_prompt="saved writer prompt",
                provider_resume_identity=IMPLEMENTATION_ROUTE,
                provider_resume_operation_id="implementation_write",
                active_writer_attempt=WriterAttemptState(
                    attempt_id=f"writer-{run_id}",
                    stage="implementing",
                    purpose="implementation",
                    pre_fingerprint=pre_fingerprint,
                    pre_changed_files=pre_files,
                ),
            )

        unchanged = interrupted_run("crash-unchanged")
        unchanged_runs = self.runs / unchanged.run_id
        orchestrator.persist(unchanged, unchanged_runs)
        recovered_unchanged = self.controller(
            self.passing_sonnet,
            luna=unexpected_luna,
            runs_dir=unchanged_runs,
        ).resume(unchanged.run_id)
        self.assertEqual(
            recovered_unchanged.stage,
            "blocked_writer_retry_required",
        )

        (self.repo / "crash-partial.py").write_text("# partial\n")
        post_files = orchestrator.changed_files(self.repo)
        post_fingerprint = orchestrator.working_tree_fingerprint(
            self.repo,
            post_files,
        )
        partial = interrupted_run("crash-partial")
        partial_runs = self.runs / partial.run_id
        orchestrator.persist(partial, partial_runs)
        recovered_partial = self.controller(
            self.passing_sonnet,
            luna=unexpected_luna,
            runs_dir=partial_runs,
        ).resume(partial.run_id)
        self.assertEqual(
            recovered_partial.stage,
            "blocked_writer_partial_changes",
        )

        failed = interrupted_run("crash-failed-record")
        failed.active_writer_attempt.post_fingerprint = post_fingerprint
        failed.active_writer_attempt.post_changed_files = post_files
        failed.active_writer_attempt.provider_record_index = 0
        failed.provider_runs.append(
            provider_record(
                IMPLEMENTATION_ROUTE,
                "implementation_write",
                command=["codex"],
                returncode=1,
                stderr="HTTP 503 Service Unavailable",
                failure_kind="unavailable",
                failure_source="stderr",
                failure_code="503",
                capability="workspace_write",
                repository_fingerprint_before=pre_fingerprint,
                repository_fingerprint_after=post_fingerprint,
            )
        )
        failed_runs = self.runs / failed.run_id
        orchestrator.persist(failed, failed_runs)
        recovered_failed = self.controller(
            self.passing_sonnet,
            luna=unexpected_luna,
            runs_dir=failed_runs,
        ).resume(failed.run_id)
        self.assertEqual(
            recovered_failed.stage,
            "blocked_writer_partial_changes",
        )
        self.assertEqual(recovered_failed.provider_runs[0].failure_kind, "unavailable")

        success = interrupted_run("crash-success-record")
        success.active_writer_attempt.post_fingerprint = post_fingerprint
        success.active_writer_attempt.post_changed_files = post_files
        success.active_writer_attempt.provider_record_index = 0
        success.provider_runs.append(
            provider_record(
                IMPLEMENTATION_ROUTE,
                "implementation_write",
                command=["codex"],
                returncode=0,
                stdout="implemented",
                capability="workspace_write",
                repository_fingerprint_before=pre_fingerprint,
                repository_fingerprint_after=post_fingerprint,
            )
        )
        success_runs = self.runs / success.run_id
        orchestrator.persist(success, success_runs)
        recovered_success = self.controller(
            self.passing_sonnet,
            luna=unexpected_luna,
            approval=lambda prompt: False,
            runs_dir=success_runs,
        ).resume(success.run_id)
        self.assertEqual(recovered_success.stage, "commit_declined")
        self.assertIsNone(recovered_success.active_writer_attempt)
        self.assertEqual(provider_calls, 0)

    def test_recovery_decision_crash_states_resume_without_implicit_writer(self) -> None:
        pre_files = orchestrator.changed_files(self.repo)
        pre_fingerprint = orchestrator.working_tree_fingerprint(
            self.repo,
            pre_files,
        )
        calls = 0

        def unexpected_luna(prompt, repo):
            nonlocal calls
            calls += 1
            raise AssertionError("resume must not invoke a recovery writer")

        retry_crash = WorkflowRun(
            run_id="retry-decision-crash",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="0" * 64,
            specification="Retry decision crash fixture.",
            resolved_correction_policy=resolve_correction_policy(),
            repo=orchestrator.repo_state(self.repo),
            stage="implementing",
            provider_resume_stage="implementing",
            provider_resume_prompt="saved prompt",
            provider_resume_identity=IMPLEMENTATION_ROUTE,
            provider_resume_operation_id="implementation_write",
            active_writer_attempt=WriterAttemptState(
                attempt_id="writer-new-retry",
                stage="implementing",
                purpose="implementation",
                pre_fingerprint=pre_fingerprint,
                pre_changed_files=pre_files,
            ),
            writer_recovery_decisions=[
                WriterRecoveryDecision(
                    decision_id="writer-recovery-01",
                    decided_at="2026-08-02T00:01:00+00:00",
                    action="retry_restored",
                    note="decision saved before retry start",
                    writer_attempt_id="writer-old-retry",
                    stage="implementing",
                    purpose="implementation",
                    pre_fingerprint=pre_fingerprint,
                    observed_fingerprint=pre_fingerprint,
                    observed_changed_files=pre_files,
                )
            ],
        )
        retry_runs = self.runs / retry_crash.run_id
        orchestrator.persist(retry_crash, retry_runs)
        recovered_retry = self.controller(
            self.passing_sonnet,
            luna=unexpected_luna,
            runs_dir=retry_runs,
        ).resume(retry_crash.run_id)
        self.assertEqual(
            recovered_retry.stage,
            "blocked_writer_retry_required",
        )
        self.assertEqual(len(recovered_retry.writer_recovery_decisions), 1)

        (self.repo / "adopted.py").write_text("# adopted\n")
        adopted_fingerprint = orchestrator.working_tree_fingerprint(self.repo)
        adopt_crash = WorkflowRun(
            run_id="adopt-decision-crash",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="0" * 64,
            specification="Adopt decision crash fixture.",
            resolved_correction_policy=resolve_correction_policy(),
            repo=orchestrator.repo_state(self.repo),
            stage="implementation_completed",
            writer_recovery_decisions=[
                WriterRecoveryDecision(
                    decision_id="writer-recovery-01",
                    decided_at="2026-08-02T00:01:00+00:00",
                    action="adopt_current",
                    note="decision and completed stage saved",
                    writer_attempt_id="writer-old-adopt",
                    stage="implementing",
                    purpose="implementation",
                    pre_fingerprint=pre_fingerprint,
                    saved_post_fingerprint=adopted_fingerprint,
                    observed_fingerprint=adopted_fingerprint,
                    observed_changed_files=["adopted.py"],
                )
            ],
        )
        adopt_runs = self.runs / adopt_crash.run_id
        orchestrator.persist(adopt_crash, adopt_runs)
        recovered_adopt = self.controller(
            self.passing_sonnet,
            luna=unexpected_luna,
            approval=lambda prompt: False,
            runs_dir=adopt_runs,
        ).resume(adopt_crash.run_id)
        self.assertEqual(recovered_adopt.stage, "commit_declined")
        self.assertEqual(len(recovered_adopt.writer_recovery_decisions), 1)
        self.assertEqual(calls, 0)

    def test_failed_correction_retry_preserves_controller_state_and_prompt(self) -> None:
        sonnet_calls = 0
        luna_calls = 0

        def sonnet(prompt, repo):
            nonlocal sonnet_calls
            sonnet_calls += 1
            if sonnet_calls == 2:
                return review_execution(
                    "FAIL",
                    "IMPLEMENTATION_DEFECT",
                    "persistent correction fixture",
                    "persistent-fixture",
                )
            return review_execution("PASS", "PASS")

        def luna(prompt, repo):
            nonlocal luna_calls
            luna_calls += 1
            if luna_calls == 1:
                (repo / "implementation.py").write_text("# initial\n")
                return ProviderExecution(["codex"], 0, "implemented", "")
            return ProviderExecution(
                ["codex"],
                1,
                "correction failed without changes",
                "Error: service unavailable",
            )

        blocked = self.controller(sonnet, luna=luna).new_run("009")
        self.assertEqual(blocked.stage, "blocked_writer_retry_required")
        self.assertEqual(blocked.active_writer_attempt.stage, "correcting")
        self.assertEqual(blocked.correction_cycles, 1)
        saved_prompt = blocked.provider_resume_prompt
        old_attempt_id = blocked.active_writer_attempt.attempt_id
        blocked.sol_guidance = "preserve this Sol round guidance"
        policy_source_index = len(blocked.provider_runs)
        blocked.provider_runs.append(
            provider_record(
                POLICY_AUTHORITY_ROUTE,
                "policy_clarification",
                command=["codex"],
                returncode=0,
                stdout="preserve recommendation",
            )
        )
        blocked.policy_decisions.append(
            orchestrator.PolicyDecision(
                decision_id="policy-01",
                approved_at="2026-08-02T00:00:00+00:00",
                trigger_finding_key="persistent-fixture",
                trigger_summary="preserve policy state",
                recommendation="preserve recommendation",
                approved_text="preserve approved policy",
                source_provider_record_index=policy_source_index,
            )
        )
        orchestrator.persist(blocked, self.runs)

        retry_calls = 0

        def correction_retry(prompt, repo):
            nonlocal retry_calls
            retry_calls += 1
            saved = orchestrator.load_run(blocked.run_id, self.runs)
            self.assertEqual(saved.correction_cycles, 1)
            self.assertEqual(
                saved.sol_guidance,
                "preserve this Sol round guidance",
            )
            self.assertEqual(len(saved.policy_decisions), 1)
            self.assertEqual(saved.provider_resume_prompt, saved_prompt)
            self.assertNotEqual(
                saved.active_writer_attempt.attempt_id,
                old_attempt_id,
            )
            self.assertEqual(len(saved.writer_recovery_decisions), 1)
            (repo / "correction.py").write_text("# recovered correction\n")
            return ProviderExecution(["codex"], 0, "corrected", "")

        recovered = self.controller(
            sonnet,
            luna=correction_retry,
            approval=lambda prompt: False,
        ).recover_writer(
            blocked.run_id,
            "retry_restored",
            "retry the restored correction once",
        )

        self.assertEqual(recovered.stage, "commit_declined")
        self.assertEqual(recovered.correction_cycles, 1)
        self.assertEqual(
            recovered.sol_guidance,
            "preserve this Sol round guidance",
        )
        self.assertEqual(len(recovered.policy_decisions), 1)
        self.assertEqual(retry_calls, 1)
        history = orchestrator._implementation_review_history(recovered)
        self.assertEqual(
            [review.finding_key for review in history],
            ["persistent-fixture"],
        )
        self.assertEqual(recovered.implementation_review.finding_key, "PASS")

    def test_external_change_after_writer_snapshot_is_detected_without_locking(self) -> None:
        observed: dict[str, object] = {}

        def luna(prompt, repo):
            saved_path = next(self.runs.glob("*.json"))
            saved = orchestrator.load_run(saved_path.stem, self.runs)
            observed["pre_files"] = saved.active_writer_attempt.pre_changed_files
            (repo / "external-race.txt").write_text("outside mutation\n")
            return ProviderExecution(["codex"], 1, "", "failure")

        run = self.controller(self.passing_sonnet, luna=luna).new_run("009")

        self.assertEqual(observed["pre_files"], [])
        self.assertEqual(run.stage, "blocked_writer_partial_changes")
        self.assertEqual(
            run.active_writer_attempt.post_changed_files,
            ["external-race.txt"],
        )
        self.assertFalse((self.repo / ".continuo.lock").exists())
        self.assertEqual(
            git(self.repo, "worktree", "list", "--porcelain").count(
                "worktree "
            ),
            1,
        )

    def test_writer_schema_round_trip_legacy_defaults_and_report(self) -> None:
        fingerprint = "a" * 64
        run = WorkflowRun(
            run_id="writer-audit",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="0" * 64,
            specification="Writer audit fixture.",
            resolved_correction_policy=resolve_correction_policy(),
            repo=orchestrator.repo_state(self.repo),
            stage="blocked_writer_partial_changes",
            provider_resume_stage="implementing",
            provider_resume_prompt="saved prompt",
            provider_resume_identity=IMPLEMENTATION_ROUTE,
            provider_resume_operation_id="implementation_write",
            active_writer_attempt=WriterAttemptState(
                attempt_id="writer-audit-1",
                stage="implementing",
                purpose="implementation",
                pre_fingerprint=fingerprint,
                pre_changed_files=[],
                post_fingerprint="b" * 64,
                post_changed_files=["partial.py"],
                provider_record_index=None,
            ),
            writer_recovery_decisions=[
                WriterRecoveryDecision(
                    decision_id="writer-recovery-01",
                    decided_at="2026-08-02T00:01:00+00:00",
                    action="retry_restored",
                    note="restored exactly",
                    writer_attempt_id="writer-old-1",
                    stage="implementing",
                    purpose="implementation",
                    pre_fingerprint=fingerprint,
                    saved_post_fingerprint="b" * 64,
                    observed_fingerprint=fingerprint,
                    observed_changed_files=[],
                ),
                WriterRecoveryDecision(
                    decision_id="writer-recovery-02",
                    decided_at="2026-08-02T00:02:00+00:00",
                    action="adopt_current",
                    note="adopt reconciled state",
                    writer_attempt_id="writer-old-2",
                    stage="correcting",
                    purpose="correction",
                    pre_fingerprint=fingerprint,
                    saved_post_fingerprint="b" * 64,
                    observed_fingerprint="c" * 64,
                    observed_changed_files=["partial.py"],
                ),
            ],
        )
        run.provider_runs.extend(
            [
                provider_record(
                    ADVERSARIAL_REVIEW_ROUTE,
                    "implementation_review",
                    command=["legacy"],
                    returncode=0,
                ),
                provider_record(
                    IMPLEMENTATION_ROUTE,
                    "implementation_write",
                    command=["codex"],
                    returncode=1,
                    duration_seconds=1.25,
                    failure_kind="unavailable",
                    failure_source="stderr",
                    failure_code="503",
                    capability="workspace_write",
                    repository_fingerprint_before=fingerprint,
                    repository_fingerprint_after="b" * 64,
                ),
            ]
        )
        run.active_writer_attempt.provider_record_index = 1
        orchestrator.persist(run, self.runs)
        loaded = orchestrator.load_run(run.run_id, self.runs)
        self.assertEqual(loaded.model_dump(), run.model_dump())

        report = orchestrator._run_report(loaded)
        self.assertEqual(report["provider_physical_attempts_total"], 2)
        self.assertEqual(
            report["provider_physical_attempt_failure_counts"],
            {"unavailable": 1},
        )
        self.assertEqual(report["writer_recovery_decisions"], 2)
        self.assertEqual(
            report["pending_writer_state"],
            "blocked_writer_partial_changes",
        )

        legacy_payload = {
            "schema_version": 6,
            "run_id": "legacy-schema-six",
            "created_at": "2026-08-01T00:00:00+00:00",
            "task_ref": "009",
            "task_file": "tasks/009-example.md",
            "task_sha256": "0" * 64,
            "specification": "Legacy schema-six fixture.",
            "repo": orchestrator.repo_state(self.repo).model_dump(),
            "provider_runs": [
                {
                    "provider": "Luna High",
                    "purpose": "implementation",
                    "command": ["codex"],
                    "returncode": 1,
                }
            ],
        }
        classification = run_migrations.classify_run_bytes(
            json.dumps(legacy_payload).encode()
        )
        self.assertEqual(classification.treatment, "migrate")
        legacy = run_migrations.migrate_classification(
            classification,
            migration_id="test-migration",
            migrated_at="2026-08-02T00:00:00+00:00",
        ).run
        self.assertIsNone(legacy.active_writer_attempt)
        self.assertEqual(legacy.writer_recovery_decisions, [])
        self.assertIsNone(legacy.provider_runs[0].capability)
        self.assertIsNone(
            legacy.provider_runs[0].repository_fingerprint_before
        )

    def test_target_identity_aliases_and_separate_checkouts(self) -> None:
        alias = Path(self.temp.name) / "repo-alias"
        alias.symlink_to(self.repo, target_is_directory=True)

        direct = orchestrator.target_identity(self.repo)
        through_alias = orchestrator.target_identity(alias)
        self.assertEqual(through_alias, direct)
        self.assertEqual(
            orchestrator.TargetCoordinator(alias, self.runs).database,
            orchestrator.TargetCoordinator(self.repo, self.runs).database,
        )

        second = Path(self.temp.name) / "second-checkout"
        second.mkdir()
        git(second, "init", "-b", "main")
        git(second, "config", "user.email", "tests@example.invalid")
        git(second, "config", "user.name", "Controller Tests")
        git(second, "remote", "add", "origin", "https://example.invalid/jobs.git")
        (second / "tasks").mkdir()
        (second / "tasks/009-example.md").write_text("Second checkout task.\n")
        git(second, "add", ".")
        git(second, "commit", "-m", "fixture")

        second_identity = orchestrator.target_identity(second)
        self.assertNotEqual(second_identity.target_key, direct.target_key)

        first_coordinator = orchestrator.TargetCoordinator(self.repo, self.runs)
        second_calls = 0

        def second_review(prompt, repo):
            nonlocal second_calls
            second_calls += 1
            return review_execution(
                "FAIL",
                "POLICY_AMBIGUITY",
                "independent target fixture",
            )

        with first_coordinator.transaction():
            result = orchestrator.Controller(
                second,
                self.runs,
                sonnet=second_review,
                terra=lambda prompt, repo: ProviderExecution(
                    ["codex"], 0, "human decision required", ""
                ),
            ).new_run("009")

        self.assertEqual(result.stage, "blocked_policy_ambiguity")
        self.assertEqual(second_calls, 1)

    def test_new_claim_precedes_provider_and_all_public_actions_contend(self) -> None:
        provider_entered = threading.Event()
        provider_release = threading.Event()
        provider_calls = 0
        result_holder = []
        errors = []

        def slow_review(prompt, repo):
            nonlocal provider_calls
            provider_calls += 1
            saved = list(self.runs.glob("*.json"))
            self.assertEqual(len(saved), 1)
            observed = orchestrator.load_run(saved[0].stem, self.runs)
            self.assertIsNotNone(observed.target_ownership)
            provider_entered.set()
            if not provider_release.wait(5):
                raise AssertionError("test provider was not released")
            return review_execution(
                "FAIL",
                "POLICY_AMBIGUITY",
                "mutex fixture",
            )

        controller = self.controller(
            slow_review,
            terra=lambda prompt, repo: ProviderExecution(
                ["codex"], 0, "human decision required", ""
            ),
        )

        def start_first() -> None:
            try:
                result_holder.append(controller.new_run("009"))
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=start_first)
        worker.start()
        self.assertTrue(provider_entered.wait(5))

        with self.assertRaisesRegex(
            orchestrator.ControllerError,
            "currently executing",
        ):
            self.controller(self.passing_sonnet).new_run("009")
        self.assertEqual(len(list(self.runs.glob("*.json"))), 1)
        self.assertEqual(provider_calls, 1)

        provider_release.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        run = result_holder[0]
        self.assertEqual(run.stage, "blocked_policy_ambiguity")
        self.assertIsNone(run.target_ownership.released_at)

        before = orchestrator.load_run(run.run_id, self.runs).model_dump()
        coordinator = orchestrator.TargetCoordinator(self.repo, self.runs)
        with coordinator.transaction():
            actions = (
                lambda: controller.resume(run.run_id),
                lambda: controller.approve_policy(run.run_id, "approved text"),
                lambda: controller.recover_writer(
                    run.run_id, "retry_restored", "recovery note"
                ),
                lambda: controller.release_target(run.run_id, "abandon run"),
            )
            for action in actions:
                with self.assertRaisesRegex(
                    orchestrator.ControllerError,
                    "currently executing",
                ):
                    action()

        self.assertEqual(
            orchestrator.load_run(run.run_id, self.runs).model_dump(),
            before,
        )
        with coordinator.transaction() as connection:
            owner = coordinator._owner(connection)
            self.assertEqual(owner["run_id"], run.run_id)

    def test_dirty_retention_clean_release_and_released_run_closure(self) -> None:
        second_provider_calls = 0
        run = self.controller(
            self.passing_sonnet,
            approval=lambda prompt: False,
        ).new_run("009")
        self.assertEqual(run.stage, "commit_declined")
        self.assertIsNone(run.target_ownership.released_at)

        with self.assertRaisesRegex(
            orchestrator.ControllerError,
            "clean checkout",
        ):
            self.controller(self.passing_sonnet).release_target(
                run.run_id,
                "cannot abandon dirty work",
            )

        def second_review(prompt, repo):
            nonlocal second_provider_calls
            second_provider_calls += 1
            return self.passing_sonnet(prompt, repo)

        dirty_block = self.controller(second_review).new_run("009")
        self.assertEqual(dirty_block.stage, "blocked_dirty_repo")
        self.assertIsNone(dirty_block.target_ownership)
        self.assertEqual(second_provider_calls, 0)

        (self.repo / "implementation.py").unlink()
        git_records = len(run.git_operations)
        provider_records = len(run.provider_runs)
        released = self.controller(self.passing_sonnet).release_target(
            run.run_id,
            "operator deliberately abandoned the clean declined run",
        )
        self.assertEqual(released.target_ownership.release_reason, "operator_released")
        self.assertEqual(
            released.target_ownership.release_note,
            "operator deliberately abandoned the clean declined run",
        )
        self.assertEqual(len(released.git_operations), git_records)
        self.assertEqual(len(released.provider_runs), provider_records)

        for action in (
            lambda: self.controller(self.passing_sonnet).resume(run.run_id),
            lambda: self.controller(self.passing_sonnet).release_target(
                run.run_id, "second release"
            ),
        ):
            with self.assertRaisesRegex(
                orchestrator.ControllerError,
                "released run",
            ):
                action()

        replacement = self.controller(
            lambda prompt, repo: review_execution(
                "FAIL", "POLICY_AMBIGUITY", "replacement owns target"
            ),
            terra=lambda prompt, repo: ProviderExecution(
                ["codex"], 0, "human decision required", ""
            ),
        ).new_run("009")
        self.assertNotEqual(replacement.run_id, run.run_id)
        self.assertEqual(
            replacement.target_ownership.target_key,
            released.target_ownership.target_key,
        )

    def test_clean_push_decline_retains_owner_and_push_releases_it(self) -> None:
        declined = self.controller(
            self.passing_sonnet,
            approval=lambda prompt: prompt.startswith("Commit"),
            runs_dir=self.runs / "declined",
        ).new_run("009")
        self.assertEqual(declined.stage, "push_declined")
        self.assertTrue(orchestrator.repo_state(self.repo).clean)
        self.assertIsNone(declined.target_ownership.released_at)

        bare = Path(self.temp.name) / "origin.git"
        git(bare.parent, "init", "--bare", str(bare))
        git(self.repo, "remote", "set-url", "origin", str(bare))
        (self.repo / "implementation.py").write_text("# next implementation\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "prepare published fixture")

        published_runs = self.runs / "published"
        published = self.controller(
            self.passing_sonnet,
            luna=lambda prompt, repo: (
                (repo / "published.py").write_text("# published\n"),
                ProviderExecution(["codex"], 0, "implemented", ""),
            )[1],
            approval=lambda prompt: True,
            runs_dir=published_runs,
        ).new_run("009")
        self.assertEqual(published.stage, "pushed_awaiting_merge")
        self.assertEqual(published.target_ownership.release_reason, "published")
        self.assertIsNone(published.target_ownership.release_note)
        self.assertTrue(orchestrator.repo_state(self.repo).clean)
        coordinator = orchestrator.TargetCoordinator(self.repo, published_runs)
        with coordinator.transaction() as connection:
            self.assertIsNone(coordinator._owner(connection))

    def test_stale_release_and_published_states_reconcile_conservatively(self) -> None:
        def policy_review(prompt, repo):
            return review_execution(
                "FAIL", "POLICY_AMBIGUITY", "stale-owner fixture"
            )

        terra = lambda prompt, repo: ProviderExecution(
            ["codex"], 0, "human decision required", ""
        )

        released_runs = self.runs / "stale-released"
        released_controller = self.controller(
            policy_review,
            terra=terra,
            runs_dir=released_runs,
        )
        old = released_controller.new_run("009")
        released_coordinator = orchestrator.TargetCoordinator(
            self.repo, released_runs
        )
        released_coordinator._release_audit(
            old,
            "operator_released",
            "release audit persisted before owner deletion",
        )
        replacement = released_controller.new_run("009")
        self.assertNotEqual(replacement.run_id, old.run_id)
        with released_coordinator.transaction() as connection:
            self.assertEqual(
                released_coordinator._owner(connection)["run_id"],
                replacement.run_id,
            )

        pushed_runs = self.runs / "stale-published"
        pushed_controller = self.controller(
            policy_review,
            terra=terra,
            runs_dir=pushed_runs,
        )
        pushed = pushed_controller.new_run("009")
        pushed.stage = "pushed_awaiting_merge"
        pushed.commit_hash = git(self.repo, "rev-parse", "HEAD")
        orchestrator.persist(pushed, pushed_runs)
        successor = pushed_controller.new_run("009")
        finalized = orchestrator.load_run(pushed.run_id, pushed_runs)
        self.assertEqual(finalized.target_ownership.release_reason, "published")
        self.assertEqual(successor.stage, "blocked_policy_ambiguity")

    def test_unknown_owner_and_invalid_database_fail_before_provider(self) -> None:
        scenarios = ("missing", "corrupt", "identity", "audit")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                runs_dir = self.runs / f"unknown-{scenario}"
                calls = 0

                def policy_review(prompt, repo):
                    nonlocal calls
                    calls += 1
                    return review_execution(
                        "FAIL", "POLICY_AMBIGUITY", "owner fixture"
                    )

                controller = self.controller(
                    policy_review,
                    terra=lambda prompt, repo: ProviderExecution(
                        ["codex"], 0, "human decision required", ""
                    ),
                    runs_dir=runs_dir,
                )
                owner = controller.new_run("009")
                path = runs_dir / f"{owner.run_id}.json"
                calls_before = calls
                if scenario == "missing":
                    path.unlink()
                elif scenario == "corrupt":
                    path.write_text("{not-json", encoding="utf-8")
                elif scenario == "identity":
                    owner.target_ownership.canonical_repo = str(
                        Path(self.temp.name) / "different-repo"
                    )
                    orchestrator.persist(owner, runs_dir)
                else:
                    owner.target_ownership.acquired_at = (
                        "2026-08-02T00:00:00+00:00"
                    )
                    orchestrator.persist(owner, runs_dir)

                with self.assertRaises(orchestrator.ControllerError):
                    controller.new_run("009")
                self.assertEqual(calls, calls_before)

        for scenario in ("corrupt", "wrong-schema"):
            with self.subTest(database=scenario):
                runs_dir = self.runs / f"database-{scenario}"
                coordinator = orchestrator.TargetCoordinator(self.repo, runs_dir)
                coordinator.database.parent.mkdir(parents=True, exist_ok=True)
                if scenario == "corrupt":
                    coordinator.database.write_bytes(b"not a sqlite database")
                else:
                    connection = orchestrator.sqlite3.connect(coordinator.database)
                    connection.execute(
                        "CREATE TABLE coordination_meta ("
                        "singleton INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL)"
                    )
                    connection.execute(
                        "INSERT INTO coordination_meta VALUES (1, 999)"
                    )
                    connection.commit()
                    connection.close()
                calls = 0

                def unexpected(prompt, repo):
                    nonlocal calls
                    calls += 1
                    return self.passing_sonnet(prompt, repo)

                with self.assertRaises(orchestrator.ControllerError):
                    self.controller(unexpected, runs_dir=runs_dir).new_run("009")
                self.assertEqual(calls, 0)
                self.assertEqual(list(runs_dir.glob("*.json")), [])

    def test_transaction_cleanup_on_exception_and_abrupt_child_exit(self) -> None:
        run = self.controller(
            lambda prompt, repo: review_execution(
                "FAIL", "POLICY_AMBIGUITY", "crash fixture"
            ),
            terra=lambda prompt, repo: ProviderExecution(
                ["codex"], 0, "human decision required", ""
            ),
        ).new_run("009")
        coordinator = orchestrator.TargetCoordinator(self.repo, self.runs)

        with self.assertRaisesRegex(RuntimeError, "synthetic crash"):
            with coordinator.transaction():
                raise RuntimeError("synthetic crash")
        with coordinator.transaction() as connection:
            self.assertEqual(coordinator._owner(connection)["run_id"], run.run_id)

        source = (
            "import os,sys\n"
            "from pathlib import Path\n"
            "import orchestrator\n"
            "coordinator = orchestrator.TargetCoordinator("
            "Path(sys.argv[1]), Path(sys.argv[2]))\n"
            "with coordinator.transaction():\n"
            "    os._exit(17)\n"
        )
        child = subprocess.run(
            [sys.executable, "-c", source, str(self.repo), str(self.runs)],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(child.returncode, 17, child.stderr)
        with coordinator.transaction() as connection:
            self.assertEqual(coordinator._owner(connection)["run_id"], run.run_id)

    def test_claim_crash_boundaries_and_writer_owner_cannot_be_bypassed(self) -> None:
        orphan_runs = self.runs / "claim-rollback"
        orphan = WorkflowRun(
            run_id="claim-rollback-orphan",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="0" * 64,
            specification="Claim rollback fixture.",
            resolved_correction_policy=resolve_correction_policy(),
            repo=orchestrator.repo_state(self.repo),
        )
        coordinator = orchestrator.TargetCoordinator(self.repo, orphan_runs)
        original_claim = coordinator._claim_legacy

        def crash_after_claim(connection, run):
            original_claim(connection, run)
            raise RuntimeError("crash before owner commit")

        with patch.object(
            coordinator,
            "_claim_legacy",
            side_effect=crash_after_claim,
        ):
            with self.assertRaisesRegex(RuntimeError, "before owner commit"):
                coordinator.claim_legacy(orphan)
        with coordinator.transaction() as connection:
            self.assertIsNone(coordinator._owner(connection))
        self.assertIsNotNone(
            orchestrator.load_run(orphan.run_id, orphan_runs).target_ownership
        )

        later = self.controller(
            lambda prompt, repo: review_execution(
                "FAIL", "POLICY_AMBIGUITY", "later clean claimant"
            ),
            terra=lambda prompt, repo: ProviderExecution(
                ["codex"], 0, "human decision required", ""
            ),
            runs_dir=orphan_runs,
        ).new_run("009")
        self.assertEqual(later.stage, "blocked_policy_ambiguity")

        committed_runs = self.runs / "claim-committed"
        created = WorkflowRun(
            run_id="claim-committed-before-provider",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="0" * 64,
            specification="Committed claim fixture.",
            resolved_correction_policy=resolve_correction_policy(),
            repo=orchestrator.repo_state(self.repo),
        )
        orchestrator.persist(created, committed_runs)
        committed_coordinator = orchestrator.TargetCoordinator(
            self.repo, committed_runs
        )
        committed_coordinator.claim_legacy(created)
        with committed_coordinator.transaction() as connection:
            self.assertEqual(
                committed_coordinator._owner(connection)["run_id"],
                created.run_id,
            )
        with self.assertRaisesRegex(
            orchestrator.ControllerError,
            "blocked or declined",
        ):
            self.controller(
                self.passing_sonnet,
                runs_dir=committed_runs,
            ).release_target(created.run_id, "not yet releasable")

        resumed = self.controller(
            lambda prompt, repo: review_execution(
                "FAIL", "POLICY_AMBIGUITY", "same owner resumes"
            ),
            terra=lambda prompt, repo: ProviderExecution(
                ["codex"], 0, "human decision required", ""
            ),
            runs_dir=committed_runs,
        ).resume(created.run_id)
        self.assertEqual(resumed.stage, "blocked_policy_ambiguity")

        writer_runs = self.runs / "writer-owner"
        writer = WorkflowRun(
            run_id="writer-block-owner",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="0" * 64,
            specification="Writer ownership fixture.",
            resolved_correction_policy=resolve_correction_policy(),
            repo=orchestrator.repo_state(self.repo),
            stage="blocked_writer_retry_required",
        )
        orchestrator.persist(writer, writer_runs)
        claimed_writer = self.controller(
            self.passing_sonnet,
            runs_dir=writer_runs,
        ).resume(writer.run_id)
        self.assertIsNotNone(claimed_writer.target_ownership)
        with self.assertRaisesRegex(
            orchestrator.ControllerError,
            "unresolved run",
        ):
            self.controller(
                self.passing_sonnet,
                runs_dir=writer_runs,
            ).new_run("009")

    def test_legacy_claim_round_trip_reporting_and_scope_boundaries(self) -> None:
        legacy_runs = self.runs / "legacy"
        legacy = WorkflowRun(
            run_id="legacy-unowned",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="0" * 64,
            specification="Legacy schema-six fixture.",
            resolved_correction_policy=resolve_correction_policy(),
            repo=orchestrator.repo_state(self.repo),
            stage="blocked_provider_failure",
        )
        path = orchestrator.persist(legacy, legacy_runs)
        before = path.read_bytes()
        loaded = orchestrator.load_run(legacy.run_id, legacy_runs)
        report = orchestrator._run_report(loaded)
        self.assertEqual(report["target_ownership_state"], "legacy")
        self.assertIsNone(report["target_key"])
        self.assertEqual(path.read_bytes(), before)

        resumed = self.controller(
            self.passing_sonnet,
            runs_dir=legacy_runs,
        ).resume(legacy.run_id)
        self.assertEqual(resumed.stage, "blocked_provider_failure")
        self.assertIsNotNone(resumed.target_ownership)
        self.assertEqual(
            orchestrator._run_report(resumed)["target_ownership_state"],
            "active",
        )

        released = TargetOwnership.model_validate(
            {
                **resumed.target_ownership.model_dump(),
                "released_at": "2026-08-02T00:01:00+00:00",
                "release_reason": "operator_released",
                "release_note": "bounded audit note",
            }
        )
        self.assertEqual(
            TargetOwnership.model_validate_json(
                released.model_dump_json()
            ).model_dump(),
            released.model_dump(),
        )

        coordinator = orchestrator.TargetCoordinator(self.repo, legacy_runs)
        self.assertEqual(coordinator.database.parent, legacy_runs / ".target-locks")
        source = Path(orchestrator.__file__).read_text(encoding="utf-8")
        target_source = source[source.index("class TargetCoordinator") :]
        self.assertNotIn("flock", target_source)
        self.assertIn("def _migration_write_lock", source)
        self.assertNotIn("force-unlock", source)
        self.assertIn("JOBS_REPO", source)
        self.assertTrue((Path(__file__).parent / "src/jobs_orchestrator").is_dir())

    def test_private_storage_failure_precedes_provider_and_git_work(self) -> None:
        self.runs.mkdir(mode=0o700)
        target = Path(self.temp.name) / "outside.json"
        target.write_text("outside secret\n", encoding="utf-8")
        target.chmod(0o640)
        (self.runs / "unsafe.json").symlink_to(target)
        provider_calls = 0
        before_head = git(self.repo, "rev-parse", "HEAD")

        def review(prompt, repo):
            nonlocal provider_calls
            provider_calls += 1
            return self.passing_sonnet(prompt, repo)

        with self.assertRaisesRegex(orchestrator.ControllerError, "symlink"):
            self.controller(review).new_run("009")

        self.assertEqual(provider_calls, 0)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), before_head)
        self.assertEqual(git(self.repo, "status", "--porcelain=v1"), "")
        self.assertEqual(target.read_text(encoding="utf-8"), "outside secret\n")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)

        safe_runs = Path(self.temp.name) / "safe-runs"
        reader_calls = 0
        writer_calls = 0

        def assert_private_storage() -> None:
            self.assertEqual(stat.S_IMODE(safe_runs.stat().st_mode), 0o700)
            records = list(safe_runs.glob("*.json"))
            self.assertEqual(len(records), 1)
            self.assertEqual(stat.S_IMODE(records[0].stat().st_mode), 0o600)
            coordinator = orchestrator.TargetCoordinator(self.repo, safe_runs)
            self.assertEqual(
                stat.S_IMODE(coordinator.database.parent.stat().st_mode), 0o700
            )
            self.assertEqual(stat.S_IMODE(coordinator.database.stat().st_mode), 0o600)

        def safe_review(prompt, repo):
            nonlocal reader_calls
            reader_calls += 1
            assert_private_storage()
            return self.passing_sonnet(prompt, repo)

        def safe_writer(prompt, repo):
            nonlocal writer_calls
            writer_calls += 1
            assert_private_storage()
            return ProviderExecution(["codex"], 1, "", "synthetic writer failure")

        result = self.controller(
            safe_review,
            luna=safe_writer,
            runs_dir=safe_runs,
        ).new_run("009")
        self.assertEqual(result.stage, "blocked_writer_retry_required")
        self.assertEqual(reader_calls, 1)
        self.assertEqual(writer_calls, 1)


class StableProviderIdentityTests(unittest.TestCase):
    def run_fixture(self, **updates) -> WorkflowRun:
        values = {
            "run_id": "identity-fixture",
            "created_at": "2026-08-02T00:00:00+00:00",
            "task_ref": "identity",
            "task_file": "tasks/identity.md",
            "task_sha256": "0" * 64,
            "specification": "Stable identity fixture.",
            "resolved_correction_policy": resolve_correction_policy(),
            "repo": RepoState(
                repo="/fixture/repo",
                branch="main",
                head="1" * 40,
                clean=True,
                origin="https://example.invalid/repo.git",
            ),
        }
        values.update(updates)
        return WorkflowRun(**values)

    def test_catalog_is_closed_unique_and_current_schema_has_no_legacy_fields(self) -> None:
        routes = tuple(orchestrator.ROUTE_IDENTITIES.values())
        self.assertEqual(
            [route.role_id for route in routes],
            [
                "implementation",
                "adversarial_review",
                "escalation_executive",
                "policy_authority",
            ],
        )
        self.assertEqual(len({route.route_id for route in routes}), 4)
        self.assertEqual(
            {route.provider_adapter_id for route in routes},
            {"codex_cli", "claude_cli"},
        )

        record = provider_record(
            ADVERSARIAL_REVIEW_ROUTE,
            "specification_review",
            command=["arbitrary-command-with-Luna-High"],
            returncode=0,
            stdout=review_execution("PASS", "PASS").stdout,
        )
        run = self.run_fixture(provider_runs=[record])
        dumped = run.model_dump(mode="json")
        self.assertEqual(dumped["schema_version"], 12)
        self.assertIsNone(dumped["migration_audit"])
        self.assertIsNone(dumped["identity_migration_audit"])
        self.assertNotIn("provider", dumped["provider_runs"][0])
        self.assertNotIn("purpose", dumped["provider_runs"][0])

    def test_catalog_and_operation_authority_are_runtime_immutable(self) -> None:
        with self.assertRaises(TypeError):
            orchestrator.ROUTE_IDENTITIES["implementation"] = (
                ADVERSARIAL_REVIEW_ROUTE
            )
        with self.assertRaises(TypeError):
            del orchestrator.ROUTE_IDENTITIES["implementation"]
        with self.assertRaises(TypeError):
            orchestrator.OPERATION_ROLES["implementation_write"] = (
                "adversarial_review"
            )
        self.assertIs(
            orchestrator.ROUTE_IDENTITIES["implementation"],
            IMPLEMENTATION_ROUTE,
        )
        self.assertEqual(
            orchestrator.OPERATION_ROLES["implementation_write"],
            "implementation",
        )

    def test_display_is_presentation_only_but_control_identity_is_closed(self) -> None:
        renamed_review = ADVERSARIAL_REVIEW_ROUTE.model_copy(
            update={"display_name": "Luna High"}
        )
        review_result = ReviewResult(
            status="FAIL",
            category="IMPLEMENTATION_DEFECT",
            finding_key="test-finding",
            summary="renamed reviewer fixture",
        )
        run = self.run_fixture(
            provider_runs=[
                provider_record(
                    renamed_review,
                    "implementation_review",
                    command=["claude"],
                    returncode=0,
                    stdout=review_execution(
                        "FAIL",
                        "IMPLEMENTATION_DEFECT",
                    ).stdout,
                )
            ],
            review_records=[
                orchestrator.ReviewRecord(
                    recorded_at="2026-08-02T00:00:00+00:00",
                    operation_id="implementation_review",
                    result=review_result,
                    provider_record_index=0,
                )
            ],
            implementation_review=review_result,
        )
        self.assertEqual(
            orchestrator._implementation_review_history(run)[0].status,
            "FAIL",
        )
        self.assertEqual(
            orchestrator._run_report(run)["role_logical_invocation_counts"],
            {"adversarial_review": 1},
        )

        for field, value in (
            ("role_id", "policy_authority"),
            ("provider_adapter_id", "codex_cli"),
            ("route_id", "builtin.policy_authority.v1"),
            ("model_id", "gpt-5.6-terra"),
        ):
            invalid = ADVERSARIAL_REVIEW_ROUTE.model_copy(update={field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.run_fixture(
                    provider_runs=[
                        provider_record(
                            invalid,
                            "implementation_review",
                            command=["fixture"],
                            returncode=0,
                        )
                    ]
                )

    def test_adapter_not_display_selects_protocol_normalization(self) -> None:
        _, native_error = claude_fixture("provider_error")
        review_run = self.run_fixture(run_id="review-adapter")
        renamed_review = ADVERSARIAL_REVIEW_ROUTE.model_copy(
            update={"display_name": "shared display"}
        )
        normalized = orchestrator._record_provider(
            review_run,
            "specification_review",
            renamed_review,
            native_error,
            capability="read_only",
        )
        self.assertEqual(normalized.failure_source, "provider_native")

        writer_run = self.run_fixture(run_id="writer-adapter")
        copied_display = IMPLEMENTATION_ROUTE.model_copy(
            update={"display_name": ADVERSARIAL_REVIEW_ROUTE.display_name}
        )
        ordinary = orchestrator._record_provider(
            writer_run,
            "implementation_write",
            copied_display,
            native_error,
            capability="workspace_write",
        )
        self.assertNotEqual(ordinary.failure_source, "provider_native")

    def test_operation_capability_pending_and_writer_links_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            orchestrator.ControllerError,
            "operation does not match role",
        ):
            orchestrator._record_provider(
                self.run_fixture(),
                "policy_clarification",
                IMPLEMENTATION_ROUTE,
                ProviderExecution(["fixture"], 0, "", ""),
                capability="workspace_write",
            )
        with self.assertRaisesRegex(
            orchestrator.ControllerError,
            "capability does not match role",
        ):
            orchestrator._record_provider(
                self.run_fixture(),
                "implementation_write",
                IMPLEMENTATION_ROUTE,
                ProviderExecution(["fixture"], 0, "", ""),
                capability="read_only",
            )
        with self.assertRaisesRegex(ValueError, "resume state must be complete"):
            self.run_fixture(
                provider_resume_stage="reviewing",
                provider_resume_prompt="saved prompt",
            )

        linked_record = provider_record(
            ADVERSARIAL_REVIEW_ROUTE.model_copy(
                update={"display_name": IMPLEMENTATION_ROUTE.display_name}
            ),
            "implementation_review",
            command=["fixture"],
            returncode=0,
        )
        with self.assertRaisesRegex(ValueError, "writer provider record link"):
            self.run_fixture(
                provider_runs=[linked_record],
                active_writer_attempt=WriterAttemptState(
                    attempt_id="writer-link",
                    stage="implementing",
                    purpose="implementation",
                    pre_fingerprint="a" * 64,
                    pre_changed_files=[],
                    provider_record_index=0,
                ),
            )

    def test_retry_attempts_keep_one_identity_and_operation(self) -> None:
        run = self.run_fixture()
        execution = ProviderExecution(
            command=["claude"],
            returncode=0,
            stdout=review_execution("PASS", "PASS").stdout,
            stderr="",
            attempts=(
                ProviderAttempt(
                    command=["claude"],
                    returncode=1,
                    stderr="HTTP 503 Service Unavailable",
                    failure_kind="unavailable",
                    failure_source="stderr",
                    failure_code="503",
                    retry_scheduled=True,
                ),
                ProviderAttempt(
                    command=["claude"],
                    returncode=0,
                    stdout=review_execution("PASS", "PASS").stdout,
                ),
            ),
        )
        renamed = ADVERSARIAL_REVIEW_ROUTE.model_copy(
            update={"display_name": "Reviewer"}
        )
        orchestrator._record_provider(
            run,
            "specification_review",
            renamed,
            execution,
            capability="read_only",
        )
        self.assertEqual(len(run.provider_runs), 2)
        self.assertTrue(run.provider_runs[0].retry_scheduled)
        self.assertFalse(run.provider_runs[1].retry_scheduled)
        self.assertEqual(
            {
                (
                    record.identity.role_id,
                    record.identity.provider_adapter_id,
                    record.identity.route_id,
                    record.identity.model_id,
                    record.identity.display_name,
                    record.operation_id,
                    record.capability,
                )
                for record in run.provider_runs
            },
            {
                (
                    "adversarial_review",
                    "claude_cli",
                    "builtin.adversarial_review.v1",
                    "sonnet",
                    "Reviewer",
                    "specification_review",
                    "read_only",
                )
            },
        )

    def test_policy_link_and_full_identity_round_trip_ignore_recommendation_text(self) -> None:
        renamed_policy = POLICY_AUTHORITY_ROUTE.model_copy(
            update={"display_name": "Reviewer"}
        )
        record = provider_record(
            renamed_policy,
            "policy_clarification",
            command=["codex"],
            returncode=0,
            stdout="Use Luna High and Sonnet 5 High in prose only.",
        )
        decision = orchestrator.PolicyDecision(
            decision_id="policy-01",
            approved_at="2026-08-02T00:01:00+00:00",
            trigger_summary="The model text says Terra High.",
            recommendation="Pretend the source is Sol High.",
            approved_text="Human-approved text.",
            source_provider_record_index=0,
        )
        run = self.run_fixture(
            provider_runs=[record],
            policy_decisions=[decision],
            provider_resume_stage="terra_resolving",
            provider_resume_prompt="saved prompt",
            provider_resume_identity=renamed_policy,
            provider_resume_operation_id="policy_clarification",
        )
        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory)
            orchestrator.persist(run, runs)
            loaded = orchestrator.load_run(run.run_id, runs)
        self.assertEqual(loaded.model_dump(), run.model_dump())
        self.assertEqual(
            loaded.policy_decisions[0].source_role_id,
            "policy_authority",
        )
        self.assertEqual(
            loaded.policy_decisions[0].source_route_id,
            POLICY_AUTHORITY_ROUTE.route_id,
        )

    def test_migrated_v8_run_is_refused_with_identity_audit_disposition(self) -> None:
        identity_audit = IdentityMigrationAudit(
            migration_id="refusal-test",
            migrated_at="2026-08-02T12:00:00+00:00",
            source_schema_version=7,
            target_schema_version=8,
            source_structural_class="V7",
            source_sha256="ab" * 32,
            applied_steps=("7_to_8",),
            reason_codes=(),
            disposition="resume_eligibility_deferred",
        )
        run = self.run_fixture(identity_migration_audit=identity_audit)
        with self.assertRaisesRegex(
            orchestrator.ControllerError,
            "run execution refused: migrated record disposition is "
            "resume_eligibility_deferred",
        ):
            orchestrator.Controller._require_executable(run)


class PrivateStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "target"
        self.repo.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_fixture(self, run_id: str = "private-run") -> WorkflowRun:
        return WorkflowRun(
            run_id=run_id,
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-private.md",
            task_sha256="0" * 64,
            specification="sensitive specification fixture",
            resolved_correction_policy=resolve_correction_policy(),
            repo=RepoState(
                repo=str(self.repo),
                branch="main",
                head="1" * 40,
                clean=True,
                origin="https://example.invalid/jobs.git",
            ),
        )

    def mode(self, path: Path) -> int:
        return stat.S_IMODE(os.lstat(path).st_mode)

    def test_private_creation_is_exact_under_hostile_umasks(self) -> None:
        ancestor = self.root / "existing"
        ancestor.mkdir(mode=0o755)
        ancestor.chmod(0o755)
        for mask in (0o000, 0o077):
            with self.subTest(mask=oct(mask)):
                runs = ancestor / f"nested-{mask:o}" / "runs"
                observed = []
                real_replace = os.replace

                def observe_replace(source, destination):
                    temporary = Path(source)
                    observed.append((self.mode(temporary), temporary.read_text()))
                    real_replace(source, destination)

                previous = os.umask(mask)
                try:
                    with patch.object(orchestrator.os, "replace", observe_replace):
                        path = orchestrator.persist(self.run_fixture(), runs)
                finally:
                    os.umask(previous)

                self.assertEqual(self.mode(ancestor), 0o755)
                self.assertEqual(self.mode(runs.parent), 0o700)
                self.assertEqual(self.mode(runs), 0o700)
                self.assertEqual(self.mode(path), 0o600)
                self.assertEqual(observed[0][0], 0o600)
                self.assertIn("sensitive specification fixture", observed[0][1])
                self.assertEqual(
                    orchestrator.load_run("private-run", runs).model_dump(),
                    self.run_fixture().model_dump(),
                )

    def test_legacy_scan_is_bounded_content_preserving_and_idempotent(self) -> None:
        runs = self.root / "legacy"
        locks = runs / ".target-locks"
        unknown_dir = runs / "unknown"
        locks.mkdir(parents=True)
        unknown_dir.mkdir()
        record = runs / "legacy.json"
        invalid = runs / "invalid.json"
        old_temp = runs / "legacy.json.tmp"
        database = locks / "target.sqlite3"
        journal = locks / "target.sqlite3-journal"
        unknown = runs / "notes.txt"
        nested_unknown = unknown_dir / "nested.json"
        record.write_text(self.run_fixture("legacy").model_dump_json() + "\n")
        invalid.write_text("{ invalid schema fixture\n")
        old_temp.write_text("private orphan fixture\n")
        database.write_bytes(b"database fixture")
        journal.write_bytes(b"journal fixture")
        unknown.write_text("unknown fixture\n")
        nested_unknown.write_text("nested unknown fixture\n")
        for path in (runs, locks):
            path.chmod(0o755)
        for path in (
            record,
            invalid,
            old_temp,
            database,
            journal,
            unknown,
            nested_unknown,
        ):
            path.chmod(0o644)
        before = {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (
                record,
                invalid,
                old_temp,
                database,
                journal,
                unknown,
                nested_unknown,
            )
        }

        result = orchestrator._prepare_private_storage(runs)

        self.assertEqual(result.hardened_directories, 2)
        self.assertEqual(result.hardened_files, 5)
        for path in (runs, locks):
            self.assertEqual(self.mode(path), 0o700)
        for path in (record, invalid, old_temp, database, journal):
            self.assertEqual(self.mode(path), 0o600)
        self.assertEqual(self.mode(unknown), 0o644)
        self.assertEqual(self.mode(unknown_dir), 0o755)
        self.assertEqual(self.mode(nested_unknown), 0o644)
        for path, snapshot in before.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), snapshot)

        second = orchestrator._prepare_private_storage(runs)
        self.assertEqual(second.hardened_directories, 0)
        self.assertEqual(second.hardened_files, 0)
        with self.assertRaisesRegex(
            orchestrator.ControllerError, "run state is invalid"
        ) as raised:
            orchestrator.load_run("invalid", runs)
        self.assertNotIn("invalid schema fixture", str(raised.exception))
        self.assertEqual(invalid.read_bytes(), before[invalid][0])
        self.assertEqual(invalid.stat().st_mtime_ns, before[invalid][1])

    def test_run_ids_are_rejected_before_storage_creation(self) -> None:
        for index, run_id in enumerate(("", ".", "..", "a/b", "a\\b", "a\0b")):
            with self.subTest(run_id=repr(run_id)):
                runs = self.root / f"invalid-{index}"
                run = self.run_fixture(run_id)
                with self.assertRaisesRegex(orchestrator.ControllerError, "safe identifier"):
                    orchestrator.persist(run, runs)
                self.assertFalse(runs.exists())
                with self.assertRaisesRegex(orchestrator.ControllerError, "safe identifier"):
                    orchestrator.load_run(run_id, runs)
                self.assertFalse(runs.exists())

    def test_unsafe_directories_and_recognized_files_fail_closed(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        outside.chmod(0o755)
        symlink_root = self.root / "symlink-runs"
        symlink_root.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(orchestrator.ControllerError, "symlink"):
            orchestrator.persist(self.run_fixture(), symlink_root)
        self.assertEqual(self.mode(outside), 0o755)

        nondirectory_root = self.root / "not-a-directory"
        nondirectory_root.write_text("not a directory fixture\n")
        with self.assertRaisesRegex(orchestrator.ControllerError, "not a directory"):
            orchestrator.persist(self.run_fixture(), nondirectory_root)

        foreign_root = self.root / "foreign-directory"
        foreign_root.mkdir(mode=0o700)
        real_lstat = orchestrator._lstat

        def foreign_directory_lstat(path):
            state = real_lstat(path)
            if state is not None and Path(path) == foreign_root:
                values = list(state)
                values[4] = os.geteuid() + 1
                return os.stat_result(values)
            return state

        with patch.object(
            orchestrator, "_lstat", foreign_directory_lstat
        ), self.assertRaisesRegex(orchestrator.ControllerError, "foreign owner"):
            orchestrator.persist(self.run_fixture(), foreign_root)

        for kind in ("symlink", "hardlink", "fifo", "foreign"):
            with self.subTest(kind=kind):
                runs = self.root / f"unsafe-{kind}"
                runs.mkdir(mode=0o700)
                artifact = runs / "unsafe.json"
                target = self.root / f"target-{kind}.json"
                target.write_text("target fixture\n")
                target.chmod(0o640)
                if kind == "symlink":
                    artifact.symlink_to(target)
                elif kind == "hardlink":
                    os.link(target, artifact)
                elif kind == "fifo":
                    os.mkfifo(artifact, 0o600)
                else:
                    artifact.write_text("foreign fixture\n")
                    real_lstat = orchestrator._lstat

                    def foreign_lstat(path):
                        state = real_lstat(path)
                        if state is not None and Path(path) == artifact:
                            values = list(state)
                            values[4] = os.geteuid() + 1
                            return os.stat_result(values)
                        return state

                context = (
                    patch.object(orchestrator, "_lstat", foreign_lstat)
                    if kind == "foreign"
                    else patch.object(
                        orchestrator, "_lstat", wraps=orchestrator._lstat
                    )
                )
                with context, self.assertRaises(orchestrator.ControllerError):
                    orchestrator._prepare_private_storage(runs)
                self.assertEqual(target.read_text(), "target fixture\n")
                self.assertEqual(self.mode(target), 0o640)

    def test_atomic_replace_failure_preserves_old_record_and_cleans_temp(self) -> None:
        runs = self.root / "atomic"
        run = self.run_fixture("atomic")
        path = orchestrator.persist(run, runs)
        before = path.read_bytes()
        run.stage = "changed"

        with patch.object(orchestrator.os, "replace", side_effect=PermissionError):
            with self.assertRaisesRegex(orchestrator.ControllerError, "atomic persistence"):
                orchestrator.persist(run, runs)

        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(runs.glob(".continuo-run-*.tmp")), [])
        self.assertEqual(orchestrator.load_run("atomic", runs).stage, "created")

    def test_interrupted_hardening_is_monotonic_and_resumable(self) -> None:
        runs = self.root / "interrupted"
        runs.mkdir(mode=0o700)
        first = runs / "a.json"
        second = runs / "b.json"
        first.write_text(self.run_fixture("a").model_dump_json())
        second.write_text(self.run_fixture("b").model_dump_json())
        first.chmod(0o644)
        second.chmod(0o644)
        secure = orchestrator._secure_regular_file
        hardened = []

        def interrupt(path):
            if hardened:
                raise orchestrator.ControllerError(
                    "private storage rejected b.json: synthetic permission failure"
                )
            result = secure(path)
            hardened.append(Path(path))
            return result

        with patch.object(orchestrator, "_secure_regular_file", interrupt):
            with self.assertRaisesRegex(
                orchestrator.ControllerError, "synthetic permission failure"
            ):
                orchestrator._prepare_private_storage(runs)

        remaining = second if hardened == [first] else first
        self.assertEqual(self.mode(hardened[0]), 0o600)
        self.assertEqual(self.mode(remaining), 0o644)
        orchestrator._prepare_private_storage(runs)
        self.assertEqual(self.mode(first), 0o600)
        self.assertEqual(self.mode(second), 0o600)

    def test_abrupt_persist_crash_leaves_private_ignored_orphan(self) -> None:
        runs = self.root / "crash"
        run = self.run_fixture("crash")
        path = orchestrator.persist(run, runs)
        before = path.read_bytes()
        child = """
import os
from pathlib import Path
import orchestrator
from models import RepoState, WorkflowRun, resolve_correction_policy
runs = Path(os.environ['CONTINUO_TEST_RUNS'])
run = WorkflowRun(
    run_id='crash',
    created_at='2026-08-02T00:00:00+00:00',
    task_ref='009',
    task_file='tasks/009.md',
    task_sha256='0' * 64,
    specification='crash replacement',
    resolved_correction_policy=resolve_correction_policy(),
    repo=RepoState(
        repo=os.environ['CONTINUO_TEST_REPO'],
        branch='main',
        head='1' * 40,
        clean=True,
        origin='https://example.invalid/jobs.git',
    ),
    stage='changed',
)
orchestrator.os.replace = lambda source, destination: os._exit(17)
orchestrator.persist(run, runs)
"""
        environment = dict(os.environ)
        environment["CONTINUO_TEST_RUNS"] = str(runs)
        environment["CONTINUO_TEST_REPO"] = str(self.repo)
        result = subprocess.run(
            [sys.executable, "-c", child],
            cwd=Path(orchestrator.__file__).parent,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 17)
        self.assertEqual(path.read_bytes(), before)
        orphans = list(runs.glob(".continuo-run-*.tmp"))
        self.assertEqual(len(orphans), 1)
        self.assertEqual(self.mode(orphans[0]), 0o600)
        self.assertEqual([item.name for item in runs.glob("*.json")], ["crash.json"])
        self.assertEqual(orchestrator.load_run("crash", runs).stage, "created")

    def test_sqlite_main_rollback_and_wal_sidecars_are_private(self) -> None:
        runs = self.root / "sqlite"
        previous = os.umask(0o000)
        try:
            coordinator = orchestrator.TargetCoordinator(self.repo, runs)
            with coordinator.transaction() as connection:
                journal = Path(f"{coordinator.database}-journal")
                self.assertTrue(journal.exists())
                self.assertEqual(self.mode(runs), 0o700)
                self.assertEqual(self.mode(coordinator.database.parent), 0o700)
                self.assertEqual(self.mode(coordinator.database), 0o600)
                self.assertEqual(self.mode(journal), 0o600)

            wal_database = coordinator.database.parent / "fixture-wal.sqlite3"
            descriptor = os.open(
                wal_database,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            os.close(descriptor)
            connection = orchestrator.sqlite3.connect(wal_database)
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("CREATE TABLE fixture(value TEXT)")
                connection.execute("INSERT INTO fixture VALUES ('private')")
                connection.commit()
                orchestrator._prepare_private_storage(runs)
                self.assertEqual(self.mode(Path(f"{wal_database}-wal")), 0o600)
                self.assertEqual(self.mode(Path(f"{wal_database}-shm")), 0o600)
            finally:
                connection.close()
        finally:
            os.umask(previous)

    def test_legacy_and_corrupt_databases_harden_without_reset(self) -> None:
        runs = self.root / "database-legacy"
        coordinator = orchestrator.TargetCoordinator(self.repo, runs)
        with coordinator.transaction() as connection:
            connection.execute(
                "INSERT INTO target_owner VALUES (1, ?, ?, ?, ?, ?, ?)",
                (*coordinator.identity, "owner-run", "2026-08-02T00:00:00+00:00"),
            )
        coordinator.database.chmod(0o644)
        with coordinator.transaction() as connection:
            owner = connection.execute(
                "SELECT run_id FROM target_owner WHERE singleton = 1"
            ).fetchone()
            self.assertEqual(owner["run_id"], "owner-run")
        self.assertEqual(self.mode(coordinator.database), 0o600)

        corrupt_runs = self.root / "database-corrupt"
        corrupt_coordinator = orchestrator.TargetCoordinator(self.repo, corrupt_runs)
        orchestrator._prepare_private_storage(corrupt_runs, create_locks=True)
        corrupt_coordinator.database.write_bytes(b"not a sqlite database")
        corrupt_coordinator.database.chmod(0o644)
        before = corrupt_coordinator.database.read_bytes()
        with self.assertRaisesRegex(orchestrator.ControllerError, "invalid or unavailable"):
            with corrupt_coordinator.transaction():
                pass
        self.assertEqual(corrupt_coordinator.database.read_bytes(), before)
        self.assertEqual(self.mode(corrupt_coordinator.database), 0o600)

        linked_runs = self.root / "database-linked"
        linked_coordinator = orchestrator.TargetCoordinator(self.repo, linked_runs)
        orchestrator._prepare_private_storage(linked_runs, create_locks=True)
        linked_target = self.root / "linked-target.sqlite3"
        linked_target.write_bytes(b"external database fixture")
        linked_target.chmod(0o640)
        linked_coordinator.database.symlink_to(linked_target)
        with self.assertRaisesRegex(orchestrator.ControllerError, "symlink"):
            with linked_coordinator.transaction():
                pass
        self.assertEqual(linked_target.read_bytes(), b"external database fixture")
        self.assertEqual(self.mode(linked_target), 0o640)

    def test_inspection_surfaces_preserve_full_record_and_concise_redaction(self) -> None:
        runs = self.root / "inspection"
        run = self.run_fixture("inspection")
        orchestrator.persist(run, runs)
        run_path = runs / "inspection.json"
        run_path.chmod(0o644)

        with patch.object(
            orchestrator, "RUNS", runs
        ), orchestrator.console.capture() as capture:
            orchestrator.status(None)
        concise = capture.get()
        self.assertIn("inspection", concise)
        self.assertNotIn("sensitive specification fixture", concise)
        self.assertEqual(self.mode(run_path), 0o600)

        run_path.chmod(0o644)
        with patch.object(
            orchestrator, "RUNS", runs
        ), orchestrator.console.capture() as capture:
            orchestrator.report("inspection")
        report = capture.get()
        self.assertIn("Hardened legacy storage permissions", report)
        self.assertNotIn("sensitive specification fixture", report)
        self.assertEqual(self.mode(run_path), 0o600)

        run_path.chmod(0o644)
        with patch.object(
            orchestrator, "RUNS", runs
        ), orchestrator.console.capture() as capture:
            orchestrator.status("inspection")
        full = capture.get()
        self.assertIn("sensitive specification fixture", full)
        self.assertEqual(self.mode(run_path), 0o600)

    def test_scope_has_no_umask_chown_cleanup_export_or_schema_change(self) -> None:
        source = Path(orchestrator.__file__).read_text(encoding="utf-8")
        self.assertNotIn("os.umask", source)
        self.assertNotIn("os.chown", source)
        self.assertNotIn("force-unlock", source)
        self.assertEqual(self.run_fixture().schema_version, 12)
        command_names = {
            command.name or command.callback.__name__.replace("_", "-")
            for command in orchestrator.app.registered_commands
        }
        self.assertTrue(
            command_names.isdisjoint(
                {"export", "purge", "redact", "cleanup", "clean-runs"}
            )
        )


class ImmutableReviewHistoryTests(unittest.TestCase):
    """Gate 2.4 / C-6 matrix: parsed review history, recovery, visibility."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "jobs"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.email", "tests@example.invalid")
        git(self.repo, "config", "user.name", "Controller Tests")
        git(self.repo, "remote", "add", "origin", "https://example.invalid/jobs.git")
        (self.repo / "tasks").mkdir()
        (self.repo / "tasks/009-example.md").write_text("Implement the example task.\n")
        (self.repo / "README.md").write_text("fixture\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "fixture")
        self.runs = Path(self.temp.name) / "runs"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def controller(self, sonnet, terra=None, sol=None, luna=None, approval=None, runs_dir=None):
        return orchestrator.Controller(
            self.repo,
            runs_dir or self.runs,
            sonnet=sonnet,
            terra=terra or (lambda prompt, repo: providers.ProviderExecution(["codex"], 0, "resolved", "")),
            sol=sol or (lambda prompt, repo: providers.ProviderExecution(["codex"], 0, "GUIDANCE: bounded guidance", "")),
            luna=luna or self.luna,
            approval=approval,
        )

    def luna(self, prompt, repo):
        (repo / "implementation.py").write_text("# implementation\n")
        return providers.ProviderExecution(["codex"], 0, "implemented", "")

    def passing_sonnet(self, prompt, repo):
        return review_execution("PASS", "PASS")

    def run_fixture(self, **updates) -> WorkflowRun:
        values = {
            "run_id": "review-history-fixture",
            "created_at": "2026-08-02T00:00:00+00:00",
            "task_ref": "009",
            "task_file": "tasks/009-example.md",
            "task_sha256": "0" * 64,
            "specification": "Review history fixture.",
            "resolved_correction_policy": resolve_correction_policy(),
            "repo": RepoState(
                repo="/fixture/repo",
                branch="main",
                head="1" * 40,
                clean=True,
                origin="https://example.invalid/repo.git",
            ),
        }
        values.update(updates)
        return WorkflowRun(**values)

    def review_result(self, category, summary="fixture", finding_key=None):
        if category == "PASS":
            return ReviewResult(
                status="PASS",
                category="PASS",
                finding_key="PASS",
                summary=summary,
            )
        return ReviewResult(
            status="FAIL",
            category=category,
            finding_key=finding_key or "fixture-finding",
            summary=summary,
        )

    def review_record(self, index, result, operation="implementation_review"):
        return ReviewRecord(
            recorded_at="2026-08-02T00:00:00+00:00",
            operation_id=operation,
            result=result,
            provider_record_index=index,
        )

    def unreadable_record(self, index, reason="invalid_review_semantics", operation="implementation_review"):
        return UnreadableReviewRecord(
            recorded_at="2026-08-02T00:00:00+00:00",
            operation_id=operation,
            provider_record_index=index,
            reason_code=reason,
        )

    def raw_review(self, index, stdout, operation="implementation_review", returncode=0):
        return provider_record(
            ADVERSARIAL_REVIEW_ROUTE,
            operation,
            command=["claude", "fixture"],
            returncode=returncode,
            stdout=stdout,
        )

    def armed_review_run(self, stage, operation, *, stdout=None, **updates):
        run = WorkflowRun(
            run_id=f"{stage}-recovery",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="0" * 64,
            specification="Recovery fixture.",
            resolved_correction_policy=resolve_correction_policy(),
            repo=orchestrator.repo_state(self.repo),
            stage=stage,
            provider_resume_stage=stage,
            provider_resume_prompt="saved prompt",
            provider_resume_identity=ADVERSARIAL_REVIEW_ROUTE,
            provider_resume_operation_id=operation,
        )
        if stdout is not None:
            run.provider_runs.append(
                self.raw_review(0, stdout, operation=operation)
            )
        for key, value in updates.items():
            setattr(run, key, value)
        return run

    def test_i1_model_shapes_schema_and_no_raw_reparse_in_control(self) -> None:
        self.assertEqual(orchestrator.CURRENT_RUN_SCHEMA_VERSION, 12)
        self.assertEqual(self.run_fixture().schema_version, 12)
        for model in (
            orchestrator.ReviewResult,
            orchestrator.ReviewRecord,
            orchestrator.UnreadableReviewRecord,
            ReviewMigrationAudit,
        ):
            self.assertTrue(model.model_config.get("frozen"))
            self.assertEqual(model.model_config.get("extra"), "forbid")
            self.assertEqual(model.model_config.get("strict"), True)
        self.assertNotIn("purpose", orchestrator.ReviewRecord.model_fields)
        self.assertNotIn("purpose", orchestrator.UnreadableReviewRecord.model_fields)
        self.assertEqual(
            set(orchestrator.ReviewRecord.model_fields["operation_id"].annotation.__args__),
            {"specification_review", "implementation_review"},
        )
        self.assertNotIn("sol_guidance", orchestrator.ReviewRecord.model_fields)
        self.assertNotIn("terra_resolution", orchestrator.ReviewRecord.model_fields)
        source = Path(orchestrator.__file__).read_text(encoding="utf-8")
        self.assertNotIn("parse_sonnet_review(ProviderExecution", source)
        self.assertEqual(
            source.count("parse_sonnet_review("),
            2,
        )
        self.assertEqual(
            Path(run_migrations.__file__)
            .read_text(encoding="utf-8")
            .count("parse_sonnet_review("),
            1,
        )

    def test_review_record_result_is_deeply_immutable(self) -> None:
        result = self.review_result(
            "IMPLEMENTATION_DEFECT",
            "immutable parsed result",
        )
        record = self.review_record(0, result)

        with self.assertRaises(ValueError):
            record.result.summary = "retroactively changed"
        with self.assertRaises(ValueError):
            result.finding_key = "changed-finding"

        self.assertEqual(record.result.summary, "immutable parsed result")
        self.assertEqual(record.result.finding_key, "fixture-finding")

    def test_i2_raw_stdout_is_audit_only_and_never_a_control_oracle(self) -> None:
        result = self.review_result("IMPLEMENTATION_DEFECT", "fixture defect")
        clean_stdout = review_execution(
            "FAIL", "IMPLEMENTATION_DEFECT", "fixture defect", "fixture-finding"
        ).stdout
        edited_stdout = clean_stdout[:-10] + "EDITED"
        runs = []
        for stdout in (clean_stdout, "not json at all", edited_stdout):
            run = self.run_fixture(
                provider_runs=[self.raw_review(0, stdout)],
                review_records=[self.review_record(0, result)],
                implementation_review=result,
            )
            runs.append(run)
        histories = [orchestrator._implementation_review_history(run) for run in runs]
        self.assertEqual(histories[0], histories[1])
        self.assertEqual(histories[1], histories[2])
        self.assertEqual(
            [orchestrator._current_finding_streak(run, result) for run in runs],
            [1, 1, 1],
        )
        reports = [orchestrator._run_report(run) for run in runs]
        self.assertEqual(
            [report["distinct_defects"] for report in reports],
            [1, 1, 1],
        )
        self.assertEqual(
            [report["provider_physical_attempts_total"] for report in reports],
            [1, 1, 1],
        )

    def test_i3_only_implementation_operations_feed_control_and_spec_is_visible(self) -> None:
        spec = self.review_result("PASS", "spec pass")
        impl = self.review_result("IMPLEMENTATION_DEFECT", "impl defect")
        run = self.run_fixture(
            provider_runs=[
                self.raw_review(0, "raw spec", operation="specification_review"),
                self.raw_review(1, "raw impl"),
            ],
            review_records=[
                self.review_record(0, spec, operation="specification_review"),
                self.review_record(1, impl),
            ],
            spec_review=spec,
            implementation_review=impl,
        )
        self.assertEqual(
            [item.finding_key for item in orchestrator._implementation_review_history(run)],
            ["fixture-finding"],
        )
        self.assertEqual(
            [item.finding_key for item in orchestrator._report_review_history(run)],
            ["fixture-finding"],
        )
        self.assertEqual(orchestrator._current_finding_streak(run, impl), 1)
        self.assertEqual(len(run.review_records), 2)
        sol_prompt = orchestrator._sol_prompt(run, self.repo, "fixture-finding", 1)
        self.assertIn("impl defect", sol_prompt)
        self.assertNotIn("spec pass", sol_prompt)

    def test_i4_review_links_fail_closed_before_any_control_use(self) -> None:
        result = self.review_result("PASS")
        non_review = provider_record(
            IMPLEMENTATION_ROUTE,
            "implementation_write",
            command=["codex"],
            returncode=0,
            capability="workspace_write",
        )
        mismatch = provider_record(
            ADVERSARIAL_REVIEW_ROUTE,
            "specification_review",
            command=["claude"],
            returncode=0,
        )
        failed = provider_record(
            ADVERSARIAL_REVIEW_ROUTE,
            "implementation_review",
            command=["claude"],
            returncode=1,
            failure_kind="unavailable",
        )
        out_of_range = self.review_record(9, result)
        duplicated = self.review_record(0, result)
        variants = [
            ("non-review record", [non_review], [self.review_record(0, result)], None),
            ("mismatched operation", [mismatch], [self.review_record(0, result)], None),
            ("failed attempt", [failed], [self.review_record(0, result)], None),
            ("out of range", [self.raw_review(0, "x")], [out_of_range], None),
            (
                "duplicated index",
                [self.raw_review(0, "x")],
                [duplicated, self.unreadable_record(0)],
                None,
            ),
        ]
        for label, records, reviews, field in variants:
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    self.run_fixture(
                        provider_runs=records,
                        review_records=reviews,
                        implementation_review=field,
                    )

    def test_i5_current_field_contradictions_reject_and_migrated_need_reason(self) -> None:
        result = self.review_result("PASS")
        with self.assertRaisesRegex(ValueError, "contradicts parsed history"):
            self.run_fixture(
                provider_runs=[self.raw_review(0, "raw")],
                review_records=[self.review_record(0, result)],
                implementation_review=self.review_result("IMPLEMENTATION_DEFECT", "stale"),
            )
        with self.assertRaisesRegex(ValueError, "contradicts parsed history"):
            self.run_fixture(
                provider_runs=[self.raw_review(0, "raw")],
                review_records=[self.review_record(0, result)],
            )
        with self.assertRaisesRegex(ValueError, "contradicts parsed history"):
            self.run_fixture(
                implementation_review=self.review_result("IMPLEMENTATION_DEFECT"),
            )

        audit = ReviewMigrationAudit(
            migration_id="review-reason-audit",
            migrated_at="2026-08-02T12:00:00+00:00",
            source_schema_version=8,
            target_schema_version=9,
            source_structural_class="V8",
            source_sha256="ab" * 32,
            applied_steps=("8_to_9",),
            reason_codes=("current_review_unreadable",),
            parsed_count=1,
            unreadable_count=0,
            disposition="resume_eligibility_deferred",
        )
        migrated = self.run_fixture(
            provider_runs=[self.raw_review(0, "raw")],
            review_records=[self.review_record(0, result)],
            implementation_review=self.review_result("IMPLEMENTATION_DEFECT", "stale"),
            review_migration_audit=audit,
        )
        with self.assertRaisesRegex(
            orchestrator.ControllerError,
            "migrated record disposition is resume_eligibility_deferred",
        ):
            orchestrator.Controller._require_executable(migrated)

        missing_reason = audit.model_copy(
            update={"reason_codes": ("legacy_policy_source_attempt_unlinked",)}
        )
        with self.assertRaisesRegex(ValueError, "contradicts migrated history"):
            self.run_fixture(
                provider_runs=[self.raw_review(0, "raw")],
                review_records=[self.review_record(0, result)],
                implementation_review=self.review_result("IMPLEMENTATION_DEFECT", "stale"),
                review_migration_audit=missing_reason,
            )

    def test_p1_new_run_has_empty_review_state_and_no_audits(self) -> None:
        run = self.run_fixture()
        self.assertEqual(run.schema_version, 12)
        self.assertEqual(run.review_records, [])
        self.assertEqual(run.unreadable_review_records, [])
        self.assertIsNone(run.review_migration_audit)
        self.assertIsNone(run.identity_migration_audit)
        self.assertIsNone(run.migration_audit)
        dumped = run.model_dump(mode="json")
        for key in ("review_records", "unreadable_review_records", "review_migration_audit"):
            self.assertIn(key, dumped)
        self.assertNotIn("reviews", dumped)

    def test_p2_each_successful_review_persists_one_parsed_record_atomically(self) -> None:
        snapshots = []
        real_persist = orchestrator.persist

        def observe(run, runs_dir):
            snapshots.append(run.model_dump())
            return real_persist(run, runs_dir)

        with patch.object(orchestrator, "persist", side_effect=observe):
            run = self.controller(
                self.passing_sonnet,
                approval=lambda prompt: False,
            ).new_run("009")

        self.assertEqual(run.stage, "commit_declined")
        records = run.review_records
        self.assertEqual(len(records), 2)
        self.assertEqual(
            [record.operation_id for record in records],
            ["specification_review", "implementation_review"],
        )
        self.assertEqual(
            [record.provider_record_index for record in records],
            [0, 2],
        )
        spec_snapshot = next(
            snapshot
            for snapshot in snapshots
            if len(snapshot["review_records"]) == 1
        )
        self.assertEqual(spec_snapshot["stage"], "spec_review_passed")
        self.assertIsNotNone(spec_snapshot["spec_review"])
        self.assertIsNone(spec_snapshot["implementation_review"])
        impl_snapshot = next(
            snapshot
            for snapshot in snapshots
            if len(snapshot["review_records"]) == 2
        )
        self.assertEqual(impl_snapshot["stage"], "implementation_reviewed")
        self.assertIsNotNone(impl_snapshot["implementation_review"])

    def test_p3_transport_retries_yield_only_one_parsed_record(self) -> None:
        attempts = (
            providers.ProviderAttempt(
                ["claude"], 1, "", "HTTP 503", failure_kind="unavailable",
                failure_source="stderr", failure_code="503", retry_scheduled=True,
            ),
            providers.ProviderAttempt(
                ["claude"], 1, "", "HTTP 503", failure_kind="unavailable",
                failure_source="stderr", failure_code="503", retry_scheduled=True,
            ),
            providers.ProviderAttempt(
                ["claude"], 0, review_execution("PASS", "PASS").stdout, "",
            ),
        )
        execution = providers.ProviderExecution(
            ["claude"], 0, review_execution("PASS", "PASS").stdout, "",
            attempts=attempts,
        )
        run = self.run_fixture()
        result = self.controller(lambda prompt, repo: execution)._review(run, "specification")
        self.assertIsNotNone(result)
        self.assertEqual(len(run.provider_runs), 3)
        self.assertEqual(
            [record.retry_scheduled for record in run.provider_runs],
            [True, True, False],
        )
        self.assertEqual(len(run.review_records), 1)
        self.assertEqual(run.review_records[0].provider_record_index, 2)
        self.assertEqual(run.unreadable_review_records, [])

    def test_p4_content_retry_links_the_retry_attempt_exactly_once(self) -> None:
        calls = []

        def sonnet(prompt, repo):
            calls.append(1)
            if len(calls) == 1:
                return providers.ProviderExecution(
                    ["claude"], 0, "{malformed envelope", ""
                )
            return review_execution("PASS", "PASS")

        run = self.run_fixture()
        result = self.controller(sonnet)._review(run, "specification")
        self.assertIsNotNone(result)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(run.provider_runs), 2)
        self.assertEqual(len(run.review_records), 1)
        self.assertEqual(run.review_records[0].provider_record_index, 1)
        self.assertEqual(run.unreadable_review_records, [])

    def test_p5_crash_after_raw_save_recovery_reparses_once(self) -> None:
        run = self.armed_review_run(
            "reviewing",
            "implementation_review",
            stdout=review_execution(
                "FAIL", "IMPLEMENTATION_DEFECT", "crash fixture", "crash-key"
            ).stdout,
        )
        orchestrator.persist(run, self.runs)

        calls = []

        def unexpected_sonnet(prompt, repo):
            calls.append(1)
            return review_execution("PASS", "PASS")

        recovered = self.controller(
            unexpected_sonnet,
            approval=lambda prompt: False,
        ).resume(run.run_id)

        self.assertEqual(len(calls), 1)
        self.assertEqual(recovered.stage, "commit_declined")
        self.assertEqual(
            [
                record.result.finding_key
                for record in recovered.review_records
            ],
            ["crash-key", "PASS"],
        )
        self.assertEqual(
            recovered.review_records[0].provider_record_index,
            0,
        )
        self.assertEqual(
            recovered.implementation_review.finding_key,
            "PASS",
        )
        self.assertEqual(recovered.unreadable_review_records, [])
        self.assertIsNone(recovered.provider_resume_stage)

        persisted = json.loads(
            next(self.runs.glob(f"{run.run_id}.json")).read_text()
        )
        self.assertEqual(
            [item["provider_record_index"] for item in persisted["review_records"]],
            [0, 2],
        )

    def test_p6_full_current_state_round_trips_exactly(self) -> None:
        from test_run_migrations import v8_ordinary_payload, v8_review_record, review_stdout

        payload = v8_ordinary_payload(
            [
                v8_review_record(
                    "specification_review",
                    review_stdout("PASS", "PASS", "round trip"),
                ),
                v8_review_record(
                    "implementation_review",
                    review_stdout(
                        "FAIL",
                        "IMPLEMENTATION_DEFECT",
                        "round trip defect",
                        "round-trip-key",
                    ),
                ),
            ],
            spec_review={
                "status": "PASS",
                "category": "PASS",
                "finding_key": "PASS",
                "summary": "round trip",
            },
            implementation_review={
                "status": "FAIL",
                "category": "IMPLEMENTATION_DEFECT",
                "finding_key": "round-trip-key",
                "summary": "round trip defect",
            },
            run_id="round-trip-migrated",
        )
        migrated = run_migrations.migrate_classification(
            run_migrations.classify_run_bytes(
                (json.dumps(payload, ensure_ascii=False) + "\n").encode()
            ),
            migration_id="round-trip-migration",
            migrated_at="2026-08-02T12:00:00+00:00",
        ).run
        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory)
            orchestrator.persist(migrated, runs)
            loaded = orchestrator.load_run(migrated.run_id, runs)
        self.assertEqual(loaded.model_dump(), migrated.model_dump())
        self.assertEqual(len(loaded.review_records), 2)
        self.assertEqual(loaded.unreadable_review_records, [])
        self.assertIsNotNone(loaded.review_migration_audit)
        self.assertEqual(
            loaded.review_migration_audit.parsed_count,
            2,
        )

    def test_c1_display_renames_never_change_control_values(self) -> None:
        result = self.review_result("IMPLEMENTATION_DEFECT", "display fixture")
        renamed = ADVERSARIAL_REVIEW_ROUTE.model_copy(
            update={"display_name": IMPLEMENTATION_ROUTE.display_name}
        )
        run = self.run_fixture(
            provider_runs=[
                provider_record(
                    renamed,
                    "implementation_review",
                    command=["claude"],
                    returncode=0,
                    stdout=review_execution(
                        "FAIL",
                        "IMPLEMENTATION_DEFECT",
                    ).stdout,
                )
            ],
            review_records=[self.review_record(0, result)],
            implementation_review=result,
        )
        self.assertEqual(
            orchestrator._current_finding_streak(run, result),
            1,
        )
        self.assertEqual(orchestrator._run_report(run)["distinct_defects"], 1)
        prompt = orchestrator._sol_prompt(run, self.repo, "fixture-finding", 1)
        self.assertNotIn("Luna High", prompt)

    def test_c2_streak_counts_implementation_records_in_physical_order(self) -> None:
        first = self.review_result("IMPLEMENTATION_DEFECT", "first defect", "first-finding")
        second = self.review_result("IMPLEMENTATION_DEFECT", "second defect", "second-finding")
        run = self.run_fixture(
            provider_runs=[
                self.raw_review(0, "a", operation="specification_review"),
                self.raw_review(1, "b"),
                self.raw_review(2, "c", operation="specification_review"),
                self.raw_review(3, "d"),
            ],
            review_records=[
                self.review_record(0, first, operation="specification_review"),
                self.review_record(1, first),
                self.review_record(2, second, operation="specification_review"),
                self.review_record(3, first),
            ],
            implementation_review=first,
            spec_review=second,
        )
        self.assertEqual(orchestrator._current_finding_streak(run, first), 2)
        self.assertEqual(
            orchestrator._current_finding_streak(run, second),
            1,
        )

    def test_c3_r6_reparse_failure_blocks_with_marker_and_no_duplicate(self) -> None:
        malformed = self.armed_review_run(
            "reviewing",
            "implementation_review",
            stdout="{not valid",
        )
        malformed.unreadable_review_records = [
            self.unreadable_record(0, "invalid_review_envelope")
        ]
        orchestrator.persist(malformed, self.runs)

        calls = []

        def unexpected_sonnet(prompt, repo):
            calls.append(1)
            return providers.ProviderExecution(
                ["claude"], 0, "{not valid", ""
            )

        recovered = self.controller(unexpected_sonnet).resume(malformed.run_id)
        self.assertEqual(calls, [])
        self.assertEqual(recovered.stage, "blocked_provider_output")
        self.assertEqual(len(recovered.unreadable_review_records), 1)
        self.assertEqual(recovered.unreadable_review_records[0].provider_record_index, 0)
        self.assertEqual(recovered.review_records, [])

        resumed_again = self.controller(unexpected_sonnet).resume(malformed.run_id)
        self.assertEqual(resumed_again.stage, "blocked_provider_output")
        self.assertEqual(len(resumed_again.unreadable_review_records), 2)
        self.assertEqual(
            [
                record.provider_record_index
                for record in resumed_again.unreadable_review_records
            ],
            [0, 1],
        )
        self.assertEqual(resumed_again.review_records, [])
        self.assertEqual(len(resumed_again.provider_runs), 2)
        self.assertEqual(len(calls), 1)

    def test_c4_r5_recovery_consumes_persisted_record_without_reparse(self) -> None:
        result = self.review_result(
            "IMPLEMENTATION_DEFECT", "persisted recovery", "persisted-key"
        )
        run = self.armed_review_run(
            "reviewing",
            "implementation_review",
            stdout="{garbage that would never parse",
        )
        run.review_records = [self.review_record(0, result)]
        run.implementation_review = result
        orchestrator.persist(run, self.runs)

        calls = []

        def unexpected_sonnet(prompt, repo):
            calls.append(1)
            return review_execution("PASS", "PASS")

        recovered = self.controller(
            unexpected_sonnet,
            approval=lambda prompt: False,
        ).resume(run.run_id)

        self.assertEqual(len(calls), 1)
        self.assertEqual(recovered.stage, "commit_declined")
        self.assertEqual(
            [
                record.result.finding_key
                for record in recovered.review_records
            ],
            ["persisted-key", "PASS"],
        )
        self.assertEqual(
            recovered.review_records[0].provider_record_index,
            0,
        )
        self.assertEqual(
            recovered.implementation_review.finding_key,
            "PASS",
        )
        self.assertEqual(recovered.unreadable_review_records, [])

    def test_c5_streak_uses_parsed_history_and_two_sol_escalations_exact(self) -> None:
        sonnet_calls = []
        sol_calls = []

        def sonnet(prompt, repo):
            sonnet_calls.append(1)
            count = len(sonnet_calls)
            if count == 1:
                return review_execution("PASS", "PASS")
            if count <= 4:
                return review_execution(
                    "FAIL", "IMPLEMENTATION_DEFECT", "same defect", "same-key"
                )
            if count == 5:
                return review_execution(
                    "FAIL", "IMPLEMENTATION_DEFECT", "new defect", "new-key"
                )
            return review_execution("PASS", "PASS")

        def sol(prompt, repo):
            sol_calls.append(1)
            return providers.ProviderExecution(["codex"], 0, "GUIDANCE: bounded", "")

        run = self.controller(
            sonnet,
            sol=sol,
            approval=lambda prompt: False,
        ).new_run("009")

        self.assertEqual(run.stage, "commit_declined")
        self.assertEqual(len(sol_calls), 2)
        self.assertEqual(run.correction_cycles, 4)
        keys = [
            record.result.finding_key
            for record in run.review_records
            if record.operation_id == "implementation_review"
        ]
        self.assertEqual(
            keys,
            ["same-key", "same-key", "same-key", "new-key", "PASS"],
        )
        self.assertEqual(len(run.unreadable_review_records), 0)

    def test_c6_unreadable_markers_visible_and_never_change_values(self) -> None:
        result = self.review_result("IMPLEMENTATION_DEFECT", "visible defect")
        run = self.run_fixture(
            run_id="unreadable-report",
            provider_runs=[
                self.raw_review(0, "raw parsed"),
                self.raw_review(1, "truncated", returncode=0),
            ],
            review_records=[self.review_record(0, result)],
            unreadable_review_records=[
                self.unreadable_record(1, "invalid_review_envelope")
            ],
            implementation_review=result,
        )
        report = orchestrator._run_report(run)
        self.assertEqual(report["distinct_defects"], 1)
        self.assertEqual(report["unreadable_review_count"], 1)
        self.assertEqual(
            report["unreadable_review_records"],
            [
                {
                    "operation_id": "implementation_review",
                    "provider_record_index": 1,
                    "reason_code": "invalid_review_envelope",
                }
            ],
        )
        self.assertEqual(orchestrator._current_finding_streak(run, result), 1)
        sol_prompt = orchestrator._sol_prompt(run, self.repo, "fixture-finding", 1)
        self.assertIn("Finding 1 [fixture-finding]:", sol_prompt)
        self.assertIn(
            "Finding 1 (unreadable, invalid_review_envelope): "
            "review output is not parseable",
            sol_prompt,
        )

        with orchestrator.console.capture() as capture:
            orchestrator._print_run_report(run)
        output = capture.get()
        self.assertIn("1 unreadable review record(s)", output)
        self.assertIn("#1 implementation_review: invalid_review_envelope", output)
        self.assertNotIn("truncated", output)

        orchestrator.persist(run, self.runs)
        with patch.object(orchestrator, "RUNS", self.runs), orchestrator.console.capture() as capture:
            orchestrator.status(run.run_id)
        status_output = capture.get()
        self.assertIn("unreadable_review_records", status_output)
        self.assertIn("invalid_review_envelope", status_output)
        self.assertIn("review_records", status_output)

    def test_c7_prompts_render_parsed_summaries_and_explicit_unreadable(self) -> None:
        result = self.review_result("IMPLEMENTATION_DEFECT", "parsed summary")
        run = self.run_fixture(
            provider_runs=[
                self.raw_review(0, "SECRET RAW STDOUT", returncode=0),
                self.raw_review(1, "SECRET RAW STDOUT 2", returncode=0),
            ],
            review_records=[self.review_record(0, result)],
            unreadable_review_records=[
                self.unreadable_record(1, "invalid_review_semantics")
            ],
            implementation_review=result,
        )
        review_prompt = orchestrator._implementation_review_prompt(run, "FIXTURE DIFF")
        self.assertIn("parsed summary", review_prompt)
        self.assertIn("unreadable review record: invalid_review_semantics", review_prompt)
        self.assertNotIn("SECRET RAW STDOUT", review_prompt)

        sol_prompt = orchestrator._sol_prompt(run, self.repo, "fixture-finding", 1)
        self.assertIn("parsed summary", sol_prompt)
        self.assertIn("unreadable, invalid_review_semantics", sol_prompt)
        self.assertNotIn("SECRET RAW STDOUT", sol_prompt)

    def test_r1_unavailable_retry_keeps_one_identity_and_raw_only(self) -> None:
        attempts = (
            providers.ProviderAttempt(
                ["claude"], 1, "", "HTTP 503", failure_kind="unavailable",
                failure_source="stderr", failure_code="503", retry_scheduled=True,
            ),
            providers.ProviderAttempt(
                ["claude"], 1, "", "HTTP 503", failure_kind="unavailable",
                failure_source="stderr", failure_code="503", retry_scheduled=True,
            ),
            providers.ProviderAttempt(
                ["claude"], 0, review_execution("PASS", "PASS").stdout, "",
            ),
        )
        execution = providers.ProviderExecution(
            ["claude"], 0, review_execution("PASS", "PASS").stdout, "",
            attempts=attempts,
        )
        run = self.run_fixture()
        result = self.controller(lambda prompt, repo: execution)._review(run, "specification")
        self.assertIsNotNone(result)
        identities = {
            (
                record.identity.role_id,
                record.identity.provider_adapter_id,
                record.identity.route_id,
                record.identity.model_id,
                record.operation_id,
            )
            for record in run.provider_runs
        }
        self.assertEqual(
            identities,
            {
                (
                    "adversarial_review",
                    "claude_cli",
                    "builtin.adversarial_review.v1",
                    "sonnet",
                    "specification_review",
                )
            },
        )
        self.assertEqual(
            [record.failure_kind for record in run.provider_runs[:2]],
            ["unavailable", "unavailable"],
        )
        self.assertEqual(len(run.review_records), 1)

    def test_r2_content_retry_uses_same_route_and_operation(self) -> None:
        calls = []

        def sonnet(prompt, repo):
            calls.append(1)
            if len(calls) == 1:
                return providers.ProviderExecution(
                    ["claude"], 0, "{malformed envelope", ""
                )
            return review_execution("PASS", "PASS")

        run = self.run_fixture()
        result = self.controller(sonnet)._review(run, "specification")
        self.assertIsNotNone(result)
        for record in run.provider_runs:
            self.assertEqual(record.identity.route_id, "builtin.adversarial_review.v1")
            self.assertEqual(record.operation_id, "specification_review")
        self.assertEqual(run.review_records[0].provider_record_index, 1)
        self.assertEqual(len(run.provider_runs), 2)

    def test_r3_crash_before_raw_save_blocks_without_invocation(self) -> None:
        run = self.armed_review_run("reviewing", "implementation_review", stdout=None)
        orchestrator.persist(run, self.runs)

        calls = []

        def unexpected_sonnet(prompt, repo):
            calls.append(1)
            return review_execution("PASS", "PASS")

        recovered = self.controller(unexpected_sonnet).resume(run.run_id)
        self.assertEqual(calls, [])
        self.assertEqual(recovered.stage, "blocked_interrupted_provider")
        self.assertEqual(recovered.review_records, [])
        self.assertEqual(recovered.unreadable_review_records, [])

    def test_r4_crash_after_raw_save_both_review_stages_recover_once(self) -> None:
        for stage, operation in (
            ("spec_reviewing", "specification_review"),
            ("reviewing", "implementation_review"),
        ):
            with self.subTest(stage=stage):
                stdout = (
                    review_execution(
                        "PASS", "PASS", "spec recovery"
                    ).stdout
                    if stage == "spec_reviewing"
                    else review_execution(
                        "FAIL", "IMPLEMENTATION_DEFECT", "impl recovery", "recovery-key"
                    ).stdout
                )
                stage_runs = self.runs / stage
                run = self.armed_review_run(stage, operation, stdout=stdout)
                orchestrator.persist(run, stage_runs)

                calls = []

                def unexpected_sonnet(prompt, repo):
                    calls.append(1)
                    return review_execution("PASS", "PASS")

                recovered = self.controller(
                    unexpected_sonnet,
                    approval=lambda prompt: False,
                    runs_dir=stage_runs,
                ).resume(run.run_id)
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(recovered.review_records), 2)
                self.assertEqual(
                    recovered.review_records[0].provider_record_index,
                    0,
                )
                self.assertEqual(
                    recovered.review_records[0].operation_id,
                    operation,
                )
                self.assertEqual(recovered.unreadable_review_records, [])
                self.assertIsNone(recovered.provider_resume_stage)
                self.assertEqual(recovered.stage, "commit_declined")
                if stage == "spec_reviewing":
                    self.assertEqual(recovered.spec_review.category, "PASS")
                    self.assertEqual(
                        [
                            record.result.finding_key
                            for record in recovered.review_records
                        ],
                        ["PASS", "PASS"],
                    )
                else:
                    self.assertEqual(
                        [
                            record.result.finding_key
                            for record in recovered.review_records
                        ],
                        ["recovery-key", "PASS"],
                    )
                    self.assertEqual(
                        recovered.implementation_review.finding_key,
                        "PASS",
                    )

    def test_l1_report_and_status_expose_review_state_under_privacy_warning(self) -> None:
        run = self.controller(
            self.passing_sonnet,
            approval=lambda prompt: False,
        ).new_run("009")
        self.assertEqual(run.stage, "commit_declined")

        with orchestrator.console.capture() as capture:
            orchestrator._print_run_report(run)
        report_output = capture.get()
        self.assertIn("Distinct defects: 0", report_output)
        self.assertNotIn("unreadable review record(s)", report_output)

        with patch.object(orchestrator, "RUNS", self.runs), orchestrator.console.capture() as capture:
            orchestrator.status(run.run_id)
        status_output = capture.get()
        self.assertIn("review_records", status_output)
        self.assertIn("specification_review", status_output)
        self.assertIn("implementation_review", status_output)
        self.assertIn("unreadable_review_records", status_output)
        self.assertIn("Implement the example task.", status_output)

    def test_l2_historical_and_migrated_reads_remain_bounded(self) -> None:
        from test_run_migrations import schema8_bytes

        source = schema8_bytes()
        run_id = json.loads(source)["run_id"]
        self.runs.mkdir(mode=0o700, exist_ok=True)
        path = self.runs / f"{run_id}.json"
        path.write_bytes(source)
        path.chmod(0o600)

        with patch.object(orchestrator, "RUNS", self.runs), orchestrator.console.capture() as capture:
            orchestrator.status(run_id)
        bounded = capture.get()
        self.assertIn("MIGRATION_REQUIRED", bounded)
        self.assertNotIn("Synthetic current review output", bounded)

        with orchestrator.console.capture():
            with self.assertRaises(typer.Exit):
                orchestrator.report(run_id)

        with self.assertRaisesRegex(
            orchestrator.ControllerError,
            "migration_required",
        ):
            self.controller(self.passing_sonnet).resume(run_id)

        migrated = run_migrations.migrate_classification(
            run_migrations.classify_run_bytes(source),
            migration_id="l2-migration",
            migrated_at="2026-08-02T12:00:00+00:00",
        ).run
        path.write_bytes(migrated.model_dump_json().encode() + b"\n")
        path.chmod(0o600)
        with patch.object(orchestrator, "RUNS", self.runs), orchestrator.console.capture() as capture:
            orchestrator.status(run_id)
        blocked = capture.get()
        self.assertIn("RESUME_BLOCKED", blocked)
        self.assertNotIn("Synthetic current review output", blocked)
        with self.assertRaisesRegex(
            orchestrator.ControllerError,
            "run execution refused",
        ):
            self.controller(self.passing_sonnet).resume(run_id)

    def test_l3_commands_and_compatibility_shims_remain(self) -> None:
        command_names = {
            command.name or command.callback.__name__.replace("_", "-")
            for command in orchestrator.app.registered_commands
        }
        for expected in (
            "run",
            "resume",
            "recover-writer",
            "release-target",
            "approve-policy",
            "report",
            "migrate-run",
            "status",
        ):
            self.assertIn(expected, command_names)
        shim = Path(__file__).parent / "src" / "jobs_orchestrator" / "__init__.py"
        self.assertTrue(shim.is_file())
        self.assertIn("metadata.distribution(\"jobs-orchestrator\")", shim.read_text(encoding="utf-8"))

    def test_b1_b4_gate24_boundaries_and_deterministic_scope(self) -> None:
        source = Path(orchestrator.__file__).read_text(encoding="utf-8")
        models_source = Path(__file__).parent.joinpath("models.py").read_text(encoding="utf-8")
        migration_source = Path(run_migrations.__file__).read_text(encoding="utf-8")
        combined = source + models_source + migration_source
        for excluded in (
            "bulk-migrate",
            "--force",
            "doctor",
            "dry-run",
            "model_catalog",
            "route_override",
            "capability_discovery",
            "eventsourcing",
            "telemetry",
            "versioned_json",
            "resolved_policy",
            "logical_calls",
        ):
            self.assertNotIn(excluded, combined)
        self.assertIn('os.environ.get("JOBS_REPO"', source)
        review_models = models_source[
            models_source.index("class ReviewRecord") : models_source.index(
                "class RepoState"
            )
        ]
        self.assertNotIn("purpose", review_models)
        self.assertNotIn("sol_guidance", review_models)
        self.assertNotIn("terra_resolution", review_models)


class PersistedCorrectionPolicyTests(unittest.TestCase):
    def run_fixture(self, **updates) -> WorkflowRun:
        values = {
            "run_id": "policy-fixture",
            "created_at": "2026-08-02T00:00:00+00:00",
            "task_ref": "policy",
            "task_file": "tasks/policy.md",
            "task_sha256": "0" * 64,
            "specification": "Persisted policy fixture.",
            "repo": RepoState(
                repo="/fixture/repo",
                branch="main",
                head="1" * 40,
                clean=True,
                origin="https://example.invalid/repo.git",
            ),
            "resolved_correction_policy": resolve_correction_policy(),
        }
        values.update(updates)
        return WorkflowRun(**values)

    def test_g25_i1_i2_closed_frozen_policy_and_counter_validation(self) -> None:
        policy = resolve_correction_policy()
        self.assertEqual(policy.policy_id, "builtin.correction_escalation.v1")
        self.assertEqual(policy.maximum_total_corrections, 12)
        self.assertEqual(policy.maximum_sol_escalations_per_persistent_finding, 2)
        self.assertEqual(
            policy.persistent_finding_actions,
            (
                "ordinary_correction",
                "sol_guided_correction",
                "sol_guided_correction",
                "block",
            ),
        )
        for model in (ResolvedCorrectionPolicy, PolicyMigrationAudit):
            self.assertTrue(model.model_config.get("frozen"))
            self.assertTrue(model.model_config.get("strict"))
            self.assertEqual(model.model_config.get("extra"), "forbid")
        with self.assertRaises(Exception):
            policy.maximum_total_corrections = 13
        with self.assertRaises(ValueError):
            ResolvedCorrectionPolicy.model_validate(
                {
                    **policy.model_dump(),
                    "persistent_finding_actions": (
                        "ordinary_correction",
                        "ordinary_correction",
                        "ordinary_correction",
                        "block",
                    ),
                }
            )
        with self.assertRaises(ValueError):
            ResolvedCorrectionPolicy.model_validate(
                {**policy.model_dump(), "unknown": "not configuration"}
            )
        with self.assertRaisesRegex(ValueError, "ordinary run lacks"):
            self.run_fixture(resolved_correction_policy=None)
        with self.assertRaisesRegex(ValueError, "correction cycles exceed"):
            self.run_fixture(correction_cycles=13)
        self.assertNotIn(
            "le",
            WorkflowRun.model_fields["correction_cycles"].metadata,
        )

    def test_g25_p1_through_p7_saved_schedule_and_global_bound(self) -> None:
        run = self.run_fixture()
        self.assertEqual(
            [
                orchestrator._correction_action(run, occurrence)
                for occurrence in range(1, 5)
            ],
            [
                "ordinary_correction",
                "sol_guided_correction",
                "sol_guided_correction",
                "block",
            ],
        )
        self.assertEqual(orchestrator._correction_action(run, 20), "block")
        at_capacity = self.run_fixture(correction_cycles=12)
        self.assertEqual(orchestrator._correction_action(at_capacity, 1), "block")
        report = orchestrator._run_report(run)
        self.assertEqual(report["resolved_correction_policy"], policy_dump(run))

    def test_g25_c2_saved_snapshot_never_calls_resolver_for_decisions(self) -> None:
        run = self.run_fixture()
        with patch.object(
            orchestrator,
            "resolve_correction_policy",
            side_effect=AssertionError("resolver must only serve new runs"),
        ):
            self.assertEqual(
                orchestrator._correction_action(run, 2),
                "sol_guided_correction",
            )


def policy_dump(run: WorkflowRun) -> dict[str, object]:
    policy = run.resolved_correction_policy
    if policy is None:
        raise AssertionError("ordinary fixture requires a policy")
    return policy.model_dump(mode="json")


class ProviderFailureContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_recorded_and_derived_fixtures_verify_and_split_protocol_outcomes(self) -> None:
        fixture, success = claude_fixture("success")
        self.assertEqual(fixture["provenance"]["kind"], "recorded_sanitized")
        self.assertEqual(providers.parse_sonnet_review(success).status, "PASS")

        for name in (
            "malformed_envelope",
            "missing_structured_output",
            "schema_invalid",
        ):
            with self.subTest(name=name):
                fixture, execution = claude_fixture(name)
                self.assertEqual(fixture["provenance"]["kind"], "derived")
                with self.assertRaises(Exception):
                    providers.parse_sonnet_review(execution)

        for name, code in (
            ("provider_error", "error_max_budget_usd"),
            ("max_turns", "error_max_turns"),
        ):
            with self.subTest(name=name):
                fixture, execution = claude_fixture(name)
                self.assertEqual(
                    fixture["provenance"]["kind"],
                    "recorded_sanitized",
                )
                normalized = providers.normalize_sonnet_execution(execution)
                self.assertEqual(normalized.returncode, 1)
                self.assertEqual(normalized.failure_kind, "provider_error")
                self.assertEqual(normalized.failure_source, "provider_native")
                self.assertEqual(normalized.failure_code, code)
                with self.assertRaises(RuntimeError):
                    providers.parse_sonnet_review(normalized)

    def test_review_semantics_reject_every_inconsistent_pass_key_variant(self) -> None:
        invalid_results = (
            {
                "category": "PASS",
                "finding_key": "PASS",
                "summary": "missing status",
            },
            {
                "status": "UNKNOWN",
                "category": "PASS",
                "finding_key": "PASS",
                "summary": "invalid enum",
            },
            {
                "status": "PASS",
                "category": "PASS",
                "finding_key": "PASS",
                "summary": "extra field",
                "unexpected": True,
            },
            {
                "status": "PASS",
                "category": "IMPLEMENTATION_DEFECT",
                "finding_key": "defect",
                "summary": "quota exceeded",
            },
            {
                "status": "FAIL",
                "category": "PASS",
                "finding_key": "PASS",
                "summary": "HTTP 503 Service Unavailable",
            },
            {
                "status": "PASS",
                "category": "PASS",
                "finding_key": "not-pass",
                "summary": "status code: 429",
            },
            {
                "status": "FAIL",
                "category": "SCOPE_VIOLATION",
                "finding_key": "PASS",
                "summary": "authentication failed",
            },
        )
        for structured in invalid_results:
            with self.subTest(structured=structured):
                execution = ProviderExecution(
                    ["claude"],
                    0,
                    json.dumps(
                        {
                            "type": "result",
                            "subtype": "success",
                            "is_error": False,
                            "structured_output": structured,
                        }
                    ),
                    "",
                )
                self.assertIsNone(
                    providers.normalize_sonnet_execution(execution).failure_kind
                )
                with self.assertRaises(ValueError):
                    providers.parse_sonnet_review(execution)

    def test_native_envelope_precedes_conflicting_stream_text_and_zero_exit(self) -> None:
        _, recorded = claude_fixture("max_turns")
        envelope = json.loads(recorded.stdout)
        envelope["result"] = (
            "FileNotFoundError: permission denied; prompt says HTTP 401; "
            "diff says service unavailable"
        )
        execution = ProviderExecution(
            recorded.command,
            0,
            json.dumps(envelope),
            "HTTP 503 Service Unavailable\n",
        )

        normalized = providers.normalize_sonnet_execution(execution)

        self.assertEqual(normalized.returncode, 0)
        self.assertTrue(providers.execution_failed(normalized))
        self.assertEqual(normalized.failure_kind, "provider_error")
        self.assertEqual(normalized.failure_source, "provider_native")
        self.assertEqual(normalized.failure_code, "error_max_turns")
        with self.assertRaises(RuntimeError):
            providers.parse_sonnet_review(execution)

    def test_os_launch_errors_are_configuration_not_provider_auth(self) -> None:
        for error in (
            FileNotFoundError(2, "No such file or directory"),
            PermissionError(13, "Permission denied"),
        ):
            with self.subTest(error=type(error).__name__):
                def runner(command, **kwargs):
                    raise error

                execution = providers._run(
                    ["missing-provider"],
                    self.repo,
                    capability="read_only",
                    runner=runner,
                    sleeper=lambda seconds: None,
                )
                self.assertEqual(execution.failure_kind, "configuration")
                self.assertEqual(execution.failure_source, "os_error")
                self.assertTrue(
                    execution.failure_code.startswith(type(error).__name__)
                )
                self.assertEqual(len(execution.attempts), 1)

    def test_stderr_requires_anchored_phrases_and_explicit_http_prefix(self) -> None:
        trusted = (
            ("HTTP 401 Unauthorized", "auth", "401"),
            ("status code: 429 Too Many Requests", "rate_limit", "429"),
            ("Error: service unavailable", "unavailable", None),
        )
        for message, kind, code in trusted:
            with self.subTest(message=message):
                evidence = providers.normalize_provider_failure(1, "", message)
                self.assertEqual(evidence.kind, kind)
                self.assertEqual(evidence.source, "stderr")
                self.assertEqual(evidence.code, code)

        for status in (401, 402, 403, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                evidence = providers.normalize_provider_failure(
                    1,
                    "",
                    f"{status} appears on source line {status}",
                )
                self.assertEqual(evidence.kind, "provider_error")
                self.assertEqual(evidence.source, "returncode")

    def test_stdout_tail_is_bounded_and_requires_explicit_contract(self) -> None:
        inside = "x" * 9000 + "\nHTTP 503 Service Unavailable\n"
        outside = "HTTP 503 Service Unavailable\n" + "x" * 9000

        disabled = providers.normalize_provider_failure(1, inside, "")
        enabled = providers.normalize_provider_failure(
            1,
            inside,
            "",
            allow_stdout_tail=True,
        )
        out_of_tail = providers.normalize_provider_failure(
            1,
            outside,
            "",
            allow_stdout_tail=True,
        )

        self.assertEqual(disabled.kind, "provider_error")
        self.assertEqual(disabled.source, "returncode")
        self.assertEqual(enabled.kind, "unavailable")
        self.assertEqual(enabled.source, "stdout_tail")
        self.assertEqual(out_of_tail.kind, "provider_error")

    def test_model_prose_prompt_transcript_and_diff_never_classify(self) -> None:
        prose = """Prompt: diagnose quota exceeded and HTTP 503.
Transcript: authentication failed; status code: 429.
Diff:
+ HTTP 401 Unauthorized
+ service unavailable
"""
        evidence = providers.normalize_provider_failure(1, prose, "")
        self.assertEqual(evidence.kind, "provider_error")
        self.assertEqual(evidence.source, "returncode")

        success = ProviderExecution(
            ["claude"],
            0,
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": prose,
                    "structured_output": {
                        "status": "PASS",
                        "category": "PASS",
                        "finding_key": "PASS",
                        "summary": prose,
                    },
                }
            ),
            "",
        )
        self.assertIsNone(
            providers.normalize_sonnet_execution(success).failure_kind
        )
        self.assertEqual(providers.parse_sonnet_review(success).status, "PASS")

    def test_native_http_status_retries_before_success_without_content_retry(self) -> None:
        _, native = claude_fixture("provider_error")
        _, success = claude_fixture("success")
        envelope = json.loads(native.stdout)
        for status, expected_kind in (
            (401, "auth"),
            (402, "billing"),
            (403, "auth"),
            (429, "rate_limit"),
            (500, "unavailable"),
            (502, "unavailable"),
            (503, "unavailable"),
            (504, "unavailable"),
        ):
            with self.subTest(status=status):
                candidate = dict(envelope, api_error_status=status)
                evidence = providers.classify_claude_native_failure(
                    subprocess.CompletedProcess(
                        ["claude"],
                        1,
                        json.dumps(candidate),
                        "quota exceeded",
                    )
                )
                self.assertEqual(evidence.kind, expected_kind)
                self.assertEqual(evidence.source, "provider_native")

        envelope["subtype"] = "error_during_execution"
        envelope["api_error_status"] = 503
        results = iter(
            (
                subprocess.CompletedProcess(
                    ["claude"],
                    1,
                    json.dumps(envelope),
                    "authentication failed",
                ),
                subprocess.CompletedProcess(
                    ["claude"],
                    0,
                    success.stdout,
                    "",
                ),
            )
        )
        calls = 0

        def runner(command, **kwargs):
            nonlocal calls
            calls += 1
            return next(results)

        execution = providers._run(
            ["claude"],
            self.repo,
            capability="read_only",
            runner=runner,
            sleeper=lambda seconds: None,
            native_classifier=providers.classify_claude_native_failure,
        )

        self.assertEqual(calls, 2)
        self.assertFalse(providers.execution_failed(execution))
        self.assertEqual(len(execution.attempts), 2)
        self.assertEqual(execution.attempts[0].failure_kind, "unavailable")
        self.assertEqual(
            execution.attempts[0].failure_source,
            "provider_native",
        )
        self.assertEqual(
            execution.attempts[0].failure_code,
            "error_during_execution",
        )
        self.assertTrue(execution.attempts[0].retry_scheduled)

    def test_audit_fields_round_trip_and_legacy_defaults_remain_none(self) -> None:
        run = WorkflowRun(
            run_id="failure-audit",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="0" * 64,
            specification="Audit failure provenance.",
            resolved_correction_policy=resolve_correction_policy(),
            repo=RepoState(
                repo=str(self.repo),
                branch="main",
                head="0" * 40,
                clean=True,
                origin="https://example.invalid/repo.git",
            ),
        )
        sources = (
            ("provider_native", "provider_error", "error_max_turns", 1),
            ("os_error", "configuration", "FileNotFoundError:2", 127),
            ("supervisor", "timeout", "timeout", 124),
            ("stderr", "auth", "401", 1),
            ("stdout_tail", "unavailable", "503", 1),
            ("returncode", "provider_error", "9", 9),
        )
        for index, (source, kind, code, returncode) in enumerate(sources):
            run.provider_runs.append(
                provider_record(
                    ADVERSARIAL_REVIEW_ROUTE,
                    "implementation_review",
                    command=["fixture", str(index)],
                    returncode=returncode,
                    stdout=f"raw stdout {source}",
                    stderr=f"raw stderr {source}",
                    duration_seconds=index + 0.25,
                    failure_kind=kind,
                    failure_source=source,
                    failure_code=code,
                    retry_scheduled=False,
                )
            )
        run.provider_runs.extend(
            (
                provider_record(
                    ADVERSARIAL_REVIEW_ROUTE,
                    "implementation_review",
                    command=["fixture", "success"],
                    returncode=0,
                    stdout="successful raw output",
                    duration_seconds=7.25,
                ),
                provider_record(
                    ADVERSARIAL_REVIEW_ROUTE,
                    "implementation_review",
                    command=["claude"],
                    returncode=1,
                    failure_kind="provider_error",
                ),
            )
        )
        orchestrator.persist(run, self.repo / "runs")

        loaded = orchestrator.load_run(run.run_id, self.repo / "runs")

        self.assertEqual(
            [record.model_dump() for record in loaded.provider_runs],
            [record.model_dump() for record in run.provider_runs],
        )
        self.assertIsNone(loaded.provider_runs[-1].failure_source)
        self.assertIsNone(loaded.provider_runs[-1].failure_code)

    def test_writer_stdout_prose_is_not_retried_but_trusted_outage_still_is(self) -> None:
        prose_calls = 0

        def prose_runner(command, **kwargs):
            nonlocal prose_calls
            prose_calls += 1
            return subprocess.CompletedProcess(
                command,
                1,
                "HTTP 503 Service Unavailable in a model-generated diff",
                "",
            )

        prose_execution = providers._run(
            ["codex", "exec", "--model", "gpt-5.6-luna"],
            self.repo,
            capability="workspace_write",
            runner=prose_runner,
            sleeper=lambda seconds: None,
        )
        self.assertEqual(prose_calls, 1)
        self.assertEqual(prose_execution.failure_kind, "provider_error")

        results = iter(
            (
                subprocess.CompletedProcess(
                    ["codex"],
                    1,
                    "partial writer output",
                    "HTTP 503 Service Unavailable",
                ),
                subprocess.CompletedProcess(
                    ["codex"],
                    0,
                    "writer repeated under pre-M0.4 policy",
                    "",
                ),
            )
        )
        trusted_calls = 0

        def trusted_runner(command, **kwargs):
            nonlocal trusted_calls
            trusted_calls += 1
            return next(results)

        trusted_execution = providers._run(
            ["codex", "exec", "--model", "gpt-5.6-luna"],
            self.repo,
            capability="workspace_write",
            runner=trusted_runner,
            sleeper=lambda seconds: None,
        )
        self.assertEqual(trusted_calls, 1)
        self.assertTrue(providers.execution_failed(trusted_execution))
        self.assertFalse(trusted_execution.attempts[0].retry_scheduled)

    def test_capability_validation_precedes_spawn_and_writer_errors_are_single_shot(self) -> None:
        runner_calls = 0
        sleep_calls: list[float] = []

        def runner(command, **kwargs):
            nonlocal runner_calls
            runner_calls += 1
            return subprocess.CompletedProcess(command, 0, "", "")

        with self.assertRaises(TypeError):
            providers._run(  # type: ignore[call-arg]
                ["provider"],
                self.repo,
                runner=runner,
            )
        with self.assertRaisesRegex(ValueError, "capability"):
            providers._run(
                ["provider"],
                self.repo,
                capability="invalid",  # type: ignore[arg-type]
                runner=runner,
            )
        self.assertEqual(runner_calls, 0)

        fixtures = (
            ("quota exceeded", "quota", None),
            ("HTTP 401 Unauthorized", "auth", "401"),
            ("status code: 429 Too Many Requests", "rate_limit", "429"),
            ("no such file or directory", "configuration", None),
            ("HTTP 503 Service Unavailable", "unavailable", "503"),
            ("unclassified writer failure", "provider_error", "1"),
        )
        for stderr, kind, code in fixtures:
            with self.subTest(kind=kind):
                calls = 0

                def failing_runner(command, **kwargs):
                    nonlocal calls
                    calls += 1
                    return subprocess.CompletedProcess(command, 1, "raw", stderr)

                execution = providers._run(
                    ["codex", "exec", "--model", "gpt-5.6-luna"],
                    self.repo,
                    capability="workspace_write",
                    runner=failing_runner,
                    sleeper=sleep_calls.append,
                )
                self.assertEqual(calls, 1)
                self.assertEqual(len(execution.attempts), 1)
                self.assertEqual(execution.failure_kind, kind)
                self.assertEqual(execution.failure_code, code)
                self.assertEqual(execution.capability, "workspace_write")
                self.assertFalse(execution.attempts[0].retry_scheduled)
                self.assertEqual(
                    execution.attempts[0].capability,
                    "workspace_write",
                )
        self.assertEqual(sleep_calls, [])

    def test_read_only_retry_keeps_capability_command_and_exact_delays(self) -> None:
        command = ["claude", "-p", "same prompt"]
        results = iter(
            (
                subprocess.CompletedProcess(
                    command,
                    1,
                    "",
                    "HTTP 503 Service Unavailable",
                ),
                subprocess.CompletedProcess(
                    command,
                    1,
                    "",
                    "HTTP 502 Bad Gateway",
                ),
                subprocess.CompletedProcess(command, 0, "success", ""),
            )
        )
        commands: list[list[str]] = []
        delays: list[float] = []

        def runner(candidate, **kwargs):
            commands.append(candidate)
            return next(results)

        execution = providers._run(
            command,
            self.repo,
            capability="read_only",
            runner=runner,
            sleeper=delays.append,
        )

        self.assertEqual(commands, [command, command, command])
        self.assertEqual(delays, [5.0, 15.0])
        self.assertEqual(
            [attempt.capability for attempt in execution.attempts],
            ["read_only", "read_only", "read_only"],
        )
        self.assertEqual(
            [attempt.retry_scheduled for attempt in execution.attempts],
            [True, True, False],
        )

    def test_workspace_interruption_is_single_shot_after_supervisor_cleanup(self) -> None:
        command = ["codex", "exec", "--model", "gpt-5.6-luna"]
        supervised = providers._SupervisedResult(
            subprocess.CompletedProcess(
                command,
                providers.PROVIDER_INTERRUPTED_RETURN_CODE,
                "partial writer output",
                "continuo-supervisor: interrupted; process_group=terminated",
            ),
            "interrupted",
        )
        sleep_calls: list[float] = []

        with patch.object(
            providers,
            "_supervise_process",
            return_value=supervised,
        ) as supervisor:
            execution = providers._run(
                command,
                self.repo,
                capability="workspace_write",
                sleeper=sleep_calls.append,
            )

        supervisor.assert_called_once()
        self.assertEqual(len(execution.attempts), 1)
        self.assertEqual(execution.failure_kind, "interrupted")
        self.assertEqual(execution.failure_source, "supervisor")
        self.assertEqual(execution.capability, "workspace_write")
        self.assertFalse(execution.attempts[0].retry_scheduled)
        self.assertEqual(sleep_calls, [])


@unittest.skipUnless(os.name == "posix", "real process-group tests require POSIX")
class ProviderSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_child(
        self,
        source: str,
        *,
        deadline: float = 0.2,
        grace: float = 0.2,
    ) -> ProviderExecution:
        return providers._run(
            [sys.executable, "-c", source],
            self.repo,
            capability="read_only",
            deadline_seconds=deadline,
            term_grace_seconds=grace,
            poll_interval_seconds=0.02,
            heartbeat_seconds=60.0,
        )

    def assert_process_gone(self, pid: int) -> None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            state = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(pid)],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            if not state or state.startswith("Z"):
                return
            time.sleep(0.02)
        self.fail(f"process {pid} survived supervisor cleanup")

    def test_success_before_deadline_preserves_streams(self) -> None:
        execution = self.run_child(
            "import sys; print('child stdout'); print('child stderr', file=sys.stderr)"
        )

        self.assertEqual(execution.returncode, 0)
        self.assertEqual(execution.stdout, "child stdout\n")
        self.assertEqual(execution.stderr, "child stderr\n")
        self.assertIsNone(execution.failure_kind)
        self.assertEqual(len(execution.attempts), 1)
        self.assertFalse(execution.attempts[0].retry_scheduled)

    def test_timeout_captures_partial_output_and_graceful_shutdown(self) -> None:
        source = """
import signal
import sys
import time

def stop(*_):
    print("term handled", file=sys.stderr, flush=True)
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
print("quota exceeded in untrusted model stdout", flush=True)
print("HTTP 503 Service Unavailable", file=sys.stderr, flush=True)
while True:
    time.sleep(0.05)
"""
        execution = self.run_child(source)

        self.assertEqual(execution.returncode, providers.PROVIDER_TIMEOUT_RETURN_CODE)
        self.assertEqual(execution.failure_kind, "timeout")
        self.assertEqual(execution.failure_source, "supervisor")
        self.assertEqual(execution.failure_code, "timeout")
        self.assertEqual(execution.stdout.count("quota exceeded"), 1)
        self.assertEqual(execution.stderr.count("HTTP 503"), 1)
        self.assertIn("term handled", execution.stderr)
        self.assertIn("exited during TERM grace", execution.stderr)
        self.assertNotIn("forced termination", execution.stderr)
        self.assertFalse(execution.attempts[0].retry_scheduled)

    def test_timeout_force_kills_parent_and_grandchild(self) -> None:
        source = """
import os
import signal
import subprocess
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([
    sys.executable,
    "-c",
    "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
])
print(f"{os.getpid()} {child.pid}", flush=True)
print("tree partial", file=sys.stderr, flush=True)
while True:
    time.sleep(0.05)
"""
        started = time.monotonic()
        execution = self.run_child(source, deadline=0.2, grace=0.15)
        elapsed = time.monotonic() - started

        parent_pid, child_pid = map(int, execution.stdout.strip().split())
        self.assertEqual(execution.failure_kind, "timeout")
        self.assertLess(elapsed, 1.5)
        self.assertIn("tree partial", execution.stderr)
        self.assertIn("forced termination", execution.stderr)
        self.assert_process_gone(parent_pid)
        self.assert_process_gone(child_pid)

    def test_inherited_descendant_pipes_do_not_outlive_cleanup(self) -> None:
        source = """
import os
import subprocess
import sys

child = subprocess.Popen([
    sys.executable,
    "-c",
    "import os,time; print(f'grandchild {os.getpid()}', flush=True); time.sleep(60)",
])
print(f"parent {os.getpid()} child {child.pid}", flush=True)
"""
        started = time.monotonic()
        execution = self.run_child(source, deadline=0.2, grace=0.15)
        elapsed = time.monotonic() - started

        lines = execution.stdout.strip().splitlines()
        parent_line = next(line for line in lines if line.startswith("parent "))
        grandchild_line = next(
            line for line in lines if line.startswith("grandchild ")
        )
        parent_pid = int(parent_line.split()[1])
        child_pid = int(parent_line.split()[3])
        self.assertEqual(grandchild_line, f"grandchild {child_pid}")
        self.assertEqual(execution.failure_kind, "timeout")
        self.assertLess(elapsed, 1.5)
        self.assert_process_gone(parent_pid)
        self.assert_process_gone(child_pid)

    def test_deadline_boundary_produces_one_terminal_attempt(self) -> None:
        execution = self.run_child(
            "import time; print('boundary', flush=True); time.sleep(0.2)",
            deadline=0.2,
            grace=0.15,
        )

        self.assertIn(
            (execution.returncode, execution.failure_kind),
            ((0, None), (providers.PROVIDER_TIMEOUT_RETURN_CODE, "timeout")),
        )
        self.assertEqual(execution.stdout.count("boundary"), 1)
        self.assertEqual(len(execution.attempts), 1)
        self.assertFalse(execution.attempts[0].retry_scheduled)

    def test_keyboard_interrupt_cleans_real_child_and_returns_terminal_attempt(self) -> None:
        timer = threading.Timer(0.15, _thread.interrupt_main)
        timer.start()
        try:
            execution = self.run_child(
                "import os,time; print(os.getpid(), flush=True); time.sleep(60)",
                deadline=10.0,
                grace=0.2,
            )
        finally:
            timer.cancel()

        pid = int(execution.stdout.strip())
        self.assertEqual(
            execution.returncode,
            providers.PROVIDER_INTERRUPTED_RETURN_CODE,
        )
        self.assertEqual(execution.failure_kind, "interrupted")
        self.assertEqual(execution.failure_source, "supervisor")
        self.assertEqual(execution.failure_code, "interrupted")
        self.assertFalse(execution.attempts[0].retry_scheduled)
        self.assertIn("operator interruption", execution.stderr)
        self.assert_process_gone(pid)

    def test_unexpected_poll_exception_cleans_child_before_propagating(self) -> None:
        values = iter([0.0])

        def exploding_clock() -> float:
            try:
                return next(values)
            except StopIteration as exc:
                raise RuntimeError("synthetic polling cancellation") from exc

        with self.assertRaisesRegex(RuntimeError, "synthetic polling cancellation"):
            providers._supervise_process(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                self.repo,
                label="test provider",
                interactive=False,
                deadline_seconds=10.0,
                term_grace_seconds=0.2,
                poll_interval_seconds=0.02,
                heartbeat_seconds=60.0,
                monotonic=exploding_clock,
            )

    def test_invalid_timing_is_rejected_before_spawn(self) -> None:
        invalid_values = (0.0, -1.0, math.nan, math.inf)
        for field in ("deadline_seconds", "term_grace_seconds"):
            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    arguments = {field: value}
                    expected = "deadline" if field == "deadline_seconds" else "grace"
                    with patch.object(providers.subprocess, "Popen") as popen:
                        with self.assertRaisesRegex(ValueError, expected):
                            providers._run(
                                [sys.executable, "-c", "pass"],
                                self.repo,
                                capability="read_only",
                                **arguments,
                            )
                        popen.assert_not_called()

    def test_missing_executable_remains_bounded_launch_failure(self) -> None:
        execution = providers._run(
            [str(self.repo / "missing-provider")],
            self.repo,
            capability="read_only",
            deadline_seconds=1.0,
        )

        self.assertEqual(execution.returncode, 127)
        self.assertEqual(execution.failure_kind, "configuration")
        self.assertEqual(len(execution.attempts), 1)
        self.assertFalse(execution.attempts[0].retry_scheduled)


if __name__ == "__main__":
    unittest.main()
