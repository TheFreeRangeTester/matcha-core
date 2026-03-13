import os
import unittest
from unittest.mock import patch

from matcha_core.evaluator import (
    OpenAICompatibleEvaluator,
    default_api_key_for_base_url,
    extract_balanced_json,
    extract_json_payload,
    normalize_base_url,
    strip_non_json_wrappers,
)


class EvaluatorHelpersTests(unittest.TestCase):
    def test_normalize_base_url_adds_v1_for_ollama(self):
        self.assertEqual(normalize_base_url("http://localhost:11434"), "http://localhost:11434/v1")
        self.assertEqual(normalize_base_url("http://localhost:11434/v1"), "http://localhost:11434/v1")

    def test_default_api_key_for_ollama(self):
        self.assertEqual(default_api_key_for_base_url("http://localhost:11434/v1"), "ollama")
        self.assertIsNone(default_api_key_for_base_url("https://api.openai.com/v1"))

    def test_strip_non_json_wrappers_removes_think_tags(self):
        raw = "<think>reasoning</think>\n```json\n{\"status\":\"implemented_as_expected\"}\n```"
        cleaned = strip_non_json_wrappers(raw)
        self.assertEqual(cleaned, '{"status":"implemented_as_expected"}')

    def test_extract_balanced_json_finds_embedded_object(self):
        raw = 'Here is the result:\n{"status":"implemented_as_expected","confidence":95}\nThanks'
        extracted = extract_balanced_json(raw)
        self.assertEqual(extracted, '{"status":"implemented_as_expected","confidence":95}')

    def test_extract_json_payload_handles_embedded_json(self):
        raw = 'Some intro text\n{"status":"implemented_as_expected","confidence":95,"evidence":[]}\ntrailing'
        extracted = extract_json_payload(raw)
        self.assertEqual(extracted, '{"status":"implemented_as_expected","confidence":95,"evidence":[]}')

    def test_extract_json_payload_raises_on_empty_response(self):
        with self.assertRaises(ValueError):
            extract_json_payload("   ")

    @patch.dict(os.environ, {"OLLAMA_MODEL": "llama3.2"}, clear=False)
    @patch.object(OpenAICompatibleEvaluator, "__init__", return_value=None)
    def test_from_env_supports_ollama_provider(self, init_mock):
        OpenAICompatibleEvaluator.from_env(provider="ollama")
        init_mock.assert_called_once_with(
            api_key="ollama",
            base_url="http://localhost:11434/v1",
            model="llama3.2",
        )

    def test_evaluate_criteria_retries_after_parse_error(self):
        evaluator = OpenAICompatibleEvaluator.__new__(OpenAICompatibleEvaluator)
        evaluator._call_model = lambda *_args, **_kwargs: "not-json"
        evaluator._repair_json_response = lambda _raw: '{"status":"implemented_as_expected","confidence":82,"short_explanation":"ok","detailed_explanation":"ok","evidence":[{"file_path":"src/auth.py","code":"def login(): pass","explanation":"login handler"}]}'
        evaluator._parse_response = OpenAICompatibleEvaluator._parse_response.__get__(evaluator, OpenAICompatibleEvaluator)

        result = evaluator.evaluate_criteria(
            criteria_description="Users can login",
            code_context="def login(): pass",
            feature_name="Auth",
        )

        self.assertEqual(result["status"], "implemented_as_expected")
        self.assertGreaterEqual(result["confidence"], 0.8)

    def test_evaluate_criteria_falls_back_when_retry_also_fails(self):
        evaluator = OpenAICompatibleEvaluator.__new__(OpenAICompatibleEvaluator)
        evaluator._call_model = lambda *_args, **_kwargs: "not-json"
        evaluator._repair_json_response = lambda _raw: "still-not-json"
        evaluator._parse_response = OpenAICompatibleEvaluator._parse_response.__get__(evaluator, OpenAICompatibleEvaluator)
        evaluator._fallback_evaluation = OpenAICompatibleEvaluator._fallback_evaluation.__get__(evaluator, OpenAICompatibleEvaluator)

        result = evaluator.evaluate_criteria(
            criteria_description="Users can login",
            code_context="def login(): pass",
            feature_name="Auth",
        )

        self.assertIn("retry failed", result["detailed_explanation"])
        self.assertIn("heuristic analysis", result["short_explanation"])

    def test_parse_response_extracts_structured_text_without_json(self):
        evaluator = OpenAICompatibleEvaluator.__new__(OpenAICompatibleEvaluator)
        evaluator._parse_response = OpenAICompatibleEvaluator._parse_response.__get__(evaluator, OpenAICompatibleEvaluator)
        evaluator._normalize_result_payload = OpenAICompatibleEvaluator._normalize_result_payload.__get__(
            evaluator, OpenAICompatibleEvaluator
        )
        evaluator._extract_structured_fields_from_text = OpenAICompatibleEvaluator._extract_structured_fields_from_text.__get__(
            evaluator, OpenAICompatibleEvaluator
        )
        evaluator._normalize_status = OpenAICompatibleEvaluator._normalize_status.__get__(
            evaluator, OpenAICompatibleEvaluator
        )

        raw = """Status: implemented
Confidence: 87%
short_explanation: Route exists and enforces premium checks
detailed_explanation: I found middleware and route handlers that gate the feature."""
        result = evaluator._parse_response(raw)

        self.assertEqual(result["status"], "implemented_differently")
        self.assertLessEqual(result["confidence"], 0.65)
        self.assertIn("premium checks", result["short_explanation"])

    def test_parse_response_extracts_from_malformed_json_like_text(self):
        evaluator = OpenAICompatibleEvaluator.__new__(OpenAICompatibleEvaluator)
        evaluator._parse_response = OpenAICompatibleEvaluator._parse_response.__get__(evaluator, OpenAICompatibleEvaluator)
        evaluator._normalize_result_payload = OpenAICompatibleEvaluator._normalize_result_payload.__get__(
            evaluator, OpenAICompatibleEvaluator
        )
        evaluator._extract_structured_fields_from_text = OpenAICompatibleEvaluator._extract_structured_fields_from_text.__get__(
            evaluator, OpenAICompatibleEvaluator
        )
        evaluator._normalize_status = OpenAICompatibleEvaluator._normalize_status.__get__(
            evaluator, OpenAICompatibleEvaluator
        )

        raw = """{
  "status": "not_implemented",
  "confidence": 100,
  "short_explanation": "The feature is not implemented."
"""
        result = evaluator._parse_response(raw)

        self.assertEqual(result["status"], "not_implemented")
        self.assertEqual(result["confidence"], 1.0)
        self.assertEqual(result["short_explanation"], "The feature is not implemented.")

    def test_parse_response_downgrades_implemented_without_evidence(self):
        evaluator = OpenAICompatibleEvaluator.__new__(OpenAICompatibleEvaluator)
        evaluator._parse_response = OpenAICompatibleEvaluator._parse_response.__get__(evaluator, OpenAICompatibleEvaluator)
        evaluator._normalize_result_payload = OpenAICompatibleEvaluator._normalize_result_payload.__get__(
            evaluator, OpenAICompatibleEvaluator
        )
        evaluator._extract_structured_fields_from_text = OpenAICompatibleEvaluator._extract_structured_fields_from_text.__get__(
            evaluator, OpenAICompatibleEvaluator
        )
        evaluator._normalize_status = OpenAICompatibleEvaluator._normalize_status.__get__(
            evaluator, OpenAICompatibleEvaluator
        )
        evaluator._enforce_evidence_quality = OpenAICompatibleEvaluator._enforce_evidence_quality.__get__(
            evaluator, OpenAICompatibleEvaluator
        )

        raw = '{"status":"implemented_as_expected","confidence":95,"short_explanation":"done","detailed_explanation":"done","evidence":[]}'
        result = evaluator._parse_response(raw)

        self.assertEqual(result["status"], "implemented_differently")
        self.assertLessEqual(result["confidence"], 0.65)

    def test_parse_response_keeps_implemented_with_evidence(self):
        evaluator = OpenAICompatibleEvaluator.__new__(OpenAICompatibleEvaluator)
        evaluator._parse_response = OpenAICompatibleEvaluator._parse_response.__get__(evaluator, OpenAICompatibleEvaluator)
        evaluator._normalize_result_payload = OpenAICompatibleEvaluator._normalize_result_payload.__get__(
            evaluator, OpenAICompatibleEvaluator
        )
        evaluator._extract_structured_fields_from_text = OpenAICompatibleEvaluator._extract_structured_fields_from_text.__get__(
            evaluator, OpenAICompatibleEvaluator
        )
        evaluator._normalize_status = OpenAICompatibleEvaluator._normalize_status.__get__(
            evaluator, OpenAICompatibleEvaluator
        )
        evaluator._enforce_evidence_quality = OpenAICompatibleEvaluator._enforce_evidence_quality.__get__(
            evaluator, OpenAICompatibleEvaluator
        )

        raw = (
            '{"status":"implemented_as_expected","confidence":95,"short_explanation":"done","detailed_explanation":"done",'
            '"evidence":[{"file_path":"server/routes.ts","code":"app.post(...)","explanation":"route"}]}'
        )
        result = evaluator._parse_response(raw)

        self.assertEqual(result["status"], "implemented_as_expected")
        self.assertGreaterEqual(result["confidence"], 0.95)
