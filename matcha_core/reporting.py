from __future__ import annotations

import html
import json
from dataclasses import asdict

from .models import AnalysisReport


def report_to_dict(report: AnalysisReport) -> dict:
    return asdict(report)


def report_to_json(report: AnalysisReport) -> str:
    return json.dumps(report_to_dict(report), indent=2, ensure_ascii=False)


def report_to_markdown(report: AnalysisReport) -> str:
    lines = [
        "# Matcha Analysis Report",
        "",
        f"- Source: `{report.source}`",
        f"- Specs: `{report.specs_path}`",
        f"- Commit: `{report.commit_hash or 'N/A'}`",
        f"- Features: `{report.total_features}`",
        f"- Criteria: `{report.total_criteria}`",
        f"- Implemented: `{report.implemented_count}`",
        f"- Different: `{report.different_count}`",
        f"- Not implemented: `{report.not_implemented_count}`",
        f"- Not specified: `{report.not_specified_count}`",
        f"- Global confidence: `{round(report.global_confidence * 100)}%`",
        "",
    ]

    for feature in report.features:
        lines.extend(
            [
                f"## {feature.feature_id}: {feature.name}",
                "",
                f"- Status in specs: `{feature.status or 'Unknown'}`",
                f"- Priority: `{feature.priority or 'Unknown'}`",
                f"- Implementation: `{feature.implementation_status}`",
                f"- Confidence: `{round(feature.confidence * 100)}%`",
                "",
            ]
        )
        if feature.description:
            lines.extend([feature.description, ""])

        for criteria in feature.criteria:
            lines.extend(
                [
                    f"### {criteria.criteria_id or 'AC'}",
                    "",
                    criteria.description,
                    "",
                    f"- Result: `{criteria.implementation_status}`",
                    f"- Confidence: `{round(criteria.confidence * 100)}%`",
                ]
            )
            if criteria.short_explanation:
                lines.append(f"- Summary: {criteria.short_explanation}")
            if criteria.referenced_files:
                files = ", ".join(f"`{path}`" for path in criteria.referenced_files)
                lines.append(f"- Referenced files: {files}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def report_to_html(report: AnalysisReport) -> str:
    feature_cards = "\n".join(_render_feature_card(feature) for feature in report.features)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Matcha Analysis Report</title>
  <style>
    :root {{
      --bg: #f5efe6;
      --bg-accent: #e5f4ef;
      --panel: #fffdf9;
      --panel-strong: #ffffff;
      --text: #1d2a24;
      --muted: #5e6b64;
      --border: #d9ddd4;
      --implemented: #0f8a5f;
      --implemented-bg: #daf5ea;
      --different: #b56a16;
      --different-bg: #fdeccf;
      --missing: #b63d3d;
      --missing-bg: #fde1df;
      --unspecified: #67757b;
      --unspecified-bg: #e8ecef;
      --shadow: 0 20px 60px rgba(24, 45, 37, 0.08);
      --radius-lg: 24px;
      --radius-md: 18px;
      --radius-sm: 12px;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(229, 244, 239, 0.95), transparent 32%),
        radial-gradient(circle at top right, rgba(255, 230, 212, 0.75), transparent 28%),
        linear-gradient(180deg, #fbf7f1 0%, var(--bg) 100%);
      line-height: 1.5;
    }}

    .shell {{
      width: min(1180px, calc(100% - 32px));
      margin: 32px auto 56px;
    }}

    .hero {{
      background:
        linear-gradient(135deg, rgba(15, 138, 95, 0.12), rgba(255, 255, 255, 0.75)),
        var(--panel-strong);
      border: 1px solid rgba(217, 221, 212, 0.9);
      border-radius: var(--radius-lg);
      padding: 32px;
      box-shadow: var(--shadow);
    }}

    .eyebrow {{
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-size: 0.78rem;
      color: var(--muted);
      margin: 0 0 8px;
    }}

    h1, h2, h3, h4, summary {{
      font-family: "Avenir Next Condensed", "Avenir Next", "Segoe UI", sans-serif;
      letter-spacing: -0.02em;
    }}

    h1 {{
      margin: 0 0 10px;
      font-size: clamp(2.3rem, 4vw, 4rem);
      line-height: 0.95;
    }}

    .subtitle {{
      margin: 0;
      max-width: 70ch;
      color: var(--muted);
      font-size: 1.02rem;
    }}

    .meta {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 14px;
      margin-top: 26px;
    }}

    .meta-card, .summary-card, .feature-card {{
      background: rgba(255, 255, 255, 0.78);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      box-shadow: 0 8px 24px rgba(24, 45, 37, 0.04);
    }}

    .meta-card {{
      padding: 16px 18px;
    }}

    .meta-label {{
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      font-size: 0.74rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 6px;
    }}

    .meta-value {{
      font-size: 0.98rem;
      word-break: break-word;
    }}

    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 14px;
      margin: 22px 0 0;
    }}

    .summary-card {{
      padding: 18px;
    }}

    .summary-number {{
      display: block;
      font-family: "Avenir Next Condensed", "Avenir Next", sans-serif;
      font-size: 2.1rem;
      line-height: 1;
      margin-bottom: 8px;
    }}

    .summary-label {{
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      color: var(--muted);
      font-size: 0.88rem;
    }}

    .section {{
      margin-top: 28px;
    }}

    .section-header {{
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 16px;
      margin-bottom: 14px;
    }}

    .section-header h2 {{
      margin: 0;
      font-size: 1.45rem;
    }}

    .section-note {{
      margin: 0;
      color: var(--muted);
      font-size: 0.95rem;
    }}

    .filters {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin: 16px 0 18px;
    }}

    .toolbar {{
      display: grid;
      gap: 14px;
      margin: 16px 0 18px;
    }}

    .search-input {{
      width: min(460px, 100%);
      border: 1px solid var(--border);
      background: rgba(255, 255, 255, 0.88);
      color: var(--text);
      border-radius: 16px;
      padding: 12px 14px;
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      font-size: 0.96rem;
      outline: none;
      box-shadow: 0 8px 24px rgba(24, 45, 37, 0.04);
    }}

    .search-input:focus {{
      border-color: rgba(15, 138, 95, 0.45);
      box-shadow: 0 0 0 4px rgba(15, 138, 95, 0.08);
    }}

    .filter-chip {{
      border: 1px solid var(--border);
      background: rgba(255, 255, 255, 0.85);
      color: var(--text);
      border-radius: 999px;
      padding: 8px 12px;
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      font-size: 0.84rem;
      cursor: pointer;
      transition: transform 120ms ease, background 120ms ease, border-color 120ms ease;
    }}

    .filter-chip:hover {{
      transform: translateY(-1px);
      border-color: rgba(15, 138, 95, 0.35);
    }}

    .filter-chip.active {{
      background: var(--bg-accent);
      border-color: rgba(15, 138, 95, 0.45);
      color: var(--implemented);
      font-weight: 600;
    }}

    .feature-list {{
      display: grid;
      gap: 16px;
    }}

    .feature-card {{
      overflow: hidden;
    }}

    .feature-head {{
      padding: 20px 22px 16px;
      border-left: 8px solid var(--status-color);
      background: linear-gradient(180deg, rgba(255,255,255,0.9), rgba(255,255,255,0.72));
    }}

    .feature-topline {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 10px;
    }}

    .feature-id {{
      font-family: "SF Mono", "Menlo", monospace;
      color: var(--muted);
      font-size: 0.86rem;
    }}

    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      padding: 6px 10px;
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      font-size: 0.79rem;
      font-weight: 600;
      background: var(--status-bg);
      color: var(--status-color);
    }}

    .feature-title {{
      margin: 0 0 10px;
      font-size: 1.42rem;
    }}

    .feature-description {{
      margin: 0 0 14px;
      color: var(--muted);
    }}

    .feature-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      color: var(--muted);
      font-size: 0.88rem;
    }}

    .feature-body {{
      padding: 0 22px 20px;
    }}

    .criteria-list {{
      display: grid;
      gap: 12px;
    }}

    details.criteria {{
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      background: rgba(255, 255, 255, 0.76);
      overflow: hidden;
    }}

    details.criteria[open] {{
      background: #fff;
    }}

    summary {{
      list-style: none;
      cursor: pointer;
      padding: 15px 18px;
      display: flex;
      justify-content: space-between;
      align-items: start;
      gap: 16px;
    }}

    summary::-webkit-details-marker {{
      display: none;
    }}

    .criteria-title {{
      margin: 0;
      font-size: 1rem;
      flex: 1;
    }}

    .criteria-side {{
      display: flex;
      flex-direction: column;
      gap: 8px;
      align-items: end;
      min-width: 140px;
    }}

    .criteria-content {{
      padding: 0 18px 18px;
      border-top: 1px solid rgba(217, 221, 212, 0.85);
    }}

    .criteria-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 10px;
      margin: 16px 0;
    }}

    .mini-card {{
      background: rgba(245, 239, 230, 0.5);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 12px 14px;
    }}

    .mini-label {{
      display: block;
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      font-size: 0.7rem;
      margin-bottom: 6px;
    }}

    .files {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 16px 0 0;
    }}

    .file-chip {{
      border-radius: 999px;
      padding: 6px 10px;
      background: var(--bg-accent);
      border: 1px solid rgba(17, 95, 69, 0.12);
      font-family: "SF Mono", "Menlo", monospace;
      font-size: 0.8rem;
    }}

    .evidence {{
      margin-top: 16px;
      display: grid;
      gap: 10px;
    }}

    .evidence-card {{
      border: 1px solid var(--border);
      border-radius: 14px;
      overflow: hidden;
      background: #fff;
    }}

    .evidence-head {{
      background: #eef3f0;
      border-bottom: 1px solid var(--border);
      padding: 10px 14px;
      font-family: "SF Mono", "Menlo", monospace;
      font-size: 0.79rem;
      color: #31413a;
    }}

    pre {{
      margin: 0;
      padding: 14px;
      background: #1c2522;
      color: #edf5ef;
      overflow-x: auto;
      font-size: 0.8rem;
      line-height: 1.55;
      font-family: "SF Mono", "Menlo", monospace;
    }}

    .evidence-note {{
      margin: 0;
      padding: 12px 14px 14px;
      color: var(--muted);
      font-size: 0.92rem;
      border-top: 1px solid var(--border);
    }}

    .empty {{
      padding: 24px;
      border: 1px dashed var(--border);
      border-radius: var(--radius-md);
      color: var(--muted);
      text-align: center;
      background: rgba(255, 255, 255, 0.45);
    }}

    @media (max-width: 720px) {{
      .shell {{
        width: min(100% - 20px, 1180px);
        margin: 18px auto 32px;
      }}

      .hero,
      .feature-head,
      .feature-body {{
        padding-left: 18px;
        padding-right: 18px;
      }}

      summary {{
        flex-direction: column;
      }}

      .criteria-side {{
        align-items: start;
        min-width: 0;
      }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <p class="eyebrow">Matcha Core</p>
      <h1>Implementation analysis report</h1>
      <p class="subtitle">A self-contained report generated from <code>SPECS.md</code> and the analyzed codebase. Use it as a local artifact, CI output, or handoff document.</p>

      <div class="meta">
        <div class="meta-card">
          <div class="meta-label">Source</div>
          <div class="meta-value">{_escape(report.source)}</div>
        </div>
        <div class="meta-card">
          <div class="meta-label">Specs file</div>
          <div class="meta-value">{_escape(report.specs_path)}</div>
        </div>
        <div class="meta-card">
          <div class="meta-label">Commit</div>
          <div class="meta-value">{_escape(report.commit_hash or "N/A")}</div>
        </div>
        <div class="meta-card">
          <div class="meta-label">Global confidence</div>
          <div class="meta-value">{_percent(report.global_confidence)}</div>
        </div>
      </div>

      <div class="summary-grid">
        {_summary_card(report.total_features, "Features")}
        {_summary_card(report.total_criteria, "Criteria")}
        {_summary_card(report.implemented_count, "Implemented", "var(--implemented)")}
        {_summary_card(report.different_count, "Different", "var(--different)")}
        {_summary_card(report.not_implemented_count, "Not Implemented", "var(--missing)")}
        {_summary_card(report.not_specified_count, "Not Specified", "var(--unspecified)")}
      </div>
    </section>

    <section class="section">
      <div class="section-header">
        <h2>Feature breakdown</h2>
        <p class="section-note">{report.total_features} feature(s) analyzed against {report.total_criteria} acceptance criteria.</p>
      </div>
      <div class="toolbar">
        <input
          class="search-input"
          id="criteria-search"
          type="search"
          placeholder="Search feature, criteria, file, or explanation"
          aria-label="Search report"
        />
        <div class="filters" aria-label="Filter criteria by status">
          <button class="filter-chip active" type="button" data-filter="all">All</button>
          <button class="filter-chip" type="button" data-filter="implemented_as_expected">Implemented</button>
          <button class="filter-chip" type="button" data-filter="implemented_differently">Different</button>
          <button class="filter-chip" type="button" data-filter="not_implemented">Missing</button>
          <button class="filter-chip" type="button" data-filter="not_specified">N/A</button>
        </div>
      </div>
      <div class="feature-list">
        {feature_cards or '<div class="empty">No features were produced for this report.</div>'}
      </div>
    </section>
  </main>
  <script>
    (() => {{
      const chips = Array.from(document.querySelectorAll('[data-filter]'));
      const searchInput = document.getElementById('criteria-search');
      const criteriaNodes = Array.from(document.querySelectorAll('[data-criteria-status]'));
      const featureNodes = Array.from(document.querySelectorAll('[data-feature-status]'));
      let activeFilter = 'all';
      let activeQuery = '';

      function applyFilters() {{
        chips.forEach((chip) => {{
          chip.classList.toggle('active', chip.dataset.filter === activeFilter);
        }});

        criteriaNodes.forEach((node) => {{
          const statusMatches = activeFilter === 'all' || node.dataset.criteriaStatus === activeFilter;
          const searchText = (node.dataset.searchText || '').toLowerCase();
          const textMatches = !activeQuery || searchText.includes(activeQuery);
          const visible = statusMatches && textMatches;
          node.style.display = visible ? '' : 'none';
        }});

        featureNodes.forEach((feature) => {{
          const visibleCriteria = feature.querySelectorAll('[data-criteria-status]');
          const anyVisible = Array.from(visibleCriteria).some((node) => node.style.display !== 'none');
          feature.style.display = anyVisible ? '' : 'none';
        }});
      }}

      chips.forEach((chip) => {{
        chip.addEventListener('click', () => {{
          activeFilter = chip.dataset.filter || 'all';
          applyFilters();
        }});
      }});

      if (searchInput) {{
        searchInput.addEventListener('input', () => {{
          activeQuery = (searchInput.value || '').trim().toLowerCase();
          applyFilters();
        }});
      }}
    }})();
  </script>
</body>
</html>
"""


def report_to_table(report: AnalysisReport, include_details: bool = False) -> str:
    headers = ["Feature", "Criteria", "Status", "Conf", "Summary"]
    rows = []
    details = []

    for feature in report.features:
        feature_name = f"{feature.feature_id}: {feature.name}".strip()
        if not feature.criteria:
            rows.append(
                [
                    feature_name,
                    "-",
                    _status_label(feature.implementation_status),
                    f"{round(feature.confidence * 100)}%",
                    _truncate(feature.description or "-", 72),
                ]
            )
            continue

        for index, criteria in enumerate(feature.criteria):
            summary_text = _truncate(criteria.short_explanation or criteria.detailed_explanation or "-", 72)
            rows.append(
                [
                    feature_name if index == 0 else "",
                    criteria.criteria_id or "-",
                    _status_label(criteria.implementation_status),
                    f"{round(criteria.confidence * 100)}%",
                    summary_text,
                ]
            )
            if include_details:
                details.append(_render_table_detail(feature_name, criteria))

    if not rows:
        rows.append(["-", "-", "-", "-", "No criteria found in report"])

    table = _render_ascii_table(headers, rows)
    summary = (
        f"Source: {report.source}\n"
        f"Specs: {report.specs_path}\n"
        f"Features: {report.total_features} | Criteria: {report.total_criteria} | "
        f"Implemented: {report.implemented_count} | Different: {report.different_count} | "
        f"Not implemented: {report.not_implemented_count} | Not specified: {report.not_specified_count} | "
        f"Global confidence: {round(report.global_confidence * 100)}%"
    )
    details_text = "\n".join(details)
    if include_details and details_text:
        return f"{summary}\n\n{table}\n\nDetails\n-------\n{details_text}\n"
    return f"{summary}\n\n{table}\n"


def _status_label(status: str) -> str:
    labels = {
        "implemented_as_expected": "Implemented",
        "implemented_differently": "Different",
        "not_implemented": "Missing",
        "not_specified": "N/A",
    }
    return labels.get(status, status or "-")


def _truncate(text: str, max_len: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_len:
        return normalized
    return f"{normalized[: max_len - 3]}..."


def _render_ascii_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))

    def _line(left: str, join: str, right: str, fill: str = "-") -> str:
        return left + join.join(fill * (width + 2) for width in widths) + right

    def _row(values: list[str]) -> str:
        cells = [f" {value.ljust(widths[idx])} " for idx, value in enumerate(values)]
        return "|" + "|".join(cells) + "|"

    lines = [
        _line("+", "+", "+"),
        _row(headers),
        _line("+", "+", "+", "="),
    ]
    lines.extend(_row(row) for row in rows)
    lines.append(_line("+", "+", "+"))
    return "\n".join(lines)


def _render_table_detail(feature_name: str, criteria) -> str:
    evidence_blocks = _parse_evidence(criteria.code_snippets)
    lines = [
        f"* {feature_name} | {criteria.criteria_id or 'AC'} | {_status_label(criteria.implementation_status)} | {_percent(criteria.confidence)}",
        f"  Requirement: {_truncate(criteria.description or '-', 160)}",
    ]

    if criteria.short_explanation:
        lines.append(f"  Summary: {_truncate(criteria.short_explanation, 220)}")
    if criteria.detailed_explanation and criteria.detailed_explanation != criteria.short_explanation:
        lines.append(f"  Detail: {_truncate(criteria.detailed_explanation, 320)}")
    if criteria.referenced_files:
        lines.append(f"  Files: {', '.join(criteria.referenced_files[:5])}")

    if evidence_blocks:
        top_evidence = evidence_blocks[:2]
        for item in top_evidence:
            location = item.get("file_path") or "unknown"
            if item.get("line_start") and item.get("line_end"):
                location = f"{location}:{item['line_start']}-{item['line_end']}"
            snippet = _truncate(" ".join((item.get("code") or "").split()), 180)
            explanation = _truncate(item.get("explanation") or "", 180)
            lines.append(f"  Evidence: {location}")
            if snippet:
                lines.append(f"    Code: {snippet}")
            if explanation:
                lines.append(f"    Why: {explanation}")
    else:
        lines.append("  Evidence: none")

    return "\n".join(lines)


def _summary_card(value: int, label: str, accent: str = "var(--text)") -> str:
    return f"""
    <div class="summary-card">
      <span class="summary-number" style="color: {accent};">{value}</span>
      <span class="summary-label">{_escape(label)}</span>
    </div>
    """


def _render_feature_card(feature) -> str:
    status = _status_meta(feature.implementation_status)
    criteria_html = "\n".join(_render_criteria(criteria) for criteria in feature.criteria)
    components = ""
    if feature.related_components:
        components = " · Components: " + ", ".join(_escape(component) for component in feature.related_components)

    return f"""
    <article class="feature-card" data-feature-status="{_escape(feature.implementation_status)}" style="--status-color: {status['color']}; --status-bg: {status['background']};">
      <div class="feature-head">
        <div class="feature-topline">
          <span class="feature-id">{_escape(feature.feature_id)}</span>
          <span class="badge">{_escape(status['label'])}</span>
        </div>
        <h3 class="feature-title">{_escape(feature.name)}</h3>
        {_paragraph(feature.description, "feature-description")}
        <div class="feature-meta">
          <span>Specs status: <strong>{_escape(feature.status or "Unknown")}</strong></span>
          <span>Priority: <strong>{_escape(feature.priority or "Unknown")}</strong></span>
          <span>Confidence: <strong>{_percent(feature.confidence)}</strong></span>
          <span>Criteria: <strong>{len(feature.criteria)}</strong></span>{components}
        </div>
      </div>
      <div class="feature-body">
        <div class="criteria-list">
          {criteria_html or '<div class="empty">No acceptance criteria found for this feature.</div>'}
        </div>
      </div>
    </article>
    """


def _render_criteria(criteria) -> str:
    status = _status_meta(criteria.implementation_status)
    evidence_blocks = _parse_evidence(criteria.code_snippets)
    evidence_html = "\n".join(_render_evidence_block(item) for item in evidence_blocks)
    search_text = _build_search_text(criteria, evidence_blocks)

    files_html = ""
    if criteria.referenced_files:
        file_chips = "".join(f'<span class="file-chip">{_escape(path)}</span>' for path in criteria.referenced_files)
        files_html = f'<div class="files">{file_chips}</div>'

    return f"""
    <details class="criteria" data-criteria-status="{_escape(criteria.implementation_status)}" data-search-text="{_escape(search_text)}">
      <summary>
        <h4 class="criteria-title">{_escape(criteria.description)}</h4>
        <div class="criteria-side">
          <span class="badge" style="background: {status['background']}; color: {status['color']};">{_escape(status['label'])}</span>
          <span class="meta-label" style="margin: 0;">{_percent(criteria.confidence)}</span>
        </div>
      </summary>
      <div class="criteria-content">
        <div class="criteria-grid">
          <div class="mini-card">
            <span class="mini-label">Criteria ID</span>
            <div>{_escape(criteria.criteria_id or "AC")}</div>
          </div>
          <div class="mini-card">
            <span class="mini-label">Result</span>
            <div>{_escape(criteria.implementation_status)}</div>
          </div>
          <div class="mini-card">
            <span class="mini-label">Confidence</span>
            <div>{_percent(criteria.confidence)}</div>
          </div>
        </div>
        {_paragraph(criteria.short_explanation, "")}
        {_paragraph(criteria.detailed_explanation, "")}
        {files_html}
        {'<div class="evidence">' + evidence_html + '</div>' if evidence_html else ''}
      </div>
    </details>
    """


def _render_evidence_block(item: dict) -> str:
    path = _escape(item.get("file_path") or "Unknown")
    start = item.get("line_start")
    end = item.get("line_end")
    line_info = ""
    if start:
        line_info = f":{start}"
        if end and end != start:
            line_info += f"-{end}"

    explanation = _escape(item.get("explanation") or "")
    code = _escape(item.get("code") or "")

    return f"""
    <section class="evidence-card">
      <div class="evidence-head">{path}{line_info}</div>
      <pre><code>{code}</code></pre>
      {f'<p class="evidence-note">{explanation}</p>' if explanation else ''}
    </section>
    """


def _build_search_text(criteria, evidence_blocks: list[dict]) -> str:
    parts = [
        criteria.criteria_id or "",
        criteria.description or "",
        criteria.short_explanation or "",
        criteria.detailed_explanation or "",
        " ".join(criteria.referenced_files or []),
    ]
    for item in evidence_blocks:
        parts.extend(
            [
                item.get("file_path") or "",
                item.get("explanation") or "",
                item.get("code") or "",
            ]
        )
    return " ".join(part for part in parts if part)


def _parse_evidence(raw: str) -> list[dict]:
    if not raw:
      return []

    try:
      parsed = json.loads(raw)
    except json.JSONDecodeError:
      return [{"file_path": "Source", "code": raw, "explanation": ""}]

    if isinstance(parsed, list):
      return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
      return [parsed]
    return []


def _paragraph(text: str, class_name: str) -> str:
    if not text:
        return ""
    class_attr = f' class="{class_name}"' if class_name else ""
    return f"<p{class_attr}>{_escape(text)}</p>"


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


def _percent(value: float) -> str:
    return f"{round((value or 0) * 100)}%"


def _status_meta(status: str) -> dict:
    mapping = {
        "implemented_as_expected": {
            "label": "Implemented",
            "color": "var(--implemented)",
            "background": "var(--implemented-bg)",
        },
        "implemented_differently": {
            "label": "Different",
            "color": "var(--different)",
            "background": "var(--different-bg)",
        },
        "not_implemented": {
            "label": "Not Implemented",
            "color": "var(--missing)",
            "background": "var(--missing-bg)",
        },
        "not_specified": {
            "label": "Not Specified",
            "color": "var(--unspecified)",
            "background": "var(--unspecified-bg)",
        },
    }
    return mapping.get(status, mapping["not_specified"])
