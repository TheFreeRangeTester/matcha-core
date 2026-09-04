import json
import tempfile
import textwrap
import unittest
from datetime import date
from pathlib import Path

from matcha_core.gate import (
    EXIT_INCONCLUSIVE,
    EXIT_PASS,
    EXIT_POLICY_FAILED,
    AllowRule,
    GatePolicy,
    PolicyError,
    build_gate_artifact,
    evaluate_gate,
    load_baseline_findings,
    load_policy,
    repository_state,
    write_json_atomic,
)
from matcha_core.models import AnalysisReport, CriteriaResult, FeatureResult, ImplementationStatus


def sample_report(status: str, confidence: float = 0.9, priority: str = "High") -> AnalysisReport:
    criterion = CriteriaResult(
        criteria_id="AC-1",
        description="The release behavior is implemented.",
        implementation_status=status,
        confidence=confidence,
        short_explanation="Example finding",
        analysis_mode="model",
    )
    feature = FeatureResult(
        feature_id="FEAT-1",
        name="Release behavior",
        priority=priority,
        status="Done",
        criteria=[criterion],
        implementation_status=status,
        confidence=confidence,
    )
    return AnalysisReport(
        source="repo",
        specs_path="SPECS.md",
        features=[feature],
        total_features=1,
        total_criteria=1,
    )


def sample_policy(**overrides) -> GatePolicy:
    values = {
        "fail_on": frozenset({"not_implemented", "implemented_differently"}),
        "priorities": frozenset({"critical", "high"}),
        "min_confidence": 0.75,
        "on_inconclusive": "block",
    }
    values.update(overrides)
    return GatePolicy(**values)


