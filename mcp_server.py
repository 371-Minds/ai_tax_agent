#!/usr/bin/env python
"""MCP server for AI Tax Agent – optimised for solopreneur / TurboTax Business filers.

Exposes tax-analysis tools as MCP tools with rich HTML UI resources built with
the mcp-ui-server SDK (https://github.com/MCP-UI-Org/mcp-ui) and compliant with
the MCP Apps standard (https://github.com/modelcontextprotocol/ext-apps).

Run:
    poetry run python mcp_server.py
or for HTTP transport:
    poetry run python mcp_server.py --transport streamable-http --port 8080
"""

import argparse
import json
import logging
from typing import Any

import textstat
from mcp.server.fastmcp import FastMCP
from mcp_ui_server import create_ui_resource

from ai_tax_agent.tools.db_tools import get_section_details_and_stats

try:
    from ai_tax_agent.tools.chroma_tools import query_form_instructions
except Exception:  # ChromaDB / embeddings not available in all environments
    def query_form_instructions(query: str):  # type: ignore[misc]
        return f"ChromaDB not available: {query}"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastMCP server instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "AI Tax Agent – Solopreneur Edition",
    instructions=(
        "You are an expert tax assistant specialised in TurboTax Business for "
        "solopreneurs (sole proprietors, freelancers, and single-member LLC owners). "
        "Help users understand Schedule C deductions, self-employment tax (SE tax), "
        "home-office deductions, vehicle expenses, QBI deduction (Section 199A), "
        "retirement contributions (SEP-IRA / Solo 401k), and quarterly estimated "
        "payments.  Always reference the relevant IRS form field or US Code section "
        "when providing guidance."
    ),
)

