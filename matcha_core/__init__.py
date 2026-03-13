from .models import AnalysisReport, AnalysisStatus, CriteriaResult, FeatureResult, ImplementationStatus
from .reporting import report_to_dict, report_to_html, report_to_json, report_to_markdown, report_to_table
from .specs_parser import SpecsParser

__version__ = "0.1.1"

__all__ = [
    "AnalysisError",
    "AnalysisReport",
    "AnalysisStatus",
    "CriteriaResult",
    "FeatureResult",
    "ImplementationStatus",
    "OpenAICompatibleEvaluator",
    "RepositoryAnalyzer",
    "SpecsParser",
    "report_to_dict",
    "report_to_html",
    "report_to_json",
    "report_to_markdown",
    "report_to_table",
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
