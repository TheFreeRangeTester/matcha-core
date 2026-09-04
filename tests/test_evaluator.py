import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from matcha_core.evaluator import (
    OpenAICompatibleEvaluator,
    ResponseParseError,
    default_reasoning_effort,
    default_api_key_for_base_url,
    extract_balanced_json,
    extract_json_payload,
    normalize_base_url,
    strip_non_json_wrappers,
)


class UnsupportedParameterError(Exception):
    status_code = 400

    def __init__(self, parameter):
        self.body = {"error": {"param": parameter}}
        super().__init__(f"Unsupported parameter: {parameter}")


class EvaluatorHelpersTests(unittest.TestCase):
    def test_normalize_base_url_adds_v1_for_ollama(self):
        self.assertEqual(normalize_base_url("http://localhost:11434"), "http://localhost:11434/v1")
        self.assertEqual(normalize_base_url("http://localhost:11434/v1"), "http://localhost:11434/v1")

    def test_default_api_key_for_ollama(self):
        self.assertEqual(default_api_key_for_base_url("http://localhost:11434/v1"), "ollama")
        self.assertIsNone(default_api_key_for_base_url("https://api.openai.com/v1"))

    def test_reasoning_models_default_to_low_effort(self):
        self.assertEqual(default_reasoning_effort("gpt-5.6-sol"), "low")
        self.assertEqual(default_reasoning_effort("o3"), "low")
        self.assertIsNone(default_reasoning_effort("gpt-4o-mini"))

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
            timeout=60.0,
            max_retries=1,
            reasoning_effort=None,
        )

    def test_json_completion_retries_empty_length_response_with_larger_budget(self):
        evaluator = OpenAICompatibleEvaluator.__new__(OpenAICompatibleEvaluator)
        evaluator._create_completion = MagicMock(
            side_effect=[
                SimpleNamespace(
                    choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(content=""))],
                    usage=SimpleNamespace(prompt_tokens=100, completion_tokens=4000, total_tokens=4100),
                ),
                SimpleNamespace(
                    choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"ok":true}'))],
                    usage=SimpleNamespace(prompt_tokens=100, completion_tokens=5000, total_tokens=5100),
                ),
            ]
        )

        result = evaluator.create_json_completion(
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=4_000,
        )

        self.assertEqual(result, '{"ok":true}')
        calls = evaluator._create_completion.call_args_list
        self.assertEqual(calls[0].kwargs["max_tokens"], 4_000)
        self.assertEqual(calls[1].kwargs["max_tokens"], 8_000)
        self.assertEqual([item["finish_reason"] for item in evaluator.last_json_completion_metadata], ["length", "stop"])

    def test_completion_retries_with_max_completion_tokens_and_remembers_capability(self):
        evaluator = OpenAICompatibleEvaluator.__new__(OpenAICompatibleEvaluator)
        evaluator.client = MagicMock()
        evaluator.model = "gpt-5.6-sol"
        first_result = object()
        second_result = object()
        evaluator.client.chat.completions.create.side_effect = [
            UnsupportedParameterError("max_tokens"),
            first_result,
            second_result,
        ]

        result = evaluator._create_completion(
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=321,
            response_format={"type": "json_object"},
        )
        cached_result = evaluator._create_completion(
            messages=[{"role": "user", "content": "again"}],
            max_tokens=654,
        )

        self.assertIs(result, first_result)
        self.assertIs(cached_result, second_result)
        calls = evaluator.client.chat.completions.create.call_args_list
        self.assertEqual(calls[0].kwargs["max_tokens"], 321)
        self.assertEqual(calls[1].kwargs["max_completion_tokens"], 321)
        self.assertIn("response_format", calls[1].kwargs)
        self.assertNotIn("max_tokens", calls[1].kwargs)
        self.assertEqual(calls[2].kwargs["max_completion_tokens"], 654)
        self.assertNotIn("max_tokens", calls[2].kwargs)

    def test_completion_removes_temperature_only_when_provider_rejects_it(self):
        evaluator = OpenAICompatibleEvaluator.__new__(OpenAICompatibleEvaluator)
        evaluator.client = MagicMock()
        evaluator.model = "gpt-5.6-sol"
        evaluator._completion_token_parameter = "max_completion_tokens"
        expected = object()
        evaluator.client.chat.completions.create.side_effect = [
            UnsupportedParameterError("temperature"),
            expected,
        ]

        result = evaluator._create_completion(
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=321,
        )

        self.assertIs(result, expected)
        calls = evaluator.client.chat.completions.create.call_args_list
        self.assertEqual(calls[0].kwargs["temperature"], 0.0)
        self.assertNotIn("temperature", calls[1].kwargs)
        self.assertFalse(evaluator._supports_temperature)

    def test_completion_removes_reasoning_effort_when_provider_rejects_it(self):
        evaluator = OpenAICompatibleEvaluator.__new__(OpenAICompatibleEvaluator)
        evaluator.client = MagicMock()
        evaluator.model = "compatible-model"
        evaluator.reasoning_effort = "low"
        expected = object()
        evaluator.client.chat.completions.create.side_effect = [
            UnsupportedParameterError("reasoning_effort"),
            expected,
        ]

        result = evaluator._create_completion(
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=321,
        )

        self.assertIs(result, expected)
        calls = evaluator.client.chat.completions.create.call_args_list
        self.assertEqual(calls[0].kwargs["reasoning_effort"], "low")
        self.assertNotIn("reasoning_effort", calls[1].kwargs)
        self.assertIsNone(evaluator.reasoning_effort)

    def test_completion_keeps_legacy_max_tokens_when_provider_accepts_it(self):
        evaluator = OpenAICompatibleEvaluator.__new__(OpenAICompatibleEvaluator)
        evaluator.client = MagicMock()
        evaluator.model = "llama3"
        expected = object()
        evaluator.client.chat.completions.create.return_value = expected

        result = evaluator._create_completion(
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=321,
        )

        self.assertIs(result, expected)
        kwargs = evaluator.client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["max_tokens"], 321)
        self.assertNotIn("max_completion_tokens", kwargs)

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

    def test_evaluate_criteria_reports_failure_when_retry_also_fails(self):
        evaluator = OpenAICompatibleEvaluator.__new__(OpenAICompatibleEvaluator)
        evaluator._call_model = lambda *_args, **_kwargs: "not-json"
        evaluator._repair_json_response = lambda _raw: "still-not-json"
        evaluator._parse_response = OpenAICompatibleEvaluator._parse_response.__get__(evaluator, OpenAICompatibleEvaluator)
        evaluator._analysis_failed_evaluation = OpenAICompatibleEvaluator._analysis_failed_evaluation.__get__(
            evaluator, OpenAICompatibleEvaluator
        )

        result = evaluator.evaluate_criteria(
            criteria_description="Users can login",
            code_context="def login(): pass",
            feature_name="Auth",
        )

        self.assertEqual(result["status"], "analysis_failed")
        self.assertEqual(result["analysis_mode"], "analysis_failed")
        self.assertIn("repair failed", result["detailed_explanation"])

    def test_parse_response_rejects_structured_text_without_json(self):
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
        with self.assertRaises(ResponseParseError):
            evaluator._parse_response(raw)

    def test_parse_response_rejects_incomplete_json(self):
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
        with self.assertRaises(ResponseParseError):
            evaluator._parse_response(raw)

    def test_no_code_context_is_inconclusive(self):
        evaluator = OpenAICompatibleEvaluator.__new__(OpenAICompatibleEvaluator)
        result = evaluator.evaluate_criteria(
            criteria_description="Users can login",
            code_context="No relevant code files found for this criteria.",
            feature_name="Auth",
        )

        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["analysis_mode"], "no_code_context")
        self.assertEqual(result["confidence"], 0.0)

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
