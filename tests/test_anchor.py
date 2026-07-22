import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


HELPER = Path(__file__).parents[1] / "skills" / "anchor-orchestrator" / "scripts" / "anchor.py"


class AnchorStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args, expect=0):
        result = subprocess.run(
            [sys.executable, str(HELPER), "--root", str(self.root), *args],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def output(self, name, content="result\n"):
        path = self.root / name
        path.write_text(content)
        return str(path)

    def workflow(self, name, nodes):
        workflows = self.root / "workflows"
        workflows.mkdir(parents=True, exist_ok=True)
        node_yaml = textwrap.indent(textwrap.dedent(nodes).strip(), "  ")
        (workflows / f"{name}.md").write_text(
            f"---\nworkflow: {name}\nversion: 1\nnodes:\n{node_yaml}\n---\nTest workflow.\n"
        )

    def start_dashboard(self):
        self.cli("init", "--template", "dashboard-refresh")
        return self.cli("start", "dashboard-refresh", "--param", "quarter=Q2")["run_id"]

    def record_and_verify(self, run_id, node, meta=None):
        args = ["record", run_id, "--node", node, "--output", self.output(f"{node}.txt")]
        if meta is not None:
            args.extend(["--meta", json.dumps(meta)])
        recorded = self.cli(*args)
        verified = self.cli("verify", run_id, "--node", node)
        return recorded, verified

    def test_blocked_node_cannot_be_recorded(self):
        run_id = self.start_dashboard()
        result = self.cli(
            "record", run_id, "--node", "publish",
            "--output", self.output("publish.txt"), expect=1,
        )
        self.assertIn("not ready", result["error"])
        self.assertEqual(result["blocked_by"], ["analyze"])

    def test_failed_verify_does_not_unlock_dependency(self):
        run_id = self.start_dashboard()
        _, failed = self.record_and_verify(run_id, "fetch_sf", {"row_count": 0})
        self.assertFalse(failed["pass"])
        self.assertEqual(failed["state"], "verify_failed")
        self.record_and_verify(run_id, "fetch_slack")

        ready = self.cli("ready", run_id)
        self.assertNotIn("reconcile", ready["ready"])
        self.assertEqual(ready["blocked"]["reconcile"], ["fetch_sf"])
        self.assertIn("fetch_sf", ready["awaiting_verification"])

    def test_passed_verify_unlocks_dependency(self):
        run_id = self.start_dashboard()
        self.record_and_verify(run_id, "fetch_sf", {"row_count": 10})
        self.record_and_verify(run_id, "fetch_slack")
        ready = self.cli("ready", run_id)
        self.assertEqual(ready["ready"], ["reconcile"])

    def test_record_requires_a_real_output(self):
        run_id = self.start_dashboard()
        result = self.cli(
            "record", run_id, "--node", "fetch_sf",
            "--meta", '{"row_count": 10}', expect=1,
        )
        self.assertIn("existing file", result["error"])

    def test_incomplete_run_cannot_end_as_success(self):
        run_id = self.start_dashboard()
        blocked = self.cli("end", run_id, expect=1)
        self.assertIn("run is incomplete", blocked["error"])
        aborted = self.cli("end", run_id, "--abort")
        self.assertTrue(aborted["aborted"])

    def test_before_gate_blocks_record_until_valid_choice(self):
        self.workflow("before-gate", """
            - id: publish
              depends_on: []
              produces: publish.output
              verify: "exists(publish.output)"
              gate: {when: before, mode: choose, options: [slack, email]}
        """)
        run_id = self.cli("start", "before-gate")["run_id"]
        output = self.output("publish.txt")
        blocked = self.cli("record", run_id, "--node", "publish", "--output", output, expect=1)
        self.assertIn("before-gate", blocked["error"])
        invalid = self.cli("gate", run_id, "--node", "publish", "--choose", "both", expect=1)
        self.assertEqual(invalid["options"], ["slack", "email"])
        self.cli("gate", run_id, "--node", "publish", "--choose", "slack")
        self.cli("record", run_id, "--node", "publish", "--output", output)
        verified = self.cli("verify", run_id, "--node", "publish")
        self.assertEqual(verified["state"], "done")

    def test_after_gate_quarantines_verified_output_until_approval(self):
        self.workflow("after-gate", """
            - id: review
              depends_on: []
              produces: review.output
              verify: "exists(review.output)"
              gate: {when: after, mode: approve}
            - id: publish
              consumes: [review.output]
              produces: publish.output
              verify: "exists(publish.output)"
        """)
        run_id = self.cli("start", "after-gate")["run_id"]
        self.cli("record", run_id, "--node", "review", "--output", self.output("review.txt"))
        verified = self.cli("verify", run_id, "--node", "review")
        self.assertEqual(verified["state"], "awaiting_gate")
        ready = self.cli("ready", run_id)
        self.assertEqual(ready["ready"], [])
        self.assertEqual(ready["blocked"]["publish"], ["review"])
        self.assertIn("review", ready["awaiting_gates"])
        self.assertFalse((self.root / "out" / "after-gate" / "review_output").exists())

        approved = self.cli("gate", run_id, "--node", "review", "--approve")
        self.assertEqual(approved["state"], "done")
        self.assertTrue((self.root / "out" / "after-gate" / "review_output").exists())
        self.assertEqual(self.cli("ready", run_id)["ready"], ["publish"])

    def test_judgment_verify_needs_an_explicit_result(self):
        self.workflow("judgment", """
            - id: analyze
              depends_on: []
              produces: analyze.findings
              verify: "subagent: every claim cites a source row"
        """)
        run_id = self.cli("start", "judgment")["run_id"]
        self.cli("record", run_id, "--node", "analyze", "--output", self.output("analysis.txt"))
        delegated = self.cli("verify", run_id, "--node", "analyze")
        self.assertIsNone(delegated["pass"])
        self.assertEqual(delegated["state"], "recorded")
        passed = self.cli("verify", run_id, "--node", "analyze", "--result", "pass")
        self.assertTrue(passed["pass"])
        self.assertEqual(passed["state"], "done")

    def test_dashboard_flow_enforces_verify_condition_and_gates_end_to_end(self):
        run_id = self.start_dashboard()
        self.record_and_verify(run_id, "fetch_sf", {"row_count": 10})
        self.record_and_verify(run_id, "fetch_slack")
        self.record_and_verify(run_id, "reconcile", {"row_count": 42})

        self.cli(
            "record", run_id, "--node", "analyze",
            "--output", self.output("analyze.txt"),
            "--meta", '{"metrics": {"anomaly_count": 0}}',
        )
        delegated = self.cli("verify", run_id, "--node", "analyze")
        self.assertEqual(delegated["delegate"], "subagent")
        reviewed = self.cli("verify", run_id, "--node", "analyze", "--result", "pass")
        self.assertEqual(reviewed["state"], "awaiting_gate")
        self.cli("gate", run_id, "--node", "analyze", "--approve")

        ready = self.cli("ready", run_id)
        self.assertEqual(ready["ready"], ["publish"])
        self.assertIn("alert", ready["done"])
        self.assertEqual(ready["gates"]["publish"]["mode"], "choose")

        self.cli("gate", run_id, "--node", "publish", "--choose", "slack")
        self.record_and_verify(run_id, "publish")
        final = self.cli("status", run_id)["view"]
        self.assertEqual(
            final["done"],
            ["fetch_sf", "fetch_slack", "reconcile", "analyze", "publish"],
        )
        self.assertEqual(final["skipped"], ["alert"])
        ended = self.cli("end", run_id)
        self.assertFalse(ended["aborted"])


if __name__ == "__main__":
    unittest.main()