class GatePolicyTests(unittest.TestCase):
    def test_bundled_policy_template_is_valid(self):
        template_path = Path(__file__).parent.parent / "docs" / "POLICY_TEMPLATE.yml"

        policy = load_policy(template_path)

        self.assertEqual(policy.version, 1)
        self.assertIn("not_implemented", policy.fail_on)
        self.assertEqual(len(policy.allow), 0)

    def test_load_policy_validates_and_normalizes_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = Path(tmp) / "policy.yml"
            policy_path.write_text(
                textwrap.dedent(
                    """
                    version: 1
                    fail_on: [not_implemented]
                    priorities: [High]
                    min_confidence: 0.8
                    on_inconclusive: block
                    allow: []
                    """
                ).strip(),
                encoding="utf-8",
            )

            policy = load_policy(policy_path)

        self.assertEqual(policy.priorities, frozenset({"high"}))
        self.assertEqual(policy.min_confidence, 0.8)

    def test_load_policy_rejects_unknown_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = Path(tmp) / "policy.yml"
            policy_path.write_text(
                "version: 1\nfail_on: [not_implemented]\npriorities: [high]\nmin_confidnce: 0.8\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(PolicyError, "Unknown policy fields"):
                load_policy(policy_path)

    def test_definitive_finding_fails_policy(self):
        result = evaluate_gate(sample_report("not_implemented"), sample_policy())

        self.assertEqual(result.exit_code, EXIT_POLICY_FAILED)
        self.assertEqual(result.decision, "fail")
        self.assertEqual(len(result.violations), 1)

    def test_low_confidence_is_inconclusive_and_out_of_scope_priority_passes(self):
        low_confidence = evaluate_gate(sample_report("not_implemented", confidence=0.5), sample_policy())
        low_priority = evaluate_gate(sample_report("not_implemented", priority="Low"), sample_policy())

        self.assertEqual(low_confidence.exit_code, EXIT_INCONCLUSIVE)
        self.assertEqual(low_confidence.incomplete[0]["reason"], "confidence_below_policy_minimum")
        self.assertEqual(low_priority.exit_code, EXIT_PASS)

    def test_unknown_priority_is_in_scope_to_prevent_policy_bypass(self):
        result = evaluate_gate(sample_report("not_implemented", priority="Unknown"), sample_policy())

        self.assertEqual(result.exit_code, EXIT_POLICY_FAILED)

    def test_allow_rule_requires_expiry_and_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = Path(tmp) / "policy.yml"
            policy_path.write_text(
                textwrap.dedent(
                    """
                    version: 1
                    fail_on: [not_implemented]
                    priorities: [high]
                    allow:
                      - feature: FEAT-1
                    """
                ).strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(PolicyError, "expires is required"):
                load_policy(policy_path)

    def test_inconclusive_blocks_without_becoming_policy_failure(self):
        result = evaluate_gate(sample_report("inconclusive", confidence=0.0), sample_policy())

        self.assertEqual(result.exit_code, EXIT_INCONCLUSIVE)
        self.assertEqual(result.decision, "inconclusive")
        self.assertEqual(len(result.incomplete), 1)
        self.assertEqual(result.violations, [])

    def test_policy_can_explicitly_ignore_inconclusive_results(self):
        policy = sample_policy(on_inconclusive="ignore")

        result = evaluate_gate(sample_report("inconclusive", confidence=0.0), policy)

        self.assertEqual(result.exit_code, EXIT_PASS)
        self.assertEqual(result.incomplete, [])

    def test_invalid_analysis_status_cannot_produce_a_pass(self):
        result = evaluate_gate(sample_report("probably_done", confidence=1.0), sample_policy())

        self.assertEqual(result.exit_code, EXIT_INCONCLUSIVE)
        self.assertEqual(result.incomplete[0]["reason"], "invalid_analysis_status")

    def test_active_allow_rule_waives_and_expired_rule_does_not(self):
        active_rule = AllowRule(
            feature_id="FEAT-1",
            criteria_id="AC-1",
            statuses=frozenset({"not_implemented"}),
            expires=date(2026, 12, 31),
            reason="Migration in progress",
        )
        expired_rule = AllowRule(
            feature_id="FEAT-1",
            criteria_id="AC-1",
            statuses=frozenset({"not_implemented"}),
            expires=date(2026, 1, 1),
            reason="Expired",
        )

        active = evaluate_gate(
            sample_report("not_implemented"),
            sample_policy(allow=(active_rule,)),
            today=date(2026, 9, 4),
        )
        expired = evaluate_gate(
            sample_report("not_implemented"),
            sample_policy(allow=(expired_rule,)),
            today=date(2026, 9, 4),
        )

        self.assertEqual(active.exit_code, EXIT_PASS)
        self.assertEqual(active.waived[0]["waiver"]["source"], "policy")
        self.assertEqual(expired.exit_code, EXIT_POLICY_FAILED)

    def test_baseline_waives_only_the_same_status(self):
        baseline = {("feat-1", "ac-1", "not_implemented")}

        same = evaluate_gate(sample_report("not_implemented"), sample_policy(), baseline)
        changed = evaluate_gate(sample_report("implemented_differently"), sample_policy(), baseline)

        self.assertEqual(same.exit_code, EXIT_PASS)
        self.assertEqual(same.waived[0]["waiver"]["source"], "baseline")
        self.assertEqual(changed.exit_code, EXIT_POLICY_FAILED)

    def test_load_baseline_accepts_gate_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline_path = Path(tmp) / "baseline.json"
            baseline_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "analysis_report": {
                            "schema_version": "1.0",
                            "features": [feature_to_dict(sample_report("not_implemented"))],
                        },
                    }
                ),
                encoding="utf-8",
            )

            findings = load_baseline_findings(baseline_path)

        self.assertIn(("feat-1", "ac-1", "not_implemented"), findings)

    def test_load_baseline_rejects_incompatible_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline_path = Path(tmp) / "baseline.json"
            baseline_path.write_text(
                json.dumps({"schema_version": "2.0", "features": []}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(PolicyError, "schema is missing or incompatible"):
                load_baseline_findings(baseline_path)

    def test_gate_artifact_is_traceable_and_written_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "repo"
            repo_path.mkdir()
            specs_path = repo_path / "SPECS.md"
            policy_path = repo_path / "policy.yml"
            output_path = repo_path / "gate.json"
            specs_path.write_text("specs", encoding="utf-8")
            policy_path.write_text("policy", encoding="utf-8")
            report = sample_report(ImplementationStatus.IMPLEMENTED_AS_EXPECTED.value)
            evaluation = evaluate_gate(report, sample_policy())

            artifact = build_gate_artifact(
                report,
                evaluation,
                repo_path=repo_path,
                specs_path=specs_path,
                policy_path=policy_path,
                policy=sample_policy(),
                baseline_path=None,
                provider="ollama",
                model="llama3.2",
                matcha_version="0.1.3",
            )
            write_json_atomic(artifact, output_path)
            stored = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(stored["kind"], "matcha_gate_result")
        self.assertEqual(stored["decision"], "pass")
        self.assertEqual(stored["run"]["provider"], "ollama")
        self.assertEqual(len(stored["input"]["specs_sha256"]), 64)
        self.assertEqual(len(stored["input"]["policy_sha256"]), 64)

    def test_repository_state_ignores_generated_artifact_but_not_other_untracked_files(self):
        from git import Actor, Repo

        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "repo"
            repo = Repo.init(repo_path)
            tracked_path = repo_path / "app.py"
            tracked_path.write_text("print('ok')\n", encoding="utf-8")
            repo.index.add(["app.py"])
            actor = Actor("Matcha Tests", "matcha@example.invalid")
            repo.index.commit("initial", author=actor, committer=actor)

            artifact_path = repo_path / "matcha-gate.json"
            artifact_path.write_text("{}\n", encoding="utf-8")
            _, dirty_with_ignored_artifact = repository_state(repo_path, ignored_paths=[artifact_path])

            other_path = repo_path / "new-source.py"
            other_path.write_text("print('new')\n", encoding="utf-8")
            _, dirty_with_source = repository_state(repo_path, ignored_paths=[artifact_path])

        self.assertFalse(dirty_with_ignored_artifact)
        self.assertTrue(dirty_with_source)


def feature_to_dict(report: AnalysisReport) -> dict:
    feature = report.features[0]
    return {
        "feature_id": feature.feature_id,
        "criteria": [
            {
                "criteria_id": criterion.criteria_id,
                "implementation_status": criterion.implementation_status,
            }
            for criterion in feature.criteria
        ],
    }
