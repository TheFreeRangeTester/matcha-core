from __future__ import annotations

import logging
import json
import os
import re
import shutil
from typing import Any, Callable, Dict, List, Optional, Protocol, Set
from uuid import uuid4

from .models import AnalysisReport, AnalysisStatus, CriteriaResult, FeatureResult, ImplementationStatus
from .specs_parser import SpecsParser

logger = logging.getLogger(__name__)

CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".java",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".cs",
    ".cpp",
    ".c",
    ".h",
    ".swift",
    ".kt",
    ".vue",
    ".svelte",
}

SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    "venv",
    ".venv",
    "dist",
    "build",
    ".next",
    "coverage",
    ".cache",
    "vendor",
}

SKIP_FEATURE_STATUSES = {
    "todo",
    "planned",
    "not started",
    "backlog",
    "not_started",
    "future",
    "deferred",
}

ProgressCallback = Callable[[str], None]


class CriteriaEvaluator(Protocol):
    def evaluate_criteria(
        self,
        criteria_description: str,
        code_context: str,
        feature_name: str,
        debug: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ...


class AnalysisError(RuntimeError):
    pass


class RepositoryAnalyzer:
    def __init__(self, evaluator: CriteriaEvaluator, parser: Optional[SpecsParser] = None):
        self.evaluator = evaluator
        self.parser = parser or SpecsParser()

    def analyze_path(
        self,
        repo_path: str,
        specs_path: Optional[str] = None,
        progress_callback: Optional[ProgressCallback] = None,
        source: Optional[str] = None,
        commit_hash: Optional[str] = None,
        debug_output_path: Optional[str] = None,
    ) -> AnalysisReport:
        resolved_specs_path = specs_path or find_specs_file(repo_path)
        if not resolved_specs_path:
            raise AnalysisError("SPECS.md file not found in repository")

        _notify(progress_callback, AnalysisStatus.PARSING.value)
        parsed_features = self.parser.parse(resolved_specs_path)
        if not parsed_features:
            raise AnalysisError("No features found in SPECS.md")

        file_index = build_file_index(repo_path)
        _notify(progress_callback, AnalysisStatus.ANALYZING.value)

        feature_results: List[FeatureResult] = []
        total_criteria = 0
        implemented = 0
        different = 0
        not_implemented = 0
        not_specified = 0
        all_confidences: List[float] = []
        debug_file = None
        if debug_output_path:
            os.makedirs(os.path.dirname(debug_output_path) or ".", exist_ok=True)
            debug_file = open(debug_output_path, "w", encoding="utf-8")

        try:
            for parsed_feature in parsed_features:
                feature_status = (parsed_feature.get("status") or "Unknown").lower()
                is_planned = feature_status in SKIP_FEATURE_STATUSES

                criteria_results: List[CriteriaResult] = []
                feature_confidences: List[float] = []
                feature_statuses: List[str] = []

                for acceptance_criteria in parsed_feature.get("acceptance_criteria", []):
                    total_criteria += 1
                    criteria_text = acceptance_criteria.get("description", "")
                    debug_record: Dict[str, Any] = {
                        "feature_id": parsed_feature.get("id", ""),
                        "feature_name": parsed_feature.get("name", ""),
                        "criteria_id": acceptance_criteria.get("id"),
                        "criteria_description": criteria_text,
                        "feature_status": parsed_feature.get("status", ""),
                    }

                    if is_planned:
                        evaluation = skipped_evaluation(parsed_feature.get("status", "Planned"))
                        relevant_files: List[Dict[str, Any]] = []
                        debug_record["analysis_mode"] = "skipped_planned"
                    else:
                        keywords = extract_keywords_from_criteria(criteria_text, parsed_feature.get("name", ""))
                        specs_referenced_files = acceptance_criteria.get("referenced_files", [])
                        relevant_files = find_relevant_files(file_index, keywords, specs_referenced_files)
                        code_context = build_smart_context(relevant_files, criteria_text, keywords)
                        debug_record["analysis_mode"] = "evaluator"
                        debug_record["keywords"] = sorted(list(keywords))[:20]
                        debug_record["relevant_files"] = [file_info["path"] for file_info in relevant_files[:10]]
                        debug_record["code_context_chars"] = len(code_context)

                        evaluator_debug: Dict[str, Any] = {}
                        raw_evaluation = self._evaluate_with_optional_debug(
                            criteria_description=criteria_text,
                            code_context=code_context,
                            feature_name=parsed_feature.get("name", ""),
                            debug=evaluator_debug,
                        )
                        if evaluator_debug:
                            debug_record["evaluator"] = evaluator_debug
                        evaluation = normalize_evaluation(raw_evaluation)

                    criteria_result = CriteriaResult(
                        criteria_id=acceptance_criteria.get("id"),
                        description=criteria_text,
                        referenced_files=[file_info["path"] for file_info in relevant_files[:5]],
                        implementation_status=evaluation["status"],
                        confidence=evaluation["confidence"],
                        short_explanation=evaluation["short_explanation"],
                        detailed_explanation=evaluation["detailed_explanation"],
                        code_snippets=evaluation["code_snippets"],
                    )
                    criteria_results.append(criteria_result)

                    status_value = criteria_result.implementation_status
                    feature_statuses.append(status_value)
                    feature_confidences.append(criteria_result.confidence)
                    all_confidences.append(criteria_result.confidence)

                    if status_value == ImplementationStatus.IMPLEMENTED_AS_EXPECTED.value:
                        implemented += 1
                    elif status_value == ImplementationStatus.IMPLEMENTED_DIFFERENTLY.value:
                        different += 1
                    elif status_value == ImplementationStatus.NOT_IMPLEMENTED.value:
                        not_implemented += 1
                    else:
                        not_specified += 1

                    if debug_file:
                        debug_record["result"] = {
                            "status": criteria_result.implementation_status,
                            "confidence": criteria_result.confidence,
                            "short_explanation": criteria_result.short_explanation,
                            "detailed_explanation": criteria_result.detailed_explanation,
                        }
                        debug_file.write(json.dumps(debug_record, ensure_ascii=False) + "\n")

                feature_results.append(
                    FeatureResult(
                        feature_id=parsed_feature.get("id", ""),
                        name=parsed_feature.get("name", ""),
                        description=parsed_feature.get("description", ""),
                        priority=parsed_feature.get("priority", ""),
                        status=parsed_feature.get("status", ""),
                        related_components=parsed_feature.get("related_components", []),
                        criteria=criteria_results,
                        implementation_status=determine_feature_status(feature_statuses),
                        confidence=sum(feature_confidences) / len(feature_confidences) if feature_confidences else 0.0,
                    )
                )

            return AnalysisReport(
                source=source or repo_path,
                specs_path=os.path.relpath(resolved_specs_path, repo_path),
                commit_hash=commit_hash,
                features=feature_results,
                total_features=len(feature_results),
                total_criteria=total_criteria,
                implemented_count=implemented,
                different_count=different,
                not_implemented_count=not_implemented,
                not_specified_count=not_specified,
                global_confidence=sum(all_confidences) / len(all_confidences) if all_confidences else 0.0,
            )
        finally:
            if debug_file:
                debug_file.close()

    def analyze_git_url(
        self,
        github_url: str,
        clone_root: str,
        repo_dir_name: Optional[str] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> AnalysisReport:
        _notify(progress_callback, AnalysisStatus.CLONING.value)

        os.makedirs(clone_root, exist_ok=True)
        repo_path = os.path.join(clone_root, repo_dir_name or f"repo_{uuid4().hex[:8]}")

        if os.path.exists(repo_path):
            shutil.rmtree(repo_path)

        try:
            from git import Repo

            repo = Repo.clone_from(github_url, repo_path, depth=1)
            commit_hash = repo.head.commit.hexsha[:7]
            return self.analyze_path(
                repo_path=repo_path,
                progress_callback=progress_callback,
                source=github_url,
                commit_hash=commit_hash,
            )
        finally:
            if os.path.exists(repo_path):
                shutil.rmtree(repo_path)

    def _evaluate_with_optional_debug(
        self,
        criteria_description: str,
        code_context: str,
        feature_name: str,
        debug: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        try:
            return self.evaluator.evaluate_criteria(
                criteria_description=criteria_description,
                code_context=code_context,
                feature_name=feature_name,
                debug=debug,
            )
        except TypeError:
            # Backward compatibility for custom evaluators that do not yet accept `debug`.
            return self.evaluator.evaluate_criteria(
                criteria_description=criteria_description,
                code_context=code_context,
                feature_name=feature_name,
            )


def _notify(progress_callback: Optional[ProgressCallback], status: str) -> None:
    if progress_callback:
        progress_callback(status)


def skipped_evaluation(feature_status: str) -> Dict[str, Any]:
    return {
        "status": ImplementationStatus.NOT_IMPLEMENTED.value,
        "confidence": 1.0,
        "short_explanation": f"Feature marked as '{feature_status}' in SPECS.md - skipped analysis.",
        "detailed_explanation": (
            f"This feature is listed as '{feature_status}' in the specification and was not analyzed. "
            "Update the feature status to 'Completed' or 'Done' in SPECS.md to include it in the analysis."
        ),
        "code_snippets": "",
    }


def normalize_evaluation(evaluation: Dict[str, Any]) -> Dict[str, Any]:
    valid_statuses = {status.value for status in ImplementationStatus}
    status = evaluation.get("status", ImplementationStatus.NOT_IMPLEMENTED.value)
    if status not in valid_statuses:
        status = ImplementationStatus.NOT_IMPLEMENTED.value

    confidence = evaluation.get("confidence", 0.0)
    if not isinstance(confidence, (int, float)):
        confidence = 0.0

    return {
        "status": status,
        "confidence": max(0.0, min(1.0, float(confidence))),
        "short_explanation": evaluation.get("short_explanation", ""),
        "detailed_explanation": evaluation.get("detailed_explanation", ""),
        "code_snippets": evaluation.get("code_snippets", ""),
    }


def find_specs_file(repo_path: str) -> Optional[str]:
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [directory for directory in dirs if directory not in SKIP_DIRS]
        for file_name in files:
            if file_name.upper() == "SPECS.MD":
                return os.path.join(root, file_name)
    return None


def build_file_index(repo_path: str) -> List[Dict[str, Any]]:
    index = []

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [directory for directory in dirs if directory not in SKIP_DIRS]

        for file_name in files:
            extension = os.path.splitext(file_name)[1].lower()
            if extension not in CODE_EXTENSIONS:
                continue

            file_path = os.path.join(root, file_name)
            relative_path = os.path.relpath(file_path, repo_path)

            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as file:
                    content = file.read()

                index.append(
                    {
                        "path": relative_path,
                        "filename": file_name,
                        "content": content,
                        "content_lower": content.lower(),
                        "filename_lower": file_name.lower(),
                        "size": len(content),
                    }
                )
            except Exception:
                logger.exception("Failed to index file %s", file_path)

    return index


def extract_keywords_from_criteria(criteria: str, feature_name: str) -> Set[str]:
    text = f"{criteria} {feature_name}".lower()
    words = re.findall(r"\b[a-z][a-z0-9_]*\b", text)

    stopwords = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "can",
        "shall",
        "need",
        "ought",
        "and",
        "or",
        "but",
        "if",
        "then",
        "else",
        "when",
        "where",
        "why",
        "how",
        "what",
        "which",
        "who",
        "whom",
        "this",
        "that",
        "these",
        "those",
        "with",
        "without",
        "for",
        "from",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "under",
        "again",
        "further",
        "once",
        "here",
        "there",
        "all",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "not",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
        "also",
        "now",
        "given",
        "while",
        "able",
        "about",
        "across",
        "actually",
        "almost",
        "always",
        "among",
        "any",
        "allow",
        "allows",
        "already",
        "although",
        "another",
        "both",
        "certain",
        "certainly",
        "done",
        "either",
        "enough",
        "even",
        "every",
        "following",
        "getting",
        "going",
        "however",
        "its",
        "keep",
        "keeps",
        "kept",
        "know",
        "knows",
        "last",
        "later",
        "least",
        "less",
        "let",
        "lets",
        "like",
        "likely",
        "made",
        "make",
        "makes",
        "many",
        "maybe",
        "means",
        "meanwhile",
        "much",
        "never",
        "next",
        "none",
        "nothing",
        "otherwise",
        "part",
        "perhaps",
        "please",
        "put",
        "puts",
        "rather",
        "really",
        "regarding",
        "right",
        "said",
        "say",
        "says",
        "see",
        "seem",
        "seemed",
        "seems",
        "several",
        "since",
        "something",
        "sometimes",
        "soon",
        "still",
        "sure",
        "take",
        "taken",
        "takes",
        "tell",
        "tends",
        "therefore",
        "thing",
        "things",
        "though",
        "thus",
        "together",
        "towards",
        "tried",
        "tries",
        "truly",
        "try",
        "trying",
        "until",
        "upon",
        "used",
        "uses",
        "using",
        "usually",
        "want",
        "wants",
        "way",
        "ways",
        "well",
        "went",
        "whether",
        "whole",
        "within",
        "yet",
        "you",
        "your",
        "max",
        "min",
        "maximum",
        "minimum",
        "user",
        "users",
        "system",
        "feature",
        "features",
        "chars",
        "character",
        "characters",
        "number",
        "numbers",
        "text",
        "string",
        "value",
        "values",
        "data",
        "item",
        "items",
        "field",
        "fields",
    }

    keywords = {word for word in words if word not in stopwords and len(word) >= 3}

    domain_patterns = [
        r"\b(create|update|delete|edit|add|remove|get|list|view|show)\b",
        r"\b(title|description|name|content|body|summary)\b",
        r"\b(idea|post|comment|user|account|profile|session)\b",
        r"\b(form|button|input|field|modal|page|component)\b",
        r"\b(api|route|endpoint|handler|controller|service)\b",
        r"\b(database|schema|model|table|migration)\b",
        r"\b(validation|validate|valid|error|required)\b",
        r"\b(auth|login|logout|register|signup|signin)\b",
    ]

    for pattern in domain_patterns:
        keywords.update(re.findall(pattern, criteria.lower()))

    return keywords


def find_relevant_files(
    file_index: List[Dict[str, Any]],
    keywords: Set[str],
    specs_referenced: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    scored_files = []
    specs_referenced = specs_referenced or []
    specs_referenced_lower = [file_path.lower() for file_path in specs_referenced]

    for file_info in file_index:
        score = 0
        matched_keywords = set()

        is_specs_referenced = any(
            ref in file_info["path"].lower() or file_info["path"].lower().endswith(ref)
            for ref in specs_referenced_lower
        )
        if is_specs_referenced:
            score += 50

        for keyword in keywords:
            if keyword in file_info["filename_lower"]:
                score += 10
                matched_keywords.add(keyword)

            count = file_info["content_lower"].count(keyword)
            if count > 0:
                score += min(count, 5) * 2
                matched_keywords.add(keyword)

        important_patterns = [
            (r"(schema|model)\.", 15),
            (r"route[rs]?\.", 12),
            (r"(controller|handler)\.", 12),
            (r"(service|storage)\.", 10),
            (r"database[-_]?storage", 12),
            (r"(component|form)\.", 8),
            (r"api/", 10),
            (r"(server|backend)/", 8),
            (r"(client|frontend)/", 6),
        ]

        for pattern, bonus in important_patterns:
            if re.search(pattern, file_info["path"].lower()):
                score += bonus

        if score > 0:
            scored_files.append(
                {
                    **file_info,
                    "score": score,
                    "matched_keywords": matched_keywords,
                    "is_specs_referenced": is_specs_referenced,
                }
            )

    scored_files.sort(key=lambda file_info: (file_info.get("is_specs_referenced", False), file_info["score"]), reverse=True)
    return scored_files[:15]


def enhance_keywords_for_validation(keywords: Set[str], criteria: str) -> Set[str]:
    enhanced = set(keywords)
    criteria_lower = criteria.lower()

    enhanced.update(
        {
            "403",
            "401",
            "400",
            "forbidden",
            "unauthorized",
            "reject",
            "prevent",
            "check",
            "verify",
            "validate",
            "restrict",
            "limit",
            "cannot",
            "not allowed",
            "permission",
            "denied",
        }
    )

    if "edit" in criteria_lower:
        enhanced.update({"update", "modify", "put", "patch", "save"})
    if "vote" in criteria_lower:
        enhanced.update({"votes", "voting", "voted", "maxvotes", "votecount"})
    if "delete" in criteria_lower:
        enhanced.update({"remove", "destroy", "del"})
    if "create" in criteria_lower:
        enhanced.update({"add", "new", "insert", "post"})
    if "own" in criteria_lower or "creator" in criteria_lower:
        enhanced.update({"owner", "ownerid", "creatorid", "userid", "author"})

    for number in re.findall(r"\d+", criteria):
        enhanced.update({number, f"max{number}", f"limit{number}"})

    return enhanced


def build_smart_context(relevant_files: List[Dict[str, Any]], criteria: str, keywords: Set[str]) -> str:
    context_parts = []
    total_size = 0
    max_total_size = 40000

    primary_keywords = set(keywords)
    enhanced_keywords = enhance_keywords_for_validation(keywords, criteria)

    for file_info in relevant_files:
        if total_size >= max_total_size:
            break

        content = file_info["content"]
        path = file_info["path"]
        is_specs_referenced = file_info.get("is_specs_referenced", False)

        if is_specs_referenced:
            extracted = content if len(content) <= 8000 else extract_functions_by_relevance(content, enhanced_keywords, primary_keywords)
            if len(extracted) > 8000:
                extracted = extracted[:8000] + "\n... [truncated]"
        else:
            if len(content) <= 4000:
                extracted = content
            else:
                extracted = extract_functions_by_relevance(content, enhanced_keywords, primary_keywords)
                if not extracted or len(extracted) < 50:
                    continue
                if len(extracted) > 7000:
                    extracted = extracted[:7000] + "\n... [truncated]"

        marker = "[SPECS REFERENCED] " if is_specs_referenced else ""
        context_parts.append(f"=== {marker}FILE: {path} ===\n{extracted}")
        total_size += len(extracted)

    if not context_parts:
        return "No relevant code files found for this criteria."

    return "\n\n".join(context_parts)


def extract_functions_by_relevance(content: str, keywords: Set[str], primary_keywords: Optional[Set[str]] = None) -> str:
    lines = content.split("\n")
    functions = find_function_blocks(lines)

    if not functions:
        return extract_relevant_lines(content, keywords)

    primary = primary_keywords or set()
    secondary = keywords - primary

    http_method_bonus: Dict[str, int] = {}
    get_words = {"accessible", "access", "view", "display", "retrieve", "show", "read", "fetch", "list", "get", "visible", "see", "browse"}
    post_words = {"create", "submit", "post", "add", "register", "send", "new"}
    put_words = {"edit", "update", "modify", "change", "put", "patch"}
    delete_words = {"delete", "remove", "destroy", "cancel"}
    if primary & get_words:
        http_method_bonus["get"] = 40
    if primary & post_words:
        http_method_bonus["post"] = 40
    if primary & put_words:
        http_method_bonus["put"] = 40
        http_method_bonus["patch"] = 40
    if primary & delete_words:
        http_method_bonus["delete"] = 40

    scored_functions = []
    for start, end, signature in functions:
        block_text = "\n".join(lines[start:end]).lower()
        score = 0
        primary_matched = 0

        for keyword in primary:
            if keyword in block_text:
                count = block_text.count(keyword)
                score += 25 * min(count, 3)
                primary_matched += 1

        for keyword in secondary:
            if keyword in block_text:
                count = block_text.count(keyword)
                score += 5 * min(count, 3)

        if primary_matched >= 2:
            score += 50

        signature_lower = signature.lower()
        route_match = re.search(r"(app|router)\.(get|post|put|patch|delete)\s*\(", signature_lower)
        if route_match:
            method = route_match.group(2)
            if method in http_method_bonus:
                score += http_method_bonus[method]

            route_path_match = re.search(r"[\"']([^\"']+)[\"']", signature_lower)
            if route_path_match:
                route_path = route_path_match.group(1)
                segments = route_path.strip("/").split("/")
                route_primary_matches = sum(1 for keyword in primary if keyword in route_path)
                if route_primary_matches > 0:
                    specificity_bonus = route_primary_matches * 30 - len(segments) * 3
                    score += max(specificity_bonus, 0)

        if score > 0:
            scored_functions.append((score, start, end, signature, primary_matched))

    scored_functions.sort(key=lambda item: item[0], reverse=True)

    primary_matches = [item for item in scored_functions if item[4] > 0]
    secondary_only = [item for item in scored_functions if item[4] == 0]

    sections = []
    total_lines = 0
    max_lines = 500

    for score, start, end, signature, _match_count in primary_matches:
        block_lines = end - start
        if total_lines + block_lines > max_lines and len(sections) >= 3:
            logger.info("Skipped function %s due to context budget", signature)
            continue
        sections.append((start, end, score))
        total_lines += block_lines

    for score, start, end, _signature, _match_count in secondary_only:
        block_lines = end - start
        if total_lines + block_lines > max_lines:
            break
        sections.append((start, end, score))
        total_lines += block_lines
        if len(sections) >= 12:
            break

    sections.sort(key=lambda item: item[0])

    result = []
    for start, end, score in sections:
        if start > 0:
            result.append(f"--- (line {start + 1}, relevance: {score}) ---")
        result.extend(lines[start:end])
        result.append("")

    return "\n".join(result) if result else extract_relevant_lines(content, keywords)


def find_function_blocks(lines: List[str]) -> List[tuple[int, int, str]]:
    function_patterns = [
        re.compile(r"^\s*(export\s+)?(async\s+)?function\s+\w+"),
        re.compile(r"^\s*(const|let|var)\s+\w+\s*=\s*(async\s+)?\("),
        re.compile(r"^\s*(const|let|var)\s+\w+\s*=\s*(async\s+)?(\w+\s*)?\=\>"),
        re.compile(r"^\s*(app|router)\.(get|post|put|patch|delete)\s*\("),
        re.compile(r"^\s*(public|private|protected)?\s*(async\s+)?\w+\s*\([^)]*\)\s*(\{|:)"),
        re.compile(r"^\s*def\s+\w+"),
        re.compile(r"^\s*class\s+\w+"),
        re.compile(r"^\s*@(app|router)\.(get|post|put|patch|delete)"),
    ]

    functions = []
    index = 0
    while index < len(lines):
        line = lines[index]
        matched = any(pattern.search(line) for pattern in function_patterns)

        if matched:
            start = index
            end = find_block_end(lines, index)
            functions.append((start, end, line.strip()[:80]))
            index = end
        else:
            index += 1

    return functions


def find_block_end(lines: List[str], start: int) -> int:
    if start >= len(lines):
        return start + 1

    first_line = lines[start]
    brace_count = first_line.count("{") - first_line.count("}")
    paren_count = first_line.count("(") - first_line.count(")")
    uses_braces = "{" in first_line

    if not uses_braces:
        base_indent = len(first_line) - len(first_line.lstrip())
        for index in range(start + 1, min(start + 5, len(lines))):
            if "{" in lines[index]:
                uses_braces = True
                brace_count += lines[index].count("{") - lines[index].count("}")
                paren_count += lines[index].count("(") - lines[index].count(")")
                start_scan = index + 1
                break
        else:
            start_scan = start + 1
    else:
        start_scan = start + 1

    if uses_braces:
        for index in range(start_scan, min(len(lines), start + 300)):
            brace_count += lines[index].count("{") - lines[index].count("}")
            paren_count += lines[index].count("(") - lines[index].count(")")
            if brace_count <= 0 and paren_count <= 0:
                return index + 1
        return min(len(lines), start + 100)

    for index in range(start + 1, min(len(lines), start + 200)):
        line = lines[index]
        if line.strip() == "":
            continue
        current_indent = len(line) - len(line.lstrip())
        if current_indent <= base_indent:
            return index
    return min(len(lines), start + 50)


def extract_relevant_lines(content: str, keywords: Set[str]) -> str:
    lines = content.split("\n")
    relevant_line_numbers = set()
    context = 15

    for index, line in enumerate(lines):
        line_lower = line.lower()
        for keyword in keywords:
            if keyword in line_lower:
                for context_index in range(max(0, index - context), min(len(lines), index + context + 1)):
                    relevant_line_numbers.add(context_index)
                break

    if not relevant_line_numbers:
        return ""

    sections = []
    previous_line = -2
    for line_number in sorted(relevant_line_numbers):
        if line_number > previous_line + 1 and sections:
            sections.append(f"... (skipped to line {line_number + 1})")
        sections.append(f"{line_number + 1}: {lines[line_number]}")
        previous_line = line_number

    return "\n".join(sections)


def determine_feature_status(statuses: List[str]) -> str:
    if not statuses:
        return ImplementationStatus.NOT_IMPLEMENTED.value

    if all(status == ImplementationStatus.IMPLEMENTED_AS_EXPECTED.value for status in statuses):
        return ImplementationStatus.IMPLEMENTED_AS_EXPECTED.value
    if all(status == ImplementationStatus.NOT_IMPLEMENTED.value for status in statuses):
        return ImplementationStatus.NOT_IMPLEMENTED.value
    if any(status == ImplementationStatus.IMPLEMENTED_DIFFERENTLY.value for status in statuses):
        return ImplementationStatus.IMPLEMENTED_DIFFERENTLY.value
    return ImplementationStatus.IMPLEMENTED_DIFFERENTLY.value
