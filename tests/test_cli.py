import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from matcha_core import cli
from matcha_core.models import AnalysisReport


class CliTests(unittest.TestCase):
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
