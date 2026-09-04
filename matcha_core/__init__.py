from .models import AnalysisReport, AnalysisStatus, CriteriaResult, FeatureResult, ImplementationStatus
from .gate import GateEvaluation, GatePolicy, PolicyError, evaluate_gate, load_policy
from .reporting import report_to_dict, report_to_html, report_to_json, report_to_markdown, report_to_table
from .specs_parser import SpecsParser, SpecsValidationError
from .onboarding import (
    DraftCriterion,
    DraftEvidence,
    DraftFeature,
    DraftQuestion,
    OpenAICompatibleSpecGenerator,
    RepositoryBootstrapper,
    SpecsBootstrapError,
    SpecsDraft,
    draft_to_dict,
    render_specs_draft,
    write_specs_draft,
)

__version__ = "0.1.3"

__all__ = [
    "AnalysisError",
    "AnalysisReport",
    "AnalysisStatus",
    "CriteriaResult",
    "DraftCriterion",
    "DraftEvidence",
    "DraftFeature",
    "DraftQuestion",
    "FeatureResult",
    "GateEvaluation",
    "GatePolicy",
    "ImplementationStatus",
    "OpenAICompatibleEvaluator",
    "OpenAICompatibleSpecGenerator",
    "PolicyError",
    "RepositoryAnalyzer",
    "RepositoryBootstrapper",
    "SpecsBootstrapError",
    "SpecsDraft",
    "SpecsParser",
    "SpecsValidationError",
    "evaluate_gate",
    "draft_to_dict",
    "load_policy",
    "report_to_dict",
    "report_to_html",
    "report_to_json",
    "report_to_markdown",
    "report_to_table",
    "render_specs_draft",
    "write_specs_draft",
]


def __getattr__(name: str):
    if name == "RepositoryAnalyzer" or name == "AnalysisError":
        from .engine import AnalysisError, RepositoryAnalyzer

        return {
            "RepositoryAnalyzer": RepositoryAnalyzer,
            "AnalysisError": AnalysisError,
        }[name]

    if name == "OpenAICompatibleEvaluator":
        from .evaluator import OpenAICompatibleEvaluator

        return OpenAICompatibleEvaluator

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
