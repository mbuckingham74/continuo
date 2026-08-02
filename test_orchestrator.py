import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import orchestrator
from models import RepoState
from providers import ProviderExecution, build_luna_command, build_sonnet_command


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def review_execution(status: str, category: str, summary: str = "ok") -> ProviderExecution:
    payload = {"structured_output": {"status": status, "category": category, "summary": summary}}
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

    def controller(self, sonnet, terra=None, luna=None, approval=None):
        return orchestrator.Controller(
            self.repo,
            self.runs,
            sonnet=sonnet,
            terra=terra or (lambda prompt, repo: ProviderExecution(["codex"], 0, "resolved", "")),
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

    def test_correction_count_never_exceeds_one(self) -> None:
        review_calls = 0
        luna_calls = 0

        def sonnet(prompt, repo):
            nonlocal review_calls
            review_calls += 1
            if review_calls == 1:
                return review_execution("PASS", "PASS")
            return review_execution("FAIL", "IMPLEMENTATION_DEFECT", "still defective")

        def luna(prompt, repo):
            nonlocal luna_calls
            luna_calls += 1
            (repo / f"implementation-{luna_calls}.py").write_text("# bounded change\n")
            return ProviderExecution(["codex"], 0, "implemented", "")

        run = self.controller(sonnet, luna=luna).new_run("009")

        self.assertEqual(run.stage, "blocked_after_correction")
        self.assertEqual(run.correction_cycles, 1)
        self.assertEqual(luna_calls, 2)
        self.assertEqual(review_calls, 3)  # spec + implementation + post-correction

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

    def test_provider_commands_preserve_restrictions(self) -> None:
        sonnet = build_sonnet_command("review")
        self.assertIn("--permission-mode", sonnet)
        self.assertEqual(sonnet[sonnet.index("--permission-mode") + 1], "plan")
        self.assertEqual(sonnet[sonnet.index("--tools") + 1], "Read,Glob,Grep")
        self.assertNotIn("Write", sonnet)
        self.assertNotIn("Edit", sonnet)
        self.assertNotIn("Bash", sonnet)

        luna = build_luna_command("implement")
        self.assertEqual(luna[luna.index("--sandbox") + 1], "workspace-write")
        luna_configs = [value for index, value in enumerate(luna) if index and luna[index - 1] == "--config"]
        self.assertIn("approval_policy=never", luna_configs)
        self.assertIn("sandbox_workspace_write.network_access=false", luna_configs)
        luna_prompt = luna[-1]
        for forbidden in ("commit", "push", "branch", "merge", ".git"):
            self.assertIn(forbidden, luna_prompt)


if __name__ == "__main__":
    unittest.main()
