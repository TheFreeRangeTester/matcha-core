from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import yaml

from .models import AnalysisReport, ImplementationStatus
from .reporting import report_to_dict

GATE_SCHEMA_VERSION = "1.0"
POLICY_VERSION = 1

EXIT_PASS = 0
EXIT_POLICY_FAILED = 10
EXIT_INCONCLUSIVE = 20
EXIT_ERROR = 30


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class AllowRule:
    feature_id: str
    criteria_id: Optional[str] = None
    statuses: frozenset[str] = frozenset()
    expires: Optional[date] = None
    reason: str = ""

    def matches(self, feature_id: str, criteria_id: Optional[str], status: str, today: date) -> bool:
        if self.expires and self.expires < today:
            return False
        if self.feature_id.lower() != feature_id.lower():
            return False
        if self.criteria_id and self.criteria_id.lower() != (criteria_id or "").lower():
            return False
        return not self.statuses or status in self.statuses


@dataclass(frozen=True)
class GatePolicy:
    fail_on: frozenset[str]
    priorities: frozenset[str]
    min_confidence: float
    on_inconclusive: str
    baseline: Optional[str] = None
    allow: tuple[AllowRule, ...] = ()
    version: int = POLICY_VERSION

    def includes_priority(self, priority: str) -> bool:
        normalized = priority.strip().lower()
        return normalized in {"", "unknown"} or "*" in self.priorities or normalized in self.priorities


@dataclass
class GateEvaluation:
    decision: str
    exit_code: int
    violations: list[dict[str, Any]] = field(default_factory=list)
    incomplete: list[dict[str, Any]] = field(default_factory=list)
    waived: list[dict[str, Any]] = field(default_factory=list)


