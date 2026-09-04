import tempfile
import textwrap
import unittest
from pathlib import Path

from matcha_core.specs_parser import SpecsParser, SpecsValidationError


class SpecsParserTests(unittest.TestCase):
    def test_bundled_template_is_valid_and_has_unique_features(self):
        template_path = Path(__file__).parent.parent / "docs" / "SPECS_TEMPLATE.md"

        features = SpecsParser().parse(str(template_path))

        feature_ids = [feature["id"] for feature in features]
        self.assertEqual(feature_ids, ["FEAT-1", "FEAT-2", "FEAT-3", "FEAT-4"])
        self.assertEqual(len(feature_ids), len(set(feature_ids)))
        self.assertTrue(all(feature["acceptance_criteria"] for feature in features))

    def test_strict_parser_ignores_fenced_examples(self):
        with tempfile.TemporaryDirectory() as tmp:
            specs_path = Path(tmp) / "SPECS.md"
            specs_path.write_text(
                textwrap.dedent(
                    """
                    # Product specs

                    ```md
                    ## FEAT-999 Example only
                    **Status**: Done
                    Acceptance Criteria:
                    - This fenced example must not be analyzed.
                    ```

                    ## FEAT-1 Real feature
                    **Status**: Done
                    Acceptance Criteria:
                    - The real behavior is present in the repository.
                    """
                ).strip(),
                encoding="utf-8",
            )

            features = SpecsParser().parse(str(specs_path))

        self.assertEqual([feature["id"] for feature in features], ["FEAT-1"])
        self.assertEqual(len(features[0]["acceptance_criteria"]), 1)

    def test_strict_parser_rejects_document_without_feature_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            specs_path = Path(tmp) / "SPECS.md"
            specs_path.write_text("# Product specs\n\nNo feature sections yet.\n", encoding="utf-8")

            with self.assertRaisesRegex(SpecsValidationError, "No feature sections found"):
                SpecsParser().parse(str(specs_path))

    def test_strict_parser_rejects_duplicate_feature_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            specs_path = Path(tmp) / "SPECS.md"
            specs_path.write_text(
                textwrap.dedent(
                    """
                    ## FEAT-1 First
                    Acceptance Criteria:
                    - The first criterion is specific and observable.

                    ## FEAT-1 Duplicate
                    Acceptance Criteria:
                    - The duplicate criterion is specific and observable.
                    """
                ).strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SpecsValidationError, "duplicate feature IDs"):
                SpecsParser().parse(str(specs_path))

    def test_strict_parser_rejects_feature_without_criteria(self):
        with tempfile.TemporaryDirectory() as tmp:
            specs_path = Path(tmp) / "SPECS.md"
            specs_path.write_text("## FEAT-1 Empty feature\n\n**Status**: Done\n", encoding="utf-8")

            with self.assertRaisesRegex(SpecsValidationError, "without acceptance criteria"):
                SpecsParser().parse(str(specs_path))