# ---------------------------------------------------------------------------
# Helper: build a simple styled HTML dashboard card
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{title}</title>
<style>
  body{{font-family:system-ui,sans-serif;margin:0;padding:16px;background:#f8fafc;color:#1e293b}}
  h2{{margin:0 0 12px;font-size:1.1rem;color:#0f172a}}
  .card{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:16px;
         margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
  .label{{font-size:.75rem;font-weight:600;text-transform:uppercase;color:#64748b;
          letter-spacing:.05em;margin-bottom:4px}}
  .value{{font-size:.95rem;color:#0f172a}}
  .chip{{display:inline-block;padding:2px 8px;border-radius:9999px;font-size:.75rem;
         font-weight:600;margin-right:4px}}
  .green{{background:#dcfce7;color:#166534}}
  .yellow{{background:#fef9c3;color:#854d0e}}
  .red{{background:#fee2e2;color:#991b1b}}
  ul{{margin:6px 0;padding-left:18px}}
  li{{margin-bottom:4px;font-size:.9rem}}
  a{{color:#2563eb;text-decoration:none}}
  a:hover{{text-decoration:underline}}
</style>
</head>
<body>
{body}
</body>
</html>"""


def _html_page(title: str, body: str) -> str:
    return _HTML_TEMPLATE.format(title=title, body=body)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def get_solopreneur_deductions(category: str = "all") -> dict[str, Any]:
    """Return key Schedule C / solopreneur deductions with IRS form references.

    Args:
        category: Filter by category. One of: "all", "home_office", "vehicle",
                  "health_insurance", "retirement", "qbi", "se_tax".
    """
    deductions: dict[str, Any] = {
        "home_office": {
            "name": "Home-Office Deduction",
            "form": "Form 8829 / Schedule C line 30",
            "us_code": "Sec. 280A",
            "methods": ["Simplified ($5/sq ft, max 300 sq ft)", "Regular (actual expenses)"],
            "turbotax_path": "Business > Business Expenses > Home Office",
            "tip": "Must be used *regularly and exclusively* for business.",
        },
        "vehicle": {
            "name": "Vehicle / Auto Expenses",
            "form": "Schedule C Part IV + Form 4562",
            "us_code": "Sec. 179, Sec. 168(k)",
            "methods": [
                "Standard mileage rate (check IRS for current year rate)",
                "Actual expense method",
            ],
            "turbotax_path": "Business > Business Expenses > Vehicle",
            "tip": "Keep a mileage log. Cannot switch methods after first year.",
        },
        "health_insurance": {
            "name": "Self-Employed Health Insurance Deduction",
            "form": "Schedule 1 line 17 (above-the-line)",
            "us_code": "Sec. 162(l)",
            "turbotax_path": "Deductions & Credits > Health Insurance",
            "tip": "Cannot exceed net self-employment income for the year.",
        },
        "retirement": {
            "name": "SEP-IRA / Solo 401(k) Contributions",
            "form": "Schedule 1 line 16",
            "us_code": "Sec. 404, Sec. 408",
            "limits": {
                "SEP-IRA": "25% of net self-employment income (max ~$69k for 2024)",
                "Solo 401(k)": "Employee deferral + 25% employer contribution",
            },
            "turbotax_path": "Deductions & Credits > Retirement Savings",
        },
        "qbi": {
            "name": "Qualified Business Income (QBI) Deduction",
            "form": "Form 8995",
            "us_code": "Sec. 199A",
            "amount": "Up to 20% of qualified business income",
            "turbotax_path": "Business > Qualified Business Income",
            "tip": "Phase-out applies above income thresholds for SSTBs.",
        },
        "se_tax": {
            "name": "Deductible Portion of Self-Employment Tax",
            "form": "Schedule 1 line 15",
            "us_code": "Sec. 164(f)",
            "amount": "50% of SE tax is deductible above-the-line",
            "turbotax_path": "Automatically calculated",
        },
    }

    if category == "all":
        result = deductions
    elif category in deductions:
        result = {category: deductions[category]}
    else:
        result = {"error": f"Unknown category '{category}'. Use one of: {list(deductions.keys())}"}

    # Build MCP-UI HTML resource
    cards_html = ""
    for key, d in (result.items() if isinstance(result, dict) and "error" not in result else []):
        form_ref = d.get("form", "")
        tip = d.get("tip", "")
        tip_html = f"<p><em>💡 {tip}</em></p>" if tip else ""
        cards_html += f"""
        <div class="card">
          <h2>{d.get('name', key)}</h2>
          <div class="label">Form Reference</div>
          <div class="value">{form_ref}</div>
          <div class="label" style="margin-top:8px">US Code</div>
          <div class="value">{d.get('us_code','')}</div>
          <div class="label" style="margin-top:8px">TurboTax Path</div>
          <div class="value">{d.get('turbotax_path','')}</div>
          {tip_html}
        </div>"""

    if "error" in result:
        cards_html = f'<div class="card"><p style="color:red">{result["error"]}</p></div>'

    html_body = f"<h2>📋 Solopreneur Deductions – {category.replace('_',' ').title()}</h2>{cards_html}"
    ui_resource = create_ui_resource(
        {
            "uri": f"ui://tax-agent/deductions/{category}",
            "content": {"type": "rawHtml", "htmlString": _html_page("Solopreneur Deductions", html_body)},
            "encoding": "text",
            "uiMetadata": {"preferred-frame-size": [520, 500]},
        }
    )

    return {
        "deductions": result,
        "_meta": {"ui": {"resourceUri": str(ui_resource.resource.uri)}},
        "_ui_resource": ui_resource.model_dump(),
    }


@mcp.tool()
def calculate_se_tax(net_self_employment_income: float) -> dict[str, Any]:
    """Calculate self-employment (SE) tax and the above-the-line deduction for a solopreneur.

    Args:
        net_self_employment_income: Net profit from Schedule C (after business expenses).
    """
    if net_self_employment_income < 0:
        net_self_employment_income = 0.0

    # SE tax calculation per IRS rules
    se_tax_base = net_self_employment_income * 0.9235  # multiply by 92.35%
    ss_wage_base = 168_600  # 2024 Social Security wage base

    ss_portion = min(se_tax_base, ss_wage_base) * 0.124
    medicare_portion = se_tax_base * 0.029
    se_tax_total = ss_portion + medicare_portion

    deductible_half = se_tax_total * 0.5

    result = {
        "net_se_income": round(net_self_employment_income, 2),
        "se_tax_base_92_35_pct": round(se_tax_base, 2),
        "social_security_portion": round(ss_portion, 2),
        "medicare_portion": round(medicare_portion, 2),
        "total_se_tax": round(se_tax_total, 2),
        "deductible_half_above_line": round(deductible_half, 2),
        "effective_se_rate_pct": round((se_tax_total / net_self_employment_income * 100) if net_self_employment_income else 0, 2),
        "turbotax_path": "Schedule SE is auto-calculated; deduction appears on Schedule 1 line 15",
        "us_code": "Sec. 1401–1403, Sec. 164(f)",
        "note": (
            "Additional 0.9% Medicare surtax applies on wages/SE income above "
            "$200,000 (single) / $250,000 (MFJ). Not included above."
        ),
    }

    # Build MCP-UI HTML
    rate_chip_class = "green" if result["effective_se_rate_pct"] < 14 else "yellow"
    html_body = f"""
    <div class="card">
      <h2>💼 Self-Employment Tax Calculator</h2>
      <div class="label">Net SE Income</div>
      <div class="value">${result['net_se_income']:,.2f}</div>
      <div class="label" style="margin-top:8px">SE Tax Base (× 92.35%)</div>
      <div class="value">${result['se_tax_base_92_35_pct']:,.2f}</div>
    </div>
    <div class="card">
      <h2>📊 Tax Breakdown</h2>
      <div class="label">Social Security (12.4%)</div>
      <div class="value">${result['social_security_portion']:,.2f}</div>
      <div class="label" style="margin-top:8px">Medicare (2.9%)</div>
      <div class="value">${result['medicare_portion']:,.2f}</div>
      <div class="label" style="margin-top:8px">Total SE Tax</div>
      <div class="value" style="font-size:1.2rem;font-weight:700">${result['total_se_tax']:,.2f}
        <span class="chip {rate_chip_class}">{result['effective_se_rate_pct']}%</span>
      </div>
    </div>
    <div class="card">
      <h2>✅ Above-the-Line Deduction</h2>
      <div class="label">50% of SE Tax (Schedule 1 line 15)</div>
      <div class="value" style="color:#166534;font-weight:700">${result['deductible_half_above_line']:,.2f}</div>
      <p style="font-size:.8rem;color:#64748b">{result['note']}</p>
    </div>
    """

    ui_resource = create_ui_resource(
        {
            "uri": "ui://tax-agent/se-tax-calculator",
            "content": {"type": "rawHtml", "htmlString": _html_page("SE Tax Calculator", html_body)},
            "encoding": "text",
            "uiMetadata": {"preferred-frame-size": [480, 520]},
        }
    )

    return {
        **result,
        "_meta": {"ui": {"resourceUri": str(ui_resource.resource.uri)}},
        "_ui_resource": ui_resource.model_dump(),
    }


@mcp.tool()
def get_tax_section_details(section_identifier: str) -> dict[str, Any]:
    """Retrieve details, form-field links, and financial statistics for a US Code section.

    Useful for understanding the impact and context of sections relevant to
    solopreneurs (e.g. '162', '179', '199A', '280A').

    Args:
        section_identifier: A US Code section number (e.g. '162') or primary-key ID.
    """
    raw = get_section_details_and_stats(section_identifier)
    if isinstance(raw, str):
        return {"error": raw}

    # Build MCP-UI HTML
    agg = raw.get("aggregation_summary", {})
    fields_html = ""
    for f in raw.get("linked_form_fields", [])[:10]:  # cap at 10 for readability
        fields_html += (
            f"<li><strong>{f['label']}</strong> ({f['form_number']}) – "
            f"${f['stats_dollars']:,.0f} / {f['stats_forms']:,.0f} forms</li>"
        )

    exemptions_html = "".join(
        f"<li>{ex['relevant_text'][:120]}…</li>"
        for ex in raw.get("exemptions", [])[:5]
    ) or "<li>None found</li>"

    html_body = f"""
    <div class="card">
      <h2>§ {raw.get('section_identifier','')} – {raw.get('section_title','')}</h2>
      <div class="label">Aggregate Impact</div>
      <div class="value">
        <span class="chip green">${agg.get('total_dollars',0):,.0f}</span>
        <span class="chip yellow">{agg.get('total_forms',0):,.0f} forms</span>
        <span class="chip yellow">{agg.get('total_people',0):,.0f} people</span>
      </div>
    </div>
    <div class="card">
      <h2>📄 Linked Form Fields (top 10)</h2>
      <ul>{fields_html or '<li>No linked fields</li>'}</ul>
    </div>
    <div class="card">
      <h2>🔍 Exemptions</h2>
      <ul>{exemptions_html}</ul>
    </div>
    """

    ui_resource = create_ui_resource(
        {
            "uri": f"ui://tax-agent/section/{section_identifier}",
            "content": {"type": "rawHtml", "htmlString": _html_page(f"Section {section_identifier}", html_body)},
            "encoding": "text",
            "uiMetadata": {"preferred-frame-size": [540, 600]},
        }
    )

    return {
        **raw,
        "_meta": {"ui": {"resourceUri": str(ui_resource.resource.uri)}},
        "_ui_resource": ui_resource.model_dump(),
    }


@mcp.tool()
def search_schedule_c_instructions(query: str) -> dict[str, Any]:
    """Search IRS form instructions relevant to Schedule C / solopreneur filing.

    Args:
        query: Natural-language query, e.g. 'home office regular and exclusive use'.
    """
    raw_result = query_form_instructions(query)
    if isinstance(raw_result, str):
        matches: list[dict[str, Any]] = []
        error = raw_result
    else:
        matches = raw_result if isinstance(raw_result, list) else [raw_result]
        error = None

    items_html = ""
    for i, item in enumerate(matches[:8], 1):
        if isinstance(item, dict):
            label = item.get("field_label", item.get("label", f"Result {i}"))
            form = item.get("form_number", "")
            text = str(item.get("full_text", item.get("text", "")))[:200]
        else:
            label = f"Result {i}"
            form = ""
            text = str(item)[:200]
        items_html += f"""
        <div class="card">
          <div class="label">{form}</div>
          <div class="value"><strong>{label}</strong></div>
          <p style="font-size:.85rem;color:#334155;margin:4px 0 0">{text}…</p>
        </div>"""

    if error:
        items_html = f'<div class="card"><p style="color:red">{error}</p></div>'
    elif not items_html:
        items_html = '<div class="card"><p>No results found. Try a broader query.</p></div>'

    html_body = f"""
    <h2>🔎 Schedule C / Form Instructions Search</h2>
    <p style="font-size:.85rem;color:#64748b">Query: <em>{query}</em></p>
    {items_html}
    """

    ui_resource = create_ui_resource(
        {
            "uri": "ui://tax-agent/search/schedule-c",
            "content": {"type": "rawHtml", "htmlString": _html_page("Schedule C Search", html_body)},
            "encoding": "text",
            "uiMetadata": {"preferred-frame-size": [540, 600]},
        }
    )

    return {
        "query": query,
        "results": matches,
        "error": error,
        "_meta": {"ui": {"resourceUri": str(ui_resource.resource.uri)}},
        "_ui_resource": ui_resource.model_dump(),
    }


@mcp.tool()
def analyze_tax_text_complexity(text: str) -> dict[str, Any]:
    """Estimate the reading complexity of a tax provision or instruction.

    Returns the Flesch Reading Ease score (lower = more complex) plus a
    plain-English summary.

    Args:
        text: The tax code text or instruction to analyse.
    """
    if not text or len(text.split()) < 10:
        score = 0.0
    else:
        try:
            score = float(textstat.flesch_reading_ease(text))
        except Exception:
            score = 0.0

    if score >= 60:
        level, chip_class = "Easy to read", "green"
    elif score >= 30:
        level, chip_class = "Moderately complex", "yellow"
    else:
        level, chip_class = "Very complex / technical", "red"

    html_body = f"""
    <div class="card">
      <h2>📊 Reading Complexity Analysis</h2>
      <div class="label">Flesch Reading Ease Score</div>
      <div class="value" style="font-size:1.4rem;font-weight:700">
        {score:.1f}
        <span class="chip {chip_class}">{level}</span>
      </div>
      <p style="font-size:.8rem;color:#64748b;margin-top:8px">
        100 = very easy · 0 = very difficult.
        Plain English averages 60–70.
      </p>
    </div>
    <div class="card">
      <h2>📝 Analysed Text (preview)</h2>
      <p style="font-size:.85rem;color:#334155">{text[:400]}{"…" if len(text)>400 else ""}</p>
    </div>
    """

    ui_resource = create_ui_resource(
        {
            "uri": "ui://tax-agent/complexity",
            "content": {"type": "rawHtml", "htmlString": _html_page("Complexity Analysis", html_body)},
            "encoding": "text",
            "uiMetadata": {"preferred-frame-size": [480, 400]},
        }
    )

    return {
        "flesch_reading_ease": round(score, 2),
        "complexity_level": level,
        "text_preview": text[:200],
        "_meta": {"ui": {"resourceUri": str(ui_resource.resource.uri)}},
        "_ui_resource": ui_resource.model_dump(),
    }


@mcp.tool()
def get_quarterly_estimated_tax_guide() -> dict[str, Any]:
    """Return a guide for solopreneurs on quarterly estimated tax payments (Form 1040-ES)."""
    guide = {
        "overview": (
            "Solopreneurs must pay federal estimated taxes quarterly if they expect "
            "to owe at least $1,000 after withholding and credits."
        ),
        "due_dates": {
            "Q1": "April 15 (income Jan 1 – Mar 31)",
            "Q2": "June 15 (income Apr 1 – May 31)",
            "Q3": "September 15 (income Jun 1 – Aug 31)",
            "Q4": "January 15 of following year (income Sep 1 – Dec 31)",
        },
        "safe_harbor": (
            "Avoid penalties by paying the lesser of: (a) 100% of prior-year tax "
            "liability, or (b) 90% of current-year tax liability. "
            "Higher-income taxpayers (AGI > $150k) must pay 110% of prior-year tax."
        ),
        "form": "Form 1040-ES",
        "us_code": "Sec. 6654",
        "turbotax_path": "File > Estimated Taxes > Form 1040-ES Vouchers",
        "payment_methods": [
            "IRS Direct Pay (free)",
            "EFTPS (Electronic Federal Tax Payment System)",
            "IRS2Go mobile app",
            "Check payable to 'United States Treasury'",
        ],
    }

    quarters_html = "".join(
        f"<li><strong>{q}</strong>: {d}</li>"
        for q, d in guide["due_dates"].items()
    )
    methods_html = "".join(f"<li>{m}</li>" for m in guide["payment_methods"])

    html_body = f"""
    <div class="card">
      <h2>📅 Quarterly Estimated Tax Payments</h2>
      <div class="label">Form / US Code</div>
      <div class="value">{guide['form']} · {guide['us_code']}</div>
      <div class="label" style="margin-top:8px">Overview</div>
      <div class="value">{guide['overview']}</div>
    </div>
    <div class="card">
      <h2>🗓️ Due Dates</h2>
      <ul>{quarters_html}</ul>
    </div>
    <div class="card">
      <h2>🛡️ Safe Harbor Rule</h2>
      <div class="value">{guide['safe_harbor']}</div>
    </div>
    <div class="card">
      <h2>💳 Payment Methods</h2>
      <ul>{methods_html}</ul>
    </div>
    """

    ui_resource = create_ui_resource(
        {
            "uri": "ui://tax-agent/estimated-tax-guide",
            "content": {"type": "rawHtml", "htmlString": _html_page("Estimated Tax Guide", html_body)},
            "encoding": "text",
            "uiMetadata": {"preferred-frame-size": [520, 580]},
        }
    )

    return {
        **guide,
        "_meta": {"ui": {"resourceUri": str(ui_resource.resource.uri)}},
        "_ui_resource": ui_resource.model_dump(),
    }


# ---------------------------------------------------------------------------
# Resources (static reference data)
# ---------------------------------------------------------------------------


@mcp.resource("tax://solopreneur/schedule-c-checklist")
def schedule_c_checklist() -> str:
    """A checklist of common Schedule C line items for solopreneurs."""
    return json.dumps(
        {
            "title": "Schedule C Filing Checklist – Solopreneur",
            "income_items": [
                "Line 1: Gross receipts / sales",
                "Line 2: Returns and allowances",
                "Line 4: Cost of goods sold (if applicable)",
            ],
            "expense_items": [
                "Line 8: Advertising",
                "Line 9: Car / truck (Form 4562 or standard mileage)",
                "Line 10: Commissions and fees",
                "Line 11: Contract labor",
                "Line 13: Depreciation (Form 4562, Sec. 179 / bonus)",
                "Line 14: Employee benefit programs",
                "Line 15: Insurance (other than health)",
                "Line 16: Interest",
                "Line 17: Legal / professional services",
                "Line 18: Office expenses",
                "Line 19: Pension / profit-sharing (SEP-IRA / Solo 401k)",
                "Line 20: Rent / lease (vehicles, machinery, property)",
                "Line 21: Repairs / maintenance",
                "Line 22: Supplies",
                "Line 23: Taxes / licenses",
                "Line 24: Travel / meals (meals at 50%)",
                "Line 25: Utilities",
                "Line 26: Wages",
                "Line 27a: Other expenses (home office via Form 8829 on line 30)",
            ],
            "key_attachments": [
                "Form 8829 – Home-office deduction",
                "Form 4562 – Depreciation and amortization",
                "Schedule SE – Self-employment tax",
                "Form 1040-ES – Estimated taxes",
                "Form 8995 – QBI deduction",
            ],
        },
        indent=2,
    )


@mcp.resource("tax://solopreneur/turbotax-business-tips")
def turbotax_business_tips() -> str:
    """Top TurboTax Business tips for solopreneurs."""
    return json.dumps(
        {
            "title": "TurboTax Business Tips for Solopreneurs",
            "tips": [
                {
                    "category": "Setup",
                    "tip": "Choose 'Self-Employed' or 'Business' edition for Schedule C support.",
                },
                {
                    "category": "Income",
                    "tip": "Import 1099-NEC / 1099-K forms directly via TurboTax bank connect.",
                },
                {
                    "category": "Deductions",
                    "tip": "Use TurboTax's 'Self-Employment Tax' section to auto-calculate SE tax and the 50% deduction.",
                },
                {
                    "category": "Home Office",
                    "tip": "TurboTax guides you through both simplified and actual-expense methods; compare both.",
                },
                {
                    "category": "Vehicle",
                    "tip": "Log total and business miles; TurboTax calculates the standard mileage deduction.",
                },
                {
                    "category": "QBI",
                    "tip": "TurboTax auto-generates Form 8995 if you qualify; verify your business type is not an SSTB.",
                },
                {
                    "category": "Retirement",
                    "tip": "Enter SEP-IRA / Solo 401k contributions under 'Self-Employed Retirement Plans'.",
                },
                {
                    "category": "Estimated Tax",
                    "tip": "Use TurboTax's '2024 Tax Estimator' during the year to avoid underpayment penalties.",
                },
            ],
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="AI Tax Agent MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="HTTP port (only used with streamable-http transport)",
    )
    args = parser.parse_args()

    if args.transport == "streamable-http":
        mcp.run(transport="streamable-http", host="0.0.0.0", port=args.port)
    else:
        mcp.run(transport="stdio")
