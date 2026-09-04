import unittest

from matcha_core.models import AnalysisReport, CriteriaResult, FeatureResult
from matcha_core.reporting import report_to_html, report_to_json, report_to_markdown, report_to_table


def sample_report() -> AnalysisReport:
    return AnalysisReport(
        source="demo-repo",
        specs_path="docs/SPECS.md",
        commit_hash="abc1234",
        total_features=1,
        total_criteria=1,
        implemented_count=1,
        different_count=0,
        not_implemented_count=0,
        not_specified_count=0,
        global_confidence=0.92,
        features=[
            FeatureResult(
                feature_id="FEAT-1",
                name="Feature <One>",
                description="Checks escaping & rendering",
                priority="High",
                status="Done",
                implementation_status="implemented_as_expected",
                confidence=0.92,
                criteria=[
                    CriteriaResult(
                        criteria_id="AC-1",
                        description="Render <escaped> content correctly.",
                        implementation_status="implemented_as_expected",
                        confidence=0.92,
                        short_explanation="Everything looks good.",
                        detailed_explanation="The renderer should escape unsafe HTML.",
                        referenced_files=["src/app.py"],
                        code_snippets='[{"file_path":"src/app.py","line_start":1,"line_end":2,"code":"print(\\"ok\\")","explanation":"Example evidence"}]',
                    )
                ],
            )
        ],
    )


class ReportingTests(unittest.TestCase):
    def test_report_to_json_contains_expected_fields(self):
        rendered = report_to_json(sample_report())
        self.assertIn('"source": "demo-repo"', rendered)
        self.assertIn('"feature_id": "FEAT-1"', rendered)
        self.assertIn('"schema_version": "1.0"', rendered)

    def test_report_to_markdown_contains_summary(self):
        rendered = report_to_markdown(sample_report())
        self.assertIn("# Matcha Analysis Report", rendered)
        self.assertIn("## FEAT-1: Feature <One>", rendered)
        self.assertIn("- Result: `implemented_as_expected`", rendered)

    def test_report_to_html_escapes_html_and_renders_evidence(self):
        rendered = report_to_html(sample_report())
        self.assertIn("<!DOCTYPE html>", rendered)
        self.assertIn("Feature &lt;One&gt;", rendered)
        self.assertIn("Render &lt;escaped&gt; content correctly.", rendered)
        self.assertIn("src/app.py:1-2", rendered)
        self.assertIn('id="criteria-search"', rendered)
        self.assertIn('data-filter="implemented_as_expected"', rendered)
        self.assertIn('data-feature-status="implemented_as_expected"', rendered)
        self.assertIn('data-criteria-status="implemented_as_expected"', rendered)
        self.assertIn('data-search-text="AC-1 Render &lt;escaped&gt; content correctly.', rendered)

    def test_report_to_html_formats_structured_feature_description(self):
        report = sample_report()
        report.features[0].description = (
            "Posting guardrails and crisis prompt\n"
            "Priority: High\n"
            "Status: Done\n"
            "Related Components: Hubman/ViewModels/HubmanViewModel.swift, Hubman/Views/ContentView.swift\n"
            "Acceptance Criteria:\n"
            "The active post flow blocks submissions that contain hashtags, links, email addresses, or phone numbers before insert.\n"
            "The database trigger validatebublcontent_trigger rejects posts that contain external links, email addresses, phone numbers, or @ handles."
        )
        rendered = report_to_html(report)

        self.assertIn('class="feature-specs"', rendered)
        self.assertIn('<span class="feature-spec-label">Summary</span>', rendered)
        self.assertIn("Posting guardrails and crisis prompt", rendered)
        self.assertIn('<span class="feature-spec-label">Related Components</span>', rendered)
        self.assertIn("Hubman/ViewModels/HubmanViewModel.swift", rendered)
        self.assertIn('<span class="feature-spec-label">Acceptance Criteria</span>', rendered)
        self.assertIn("<li>The active post flow blocks submissions", rendered)
        self.assertIn("<li>The database trigger validatebublcontent_trigger rejects posts", rendered)
        self.assertNotIn('class="feature-description">Priority: High', rendered)

    def test_report_to_table_renders_ascii_summary(self):
        rendered = report_to_table(sample_report())
        self.assertIn("Source: demo-repo", rendered)
        self.assertIn("| Feature", rendered)
        self.assertIn("Implemented", rendered)
        self.assertIn("Everything looks good.", rendered)
        self.assertNotIn("Details", rendered)
        self.assertNotIn("Evidence: src/app.py:1-2", rendered)

    def test_report_to_table_can_render_detailed_evidence(self):
        rendered = report_to_table(sample_report(), include_details=True)
        self.assertIn("Details", rendered)
        self.assertIn("Files: src/app.py", rendered)
        self.assertIn("Evidence: src/app.py:1-2", rendered)
        self.assertIn("Code: print(\"ok\")", rendered)
