from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence

from .evaluator import OpenAICompatibleEvaluator, extract_json_payload


ONBOARDING_SCHEMA_VERSION = "1.0"
DEFAULT_MAX_FEATURES = 12
DEFAULT_MAX_CONTEXT_CHARS = 90_000
MAX_FILE_CHARS = 3_500

SOURCE_EXTENSIONS = {
    ".c",
    ".cpp",
    ".cs",
    ".go",
    ".h",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".md",
    ".php",
    ".plist",
    ".py",
    ".rb",
    ".rs",
    ".svelte",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}

SPECIAL_SOURCE_NAMES = {
    "dockerfile",
    "gemfile",
    "makefile",
    "package.swift",
    "podfile",
    "project.pbxproj",
}

SKIP_DIRS = {
    ".build",
    ".cache",
    ".deriveddata",
    ".git",
    ".next",
    ".playwright",
    ".swiftpm",
    ".venv",
    "build",
    "coverage",
    "deriveddata",
    "dist",
    "node_modules",
    "pods",
    "vendor",
    "venv",
    "__pycache__",
}

SENSITIVE_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
    "secrets.json",
}

SENSITIVE_SUFFIXES = {".cer", ".der", ".key", ".keystore", ".p12", ".pem"}
LOW_SIGNAL_NAMES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "yarn.lock",
}

ProgressCallback = Callable[[str], None]

EVIDENCE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["path", "rationale"],
    "additionalProperties": False,
}

CRITERION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence": {"type": "array", "items": EVIDENCE_JSON_SCHEMA, "minItems": 1},
    },
    "required": ["description", "confidence", "evidence"],
    "additionalProperties": False,
}

FEATURE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "priority": {"type": "string", "enum": ["Critical", "High", "Medium", "Low", "Unknown"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence": {"type": "array", "items": EVIDENCE_JSON_SCHEMA, "minItems": 1},
        "acceptance_criteria": {"type": "array", "items": CRITERION_JSON_SCHEMA, "minItems": 1},
    },
    "required": ["name", "description", "priority", "confidence", "evidence", "acceptance_criteria"],
    "additionalProperties": False,
}

ONBOARDING_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "project_summary": {"type": "string"},
        "features": {"type": "array", "items": FEATURE_JSON_SCHEMA, "minItems": 1, "maxItems": 50},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "feature_name": {"type": "string"},
                    "question": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["feature_name", "question", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["project_summary", "features", "questions"],
    "additionalProperties": False,
}


class SpecsBootstrapError(RuntimeError):
    """Raised when a repository cannot produce a trustworthy specs draft."""


class SpecDraftGenerator(Protocol):
    model: str

    def generate_specs(
        self,
        repository_context: str,
        max_features: int,
        language: str,
        debug: Optional[Dict[str, Any]] = None,
    ) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class DraftEvidence:
    path: str
    rationale: str


@dataclass(frozen=True)
class DraftCriterion:
    description: str
    confidence: float
    evidence: List[DraftEvidence] = field(default_factory=list)


@dataclass(frozen=True)
class DraftFeature:
    feature_id: str
    name: str
    description: str
    priority: str
    confidence: float
    evidence: List[DraftEvidence] = field(default_factory=list)
    acceptance_criteria: List[DraftCriterion] = field(default_factory=list)


@dataclass(frozen=True)
class DraftQuestion:
    feature_id: Optional[str]
    question: str
    reason: str


@dataclass(frozen=True)
class SpecsDraft:
    source: str
    project_summary: str
    features: List[DraftFeature]
    questions: List[DraftQuestion]
    selected_files: List[str]
    model: str
    provider: str
    generated_at: str
    schema_version: str = ONBOARDING_SCHEMA_VERSION


