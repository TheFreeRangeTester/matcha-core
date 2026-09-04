from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Optional

from .models import ImplementationStatus

logger = logging.getLogger(__name__)


def _exception_mentions_parameter(exc: Exception, parameter: str) -> bool:
    """Return whether a provider error explicitly names a rejected parameter."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict) and error.get("param") == parameter:
            return True
    return parameter.lower() in str(exc).lower()


def default_reasoning_effort(model: str) -> Optional[str]:
    """Use a bounded reasoning default for OpenAI reasoning-model families."""
    normalized = (model or "").lower()
    if normalized.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
        return "low"
    return None


def _completion_metadata(response: Any) -> Dict[str, Any]:
    choices = getattr(response, "choices", None) or []
    finish_reason = getattr(choices[0], "finish_reason", None) if choices else None
    usage = getattr(response, "usage", None)
    usage_data: Dict[str, Any] = {}
    if usage is not None:
        if hasattr(usage, "model_dump"):
            usage_data = usage.model_dump(exclude_none=True)
        else:
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = getattr(usage, field, None)
                if value is not None:
                    usage_data[field] = value
    return {"finish_reason": finish_reason, "usage": usage_data}


class ResponseParseError(ValueError):
    pass


class OpenAICompatibleEvaluator:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "gpt-4o-mini",
        timeout: float = 60.0,
        max_retries: int = 1,
        reasoning_effort: Optional[str] = None,
    ):
        normalized_base_url = normalize_base_url(base_url)
        resolved_api_key = api_key or default_api_key_for_base_url(normalized_base_url)

        if not resolved_api_key and not normalized_base_url:
            raise ValueError("An API key is required unless you point to an OpenAI-compatible endpoint such as Ollama.")

        from openai import OpenAI

        self.client = OpenAI(
            api_key=resolved_api_key,
            base_url=normalized_base_url,
            timeout=timeout,
            max_retries=max_retries,
        )
        self.model = model
        self._completion_token_parameter = "max_tokens"
        self._supports_temperature = True
        self.reasoning_effort = reasoning_effort or default_reasoning_effort(model)
        self.last_json_completion_metadata: list[Dict[str, Any]] = []

    @classmethod
    def from_env(
        cls,
        provider: str = "openai",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 60.0,
        max_retries: int = 1,
        reasoning_effort: Optional[str] = None,
    ) -> "OpenAICompatibleEvaluator":
        provider = provider.lower()

        if provider == "ollama":
            resolved_base_url = base_url or os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434/v1"
            resolved_model = model or os.environ.get("OLLAMA_MODEL") or "llama3.2"
            resolved_api_key = api_key or os.environ.get("OLLAMA_API_KEY") or "ollama"
            return cls(
                api_key=resolved_api_key,
                base_url=resolved_base_url,
                model=resolved_model,
                timeout=timeout,
                max_retries=max_retries,
                reasoning_effort=reasoning_effort,
            )

        resolved_api_key = (
            api_key
            or os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        resolved_base_url = (
            base_url
            or os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
        )
        resolved_model = (
            model
            or os.environ.get("AI_INTEGRATIONS_OPENAI_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or "gpt-4o-mini"
        )
        resolved_reasoning_effort = reasoning_effort or os.environ.get("OPENAI_REASONING_EFFORT")
        return cls(
            api_key=resolved_api_key,
            base_url=resolved_base_url,
            model=resolved_model,
            timeout=timeout,
            max_retries=max_retries,
            reasoning_effort=resolved_reasoning_effort,
        )

    def evaluate_criteria(
        self,
        criteria_description: str,
        code_context: str,
        feature_name: str,
        debug: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if debug is not None:
            debug["model"] = self.model
            debug["criteria_description"] = criteria_description
            debug["feature_name"] = feature_name
            debug["code_context_chars"] = len(code_context or "")

        if not code_context or code_context == "No relevant code files found for this criteria.":
            if debug is not None:
                debug["analysis_mode"] = "no_code_context"
            return {
                "status": ImplementationStatus.INCONCLUSIVE.value,
                "confidence": 0.0,
                "short_explanation": "No relevant code context was found",
                "detailed_explanation": (
                    "Matcha could not retrieve code relevant enough to evaluate this criterion safely. "
                    "This is inconclusive, not proof that the behavior is missing."
                ),
                "code_snippets": "",
                "analysis_mode": "no_code_context",
                "error": None,
            }

        try:
            response = self._call_model(criteria_description, code_context, feature_name)
            if debug is not None:
                debug["primary_response"] = response[:12000]
            try:
                parsed = self._parse_response(response)
                if debug is not None:
                    debug["analysis_mode"] = "parsed"
                    debug["parse_source"] = parsed.get("_parse_source", "unknown")
                parsed["analysis_mode"] = "model"
                parsed["error"] = None
                return parsed
            except ResponseParseError as parse_exc:
                logger.warning("Primary model response could not be parsed; attempting JSON repair pass.")
                if debug is not None:
                    debug["primary_parse_error"] = str(parse_exc)
                repaired_response = self._repair_json_response(response)
                if debug is not None:
                    debug["repair_response"] = repaired_response[:12000]
                try:
                    parsed = self._parse_response(repaired_response)
                    if debug is not None:
                        debug["analysis_mode"] = "parsed_after_repair"
                        debug["parse_source"] = parsed.get("_parse_source", "unknown")
                    parsed["analysis_mode"] = "model_repaired"
                    parsed["error"] = None
                    return parsed
                except ResponseParseError as repair_exc:
                    if debug is not None:
                        debug["repair_parse_error"] = str(repair_exc)
                        debug["analysis_mode"] = "analysis_failed"
                    return self._analysis_failed_evaluation(
                        f"Model response could not be parsed: {parse_exc}; repair failed: {repair_exc}"
                    )
        except Exception as exc:
            if debug is not None:
                debug["analysis_mode"] = "analysis_failed"
                debug["exception"] = str(exc)
            return self._analysis_failed_evaluation(f"Model evaluation failed: {exc}")

    def create_json_completion(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2000,
        json_schema: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Return text from an OpenAI-compatible completion that prefers JSON mode."""
        response_format = None
        if json_schema is not None:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "matcha_structured_output",
                    "strict": True,
                    "schema": json_schema,
                },
            }
        response = self._create_completion(
            messages=messages,
            prefer_json_object=json_schema is None,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        metadata = _completion_metadata(response)
        attempts = [metadata]
        content = response.choices[0].message.content or ""

        if not content.strip() and metadata.get("finish_reason") == "length":
            response = self._create_completion(
                messages=messages,
                prefer_json_object=json_schema is None,
                max_tokens=min(max_tokens * 2, 32_000),
                response_format=response_format,
            )
            attempts.append(_completion_metadata(response))
            content = response.choices[0].message.content or ""

        self.last_json_completion_metadata = attempts
        return content

    def _call_model(self, criteria: str, code: str, feature: str) -> str:
        system_prompt = """You are a meticulous software auditor. Examine the supplied code carefully before making conclusions.

ANALYSIS PROCESS (you must follow these steps):
1. SCAN: List the files/sections you received
2. SEARCH: Look for code related to the requirement (routes, handlers, validations, database operations)
3. EXAMINE: For each relevant code section, describe what it does
4. MATCH: Compare what the code does vs what the requirement asks for
5. CONCLUDE: Only after steps 1-4, give your verdict

KEY RULES:
- Comments (// or /* */) are NOT implementations
- The code MAY be deep in a function - read the ENTIRE context
- Look for: if statements, error responses (403/400), variable names, function logic
- If you find code that fulfills the requirement, it IS implemented
- MULTI-FILE LOGIC: Features may span multiple files (routes, storage, client). Trace the full flow before concluding.

CONFIDENCE:
- 80-100%: Clear code directly handles this requirement
- 50-79%: Related code exists but unclear if it fully covers this
- 0-49%: Uncertain, limited evidence

Always respond with ONLY valid JSON, no markdown."""

        user_prompt = f"""Check if this feature requirement is built into the code.

FEATURE: {feature}

REQUIREMENT:
{criteria}

CODE TO ANALYZE:
{code}

SEARCH STRATEGY - Look carefully for:
1. API route handlers (app.put, app.post, router.put, etc.) that handle this feature
2. Validation/restriction logic: if statements checking conditions like "if (votes > 100)", "if (creatorId !== userId)"
3. Error responses (403, 401, 400) with messages explaining restrictions
4. Database queries or storage methods that implement this behavior
5. Schema definitions with constraints

IMPORTANT: Read the ENTIRE code context. The relevant logic may be deep in a function, not at the top.
Look for patterns like:
- "if (something > limit)" → vote/count restrictions
- "if (ownerId !== userId)" → ownership checks
- "return res.status(403)" → permission denied responses
- Variable names containing: edit, update, vote, owner, creator, limit, max

IMPLEMENTATION PATTERNS - Features may be implemented indirectly:
- "unlimited X" may be a very high quota (e.g., limit=999999 or limit=Infinity for premium users), not a separate "unlimited" route
- "advanced analytics" may be additional dashboard stats/endpoints gated behind premium checks, not a separate analytics module
- "premium feature" may be enforced via middleware (requirePremiumAccess) or role checks (isPremium, hasPremium)
- Rejection/denial may use DELETE instead of a dedicated /reject endpoint
- Features may be assembled across multiple functions and files — trace the full flow

Respond in this exact JSON format:
{{
    "reasoning": {{
        "files_analyzed": ["<list files you examined>"],
        "relevant_code_found": "<describe the specific code you found related to this requirement, or 'none' if nothing found>",
        "how_it_works": "<explain what the code does - the logic, conditions, responses>",
        "matches_requirement": "<yes/no/partial - does the code behavior match what the requirement asks for?>"
    }},
    "status": "<CHOOSE ONE: implemented_as_expected | implemented_differently | not_implemented | not_specified>",
    "confidence": <number 0-100>,
    "short_explanation": "<Simple one-sentence summary anyone can understand>",
    "detailed_explanation": "<2-3 sentences explaining what you found in plain language>",
    "evidence": [
        {{
            "file_path": "<path to the file>",
            "line_start": <approximate starting line number>,
            "line_end": <approximate ending line number>,
            "code": "<the most relevant 3-10 lines of code>",
            "explanation": "<what this code does>"
        }}
    ]
}}

CRITICAL: Provide a concise evidence rationale in the "reasoning" section. Your status MUST be consistent with your reasoning.
- If reasoning.matches_requirement is "yes" → status must be "implemented_as_expected"
- If reasoning.matches_requirement is "partial" → status must be "implemented_differently"
- If reasoning.matches_requirement is "no" or relevant_code_found is "none" → status must be "not_implemented"

IMPORTANT for evidence:
- Include 1-3 evidence items for implemented features
- Extract the file path from the "=== FILE: xxx ===" markers in the code
- Use the line numbers from "... (line X" markers if available
- For each evidence item, show the KEY code that implements the requirement
- Empty array if not_implemented

STATUS MEANINGS:
- implemented_as_expected: You found code that implements this feature as described in the requirement
- implemented_differently: You found code for this feature but it works differently than specified
- not_implemented: You could NOT find any code that implements this feature
- not_specified: The requirement is too vague to evaluate

IMPORTANT:
- If status is "not_implemented", confidence should be HIGH (you're confident it's missing)
- If status is "implemented", provide evidence showing the actual code
- Code comments are NOT implementations - look for actual logic, database fields, API handlers"""

        logger.info("=== LLM ANALYSIS for: %s ===", feature)
        logger.info("Criteria: %s...", criteria[:200])
        logger.info("Code length: %s chars", len(code))

        response = self._create_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            prefer_json_object=True,
        )

        result = response.choices[0].message.content or ""
        logger.info("LLM response received: %s chars", len(result))
        return result

    def _repair_json_response(self, raw_response: str) -> str:
        repair_prompt = (
            "Convert the following model output into a valid JSON object. "
            "Return ONLY valid JSON with no markdown fences or extra text. "
            "Keep fields: reasoning, status, confidence, short_explanation, detailed_explanation, evidence.\n\n"
            f"MODEL_OUTPUT:\n{raw_response[:12000]}"
        )
        response = self._create_completion(
            messages=[{"role": "user", "content": repair_prompt}],
            prefer_json_object=True,
            max_tokens=1200,
        )
        return response.choices[0].message.content or ""

    def _create_completion(
        self,
        messages: list[dict[str, str]],
        prefer_json_object: bool = False,
        max_tokens: int = 2000,
        response_format: Optional[Dict[str, Any]] = None,
    ):
        token_parameter = getattr(self, "_completion_token_parameter", "max_tokens")
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            token_parameter: max_tokens,
        }
        if getattr(self, "_supports_temperature", True):
            kwargs["temperature"] = 0.0
        reasoning_effort = getattr(self, "reasoning_effort", None)
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort

        if response_format is not None:
            kwargs["response_format"] = response_format
        elif prefer_json_object:
            kwargs["response_format"] = {"type": "json_object"}

        # OpenAI-compatible providers do not expose one perfectly uniform
        # request surface. Retry only when the provider explicitly rejects an
        # optional or renamed parameter, and remember the capability for later
        # calls made by this evaluator instance.
        for _attempt in range(5):
            try:
                return self.client.chat.completions.create(**kwargs)
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                if status_code not in {400, 404, 422}:
                    raise

                if "max_tokens" in kwargs and _exception_mentions_parameter(exc, "max_tokens"):
                    token_budget = kwargs.pop("max_tokens")
                    kwargs["max_completion_tokens"] = token_budget
                    self._completion_token_parameter = "max_completion_tokens"
                    continue

                if "temperature" in kwargs and _exception_mentions_parameter(exc, "temperature"):
                    kwargs.pop("temperature")
                    self._supports_temperature = False
                    continue

                if "reasoning_effort" in kwargs and _exception_mentions_parameter(exc, "reasoning_effort"):
                    kwargs.pop("reasoning_effort")
                    self.reasoning_effort = None
                    continue

                # Some compatible backends (including certain Ollama setups)
                # reject response_format even though they implement the rest of
                # Chat Completions.
                if "response_format" in kwargs:
                    kwargs.pop("response_format")
                    continue

                raise

        raise RuntimeError("Provider compatibility retries were exhausted")

    def _parse_response(self, response: str) -> Dict[str, Any]:
        try:
            json_match = extract_json_payload(response)
            result = json.loads(json_match)
            self._validate_result_payload(result)
            return self._normalize_result_payload(result)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, ResponseParseError) as exc:
            logger.warning("Failed to parse model response (%s chars): %s", len(response or ""), exc)
            raise ResponseParseError(f"Failed to parse LLM response: {exc}") from exc

    def _validate_result_payload(self, result: Dict[str, Any]) -> None:
        if not isinstance(result, dict):
            raise ResponseParseError("Model response must be a JSON object")

        required_fields = {
            "status": str,
            "confidence": (int, float),
            "short_explanation": str,
            "detailed_explanation": str,
            "evidence": list,
        }
        for field_name, expected_type in required_fields.items():
            if field_name not in result:
                raise ResponseParseError(f"Model response is missing required field: {field_name}")
            if not isinstance(result[field_name], expected_type):
                raise ResponseParseError(f"Model response field has invalid type: {field_name}")

        model_statuses = {
            ImplementationStatus.IMPLEMENTED_AS_EXPECTED.value,
            ImplementationStatus.IMPLEMENTED_DIFFERENTLY.value,
            ImplementationStatus.NOT_IMPLEMENTED.value,
            ImplementationStatus.NOT_SPECIFIED.value,
        }
        if result["status"] not in model_statuses:
            raise ResponseParseError(f"Model response has invalid status: {result['status']}")

    def _normalize_result_payload(self, result: Dict[str, Any]) -> Dict[str, Any]:
        status = self._normalize_status(result.get("status"))

        confidence = result.get("confidence", 0)
        if isinstance(confidence, (int, float)):
            confidence_value = float(confidence)
            if confidence_value <= 1:
                normalized_confidence = min(1.0, max(0.0, confidence_value))
            else:
                normalized_confidence = min(100.0, max(0.0, confidence_value)) / 100.0
        else:
            normalized_confidence = 0.5

        evidence = result.get("evidence", [])
        evidence_items: list[dict[str, Any]] = []
        if isinstance(evidence, list):
            evidence_items = [item for item in evidence if isinstance(item, dict)]
            code_snippets = json.dumps(evidence_items)
        else:
            code_snippets = result.get("code_snippets", "")
            if code_snippets:
                code_snippets = json.dumps([{"file_path": "unknown", "code": code_snippets, "explanation": ""}])

        status, normalized_confidence = self._enforce_evidence_quality(
            status=status,
            confidence=normalized_confidence,
            evidence=evidence_items,
        )

        return {
            "status": status,
            "confidence": normalized_confidence,
            "short_explanation": result.get("short_explanation", ""),
            "detailed_explanation": result.get("detailed_explanation", ""),
            "code_snippets": code_snippets,
            "_parse_source": result.get("_parse_source", "json"),
        }

    def _enforce_evidence_quality(
        self,
        status: str,
        confidence: float,
        evidence: list[dict[str, Any]],
    ) -> tuple[str, float]:
        if status not in {
            ImplementationStatus.IMPLEMENTED_AS_EXPECTED.value,
            ImplementationStatus.IMPLEMENTED_DIFFERENTLY.value,
        }:
            return status, confidence

        has_quality_evidence = any(
            isinstance(item.get("file_path"), str)
            and item.get("file_path", "").strip()
            and isinstance(item.get("code"), str)
            and item.get("code", "").strip()
            for item in evidence
        )

        if has_quality_evidence:
            return status, confidence

        if status == ImplementationStatus.IMPLEMENTED_AS_EXPECTED.value:
            return ImplementationStatus.IMPLEMENTED_DIFFERENTLY.value, min(confidence, 0.65)
        return status, min(confidence, 0.65)

    def _normalize_status(self, raw_status: Any) -> str:
        valid_statuses = {status.value for status in ImplementationStatus}
        if isinstance(raw_status, str) and raw_status in valid_statuses:
            return raw_status
        if not isinstance(raw_status, str):
            return ImplementationStatus.NOT_IMPLEMENTED.value

        normalized = raw_status.strip().lower().replace(" ", "_")
        alias_map = {
            "implemented": ImplementationStatus.IMPLEMENTED_AS_EXPECTED.value,
            "implemented_as_expected": ImplementationStatus.IMPLEMENTED_AS_EXPECTED.value,
            "implemented_differently": ImplementationStatus.IMPLEMENTED_DIFFERENTLY.value,
            "different": ImplementationStatus.IMPLEMENTED_DIFFERENTLY.value,
            "partial": ImplementationStatus.IMPLEMENTED_DIFFERENTLY.value,
            "not_implemented": ImplementationStatus.NOT_IMPLEMENTED.value,
            "missing": ImplementationStatus.NOT_IMPLEMENTED.value,
            "not_found": ImplementationStatus.NOT_IMPLEMENTED.value,
            "not_specified": ImplementationStatus.NOT_SPECIFIED.value,
            "unspecified": ImplementationStatus.NOT_SPECIFIED.value,
        }
        return alias_map.get(normalized, ImplementationStatus.NOT_IMPLEMENTED.value)

    def _extract_structured_fields_from_text(self, response: str) -> Optional[Dict[str, Any]]:
        if not response.strip():
            return None

        def extract_quoted_field(field_name: str) -> Optional[str]:
            match = re.search(rf'"{field_name}"\s*:\s*"((?:[^"\\]|\\.)*)"', response, flags=re.IGNORECASE | re.DOTALL)
            if not match:
                return None
            return bytes(match.group(1), "utf-8").decode("unicode_escape").strip()

        status_patterns = [
            (r'"status"\s*:\s*"([^"]+)"', None),
            (r"\bimplemented_as_expected\b", ImplementationStatus.IMPLEMENTED_AS_EXPECTED.value),
            (r"\bimplemented_differently\b", ImplementationStatus.IMPLEMENTED_DIFFERENTLY.value),
            (r"\bnot_implemented\b", ImplementationStatus.NOT_IMPLEMENTED.value),
            (r"\bnot_specified\b", ImplementationStatus.NOT_SPECIFIED.value),
            (r"\bstatus\s*[:=]\s*(implemented|different|partial|not implemented|missing|not specified)\b", None),
        ]

        lowered = response.lower()
        status = None
        for pattern, mapped in status_patterns:
            match = re.search(pattern, lowered)
            if not match:
                continue
            if mapped:
                status = mapped
            else:
                status = self._normalize_status(match.group(1))
            break

        confidence = 50.0
        json_conf_match = re.search(r'"confidence"\s*:\s*(-?\d+(?:\.\d+)?)', lowered)
        if json_conf_match:
            confidence = float(json_conf_match.group(1))
        else:
            decimal_match = re.search(r"\bconfidence\s*[:=]?\s*(0(?:\.\d+)?|1(?:\.0+)?)\b", lowered)
            if decimal_match:
                confidence = float(decimal_match.group(1))
            else:
                conf_match = re.search(r"\bconfidence\s*[:=]?\s*(\d{1,3})(?:\s*%)?\b", lowered)
                if conf_match:
                    confidence = float(conf_match.group(1))

        short_expl = extract_quoted_field("short_explanation") or ""
        detailed_expl = extract_quoted_field("detailed_explanation") or ""

        if not short_expl:
            short_match = re.search(r"\bshort_explanation\s*[:=]\s*(.+)", response, flags=re.IGNORECASE)
            if short_match:
                short_expl = short_match.group(1).strip()

        if not detailed_expl:
            detailed_match = re.search(r"\bdetailed_explanation\s*[:=]\s*(.+)", response, flags=re.IGNORECASE)
            if detailed_match:
                detailed_expl = detailed_match.group(1).strip()

        if not short_expl or short_expl == "{":
            reasoning_match = re.search(r'"relevant_code_found"\s*:\s*"((?:[^"\\]|\\.)*)"', response, flags=re.IGNORECASE | re.DOTALL)
            if reasoning_match:
                short_expl = bytes(reasoning_match.group(1), "utf-8").decode("unicode_escape").strip()[:180]

        if not short_expl:
            first_line = next((line.strip() for line in response.splitlines() if line.strip()), "")
            short_expl = first_line[:180] if first_line and first_line != "{" else "LLM returned malformed JSON; extracted partial structured result."
        if not detailed_expl:
            detailed_expl = response.strip()[:1000]

        if status is None and "not implemented" in lowered:
            status = ImplementationStatus.NOT_IMPLEMENTED.value
        if status is None and any(token in lowered for token in ("implemented", "exists in code", "found in")):
            status = ImplementationStatus.IMPLEMENTED_AS_EXPECTED.value
        if status is None:
            return None

        return {
            "status": status,
            "confidence": confidence,
            "short_explanation": short_expl,
            "detailed_explanation": detailed_expl,
            "evidence": [],
            "_parse_source": "text_extraction",
        }

    def _fallback_evaluation(
        self,
        criteria: str,
        code: str,
        error: str,
    ) -> Dict[str, Any]:
        criteria_lower = criteria.lower()
        code_lower = code.lower()

        keywords = self._extract_keywords(criteria_lower)
        matches = sum(1 for keyword in keywords if keyword in code_lower)
        match_ratio = matches / len(keywords) if keywords else 0

        if match_ratio > 0.7:
            status = ImplementationStatus.IMPLEMENTED_AS_EXPECTED.value
            confidence = 0.6
            explanation = "High keyword match found in code (heuristic analysis)"
        elif match_ratio > 0.3:
            status = ImplementationStatus.IMPLEMENTED_DIFFERENTLY.value
            confidence = 0.4
            explanation = "Partial keyword match found in code (heuristic analysis)"
        else:
            status = ImplementationStatus.NOT_IMPLEMENTED.value
            confidence = 0.3
            explanation = "Low keyword match in code (heuristic analysis)"

        return {
            "status": ImplementationStatus.INCONCLUSIVE.value,
            "confidence": 0.0,
            "short_explanation": "Heuristic fallback is not authoritative",
            "detailed_explanation": (
                f"A legacy heuristic suggested '{status}' at confidence {confidence:.2f}, but Matcha does not "
                f"treat heuristics as gate-safe evidence. Cause: {error}. Keywords: {', '.join(keywords[:5])}"
            ),
            "code_snippets": "",
            "analysis_mode": "heuristic_non_authoritative",
            "error": error,
        }

    def _analysis_failed_evaluation(self, error: str) -> Dict[str, Any]:
        return {
            "status": ImplementationStatus.ANALYSIS_FAILED.value,
            "confidence": 0.0,
            "short_explanation": "The criterion could not be evaluated",
            "detailed_explanation": error,
            "code_snippets": "",
            "analysis_mode": "analysis_failed",
            "error": error,
        }

    def _extract_keywords(self, text: str) -> list[str]:
        words = re.findall(r"\b[a-z]{4,}\b", text)
        stopwords = {"should", "must", "when", "that", "this", "with", "from", "have", "will", "been", "were", "being"}
        return [word for word in words if word not in stopwords][:10]