def load_policy(path: str | Path) -> GatePolicy:
    policy_path = Path(path)
    try:
        raw = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PolicyError(f"Policy file not found: {policy_path}") from exc
    except yaml.YAMLError as exc:
        raise PolicyError(f"Invalid policy YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise PolicyError("Policy must be a YAML mapping")

    allowed_keys = {"version", "fail_on", "priorities", "min_confidence", "on_inconclusive", "baseline", "allow"}
    unknown_keys = sorted(set(raw) - allowed_keys)
    if unknown_keys:
        raise PolicyError(f"Unknown policy fields: {', '.join(unknown_keys)}")

    version = raw.get("version")
    if version != POLICY_VERSION:
        raise PolicyError(f"Unsupported policy version: {version!r}; expected {POLICY_VERSION}")

    fail_on = _string_set(raw.get("fail_on"), "fail_on")
    allowed_failure_statuses = {
        ImplementationStatus.NOT_IMPLEMENTED.value,
        ImplementationStatus.IMPLEMENTED_DIFFERENTLY.value,
        ImplementationStatus.NOT_SPECIFIED.value,
    }
    invalid_statuses = fail_on - allowed_failure_statuses
    if invalid_statuses:
        raise PolicyError(f"fail_on contains unsupported statuses: {', '.join(sorted(invalid_statuses))}")
    if not fail_on:
        raise PolicyError("fail_on must contain at least one status")

    priorities = _string_set(raw.get("priorities"), "priorities")
    if not priorities:
        raise PolicyError("priorities must contain at least one priority or '*'")

    min_confidence = raw.get("min_confidence", 0.75)
    if isinstance(min_confidence, bool) or not isinstance(min_confidence, (int, float)):
        raise PolicyError("min_confidence must be a number between 0 and 1")
    min_confidence = float(min_confidence)
    if not 0 <= min_confidence <= 1:
        raise PolicyError("min_confidence must be between 0 and 1")

    on_inconclusive = raw.get("on_inconclusive", "block")
    if on_inconclusive not in {"block", "ignore"}:
        raise PolicyError("on_inconclusive must be 'block' or 'ignore'")

    baseline = raw.get("baseline")
    if baseline is not None and (not isinstance(baseline, str) or not baseline.strip()):
        raise PolicyError("baseline must be a non-empty path")

    allow_raw = raw.get("allow", [])
    if not isinstance(allow_raw, list):
        raise PolicyError("allow must be a list")
    allow = tuple(_parse_allow_rule(item, index) for index, item in enumerate(allow_raw, start=1))

    return GatePolicy(
        version=version,
        fail_on=frozenset(fail_on),
        priorities=frozenset(priority.lower() for priority in priorities),
        min_confidence=min_confidence,
        on_inconclusive=on_inconclusive,
        baseline=baseline,
        allow=allow,
    )


def evaluate_gate(
    report: AnalysisReport,
    policy: GatePolicy,
    baseline_findings: Optional[set[tuple[str, str, str]]] = None,
    today: Optional[date] = None,
) -> GateEvaluation:
    baseline_findings = baseline_findings or set()
    today = today or datetime.now(timezone.utc).date()
    violations: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    waived: list[dict[str, Any]] = []

    incomplete_statuses = {
        ImplementationStatus.INCONCLUSIVE.value,
        ImplementationStatus.ANALYSIS_FAILED.value,
    }
    valid_statuses = {status.value for status in ImplementationStatus}

    for feature in report.features:
        if not policy.includes_priority(feature.priority):
            continue
        for criterion in feature.criteria:
            finding = _finding_dict(feature, criterion)
            status = criterion.implementation_status

            if status not in valid_statuses:
                if policy.on_inconclusive == "block":
                    finding["reason"] = "invalid_analysis_status"
                    incomplete.append(finding)
                continue
            if status in incomplete_statuses:
                if policy.on_inconclusive == "block":
                    incomplete.append(finding)
                continue
            if status == ImplementationStatus.SKIPPED.value:
                continue
            if criterion.confidence < policy.min_confidence:
                if policy.on_inconclusive == "block":
                    finding["reason"] = "confidence_below_policy_minimum"
                    incomplete.append(finding)
                continue
            if status not in policy.fail_on:
                continue

            matching_rule = next(
                (
                    rule
                    for rule in policy.allow
                    if rule.matches(feature.feature_id, criterion.criteria_id, status, today)
                ),
                None,
            )
            if matching_rule:
                finding["waiver"] = {
                    "source": "policy",
                    "reason": matching_rule.reason,
                    "expires": matching_rule.expires.isoformat() if matching_rule.expires else None,
                }
                waived.append(finding)
                continue

            baseline_key = _finding_key(feature.feature_id, criterion.criteria_id, status)
            if baseline_key in baseline_findings:
                finding["waiver"] = {"source": "baseline", "reason": "Existing baseline finding", "expires": None}
                waived.append(finding)
                continue

            violations.append(finding)

    if violations:
        return GateEvaluation("fail", EXIT_POLICY_FAILED, violations, incomplete, waived)
    if incomplete:
        return GateEvaluation("inconclusive", EXIT_INCONCLUSIVE, violations, incomplete, waived)
    return GateEvaluation("pass", EXIT_PASS, violations, incomplete, waived)


def load_baseline_findings(path: str | Path) -> set[tuple[str, str, str]]:
    baseline_path = Path(path)
    try:
        payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PolicyError(f"Baseline file not found: {baseline_path}") from exc
    except json.JSONDecodeError as exc:
        raise PolicyError(f"Baseline is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise PolicyError("Baseline must be a JSON object")
    report = payload.get("analysis_report", payload)
    if not isinstance(report, dict) or not isinstance(report.get("features"), list):
        raise PolicyError("Baseline must be a Matcha report or gate artifact")
    artifact_schema = payload.get("schema_version")
    report_schema = report.get("schema_version")
    if not _compatible_schema(artifact_schema) or not _compatible_schema(report_schema):
        raise PolicyError("Baseline schema is missing or incompatible; expected major version 1")

    findings = set()
    for feature in report["features"]:
        if not isinstance(feature, dict):
            continue
        feature_id = str(feature.get("feature_id") or "")
        for criterion in feature.get("criteria", []):
            if not isinstance(criterion, dict):
                continue
            findings.add(
                _finding_key(
                    feature_id,
                    criterion.get("criteria_id"),
                    str(criterion.get("implementation_status") or ""),
                )
            )
    return findings


def build_gate_artifact(
    report: AnalysisReport,
    evaluation: GateEvaluation,
    *,
    repo_path: str | Path,
    specs_path: str | Path,
    policy_path: str | Path,
    policy: GatePolicy,
    baseline_path: Optional[str | Path],
    provider: str,
    model: str,
    matcha_version: str,
    feature_ids: Optional[list[str]] = None,
    generated_paths: Optional[list[str | Path]] = None,
) -> dict[str, Any]:
    commit_hash, dirty = repository_state(repo_path, ignored_paths=generated_paths)
    created_at = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": GATE_SCHEMA_VERSION,
        "kind": "matcha_gate_result",
        "run": {
            "id": str(uuid4()),
            "created_at": created_at,
            "matcha_version": matcha_version,
            "provider": provider,
            "model": model,
        },
        "input": {
            "repository": str(Path(repo_path).resolve()),
            "commit_hash": commit_hash,
            "dirty": dirty,
            "specs_path": str(Path(specs_path).resolve()),
            "specs_sha256": sha256_file(specs_path),
            "policy_path": str(Path(policy_path).resolve()),
            "policy_sha256": sha256_file(policy_path),
            "baseline_path": str(Path(baseline_path).resolve()) if baseline_path else None,
            "baseline_sha256": sha256_file(baseline_path) if baseline_path else None,
            "feature_ids": feature_ids or [],
        },
        "policy": policy_to_dict(policy),
        "decision": evaluation.decision,
        "exit_code": evaluation.exit_code,
        "summary": {
            "violations": len(evaluation.violations),
            "incomplete": len(evaluation.incomplete),
            "waived": len(evaluation.waived),
        },
        "violations": evaluation.violations,
        "incomplete": evaluation.incomplete,
        "waived": evaluation.waived,
        "analysis_report": report_to_dict(report),
    }


def build_gate_error_artifact(
    error: str,
    *,
    repo_path: str | Path,
    policy_path: Optional[str | Path],
    provider: str,
    model: Optional[str],
    matcha_version: str,
    generated_paths: Optional[list[str | Path]] = None,
) -> dict[str, Any]:
    commit_hash, dirty = repository_state(repo_path, ignored_paths=generated_paths)
    resolved_policy = Path(policy_path).expanduser().resolve() if policy_path else None
    return {
        "schema_version": GATE_SCHEMA_VERSION,
        "kind": "matcha_gate_result",
        "run": {
            "id": str(uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "matcha_version": matcha_version,
            "provider": provider,
            "model": model,
        },
        "input": {
            "repository": str(Path(repo_path).expanduser().resolve()),
            "commit_hash": commit_hash,
            "dirty": dirty,
            "policy_path": str(resolved_policy) if resolved_policy else None,
            "policy_sha256": sha256_file(resolved_policy) if resolved_policy and resolved_policy.is_file() else None,
        },
        "decision": "error",
        "exit_code": EXIT_ERROR,
        "error": error,
    }


def write_json_atomic(payload: dict[str, Any], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def resolve_policy_path(repo_path: str | Path, explicit_path: Optional[str | Path]) -> Path:
    if explicit_path:
        return Path(explicit_path).expanduser().resolve()
    return Path(repo_path).resolve() / ".matcha" / "policy.yml"


def resolve_baseline_path(policy: GatePolicy, policy_path: str | Path, explicit_path: Optional[str | Path]) -> Optional[Path]:
    if explicit_path:
        return Path(explicit_path).expanduser().resolve()
    if not policy.baseline:
        return None
    path = Path(policy.baseline).expanduser()
    if not path.is_absolute():
        path = Path(policy_path).resolve().parent / path
    return path.resolve()


def repository_state(
    repo_path: str | Path,
    ignored_paths: Optional[list[str | Path]] = None,
) -> tuple[Optional[str], Optional[bool]]:
    try:
        from git import Repo

        repo = Repo(str(Path(repo_path).resolve()), search_parent_directories=True)
        dirty = repo.is_dirty(untracked_files=False)
        ignored = {Path(path).expanduser().resolve() for path in (ignored_paths or [])}
        if not dirty:
            worktree = Path(repo.working_tree_dir or repo_path).resolve()
            dirty = any((worktree / path).resolve() not in ignored for path in repo.untracked_files)
        return repo.head.commit.hexsha, dirty
    except Exception:
        return None, None


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def policy_to_dict(policy: GatePolicy) -> dict[str, Any]:
    return {
        "version": policy.version,
        "fail_on": sorted(policy.fail_on),
        "priorities": sorted(policy.priorities),
        "min_confidence": policy.min_confidence,
        "on_inconclusive": policy.on_inconclusive,
        "baseline": policy.baseline,
        "allow": [
            {
                "feature": rule.feature_id,
                "criteria": rule.criteria_id,
                "statuses": sorted(rule.statuses),
                "expires": rule.expires.isoformat() if rule.expires else None,
                "reason": rule.reason,
            }
            for rule in policy.allow
        ],
    }


def _parse_allow_rule(raw: Any, index: int) -> AllowRule:
    if not isinstance(raw, dict):
        raise PolicyError(f"allow[{index}] must be a mapping")
    allowed_keys = {"feature", "criteria", "statuses", "expires", "reason"}
    unknown_keys = sorted(set(raw) - allowed_keys)
    if unknown_keys:
        raise PolicyError(f"allow[{index}] has unknown fields: {', '.join(unknown_keys)}")

    feature_id = raw.get("feature")
    if not isinstance(feature_id, str) or not feature_id.strip():
        raise PolicyError(f"allow[{index}].feature must be a non-empty string")
    criteria_id = raw.get("criteria")
    if criteria_id is not None and (not isinstance(criteria_id, str) or not criteria_id.strip()):
        raise PolicyError(f"allow[{index}].criteria must be a non-empty string")

    statuses_raw = raw.get("statuses", [])
    statuses = _string_set(statuses_raw, f"allow[{index}].statuses", allow_empty=True)
    valid_statuses = {status.value for status in ImplementationStatus}
    invalid_statuses = statuses - valid_statuses
    if invalid_statuses:
        raise PolicyError(f"allow[{index}] contains unsupported statuses: {', '.join(sorted(invalid_statuses))}")

    expires = raw.get("expires")
    if isinstance(expires, datetime):
        expires = expires.date()
    elif isinstance(expires, str):
        try:
            expires = date.fromisoformat(expires)
        except ValueError as exc:
            raise PolicyError(f"allow[{index}].expires must use YYYY-MM-DD") from exc
    elif expires is not None and not isinstance(expires, date):
        raise PolicyError(f"allow[{index}].expires must use YYYY-MM-DD")
    if expires is None:
        raise PolicyError(f"allow[{index}].expires is required for temporary exceptions")

    reason = raw.get("reason", "")
    if not isinstance(reason, str) or not reason.strip():
        raise PolicyError(f"allow[{index}].reason must be a non-empty string")

    return AllowRule(
        feature_id=feature_id.strip(),
        criteria_id=criteria_id.strip() if criteria_id else None,
        statuses=frozenset(statuses),
        expires=expires,
        reason=reason.strip(),
    )


def _string_set(value: Any, field_name: str, allow_empty: bool = False) -> set[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise PolicyError(f"{field_name} must be a list of non-empty strings")
    values = {item.strip().lower() for item in value}
    if not values and not allow_empty:
        raise PolicyError(f"{field_name} must not be empty")
    return values


def _finding_key(feature_id: str, criteria_id: Optional[str], status: str) -> tuple[str, str, str]:
    return (feature_id.strip().lower(), (criteria_id or "").strip().lower(), status.strip().lower())


def _compatible_schema(value: Any) -> bool:
    return isinstance(value, str) and value.split(".", 1)[0] == "1"


def _finding_dict(feature: Any, criterion: Any) -> dict[str, Any]:
    return {
        "feature_id": feature.feature_id,
        "feature_name": feature.name,
        "priority": feature.priority,
        "criteria_id": criterion.criteria_id,
        "criteria": criterion.description,
        "status": criterion.implementation_status,
        "confidence": criterion.confidence,
        "analysis_mode": criterion.analysis_mode,
        "summary": criterion.short_explanation,
        "referenced_files": list(criterion.referenced_files),
    }
