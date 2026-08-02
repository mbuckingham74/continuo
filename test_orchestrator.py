import _thread
import hashlib
import json
import math
import os
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
from models import RepoState, ReviewResult, WorkflowRun
from providers import ProviderExecution, build_luna_command, build_sol_command, build_sonnet_command


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
        "structured_output": {
            "status": status,
            "category": category,
            "finding_key": finding_key,
            "summary": summary,
        }
    }
    return ProviderExecution(["claude"], 0, json.dumps(payload), "")


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

    def controller(self, sonnet, terra=None, sol=None, luna=None, approval=None):
        return orchestrator.Controller(
            self.repo,
            self.runs,
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
        orchestrator.persist(before_save, self.runs)
        resumed_before = controller.resume(before_save.run_id)
        self.assertEqual(resumed_before.stage, "commit_declined")
        self.assertEqual(luna_calls, calls_after_implementation)
        self.assertEqual(resumed_before.changed_files, [special])

        after_save = completed.model_copy(deep=True)
        after_save.run_id = "after-verification-save"
        after_save.stage = "implementation_verified"
        after_save.implementation_review = None
        orchestrator.persist(after_save, self.runs)
        resumed_after = controller.resume(after_save.run_id)
        self.assertEqual(resumed_after.stage, "commit_declined")
        self.assertEqual(luna_calls, calls_after_implementation)

    def test_change_enumeration_failure_blocks_before_implementation_review(self) -> None:
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

        self.assertEqual(run.stage, "blocked_unexpected_repo_state")
        self.assertEqual(sonnet_calls, 1)
        self.assertFalse(run.verification["change_enumeration"])
        self.assertIn("malformed porcelain fixture", run.verification["change_enumeration_error"])
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
                    "503 Service Unavailable",
                ),
                subprocess.CompletedProcess(
                    ["claude"],
                    502,
                    "",
                    "502 Bad Gateway",
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
            ("402 Payment Required", "billing"),
            ("401 Unauthorized", "auth"),
            ("429 Too Many Requests", "rate_limit"),
            ("503 Service Unavailable", "unavailable"),
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

        self.assertEqual(run.stage, "blocked_provider_timeout")
        self.assertEqual(luna_calls, 1)
        self.assertEqual(run.provider_runs[-1].failure_kind, "timeout")
        self.assertIn("writer partial", run.provider_runs[-1].stdout)

        resumed = controller.resume(run.run_id)
        self.assertEqual(resumed.stage, "blocked_provider_timeout")
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
print("partial stdout", flush=True)
print("partial stderr", file=sys.stderr, flush=True)
while True:
    time.sleep(0.05)
"""
        execution = self.run_child(source)

        self.assertEqual(execution.returncode, providers.PROVIDER_TIMEOUT_RETURN_CODE)
        self.assertEqual(execution.failure_kind, "timeout")
        self.assertEqual(execution.stdout.count("partial stdout"), 1)
        self.assertEqual(execution.stderr.count("partial stderr"), 1)
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
                                **arguments,
                            )
                        popen.assert_not_called()

    def test_missing_executable_remains_bounded_launch_failure(self) -> None:
        execution = providers._run(
            [str(self.repo / "missing-provider")],
            self.repo,
            deadline_seconds=1.0,
        )

        self.assertEqual(execution.returncode, 127)
        self.assertEqual(execution.failure_kind, "configuration")
        self.assertEqual(len(execution.attempts), 1)
        self.assertFalse(execution.attempts[0].retry_scheduled)


if __name__ == "__main__":
    unittest.main()
