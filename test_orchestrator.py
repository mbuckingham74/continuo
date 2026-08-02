import _thread
import hashlib
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
from models import (
    RepoState,
    ReviewResult,
    TargetOwnership,
    WorkflowRun,
    WriterAttemptState,
    WriterRecoveryDecision,
)
from providers import ProviderExecution, build_luna_command, build_sol_command, build_sonnet_command


CLAUDE_FIXTURES = Path(__file__).parent / "test_fixtures/claude"


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
        before_save.implementation_review = None
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
        after_save.implementation_review = None
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
            [record.provider for record in run.provider_runs],
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
        run.implementation_review = ReviewResult(
            status="FAIL",
            category="IMPLEMENTATION_DEFECT",
            summary="legacy blocked finding",
        )
        run.last_error = "implementation review still fails after one correction"
        orchestrator.persist(run, self.runs)

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
        run.implementation_review = ReviewResult(
            status="FAIL",
            category="IMPLEMENTATION_DEFECT",
            finding_key="remote-scope-ownership",
            summary="remote scope ownership remains unresolved",
        )
        run.sol_guidance = "Decision 3 versus Decision 4 ownership is ambiguous."
        run.terra_resolution = (
            "Analysis.\n\n"
            "Proposed approval text:\n\n"
            "> Remote-role scope is positive evidence for remote reality.\n"
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
        self.assertEqual(decision.source_provider, "Terra High")
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
            "implementation",
            "Luna High",
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
            "implementation",
            "Sonnet 5 High",
            ProviderExecution(
                command=["claude"],
                returncode=0,
                stdout="legacy review output",
                stderr="",
            ),
            capability="read_only",
        )

        report = orchestrator._run_report(run)

        self.assertEqual(report["provider_calls_total"], 2)
        self.assertEqual(report["provider_counts"]["Luna High"], 1)
        self.assertEqual(report["provider_counts"]["Sonnet 5 High"], 1)
        self.assertAlmostEqual(report["provider_seconds"]["Luna High"], 12.5)
        self.assertEqual(report["untimed_counts"]["Sonnet 5 High"], 1)
        self.assertEqual(report["verification_runs"], 1)
        self.assertEqual(report["wall_seconds"], 90.0)

        self.assertEqual(
            run.provider_runs[0].duration_seconds,
            12.5,
        )


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
        run.provider_runs.append(
            orchestrator.ProviderRecord(
                provider="Sonnet 5 High",
                purpose="implementation",
                command=old.command,
                returncode=old.returncode,
                stdout=old.stdout,
                stderr=old.stderr,
            )
        )

        # Simulate the real T008-style hard stop: Sol path exhausted,
        # but final QA has uncovered a genuinely new defect.
        run.stage = "blocked_after_escalation"
        run.correction_cycles = 3
        run.implementation_review = ReviewResult(
            status="FAIL",
            category="IMPLEMENTATION_DEFECT",
            finding_key="geography-modal-alternatives",
            summary="new geography modal defect",
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
            repo=orchestrator.repo_state(self.repo),
            stage="spec_reviewing",
            provider_resume_stage="spec_reviewing",
            provider_resume_prompt="saved prompt",
        )
        run.provider_runs.append(
            orchestrator.ProviderRecord(
                provider="Sonnet 5 High",
                purpose="specification",
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
            repo=orchestrator.repo_state(self.repo),
            stage="spec_reviewing",
            provider_resume_stage="spec_reviewing",
            provider_resume_prompt="saved prompt",
        )
        normalized = orchestrator._record_provider(
            run,
            "specification",
            "Sonnet 5 High",
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
            repo=orchestrator.repo_state(self.repo),
            stage="spec_reviewing",
            provider_resume_stage="spec_reviewing",
            provider_resume_prompt="saved prompt",
        )
        orchestrator._record_provider(
            malformed_run,
            "specification",
            "Sonnet 5 High",
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
            repo=orchestrator.repo_state(self.repo),
            stage="spec_reviewing",
            provider_resume_stage="spec_reviewing",
            provider_resume_prompt="saved prompt",
        )
        orchestrator._record_provider(
            success_run,
            "specification",
            "Sonnet 5 High",
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
                record.purpose
                for record in recovered_success.provider_runs
                if record.provider == "Sonnet 5 High"
            ],
            ["specification", "implementation"],
        )

    def test_legacy_failure_recovery_does_not_scan_model_stdout(self) -> None:
        run = WorkflowRun(
            run_id="legacy-model-prose",
            created_at="2026-08-02T00:00:00+00:00",
            task_ref="009",
            task_file="tasks/009-example.md",
            task_sha256="0" * 64,
            specification="Test conservative legacy recovery.",
            repo=orchestrator.repo_state(self.repo),
            stage="implementing",
            provider_resume_stage="implementing",
            provider_resume_prompt="saved prompt",
        )
        run.provider_runs.append(
            orchestrator.ProviderRecord(
                provider="Luna High",
                purpose="implementation",
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
            record for record in run.provider_runs if record.provider == "Luna High"
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
            if record.provider == "Luna High"
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
        self.assertEqual(recovered.provider_runs[-1].provider, "Sonnet 5 High")
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
        self.assertEqual(report["provider_calls_total"], provider_count + 1)

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
                repo=orchestrator.repo_state(self.repo),
                stage="implementing",
                provider_resume_stage="implementing",
                provider_resume_prompt="saved writer prompt",
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
            orchestrator.ProviderRecord(
                provider="Luna High",
                purpose="implementation",
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
            orchestrator.ProviderRecord(
                provider="Luna High",
                purpose="implementation",
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
            repo=orchestrator.repo_state(self.repo),
            stage="implementing",
            provider_resume_stage="implementing",
            provider_resume_prompt="saved prompt",
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
        blocked.policy_decisions.append(
            orchestrator.PolicyDecision(
                decision_id="policy-01",
                approved_at="2026-08-02T00:00:00+00:00",
                trigger_finding_key="persistent-fixture",
                trigger_summary="preserve policy state",
                recommendation="preserve recommendation",
                approved_text="preserve approved policy",
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
            repo=orchestrator.repo_state(self.repo),
            stage="blocked_writer_partial_changes",
            provider_resume_stage="implementing",
            provider_resume_prompt="saved prompt",
            active_writer_attempt=WriterAttemptState(
                attempt_id="writer-audit-1",
                stage="implementing",
                purpose="implementation",
                pre_fingerprint=fingerprint,
                pre_changed_files=[],
                post_fingerprint="b" * 64,
                post_changed_files=["partial.py"],
                provider_record_index=1,
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
                orchestrator.ProviderRecord(
                    provider="legacy provider",
                    purpose="legacy",
                    command=["legacy"],
                    returncode=0,
                ),
                orchestrator.ProviderRecord(
                    provider="Luna High",
                    purpose="implementation",
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
        orchestrator.persist(run, self.runs)
        loaded = orchestrator.load_run(run.run_id, self.runs)
        self.assertEqual(loaded.model_dump(), run.model_dump())

        report = orchestrator._run_report(loaded)
        self.assertEqual(report["provider_calls_total"], 2)
        self.assertEqual(report["provider_failure_counts"], {"unavailable": 1})
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
        legacy = WorkflowRun.model_validate(legacy_payload)
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
        self.assertNotIn("flock", source)
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
from models import RepoState, WorkflowRun
runs = Path(os.environ['CONTINUO_TEST_RUNS'])
run = WorkflowRun(
    run_id='crash',
    created_at='2026-08-02T00:00:00+00:00',
    task_ref='009',
    task_file='tasks/009.md',
    task_sha256='0' * 64,
    specification='crash replacement',
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
        self.assertEqual(self.run_fixture().schema_version, 6)
        command_names = {
            command.name or command.callback.__name__.replace("_", "-")
            for command in orchestrator.app.registered_commands
        }
        self.assertTrue(
            command_names.isdisjoint(
                {"export", "purge", "redact", "cleanup", "clean-runs"}
            )
        )


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
                orchestrator.ProviderRecord(
                    provider="fixture provider",
                    purpose=f"source-{source}",
                    command=["fixture", str(index)],
                    returncode=returncode,
                    stdout=f"raw stdout {source}",
                    stderr=f"raw stderr {source}",
                    duration_seconds=index + 0.25,
                    failure_kind=kind,
                    failure_source=source,
                    failure_code=code,
                    retry_scheduled=source == "stdout_tail",
                )
            )
        run.provider_runs.extend(
            (
                orchestrator.ProviderRecord(
                    provider="fixture provider",
                    purpose="success",
                    command=["fixture", "success"],
                    returncode=0,
                    stdout="successful raw output",
                    duration_seconds=7.25,
                ),
                orchestrator.ProviderRecord(
                    provider="Sonnet 5 High",
                    purpose="legacy failure",
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
