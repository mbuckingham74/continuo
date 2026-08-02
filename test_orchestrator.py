import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import orchestrator
from models import RepoState, ReviewResult
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


if __name__ == "__main__":
    unittest.main()
