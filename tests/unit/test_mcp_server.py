"""Unit tests for the solopreneur MCP server tools.

These tests validate standalone, stateless tool functions that do NOT require
the SQLite database or ChromaDB to be populated.
"""
import json
import sys
import os

import pytest

# Ensure the project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture(autouse=True)
def set_db_env(monkeypatch):
    """Provide a default DATABASE_URL so settings.py can load."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///tax_data.db")


@pytest.fixture(scope="module")
def server():
    """Import the MCP server module once for the test session."""
    os.environ.setdefault("DATABASE_URL", "sqlite:///tax_data.db")
    import mcp_server as srv
    return srv


# ---------------------------------------------------------------------------
# calculate_se_tax
# ---------------------------------------------------------------------------

class TestCalculateSeTax:
    def test_basic_calculation(self, server):
        result = server.calculate_se_tax(85_000)
        assert result["net_se_income"] == 85_000.0
        # SE tax base should be 85000 * 0.9235
        assert abs(result["se_tax_base_92_35_pct"] - 78_497.50) < 1
        # Total SE tax should be positive
        assert result["total_se_tax"] > 0
        # Deductible half should be exactly 50% of total SE tax
        assert abs(result["deductible_half_above_line"] - result["total_se_tax"] / 2) < 0.02

    def test_zero_income(self, server):
        result = server.calculate_se_tax(0)
        assert result["total_se_tax"] == 0.0
        assert result["deductible_half_above_line"] == 0.0

    def test_negative_income_clamped(self, server):
        result = server.calculate_se_tax(-10_000)
        assert result["total_se_tax"] == 0.0

    def test_has_meta_ui_resource(self, server):
        result = server.calculate_se_tax(50_000)
        assert "_meta" in result
        assert result["_meta"]["ui"]["resourceUri"].startswith("ui://")
        assert "_ui_resource" in result

    def test_above_ss_wage_base(self, server):
        """Income above the SS wage base should only add Medicare on the excess."""
        result_high = server.calculate_se_tax(400_000)
        result_low = server.calculate_se_tax(100_000)
        # Effective rate should be lower for very high earners (SS cap effect)
        assert result_high["effective_se_rate_pct"] < result_low["effective_se_rate_pct"]


# ---------------------------------------------------------------------------
# get_solopreneur_deductions
# ---------------------------------------------------------------------------

class TestGetSolopreneurDeductions:
    def test_all_categories(self, server):
        result = server.get_solopreneur_deductions("all")
        deductions = result["deductions"]
        expected_keys = {"home_office", "vehicle", "health_insurance", "retirement", "qbi", "se_tax"}
        assert expected_keys.issubset(set(deductions.keys()))

    def test_single_category(self, server):
        result = server.get_solopreneur_deductions("qbi")
        assert "qbi" in result["deductions"]
        assert "199A" in result["deductions"]["qbi"]["us_code"]

    def test_unknown_category_returns_error(self, server):
        result = server.get_solopreneur_deductions("nonexistent")
        assert "error" in result["deductions"]

    def test_has_turbotax_path(self, server):
        result = server.get_solopreneur_deductions("home_office")
        assert "turbotax_path" in result["deductions"]["home_office"]

    def test_has_meta_ui_resource(self, server):
        result = server.get_solopreneur_deductions("vehicle")
        assert result["_meta"]["ui"]["resourceUri"].startswith("ui://")


# ---------------------------------------------------------------------------
# get_quarterly_estimated_tax_guide
# ---------------------------------------------------------------------------

class TestGetQuarterlyEstimatedTaxGuide:
    def test_all_quarters_present(self, server):
        result = server.get_quarterly_estimated_tax_guide()
        assert set(result["due_dates"].keys()) == {"Q1", "Q2", "Q3", "Q4"}

    def test_form_reference(self, server):
        result = server.get_quarterly_estimated_tax_guide()
        assert result["form"] == "Form 1040-ES"

    def test_safe_harbor_mentioned(self, server):
        result = server.get_quarterly_estimated_tax_guide()
        assert "safe_harbor" in result
        assert "110%" in result["safe_harbor"]

    def test_has_meta_ui_resource(self, server):
        result = server.get_quarterly_estimated_tax_guide()
        assert result["_meta"]["ui"]["resourceUri"].startswith("ui://")


# ---------------------------------------------------------------------------
# analyze_tax_text_complexity
# ---------------------------------------------------------------------------

class TestAnalyzeTaxTextComplexity:
    def test_complex_legal_text(self, server):
        text = (
            "Notwithstanding any other provision of this subchapter, the adjusted "
            "basis for determining the gain or loss from the sale or other disposition "
            "of property, whenever acquired, shall be the basis determined under "
            "section 1012 or other applicable sections of this subchapter, adjusted "
            "as provided in section 1016."
        )
        result = server.analyze_tax_text_complexity(text)
        assert "flesch_reading_ease" in result
        assert isinstance(result["flesch_reading_ease"], float)
        assert "complexity_level" in result

    def test_short_text_returns_zero(self, server):
        result = server.analyze_tax_text_complexity("Too short.")
        assert result["flesch_reading_ease"] == 0.0

    def test_has_meta_ui_resource(self, server):
        result = server.analyze_tax_text_complexity(
            "The deduction shall be allowed for all ordinary and necessary business expenses."
        )
        assert result["_meta"]["ui"]["resourceUri"].startswith("ui://")


# ---------------------------------------------------------------------------
# Static MCP resources
# ---------------------------------------------------------------------------

class TestStaticResources:
    def test_schedule_c_checklist_is_valid_json(self, server):
        raw = server.schedule_c_checklist()
        data = json.loads(raw)
        assert "income_items" in data
        assert "expense_items" in data
        assert "key_attachments" in data

    def test_turbotax_tips_is_valid_json(self, server):
        raw = server.turbotax_business_tips()
        data = json.loads(raw)
        assert "tips" in data
        assert len(data["tips"]) > 0
        for tip in data["tips"]:
            assert "category" in tip
            assert "tip" in tip