def normalize_base_url(base_url: Optional[str]) -> Optional[str]:
    if not base_url:
        return None

    cleaned = base_url.rstrip("/")
    if "11434" in cleaned and not cleaned.endswith("/v1"):
        return f"{cleaned}/v1"
    return cleaned


def default_api_key_for_base_url(base_url: Optional[str]) -> Optional[str]:
    if not base_url:
        return None
    if "11434" in base_url:
        return "ollama"
    return None


def extract_json_payload(response: str) -> str:
    if not response or not response.strip():
        raise ValueError("Model returned an empty response")

    cleaned = strip_non_json_wrappers(response)

    try:
        json.loads(cleaned)
        return cleaned
    except json.JSONDecodeError:
        pass

    fence_match = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", cleaned, re.DOTALL | re.IGNORECASE)
    if fence_match:
        candidate = fence_match.group(1).strip()
        json.loads(candidate)
        return candidate

    candidate = extract_balanced_json(cleaned)
    if candidate is None:
        raise ValueError("No JSON object found in model response")

    json.loads(candidate)
    return candidate


def strip_non_json_wrappers(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    return cleaned


def extract_balanced_json(text: str) -> Optional[str]:
    start = None
    depth = 0
    opening = None
    in_string = False
    escape = False

    for index, char in enumerate(text):
        if start is None:
            if char in "{[":
                start = index
                opening = char
                depth = 1
                in_string = False
                escape = False
            continue

        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue

        if char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                candidate = text[start:index + 1].strip()
                expected_close = "}" if opening == "{" else "]"
                if candidate.endswith(expected_close):
                    return candidate

    return None
