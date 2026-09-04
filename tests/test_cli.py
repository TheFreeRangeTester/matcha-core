import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from matcha_core import cli
from matcha_core.models import AnalysisReport, CriteriaResult, FeatureResult


class CliTests(unittest.TestCase):
    def test_provider_arguments_accept_reasoning_effort(self):
        args = cli.build_parser().parse_args(
            ["onboard", "/tmp/repo", "--provider", "openai", "--reasoning-effort", "medium"]
        )

        self.assertEqual(args.reasoning_effort, "medium")

    def test_onboard_writes_reviewable_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "repo"
            repo_path.mkdir()
            output_path = Path(tmp) / "SPECS.draft.md"
            draft = MagicMock()
            draft.features = [object()]
            draft.questions = []
            generator = MagicMock()
            generator.model = "fake-model"
            bootstrapper = MagicMock()
            bootstrapper.bootstrap_path.return_value = draft

            with patch("matcha_core.onboarding.OpenAICompatibleSpecGenerator.from_env", return_value=generator):
                with patch("matcha_core.onboarding.RepositoryBootstrapper", return_value=bootstrapper):
                    with patch("matcha_core.onboarding.write_specs_draft") as writer:
                        exit_code = cli.main(
                            [
                                "onboard",
                                str(repo_path),
                                "--provider",
                                "ollama",
                                "--output",
                                str(output_path),
                                "--language",
                                "Spanish",
                                "--quiet",
                            ]
                        )

            self.assertEqual(exit_code, 0)
            kwargs = bootstrapper.bootstrap_path.call_args.kwargs
            self.assertEqual(kwargs["provider"], "ollama")
            self.assertEqual(kwargs["language"], "Spanish")
            self.assertEqual(kwargs["max_features"], 8)
            self.assertEqual(kwargs["max_context_chars"], 18_000)
            self.assertEqual(generator.model, "fake-model")
            writer.assert_called_once_with(draft, output_path.resolve(), overwrite=False)

    def test_bootstrap_alias_returns_one_when_generation_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "repo"
            repo_path.mkdir()
            stderr = io.StringIO()

            with patch(
                "matcha_core.onboarding.OpenAICompatibleSpecGenerator.from_env",
                side_effect=RuntimeError("provider unavailable"),
            ):
                with patch("sys.stderr", stderr):
                    exit_code = cli.main(["bootstrap", str(repo_path), "--quiet"])

            self.assertEqual(exit_code, 1)
            self.assertIn("onboarding error: provider unavailable", stderr.getvalue())

    def test_check_writes_pass_artifact_and_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "repo"
            repo_path.mkdir()
            (repo_path / "SPECS.md").write_text("specs", encoding="utf-8")
            policy_path = repo_path / "policy.yml"
            policy_path.write_text(
                "version: 1\nfail_on: [not_implemented]\npriorities: [high]\non_inconclusive: block\n",
                encoding="utf-8",
            )
            output_path = Path(tmp) / "gate.json"
            fake_report = AnalysisReport(source="repo", specs_path="SPECS.md")
            evaluator = MagicMock()
            evaluator.model = "llama3.2"

            with patch("matcha_core.evaluator.OpenAICompatibleEvaluator.from_env", return_value=evaluator):
                analyzer_instance = MagicMock()
                analyzer_instance.analyze_path.return_value = fake_report
                with patch("matcha_core.engine.RepositoryAnalyzer", return_value=analyzer_instance):
                    exit_code = cli.main(
                        [
                            "check",
                            str(repo_path),
                            "--policy",
                            str(policy_path),
                            "--provider",
                            "ollama",
                            "--output",
                            str(output_path),
                            "--quiet",
                        ]
                    )

            artifact = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(artifact["decision"], "pass")
            self.assertEqual(artifact["run"]["model"], "llama3.2")

    def test_check_returns_ten_for_policy_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "repo"
            repo_path.mkdir()
            (repo_path / "SPECS.md").write_text("specs", encoding="utf-8")
            policy_path = repo_path / "policy.yml"
            policy_path.write_text(
                "version: 1\nfail_on: [not_implemented]\npriorities: [high]\nmin_confidence: 0.75\n",
                encoding="utf-8",
            )
            output_path = Path(tmp) / "gate.json"
            criterion = CriteriaResult(
                criteria_id="AC-1",
                description="Required behavior",
                implementation_status="not_implemented",
                confidence=0.9,
            )
            feature = FeatureResult(feature_id="FEAT-1", name="Feature", priority="High", criteria=[criterion])
            fake_report = AnalysisReport(source="repo", specs_path="SPECS.md", features=[feature])
            evaluator = MagicMock()
            evaluator.model = "gpt-test"

            with patch("matcha_core.evaluator.OpenAICompatibleEvaluator.from_env", return_value=evaluator):
                analyzer_instance = MagicMock()
                analyzer_instance.analyze_path.return_value = fake_report
                with patch("matcha_core.engine.RepositoryAnalyzer", return_value=analyzer_instance):
                    exit_code = cli.main(
                        [
                            "check",
                            str(repo_path),
                            "--policy",
                            str(policy_path),
                            "--output",
                            str(output_path),
                            "--quiet",
                        ]
                    )

            artifact = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 10)
            self.assertEqual(artifact["decision"], "fail")
            self.assertEqual(artifact["violations"][0]["criteria_id"], "AC-1")

    def test_check_returns_thirty_and_writes_error_artifact_for_missing_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "repo"
            repo_path.mkdir()
            output_path = Path(tmp) / "gate.json"

            exit_code = cli.main(
                ["check", str(repo_path), "--output", str(output_path), "--quiet"]
            )

            artifact = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 30)
            self.assertEqual(artifact["decision"], "error")

    def test_main_writes_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "repo"
            repo_path.mkdir()
            output_path = Path(tmp) / "report.json"
            fake_report = AnalysisReport(source="repo", specs_path="SPECS.md")

            with patch("matcha_core.evaluator.OpenAICompatibleEvaluator.from_env", return_value=object()):
                analyzer_instance = MagicMock()
                analyzer_instance.analyze_path.return_value = fake_report

                with patch("matcha_core.engine.RepositoryAnalyzer", return_value=analyzer_instance):
                    with patch("matcha_core.reporting.report_to_json", return_value='{"ok": true}'):
                        exit_code = cli.main(
                            [
                                "analyze",
                                str(repo_path),
                                "--format",
                                "json",
                                "--output",
                                str(output_path),
                                "--quiet",
                            ]
                        )

            self.assertEqual(exit_code, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), '{"ok": true}')

    def test_main_infers_html_from_output_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "repo"
            repo_path.mkdir()
            output_path = Path(tmp) / "report.html"
            fake_report = AnalysisReport(source="repo", specs_path="SPECS.md")

            with patch("matcha_core.evaluator.OpenAICompatibleEvaluator.from_env", return_value=object()):
                analyzer_instance = MagicMock()
                analyzer_instance.analyze_path.return_value = fake_report

                with patch("matcha_core.engine.RepositoryAnalyzer", return_value=analyzer_instance):
                    with patch("matcha_core.reporting.report_to_html", return_value="<html>ok</html>") as html_mock:
                        exit_code = cli.main(
                            [
                                "analyze",
                                str(repo_path),
                                "--output",
                                str(output_path),
                                "--quiet",
                            ]
                        )

            self.assertEqual(exit_code, 0)
            html_mock.assert_called_once_with(fake_report)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "<html>ok</html>")

    def test_main_returns_one_when_analysis_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "repo"
            repo_path.mkdir()
            stderr = io.StringIO()

            with patch("matcha_core.evaluator.OpenAICompatibleEvaluator.from_env", side_effect=RuntimeError("boom")):
                with patch("sys.stderr", stderr):
                    exit_code = cli.main(["analyze", str(repo_path), "--quiet"])

            self.assertEqual(exit_code, 1)
            self.assertIn("[matcha] error: boom", stderr.getvalue())

    def test_progress_printer_uses_human_labels(self):
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            cli.progress_printer("parsing")

        self.assertIn("[matcha] parsing specs", stderr.getvalue())

    def test_main_supports_table_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "repo"
            repo_path.mkdir()
            output_path = Path(tmp) / "report.txt"
            fake_report = AnalysisReport(source="repo", specs_path="SPECS.md")

            with patch("matcha_core.evaluator.OpenAICompatibleEvaluator.from_env", return_value=object()):
                analyzer_instance = MagicMock()
                analyzer_instance.analyze_path.return_value = fake_report

                with patch("matcha_core.engine.RepositoryAnalyzer", return_value=analyzer_instance):
                    with patch("matcha_core.reporting.report_to_table", return_value="table output"):
                        exit_code = cli.main(
                            [
                                "analyze",
                                str(repo_path),
                                "--format",
                                "table",
                                "--output",
                                str(output_path),
                                "--quiet",
                            ]
                        )

            self.assertEqual(exit_code, 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "table output")

    def test_main_passes_show_evidence_to_table_renderer(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "repo"
            repo_path.mkdir()
            fake_report = AnalysisReport(source="repo", specs_path="SPECS.md")

            with patch("matcha_core.evaluator.OpenAICompatibleEvaluator.from_env", return_value=object()):
                analyzer_instance = MagicMock()
                analyzer_instance.analyze_path.return_value = fake_report

                with patch("matcha_core.engine.RepositoryAnalyzer", return_value=analyzer_instance):
                    with patch("matcha_core.reporting.report_to_table", return_value="table output") as table_mock:
                        exit_code = cli.main(
                            [
                                "analyze",
                                str(repo_path),
                                "--format",
                                "table",
                                "--show-evidence",
                                "--quiet",
                            ]
                        )

            self.assertEqual(exit_code, 0)
            table_mock.assert_called_once_with(fake_report, include_details=True)

    def test_main_defaults_to_table_without_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "repo"
            repo_path.mkdir()
            fake_report = AnalysisReport(source="repo", specs_path="SPECS.md")
            stdout = io.StringIO()

            with patch("matcha_core.evaluator.OpenAICompatibleEvaluator.from_env", return_value=object()):
                analyzer_instance = MagicMock()
                analyzer_instance.analyze_path.return_value = fake_report

                with patch("matcha_core.engine.RepositoryAnalyzer", return_value=analyzer_instance):
                    with patch("matcha_core.reporting.report_to_table", return_value="table output") as table_mock:
                        with patch("sys.stdout", stdout):
                            exit_code = cli.main(
                                [
                                    "analyze",
                                    str(repo_path),
                                    "--quiet",
                                ]
                            )

            self.assertEqual(exit_code, 0)
            table_mock.assert_called_once_with(fake_report, include_details=False)
            self.assertEqual(stdout.getvalue().strip(), "table output")

    def test_main_supports_reporter_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "repo"
            repo_path.mkdir()
            output_path = Path(tmp) / "report.md"
            fake_report = AnalysisReport(source="repo", specs_path="SPECS.md")

            with patch("matcha_core.evaluator.OpenAICompatibleEvaluator.from_env", return_value=object()):
                analyzer_instance = MagicMock()
                analyzer_instance.analyze_path.return_value = fake_report

                with patch("matcha_core.engine.RepositoryAnalyzer", return_value=analyzer_instance):
                    with patch("matcha_core.reporting.report_to_markdown", return_value="# ok") as markdown_mock:
                        exit_code = cli.main(
                            [
                                "analyze",
                                str(repo_path),
                                "--reporter",
                                "markdown",
                                "--output",
                                str(output_path),
                                "--quiet",
                            ]
                        )

            self.assertEqual(exit_code, 0)
            markdown_mock.assert_called_once_with(fake_report)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "# ok")

    def test_main_warns_when_format_conflicts_with_output_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "repo"
            repo_path.mkdir()
            output_path = Path(tmp) / "report.html"
            fake_report = AnalysisReport(source="repo", specs_path="SPECS.md")
            stderr = io.StringIO()

            with patch("matcha_core.evaluator.OpenAICompatibleEvaluator.from_env", return_value=object()):
                analyzer_instance = MagicMock()
                analyzer_instance.analyze_path.return_value = fake_report

                with patch("matcha_core.engine.RepositoryAnalyzer", return_value=analyzer_instance):
                    with patch("matcha_core.reporting.report_to_json", return_value='{"ok": true}') as json_mock:
                        with patch("sys.stderr", stderr):
                            exit_code = cli.main(
                                [
                                    "analyze",
                                    str(repo_path),
                                    "--format",
                                    "json",
                                    "--output",
                                    str(output_path),
                                ]
                            )

            self.assertEqual(exit_code, 0)
            json_mock.assert_called_once_with(fake_report)
            self.assertIn("output file extension suggests 'html'", stderr.getvalue())

    def test_main_passes_feature_filter_to_analyzer(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "repo"
            repo_path.mkdir()
            fake_report = AnalysisReport(source="repo", specs_path="SPECS.md")

            with patch("matcha_core.evaluator.OpenAICompatibleEvaluator.from_env", return_value=object()):
                analyzer_instance = MagicMock()
                analyzer_instance.analyze_path.return_value = fake_report

                with patch("matcha_core.engine.RepositoryAnalyzer", return_value=analyzer_instance):
                    with patch("matcha_core.reporting.report_to_table", return_value="table output"):
                        exit_code = cli.main(
                            [
                                "analyze",
                                str(repo_path),
                                "--feature",
                                "FEAT-004",
                                "--quiet",
                            ]
                        )

            self.assertEqual(exit_code, 0)
            kwargs = analyzer_instance.analyze_path.call_args.kwargs
            self.assertEqual(kwargs["feature_ids"], ["FEAT-004"])

    def test_main_passes_debug_llm_path_to_analyzer(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "repo"
            repo_path.mkdir()
            debug_path = Path(tmp) / "llm-debug.jsonl"
            fake_report = AnalysisReport(source="repo", specs_path="SPECS.md")

            with patch("matcha_core.evaluator.OpenAICompatibleEvaluator.from_env", return_value=object()):
                analyzer_instance = MagicMock()
                analyzer_instance.analyze_path.return_value = fake_report
                with patch("matcha_core.engine.RepositoryAnalyzer", return_value=analyzer_instance):
                    with patch("matcha_core.reporting.report_to_json", return_value='{"ok": true}'):
                        exit_code = cli.main(
                            [
                                "analyze",
                                str(repo_path),
                                "--format",
                                "json",
                                "--debug-llm",
                                str(debug_path),
                                "--quiet",
                            ]
                        )

            self.assertEqual(exit_code, 0)
            analyzer_instance.analyze_path.assert_called_once()
            kwargs = analyzer_instance.analyze_path.call_args.kwargs
            self.assertEqual(kwargs["debug_output_path"], str(debug_path.resolve()))