class OpenAICompatibleSpecGenerator:
    def __init__(self, client: OpenAICompatibleEvaluator):
        self.client = client
        self.model = client.model

    @classmethod
    def from_env(
        cls,
        provider: str = "openai",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 60.0,
        reasoning_effort: Optional[str] = None,
    ) -> "OpenAICompatibleSpecGenerator":
        return cls(
            OpenAICompatibleEvaluator.from_env(
                provider=provider,
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout=timeout,
                reasoning_effort=reasoning_effort,
            )
        )

    def generate_specs(
        self,
        repository_context: str,
        max_features: int,
        language: str,
        debug: Optional[Dict[str, Any]] = None,
    ) -> Mapping[str, Any]:
        system_prompt = """You discover product behavior from repository evidence.

Create a reviewable specification draft, not a statement of product intent. Infer only behavior supported by supplied source, tests, or documentation. Do not treat comments, TODOs, roadmap text, dependencies, build plumbing, or visual styling as shipped product behavior unless executable code or tests support it.

Each feature and every acceptance criterion must cite at least one exact path from SELECTED FILE CONTENT. Use observable behavior and product/business rules. Prefer a small set of coherent features over an exhaustive file inventory. Surface ambiguity as questions instead of inventing an answer.

Return only one valid JSON object. Do not use markdown fences."""

        user_prompt = f"""Generate at most {max_features} feature drafts in {language}.

Required JSON shape:
{{
  "project_summary": "short evidence-based summary",
  "features": [
    {{
      "name": "short feature name",
      "description": "what users or the system can observably do",
      "priority": "Critical | High | Medium | Low | Unknown",
      "confidence": 0.0,
      "evidence": [{{"path": "exact/relative/path", "rationale": "what this file proves"}}],
      "acceptance_criteria": [
        {{
          "description": "specific observable behavior",
          "confidence": 0.0,
          "evidence": [{{"path": "exact/relative/path", "rationale": "what this file proves"}}]
        }}
      ]
    }}
  ],
  "questions": [
    {{"feature_name": "matching feature name or empty", "question": "product question", "reason": "why code cannot answer it"}}
  ]
}}

Confidence must be between 0 and 1. Do not assign product priority from code importance alone: use Unknown when product priority is not evidenced. Do not emit future features as implemented behavior. A feature without priority, confidence, evidence, and acceptance_criteria is invalid and will be rejected.

REPOSITORY CONTEXT:
{repository_context}"""

        if debug is not None:
            debug["model"] = self.model
            debug["repository_context_chars"] = len(repository_context)
            debug["max_features"] = max_features
            debug["language"] = language

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        raw = self.client.create_json_completion(
            messages=messages,
            max_tokens=min(12_000, max(3_000, max_features * 800)),
            json_schema=ONBOARDING_JSON_SCHEMA,
        )
        if debug is not None:
            debug["primary_response"] = raw[:30_000]
            debug["primary_completion"] = self.client.last_json_completion_metadata

        try:
            payload = json.loads(extract_json_payload(raw))
        except (json.JSONDecodeError, ValueError) as exc:
            raise SpecsBootstrapError(f"Model returned invalid onboarding JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise SpecsBootstrapError("Model onboarding response must be a JSON object")

        shape_issue = draft_payload_shape_issue(payload)
        if shape_issue:
            repair_prompt = f"""Your previous JSON is invalid: {shape_issue}

Return a corrected complete object using the exact required JSON shape from the original request. Every feature and acceptance criterion must include confidence and evidence objects with exact paths from SELECTED FILE CONTENT. Use priority Unknown when it cannot be inferred. Preserve useful candidate features, but do not preserve unsupported claims. Return only JSON."""
            repaired_raw = self.client.create_json_completion(
                messages=messages
                + [
                    {"role": "assistant", "content": raw[:20_000]},
                    {"role": "user", "content": repair_prompt},
                ],
                max_tokens=min(12_000, max(3_000, max_features * 800)),
                json_schema=ONBOARDING_JSON_SCHEMA,
            )
            if debug is not None:
                debug["primary_shape_error"] = shape_issue
                debug["repair_response"] = repaired_raw[:30_000]
                debug["repair_completion"] = self.client.last_json_completion_metadata
            try:
                payload = json.loads(extract_json_payload(repaired_raw))
            except (json.JSONDecodeError, ValueError) as exc:
                raise SpecsBootstrapError(f"Model returned invalid onboarding JSON after repair: {exc}") from exc
            if not isinstance(payload, dict):
                raise SpecsBootstrapError("Repaired onboarding response must be a JSON object")
            repaired_issue = draft_payload_shape_issue(payload)
            if repaired_issue:
                raise SpecsBootstrapError(f"Model onboarding response is incomplete after repair: {repaired_issue}")
        return payload


class RepositoryBootstrapper:
    def __init__(self, generator: SpecDraftGenerator):
        self.generator = generator

    def bootstrap_path(
        self,
        repo_path: str | Path,
        *,
        provider: str,
        max_features: int = DEFAULT_MAX_FEATURES,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
        language: str = "English",
        progress_callback: Optional[ProgressCallback] = None,
        debug_output_path: str | Path | None = None,
    ) -> SpecsDraft:
        repo = Path(repo_path).expanduser().resolve()
        if not repo.is_dir():
            raise SpecsBootstrapError(f"Repository path does not exist or is not a directory: {repo}")
        if max_features < 1 or max_features > 50:
            raise SpecsBootstrapError("max_features must be between 1 and 50")
        if max_context_chars < 10_000:
            raise SpecsBootstrapError("max_context_chars must be at least 10000")

        _notify(progress_callback, "indexing")
        context, selected_files = build_repository_context(repo, max_context_chars=max_context_chars)
        if not selected_files:
            raise SpecsBootstrapError("No supported, non-sensitive source files were found")

        _notify(progress_callback, "generating")
        debug: Dict[str, Any] = {"selected_files": selected_files}
        try:
            payload = self.generator.generate_specs(
                repository_context=context,
                max_features=max_features,
                language=language,
                debug=debug,
            )
            draft = normalize_specs_draft(
                payload,
                repo_path=repo,
                selected_files=selected_files,
                provider=provider,
                model=self.generator.model,
                max_features=max_features,
            )
        except Exception as exc:
            debug["error"] = str(exc)
            raise
        finally:
            if debug_output_path:
                write_json_atomic(debug, Path(debug_output_path).expanduser().resolve(), overwrite=True)
        _notify(progress_callback, "rendering")
        return draft


def build_repository_context(repo_path: Path, *, max_context_chars: int) -> tuple[str, List[str]]:
    candidates: List[tuple[int, str, Path]] = []
    for root, dir_names, file_names in os.walk(repo_path):
        dir_names[:] = sorted(name for name in dir_names if name.lower() not in SKIP_DIRS)
        root_path = Path(root)
        for file_name in sorted(file_names):
            path = root_path / file_name
            relative = path.relative_to(repo_path).as_posix()
            if not is_context_candidate(path, relative):
                continue
            candidates.append((_context_priority(relative), relative, path))

    candidates.sort(key=lambda item: (item[0], item[1].lower()))
    tree = "\n".join(relative for _, relative, _ in candidates[:800])
    sections = [f"REPOSITORY FILE INDEX:\n{tree}", "SELECTED FILE CONTENT:"]
    selected_files: List[str] = []
    current_chars = sum(len(section) for section in sections)

    for _, relative, path in candidates:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not content.strip():
            continue
        content = content[:MAX_FILE_CHARS]
        section = f"\n=== FILE: {relative} ===\n{content}\n"
        if current_chars + len(section) > max_context_chars:
            continue
        sections.append(section)
        selected_files.append(relative)
        current_chars += len(section)

    return "\n".join(sections), selected_files


def is_context_candidate(path: Path, relative_path: str) -> bool:
    lower_name = path.name.lower()
    lower_relative = relative_path.lower()
    if lower_name in LOW_SIGNAL_NAMES or lower_name in SENSITIVE_NAMES:
        return False
    if path.suffix.lower() in SENSITIVE_SUFFIXES:
        return False
    if lower_name.startswith(".env.") or "secret" in lower_name or "credential" in lower_name:
        return False
    if ".xcassets/" in lower_relative or "xcuserdata/" in lower_relative:
        return False
    if lower_name.startswith("specs") and path.suffix.lower() == ".md":
        return False
    if any(part.lower() in SKIP_DIRS for part in Path(lower_relative).parts[:-1]):
        return False
    return path.suffix.lower() in SOURCE_EXTENSIONS or lower_name in SPECIAL_SOURCE_NAMES


def _context_priority(relative_path: str) -> int:
    lower = relative_path.lower()
    name = Path(lower).name
    if name.startswith("readme") or lower.startswith("docs/"):
        return 0
    if path_looks_like_product_source(lower):
        return 1
    if "test" in lower or "spec" in lower:
        return 2
    if name in SPECIAL_SOURCE_NAMES or name in {"package.json", "pyproject.toml", "cargo.toml"}:
        return 4
    return 3


def path_looks_like_product_source(relative_path: str) -> bool:
    extension = Path(relative_path).suffix.lower()
    if extension not in {
        ".c", ".cpp", ".cs", ".go", ".h", ".java", ".js", ".jsx", ".kt",
        ".php", ".py", ".rb", ".rs", ".svelte", ".swift", ".ts", ".tsx", ".vue",
    }:
        return False
    return "test" not in relative_path and "spec" not in relative_path


def normalize_specs_draft(
    payload: Mapping[str, Any],
    *,
    repo_path: Path,
    selected_files: Sequence[str],
    provider: str,
    model: str,
    max_features: int,
) -> SpecsDraft:
    if not isinstance(payload, Mapping):
        raise SpecsBootstrapError("Onboarding payload must be an object")
    project_summary = _required_text(payload.get("project_summary"), "project_summary", min_length=10)
    raw_features = payload.get("features")
    if not isinstance(raw_features, list) or not raw_features:
        raise SpecsBootstrapError("Onboarding payload must contain at least one feature")
    if len(raw_features) > max_features:
        raise SpecsBootstrapError(f"Model returned more than the requested {max_features} features")

    selected = set(selected_files)
    normalized_features: List[DraftFeature] = []
    seen_names = set()
    for index, raw_feature in enumerate(raw_features, start=1):
        if not isinstance(raw_feature, Mapping):
            raise SpecsBootstrapError(f"features[{index - 1}] must be an object")
        name = _required_text(raw_feature.get("name"), f"features[{index - 1}].name", min_length=3)
        name_key = re.sub(r"\W+", " ", name).strip().lower()
        if name_key in seen_names:
            raise SpecsBootstrapError(f"Duplicate feature name: {name}")
        seen_names.add(name_key)
        description = _required_text(
            raw_feature.get("description"),
            f"features[{index - 1}].description",
            min_length=10,
        )
        priority = _normalize_priority(raw_feature.get("priority"), index)
        evidence = _normalize_evidence(raw_feature.get("evidence"), selected, f"features[{index - 1}].evidence")
        raw_criteria = raw_feature.get("acceptance_criteria")
        if not isinstance(raw_criteria, list) or not raw_criteria:
            raise SpecsBootstrapError(f"features[{index - 1}] must contain acceptance_criteria")
        criteria: List[DraftCriterion] = []
        for criterion_index, raw_criterion in enumerate(raw_criteria):
            if not isinstance(raw_criterion, Mapping):
                raise SpecsBootstrapError(
                    f"features[{index - 1}].acceptance_criteria[{criterion_index}] must be an object"
                )
            criteria.append(
                DraftCriterion(
                    description=_required_text(
                        raw_criterion.get("description"),
                        f"features[{index - 1}].acceptance_criteria[{criterion_index}].description",
                        min_length=10,
                    ),
                    confidence=_normalize_confidence(
                        raw_criterion.get("confidence"),
                        f"features[{index - 1}].acceptance_criteria[{criterion_index}].confidence",
                    ),
                    evidence=_normalize_evidence(
                        raw_criterion.get("evidence"),
                        selected,
                        f"features[{index - 1}].acceptance_criteria[{criterion_index}].evidence",
                    ),
                )
            )
        normalized_features.append(
            DraftFeature(
                feature_id=f"FEAT-{index:03d}",
                name=name,
                description=description,
                priority=priority,
                confidence=_normalize_confidence(raw_feature.get("confidence"), f"features[{index - 1}].confidence"),
                evidence=evidence,
                acceptance_criteria=criteria,
            )
        )

    feature_ids_by_name = {feature.name.strip().lower(): feature.feature_id for feature in normalized_features}
    questions = _normalize_questions(payload.get("questions", []), feature_ids_by_name)
    return SpecsDraft(
        source=str(repo_path),
        project_summary=project_summary,
        features=normalized_features,
        questions=questions,
        selected_files=list(selected_files),
        model=model,
        provider=provider,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def _required_text(value: Any, field_name: str, *, min_length: int) -> str:
    if not isinstance(value, str) or len(value.strip()) < min_length:
        raise SpecsBootstrapError(f"{field_name} must be text with at least {min_length} characters")
    return " ".join(value.strip().split())


def _normalize_priority(value: Any, feature_index: int) -> str:
    if not isinstance(value, str):
        raise SpecsBootstrapError(f"features[{feature_index - 1}].priority must be text")
    priorities = {
        "critical": "Critical",
        "high": "High",
        "medium": "Medium",
        "low": "Low",
        "unknown": "Unknown",
    }
    normalized = priorities.get(value.strip().lower())
    if not normalized:
        raise SpecsBootstrapError(f"features[{feature_index - 1}].priority is invalid: {value}")
    return normalized


def _normalize_confidence(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpecsBootstrapError(f"{field_name} must be a number between 0 and 1")
    confidence = float(value)
    if confidence > 1 and confidence <= 100:
        confidence /= 100
    if not 0 <= confidence <= 1:
        raise SpecsBootstrapError(f"{field_name} must be between 0 and 1")
    return round(confidence, 4)


def _normalize_evidence(value: Any, selected_files: set[str], field_name: str) -> List[DraftEvidence]:
    if not isinstance(value, list) or not value:
        raise SpecsBootstrapError(f"{field_name} must contain at least one evidence item")
    evidence: List[DraftEvidence] = []
    seen = set()
    for index, raw_item in enumerate(value):
        if not isinstance(raw_item, Mapping):
            raise SpecsBootstrapError(f"{field_name}[{index}] must be an object")
        path = _required_text(raw_item.get("path"), f"{field_name}[{index}].path", min_length=1)
        normalized_path = path.replace("\\", "/")
        while normalized_path.startswith("./"):
            normalized_path = normalized_path[2:]
        if normalized_path.startswith("/") or ".." in Path(normalized_path).parts:
            raise SpecsBootstrapError(f"{field_name}[{index}] references an invalid path: {path}")
        if normalized_path not in selected_files:
            raise SpecsBootstrapError(f"{field_name}[{index}] references unavailable evidence: {path}")
        rationale = _required_text(raw_item.get("rationale"), f"{field_name}[{index}].rationale", min_length=5)
        if normalized_path not in seen:
            evidence.append(DraftEvidence(path=normalized_path, rationale=rationale))
            seen.add(normalized_path)
    return evidence


def _normalize_questions(value: Any, feature_ids_by_name: Mapping[str, str]) -> List[DraftQuestion]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SpecsBootstrapError("questions must be a list")
    questions: List[DraftQuestion] = []
    for index, raw_question in enumerate(value):
        if not isinstance(raw_question, Mapping):
            raise SpecsBootstrapError(f"questions[{index}] must be an object")
        feature_name = raw_question.get("feature_name", "")
        if not isinstance(feature_name, str):
            raise SpecsBootstrapError(f"questions[{index}].feature_name must be text")
        feature_id = feature_ids_by_name.get(feature_name.strip().lower()) if feature_name.strip() else None
        questions.append(
            DraftQuestion(
                feature_id=feature_id,
                question=_required_text(raw_question.get("question"), f"questions[{index}].question", min_length=5),
                reason=_required_text(raw_question.get("reason"), f"questions[{index}].reason", min_length=5),
            )
        )
    return questions


def render_specs_draft(draft: SpecsDraft) -> str:
    lines = [
        "# Matcha Specs Draft",
        "",
        "> Generated from repository evidence. This is observed behavior, not confirmed product intent.",
        "> Review every feature and change `Status: Draft` before using this file as a CI gate.",
        "",
        f"**Source**: `{draft.source}`",
        f"**Generated At**: {draft.generated_at}",
        f"**Provider**: {draft.provider}",
        f"**Model**: {draft.model}",
        f"**Draft Schema**: {draft.schema_version}",
        "",
        "## Project Summary",
        "",
        draft.project_summary,
        "",
    ]

    for feature in draft.features:
        component_paths = _unique_paths(
            [item.path for item in feature.evidence]
            + [item.path for criterion in feature.acceptance_criteria for item in criterion.evidence]
        )
        lines.extend(
            [
                f"## {feature.feature_id} {feature.name}",
                f"**Priority**: {feature.priority}",
                "**Status**: Draft",
                f"**Confidence**: {feature.confidence:.2f}",
                "**Related Components**: " + ", ".join(f"`{path}`" for path in component_paths[:10]),
                "",
                feature.description,
                "",
                "Evidence:",
            ]
        )
        for evidence in feature.evidence:
            lines.append(f"- `{evidence.path}` — {evidence.rationale}")
        lines.extend(["", "Acceptance Criteria:"])
        for criterion in feature.acceptance_criteria:
            paths = _unique_paths([item.path for item in criterion.evidence])
            evidence_suffix = ", ".join(f"`{path}`" for path in paths)
            lines.append(
                f"- {criterion.description} (Draft confidence: {criterion.confidence:.2f}; evidence: {evidence_suffix})"
            )
        lines.append("")

    lines.extend(["## Product Review Questions", ""])
    if draft.questions:
        for question in draft.questions:
            prefix = f"[{question.feature_id}] " if question.feature_id else ""
            lines.append(f"- {prefix}{question.question} — {question.reason}")
    else:
        lines.append("- No explicit questions were produced; human review is still required.")
    lines.append("")
    return "\n".join(lines)


def draft_to_dict(draft: SpecsDraft) -> Dict[str, Any]:
    return {
        "schema_version": draft.schema_version,
        "source": draft.source,
        "project_summary": draft.project_summary,
        "provider": draft.provider,
        "model": draft.model,
        "generated_at": draft.generated_at,
        "selected_files": draft.selected_files,
        "features": [
            {
                "id": feature.feature_id,
                "name": feature.name,
                "description": feature.description,
                "priority": feature.priority,
                "status": "Draft",
                "confidence": feature.confidence,
                "evidence": [vars(item) for item in feature.evidence],
                "acceptance_criteria": [
                    {
                        "description": criterion.description,
                        "confidence": criterion.confidence,
                        "evidence": [vars(item) for item in criterion.evidence],
                    }
                    for criterion in feature.acceptance_criteria
                ],
            }
            for feature in draft.features
        ],
        "questions": [vars(question) for question in draft.questions],
    }


def write_specs_draft(draft: SpecsDraft, output_path: Path, *, overwrite: bool = False) -> None:
    write_text_atomic(render_specs_draft(draft), output_path, overwrite=overwrite)


def write_text_atomic(content: str, output_path: Path, *, overwrite: bool = False) -> None:
    output_path = output_path.expanduser().resolve()
    if output_path.exists() and not overwrite:
        raise SpecsBootstrapError(f"Refusing to overwrite existing file without --force: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output_path.name}.", dir=output_path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary_name, output_path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def write_json_atomic(payload: Mapping[str, Any], output_path: Path, *, overwrite: bool = False) -> None:
    write_text_atomic(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", output_path, overwrite=overwrite)


def _unique_paths(paths: Sequence[str]) -> List[str]:
    return list(dict.fromkeys(paths))


def draft_payload_shape_issue(payload: Mapping[str, Any]) -> Optional[str]:
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        return "features must be a non-empty list"
    for feature_index, feature in enumerate(features):
        if not isinstance(feature, Mapping):
            return f"features[{feature_index}] must be an object"
        for field_name in ("name", "description", "priority", "confidence", "evidence", "acceptance_criteria"):
            if field_name not in feature:
                return f"features[{feature_index}] is missing {field_name}"
        criteria = feature.get("acceptance_criteria")
        if not isinstance(criteria, list) or not criteria:
            return f"features[{feature_index}].acceptance_criteria must be a non-empty list"
        for criterion_index, criterion in enumerate(criteria):
            if not isinstance(criterion, Mapping):
                return f"features[{feature_index}].acceptance_criteria[{criterion_index}] must be an object"
            for field_name in ("description", "confidence", "evidence"):
                if field_name not in criterion:
                    return (
                        f"features[{feature_index}].acceptance_criteria[{criterion_index}] "
                        f"is missing {field_name}"
                    )
    return None


def _notify(progress_callback: Optional[ProgressCallback], status: str) -> None:
    if progress_callback:
        progress_callback(status)
