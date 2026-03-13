import tempfile
import textwrap
import unittest
from pathlib import Path

from matcha_core.engine import AnalysisError, RepositoryAnalyzer
from matcha_core.models import ImplementationStatus


class FakeEvaluator:
    def __init__(self):
        self.calls = []

    def evaluate_criteria(self, criteria_description: str, code_context: str, feature_name: str, debug=None):
        self.calls.append(
            {
                "criteria_description": criteria_description,
                "code_context": code_context,
                "feature_name": feature_name,
            }
        )
        return {
            "status": ImplementationStatus.IMPLEMENTED_AS_EXPECTED.value,
            "confidence": 0.85,
            "short_explanation": "Fake evaluator says it is implemented.",
            "detailed_explanation": "The fake evaluator found enough code context to mark this as implemented.",
            "code_snippets": "",
        }


class RepositoryAnalyzerTests(unittest.TestCase):
    def test_analyze_path_builds_report_for_local_repo(self):
        evaluator = FakeEvaluator()
        analyzer = RepositoryAnalyzer(evaluator=evaluator)
        progress = []

        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            (repo_path / "SPECS.md").write_text(
                textwrap.dedent(
                    """
                    ## FEAT-1 User login
                    **Priority**: High
                    **Status**: Done

                    Acceptance Criteria:
                    - The system should allow users to login with email and password.
                    """
                ).strip(),
                encoding="utf-8",
            )
            src = repo_path / "src"
            src.mkdir()
            (src / "auth.py").write_text(
                "def login_user(email, password):\n    return {'ok': True}\n",
                encoding="utf-8",
            )

            report = analyzer.analyze_path(
                repo_path=str(repo_path),
                progress_callback=progress.append,
            )

        self.assertEqual(progress, ["parsing", "analyzing"])
        self.assertEqual(report.total_features, 1)
        self.assertEqual(report.total_criteria, 1)
        self.assertEqual(report.implemented_count, 1)
        self.assertEqual(report.features[0].implementation_status, ImplementationStatus.IMPLEMENTED_AS_EXPECTED.value)
        self.assertEqual(report.features[0].criteria[0].implementation_status, ImplementationStatus.IMPLEMENTED_AS_EXPECTED.value)
        self.assertEqual(len(evaluator.calls), 1)
        self.assertIn("src/auth.py", evaluator.calls[0]["code_context"])

    def test_analyze_path_skips_planned_features(self):
        evaluator = FakeEvaluator()
        analyzer = RepositoryAnalyzer(evaluator=evaluator)

        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            (repo_path / "SPECS.md").write_text(
                textwrap.dedent(
                    """
                    ## FEAT-1 Future dashboard
                    **Status**: Planned

                    Acceptance Criteria:
                    - The system should show a dashboard.
                    """
                ).strip(),
                encoding="utf-8",
            )
            (repo_path / "dashboard.py").write_text("print('placeholder')\n", encoding="utf-8")

            report = analyzer.analyze_path(str(repo_path))

        self.assertEqual(len(evaluator.calls), 0)
        self.assertEqual(report.not_implemented_count, 1)
        self.assertEqual(report.features[0].criteria[0].confidence, 1.0)
        self.assertEqual(
            report.features[0].criteria[0].implementation_status,
            ImplementationStatus.NOT_IMPLEMENTED.value,
        )

    def test_analyze_path_requires_specs(self):
        analyzer = RepositoryAnalyzer(evaluator=FakeEvaluator())

        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            (repo_path / "app.py").write_text("print('hi')\n", encoding="utf-8")

            with self.assertRaises(AnalysisError):
                analyzer.analyze_path(str(repo_path))

    def test_analyze_path_writes_debug_jsonl(self):
        evaluator = FakeEvaluator()
        analyzer = RepositoryAnalyzer(evaluator=evaluator)

        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            debug_path = repo_path / "llm-debug.jsonl"
            (repo_path / "SPECS.md").write_text(
                textwrap.dedent(
                    """
                    ## FEAT-1 User login
                    **Priority**: High
                    **Status**: Done

                    Acceptance Criteria:
                    - The system should allow users to login with email and password.
                    """
                ).strip(),
                encoding="utf-8",
            )
            (repo_path / "src").mkdir()
            (repo_path / "src" / "auth.py").write_text("def login_user(email, password):\n    return {'ok': True}\n", encoding="utf-8")

            analyzer.analyze_path(str(repo_path), debug_output_path=str(debug_path))

            lines = debug_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertIn('"criteria_description": "The system should allow users to login with email and password."', lines[0])
            self.assertIn('"analysis_mode": "evaluator"', lines[0])
