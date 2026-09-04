from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class AnalysisStatus(str, Enum):
    PENDING = "pending"
    CLONING = "cloning"
    PARSING = "parsing"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"


class ImplementationStatus(str, Enum):
    IMPLEMENTED_AS_EXPECTED = "implemented_as_expected"
    IMPLEMENTED_DIFFERENTLY = "implemented_differently"
    NOT_IMPLEMENTED = "not_implemented"
    NOT_SPECIFIED = "not_specified"
    SKIPPED = "skipped"
    INCONCLUSIVE = "inconclusive"
    ANALYSIS_FAILED = "analysis_failed"


@dataclass
class CriteriaResult:
    description: str
    criteria_id: Optional[str] = None
    referenced_files: List[str] = field(default_factory=list)
    implementation_status: str = ImplementationStatus.INCONCLUSIVE.value
    confidence: float = 0.0
    short_explanation: str = ""
    detailed_explanation: str = ""
    code_snippets: str = ""
    analysis_mode: str = "model"
    error: Optional[str] = None


@dataclass
class FeatureResult:
    feature_id: str
    name: str
    description: str = ""
    priority: str = ""
    status: str = ""
    related_components: List[str] = field(default_factory=list)
    criteria: List[CriteriaResult] = field(default_factory=list)
    implementation_status: str = ImplementationStatus.INCONCLUSIVE.value
    confidence: float = 0.0


@dataclass
class AnalysisReport:
    source: str
    specs_path: str
    features: List[FeatureResult] = field(default_factory=list)
    commit_hash: Optional[str] = None
    total_features: int = 0
    total_criteria: int = 0
    implemented_count: int = 0
    different_count: int = 0
    not_implemented_count: int = 0
    not_specified_count: int = 0
    skipped_count: int = 0
    inconclusive_count: int = 0
    analysis_failed_count: int = 0
    global_confidence: float = 0.0
    schema_version: str = "1.0"
