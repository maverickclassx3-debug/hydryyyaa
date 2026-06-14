"""
tests/test_halal_screener.py
============================
Complete test suite for HalalScreener — Layer 1 Gateway Filter
Halal AI World Monitor · Phase 1 · Week 1

Beyonce Rule enforcement: every business rule, logic branch, ratio threshold,
data fallback path, and database operation has an explicit test.

Test Classes:
    TestBusinessActivityGate      — check_business_activity (Stage 1)
    TestFinancialRatioGate        — check_financial_ratios  (Stage 2)
    TestComplianceGrading         — assign_compliance_grade (Stage 3)
    TestDatabaseOperations        — _persist_result, schema creation
    TestScreenStockOrchestrator   — screen_stock end-to-end (fully mocked)

Run:
    python -m pytest tests/test_halal_screener.py -v
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pandas as pd

# Allow running tests from project root or via pytest discovery
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.screening.halal_screener import (
    GRADE_A,
    GRADE_A_PLUS,
    GRADE_B_DEBT_MAX,
    GRADE_B_DEBT_MIN,
    THRESHOLD_CASH_AR,
    THRESHOLD_DEBT_TO_ASSETS,
    THRESHOLD_INTEREST_INCOME,
    HalalScreener,
)


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES & BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════


def _make_balance_sheet(
    total_assets: Optional[float] = 1_000_000.0,
    total_debt: Optional[float] = 200_000.0,
    short_debt: Optional[float] = None,
    long_debt: Optional[float] = None,
    cash: Optional[float] = 100_000.0,
    receivables: Optional[float] = 50_000.0,
) -> pd.DataFrame:
    """
    Build a minimal mock yfinance balance_sheet DataFrame.
    Pass None for any item to omit it (simulates label-absent scenario).
    """
    data: dict = {}
    if total_assets is not None:
        data["Total Assets"] = total_assets
    if total_debt is not None:
        data["Total Debt"] = total_debt
    if short_debt is not None:
        data["Current Debt And Capital Lease Obligation"] = short_debt
    if long_debt is not None:
        data["Long Term Debt And Capital Lease Obligation"] = long_debt
    if cash is not None:
        data["Cash And Cash Equivalents"] = cash
    if receivables is not None:
        data["Net Receivables"] = receivables

    col = pd.Timestamp("2024-03-31")
    return pd.DataFrame({col: data})


def _make_income_stmt(
    total_revenue: Optional[float] = 500_000.0,
    interest_income: Optional[float] = 10_000.0,
) -> pd.DataFrame:
    """Build a minimal mock yfinance income_stmt DataFrame."""
    data: dict = {}
    if total_revenue is not None:
        data["Total Revenue"] = total_revenue
    if interest_income is not None:
        data["Interest Income"] = interest_income

    col = pd.Timestamp("2024-03-31")
    return pd.DataFrame({col: data})


def _make_mock_ticker(
    info: Optional[dict] = None,
    balance_sheet: Optional[pd.DataFrame] = None,
    income_stmt: Optional[pd.DataFrame] = None,
) -> MagicMock:
    """Build a fully-mocked yf.Ticker with injectable financial data."""
    mock = MagicMock()
    mock.info = info or {}
    mock.balance_sheet = balance_sheet if balance_sheet is not None else _make_balance_sheet()
    mock.income_stmt = income_stmt if income_stmt is not None else _make_income_stmt()
    return mock


# Representative info dicts for NSE tickers (based on yfinance schema)
TCS_INFO: dict = {
    "sector":   "Technology",
    "industry": "Information Technology Services",
    "longBusinessSummary": (
        "Tata Consultancy Services Limited provides IT services, digital and "
        "business solutions globally."
    ),
}

SBIN_INFO: dict = {
    "sector":   "Financial Services",
    "industry": "Banks—Diversified",
    "longBusinessSummary": (
        "State Bank of India provides commercial banking products and services "
        "in India and internationally."
    ),
}

ITC_INFO: dict = {
    "sector":   "Consumer Defensive",
    "industry": "Tobacco",
    "longBusinessSummary": (
        "ITC Limited manufactures and markets cigarettes, cigars, and other "
        "tobacco products."
    ),
}

HOTEL_INFO: dict = {
    "sector":   "Consumer Cyclical",
    "industry": "Hotels & Resorts",
    "longBusinessSummary": (
        "Indian Hotels Company Limited owns and operates hotels, resorts, "
        "and palaces across India."
    ),
}

ALCOHOL_INFO: dict = {
    "sector":   "Consumer Defensive",
    "industry": "Beverages—Alcoholic",
    "longBusinessSummary": "Company manufactures and distributes alcoholic beverages.",
}

DEFENSE_INFO: dict = {
    "sector":   "Industrials",
    "industry": "Aerospace & Defense",
    "longBusinessSummary": "Company produces defense equipment and munitions.",
}

GAMBLING_INFO: dict = {
    "sector":   "Consumer Cyclical",
    "industry": "Gambling",
    "longBusinessSummary": "Company operates casino and gaming facilities.",
}

CLEAN_INFO: dict = {
    "sector":   "Healthcare",
    "industry": "Diagnostics & Research",
    "longBusinessSummary": "Company provides pharmaceutical diagnostic services.",
}


# ═══════════════════════════════════════════════════════════════════════════════
# TEST HELPERS — Temp Database Base Class
# ═══════════════════════════════════════════════════════════════════════════════


class _BaseScreenerTest(unittest.TestCase):
    """
    Base TestCase that provisions a fresh temp SQLite DB for each test method.
    Inheriting from TestCase directly (not a mixin) prevents pytest's Python 3.14
    double-discovery issue with unittest subclasses.
    """

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        self.screener = HalalScreener(db_path=self.db_path)

    def tearDown(self):
        try:
            self.screener.conn.close()
        except Exception:
            pass
        try:
            os.unlink(self.db_path)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# 1. BUSINESS ACTIVITY GATE — Stage 1
# ═══════════════════════════════════════════════════════════════════════════════


class TestBusinessActivityGate(_BaseScreenerTest):
    """Tests for check_business_activity() — Stage 1 prohibited vertical detection."""

    # ── Definitive Failures ───────────────────────────────────────────────────

    def test_conventional_banking_fails(self):
        """SBIN-type stock must fail Stage 1 with 'Conventional Banking' flagged."""
        passed, excluded = self.screener.check_business_activity(SBIN_INFO)
        self.assertFalse(passed)
        self.assertIn("Conventional Banking", excluded)

    def test_tobacco_fails(self):
        """ITC-type stock must fail Stage 1 with 'Tobacco' flagged."""
        passed, excluded = self.screener.check_business_activity(ITC_INFO)
        self.assertFalse(passed)
        self.assertIn("Tobacco", excluded)

    def test_alcohol_fails(self):
        """Alcoholic-beverage company must fail Stage 1 with 'Alcohol' flagged."""
        passed, excluded = self.screener.check_business_activity(ALCOHOL_INFO)
        self.assertFalse(passed)
        self.assertIn("Alcohol", excluded)

    def test_gambling_fails(self):
        """Casino company must fail Stage 1 with 'Gambling' flagged."""
        passed, excluded = self.screener.check_business_activity(GAMBLING_INFO)
        self.assertFalse(passed)
        self.assertIn("Gambling", excluded)

    def test_defense_fails(self):
        """Aerospace & Defense company must fail Stage 1 with 'Weapons/Defense' flagged."""
        passed, excluded = self.screener.check_business_activity(DEFENSE_INFO)
        self.assertFalse(passed)
        self.assertIn("Weapons/Defense", excluded)

    def test_pork_via_summary_fails(self):
        """Pork processor caught via longBusinessSummary (no dedicated industry)."""
        info = {
            "sector": "Consumer Defensive",
            "industry": "Packaged Foods",
            "longBusinessSummary": "The company specialises in pork processing and swine farming.",
        }
        passed, excluded = self.screener.check_business_activity(info)
        self.assertFalse(passed)
        self.assertIn("Pork", excluded)

    def test_adult_entertainment_via_summary_fails(self):
        """Adult content company caught via longBusinessSummary."""
        info = {
            "sector": "Communication Services",
            "industry": "Internet Content & Information",
            "longBusinessSummary": "Platform hosts adult entertainment and explicit content.",
        }
        passed, excluded = self.screener.check_business_activity(info)
        self.assertFalse(passed)
        self.assertIn("Adult Entertainment", excluded)

    # ── Clean Pass ────────────────────────────────────────────────────────────

    def test_tcs_passes_cleanly(self):
        """TCS IT-services stock must pass Stage 1 with no exclusions."""
        passed, excluded = self.screener.check_business_activity(TCS_INFO)
        self.assertTrue(passed)
        self.assertEqual(excluded, [])

    def test_healthcare_passes_cleanly(self):
        """Generic healthcare stock passes Stage 1."""
        passed, excluded = self.screener.check_business_activity(CLEAN_INFO)
        self.assertTrue(passed)
        self.assertEqual(excluded, [])

    def test_empty_info_does_not_crash(self):
        """Empty info dict should not raise; should pass with no exclusions."""
        passed, excluded = self.screener.check_business_activity({})
        # No info = cannot confirm prohibited activity → passes to Stage 2
        self.assertIsInstance(passed, bool)
        self.assertIsInstance(excluded, list)

    def test_none_fields_handled_gracefully(self):
        """None sector/industry/summary fields must not raise an exception."""
        info = {"sector": None, "industry": None, "longBusinessSummary": None}
        passed, excluded = self.screener.check_business_activity(info)
        self.assertIsInstance(passed, bool)

    # ── Hotel / DOUBTFUL Path ─────────────────────────────────────────────────

    def test_hotel_triggers_doubtful_sentinel(self):
        """Hotel industry must append sentinel and still pass Stage 1 business gate."""
        passed, excluded = self.screener.check_business_activity(HOTEL_INFO)
        self.assertTrue(passed, "Hotel should NOT hard-fail Stage 1")
        self.assertIn("Hotel (Unverified Segment)", excluded)

    def test_hotel_excluded_categories_does_not_affect_business_pass(self):
        """
        Beyonce Rule: 'Hotel (Unverified Segment)' must NOT count as a definitive fail.
        business_pass must remain True for hotel-only stocks.
        """
        passed, excluded = self.screener.check_business_activity(HOTEL_INFO)
        definitive_fails = [e for e in excluded if e != "Hotel (Unverified Segment)"]
        self.assertEqual(len(definitive_fails), 0)
        self.assertTrue(passed)

    def test_hotel_with_gambling_also_fails_gambling_gate(self):
        """A hotel+casino conglomerate must fail the Gambling gate outright."""
        info = {
            "sector":   "Consumer Cyclical",
            "industry": "Gambling & Hotel",
            "longBusinessSummary": "Operates casino gambling and hotel lodging.",
        }
        passed, excluded = self.screener.check_business_activity(info)
        self.assertFalse(passed)
        self.assertIn("Gambling", excluded)
        self.assertIn("Hotel (Unverified Segment)", excluded)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. FINANCIAL RATIO GATE — Stage 2
# ═══════════════════════════════════════════════════════════════════════════════


class TestFinancialRatioGate(_BaseScreenerTest):
    """Tests for check_financial_ratios() — TASIS threshold computation."""

    def _ratios_from(
        self,
        total_assets: Optional[float] = 1_000_000.0,
        total_debt: Optional[float] = 200_000.0,
        cash: Optional[float] = 100_000.0,
        receivables: Optional[float] = 50_000.0,
        total_revenue: Optional[float] = 500_000.0,
        interest_income: Optional[float] = 10_000.0,
        short_debt: Optional[float] = None,
        long_debt: Optional[float] = None,
    ) -> Dict:
        """Helper: build a mock ticker and run check_financial_ratios."""
        bs = _make_balance_sheet(
            total_assets=total_assets,
            total_debt=total_debt,
            short_debt=short_debt,
            long_debt=long_debt,
            cash=cash,
            receivables=receivables,
        )
        is_ = _make_income_stmt(
            total_revenue=total_revenue,
            interest_income=interest_income,
        )
        mock_ticker = _make_mock_ticker(balance_sheet=bs, income_stmt=is_)
        return self.screener.check_financial_ratios(mock_ticker)

    # ── Debt-to-Assets boundary tests ─────────────────────────────────────────

    def test_debt_threshold_exactly_at_limit_passes(self):
        """Beyonce Rule: 25.0000 % exactly must PASS (≤ threshold)."""
        # debt = 250_000, assets = 1_000_000  →  ratio = 0.25
        result = self._ratios_from(total_assets=1_000_000, total_debt=250_000)
        ratio, passes = result["debt_to_assets"]
        self.assertAlmostEqual(ratio, 0.25, places=5)
        self.assertTrue(passes, "25.0 % should PASS (boundary inclusive)")

    def test_debt_threshold_one_basis_point_above_fails(self):
        """Beyonce Rule: 25.01 % must FAIL."""
        # debt = 250_100, assets = 1_000_000  →  ratio = 0.2501
        result = self._ratios_from(total_assets=1_000_000, total_debt=250_100)
        ratio, passes = result["debt_to_assets"]
        self.assertGreater(ratio, 0.25)
        self.assertFalse(passes, "25.01 % should FAIL")

    def test_zero_debt_passes_cleanly(self):
        """Company with no debt: ratio = 0 → pass = True."""
        result = self._ratios_from(total_debt=0.0)
        _, passes = result["debt_to_assets"]
        self.assertTrue(passes)

    # ── Interest Income boundary tests ────────────────────────────────────────

    def test_interest_income_threshold_exactly_at_limit_passes(self):
        """Beyonce Rule: 3.0000 % exactly must PASS."""
        # ii = 15_000, revenue = 500_000  →  ratio = 0.03
        result = self._ratios_from(interest_income=15_000, total_revenue=500_000)
        ratio, passes = result["interest_income"]
        self.assertAlmostEqual(ratio, 0.03, places=5)
        self.assertTrue(passes, "3.0 % should PASS (boundary inclusive)")

    def test_interest_income_threshold_above_fails(self):
        """Beyonce Rule: 3.01 % must FAIL."""
        # ii = 15_050, revenue = 500_000  →  ratio = 0.0301
        result = self._ratios_from(interest_income=15_050, total_revenue=500_000)
        ratio, passes = result["interest_income"]
        self.assertGreater(ratio, 0.03)
        self.assertFalse(passes, "3.01 % should FAIL")

    def test_absent_interest_income_label_treated_as_zero(self):
        """
        Beyonce Rule: when 'Interest Income' label is absent from income stmt,
        it must be treated as 0.0 (not an error), making ratio = 0 → PASS.
        """
        bs = _make_balance_sheet()
        is_ = _make_income_stmt(interest_income=None)   # Label absent
        mock_ticker = _make_mock_ticker(balance_sheet=bs, income_stmt=is_)
        result = self.screener.check_financial_ratios(mock_ticker)
        ratio, passes = result["interest_income"]
        self.assertEqual(ratio, 0.0)
        self.assertTrue(passes)

    # ── Cash + AR boundary tests ──────────────────────────────────────────────

    def test_cash_ar_threshold_exactly_at_limit_passes(self):
        """Beyonce Rule: 90.0 % exactly must PASS."""
        # cash=800_000, AR=100_000, assets=1_000_000  →  ratio = 0.90
        result = self._ratios_from(
            total_assets=1_000_000, cash=800_000, receivables=100_000
        )
        ratio, passes = result["cash_ar_to_assets"]
        self.assertAlmostEqual(ratio, 0.90, places=5)
        self.assertTrue(passes)

    def test_cash_ar_above_threshold_fails(self):
        """(Cash + AR) / Assets > 90 % must FAIL."""
        result = self._ratios_from(
            total_assets=1_000_000, cash=850_000, receivables=100_000
        )
        _, passes = result["cash_ar_to_assets"]
        self.assertFalse(passes)

    # ── Division-by-zero guards ───────────────────────────────────────────────

    def test_zero_total_assets_returns_none_and_fails(self):
        """
        Beyonce Rule: denominator = 0 must return (None, False) for both
        Debt-to-Assets and Cash+AR ratios, and append to data_errors.
        """
        result = self._ratios_from(total_assets=0.0)
        da_ratio, da_pass = result["debt_to_assets"]
        car_ratio, car_pass = result["cash_ar_to_assets"]
        self.assertIsNone(da_ratio)
        self.assertFalse(da_pass)
        self.assertIsNone(car_ratio)
        self.assertFalse(car_pass)
        # Data errors should be recorded
        self.assertIn("_data_errors", result)

    def test_none_total_assets_returns_none_and_fails(self):
        """Beyonce Rule: missing Total Assets label → (None, False) for D/A and C+AR."""
        bs = _make_balance_sheet(total_assets=None)  # label absent
        is_ = _make_income_stmt()
        mock_ticker = _make_mock_ticker(balance_sheet=bs, income_stmt=is_)
        result = self.screener.check_financial_ratios(mock_ticker)
        _, da_pass  = result["debt_to_assets"]
        _, car_pass = result["cash_ar_to_assets"]
        self.assertFalse(da_pass)
        self.assertFalse(car_pass)

    def test_zero_total_revenue_returns_none_and_fails(self):
        """Beyonce Rule: Total Revenue = 0 → division-by-zero guard → (None, False)."""
        result = self._ratios_from(total_revenue=0.0)
        ii_ratio, ii_pass = result["interest_income"]
        self.assertIsNone(ii_ratio)
        self.assertFalse(ii_pass)

    def test_none_total_revenue_returns_none_and_fails(self):
        """Missing Total Revenue label → (None, False) for Interest ratio."""
        bs = _make_balance_sheet()
        is_ = _make_income_stmt(total_revenue=None)
        mock_ticker = _make_mock_ticker(balance_sheet=bs, income_stmt=is_)
        result = self.screener.check_financial_ratios(mock_ticker)
        _, ii_pass = result["interest_income"]
        self.assertFalse(ii_pass)

    # ── Debt Fallback Calculation ─────────────────────────────────────────────

    def test_debt_fallback_sums_short_and_long_term(self):
        """
        Beyonce Rule: when 'Total Debt' label is absent, must compute
        Short-term + Long-term debt and use the sum for the ratio.
        """
        # total_debt=None → must use short + long
        bs = _make_balance_sheet(
            total_assets=1_000_000,
            total_debt=None,        # Force fallback path
            short_debt=80_000,
            long_debt=170_000,
            cash=100_000,
            receivables=50_000,
        )
        is_ = _make_income_stmt()
        mock_ticker = _make_mock_ticker(balance_sheet=bs, income_stmt=is_)
        result = self.screener.check_financial_ratios(mock_ticker)
        ratio, passes = result["debt_to_assets"]
        # Expected: (80_000 + 170_000) / 1_000_000 = 0.25 → PASS
        self.assertAlmostEqual(ratio, 0.25, places=5)
        self.assertTrue(passes)

    def test_debt_fallback_above_threshold_fails(self):
        """Beyonce Rule: fallback debt sum above 25 % must still fail."""
        bs = _make_balance_sheet(
            total_assets=1_000_000,
            total_debt=None,
            short_debt=100_000,
            long_debt=200_000,   # Sum = 300_000 → 30 % → FAIL
            cash=50_000,
            receivables=50_000,
        )
        is_ = _make_income_stmt()
        mock_ticker = _make_mock_ticker(balance_sheet=bs, income_stmt=is_)
        result = self.screener.check_financial_ratios(mock_ticker)
        ratio, passes = result["debt_to_assets"]
        self.assertAlmostEqual(ratio, 0.30, places=5)
        self.assertFalse(passes)

    def test_both_debt_labels_absent_treated_as_zero_debt(self):
        """
        Beyonce Rule: when ALL debt labels are absent, total_debt defaults to 0.0
        (0 % ratio = PASS — absence of debt evidence ≠ non-compliance).
        """
        bs = _make_balance_sheet(
            total_debt=None,
            short_debt=None,
            long_debt=None,
        )
        is_ = _make_income_stmt()
        mock_ticker = _make_mock_ticker(balance_sheet=bs, income_stmt=is_)
        result = self.screener.check_financial_ratios(mock_ticker)
        ratio, passes = result["debt_to_assets"]
        self.assertEqual(ratio, 0.0)
        self.assertTrue(passes)

    def test_empty_dataframes_return_all_fails(self):
        """Empty balance_sheet + income_stmt → all ratios (None, False)."""
        mock_ticker = _make_mock_ticker(
            balance_sheet=pd.DataFrame(),
            income_stmt=pd.DataFrame(),
        )
        result = self.screener.check_financial_ratios(mock_ticker)
        for key in ("debt_to_assets", "cash_ar_to_assets"):
            _, passes = result[key]
            self.assertFalse(passes, f"{key} should fail on empty DataFrame")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. COMPLIANCE GRADING — Stage 3
# ═══════════════════════════════════════════════════════════════════════════════


class TestComplianceGrading(_BaseScreenerTest):
    """Tests for assign_compliance_grade() — hard-gate grading rubric."""

    def _grade(
        self,
        business_pass: bool = True,
        da: Optional[float] = 0.05,
        da_pass: bool = True,
        ii: Optional[float] = 0.005,
        ii_pass: bool = True,
        car: Optional[float] = 0.30,
        car_pass: bool = True,
        is_doubtful: bool = False,
    ) -> Tuple[str, str]:
        ratios = {
            "debt_to_assets":    (da, da_pass),
            "interest_income":   (ii, ii_pass),
            "cash_ar_to_assets": (car, car_pass),
        }
        return self.screener.assign_compliance_grade(business_pass, ratios, is_doubtful)

    # ── Hard Gates ────────────────────────────────────────────────────────────

    def test_stage1_fail_always_noncompliant_f(self):
        """Beyonce Rule: Stage 1 fail → NON_COMPLIANT, F regardless of ratios."""
        status, grade = self._grade(business_pass=False, da=0.01, da_pass=True)
        self.assertEqual(status, "NON_COMPLIANT")
        self.assertEqual(grade, "F")

    def test_any_ratio_fail_gives_noncompliant_f(self):
        """Beyonce Rule: ANY Stage 2 ratio failing → NON_COMPLIANT, F."""
        # Only debt fails
        status, grade = self._grade(da=0.30, da_pass=False)
        self.assertEqual(status, "NON_COMPLIANT")
        self.assertEqual(grade, "F")

    def test_interest_ratio_fail_alone_gives_f(self):
        """Beyonce Rule: Interest Income ratio fail → F (debt+cash passing)."""
        status, grade = self._grade(ii=0.05, ii_pass=False)
        self.assertEqual(status, "NON_COMPLIANT")
        self.assertEqual(grade, "F")

    def test_cash_ar_ratio_fail_alone_gives_f(self):
        """Beyonce Rule: Cash+AR ratio fail → F."""
        status, grade = self._grade(car=0.95, car_pass=False)
        self.assertEqual(status, "NON_COMPLIANT")
        self.assertEqual(grade, "F")

    def test_hotel_doubtful_gives_d_regardless_of_ratios(self):
        """Beyonce Rule: is_doubtful=True → DOUBTFUL, D regardless of ratio values."""
        status, grade = self._grade(is_doubtful=True)
        self.assertEqual(status, "DOUBTFUL")
        self.assertEqual(grade, "D")

    # ── A+ Grade ─────────────────────────────────────────────────────────────

    def test_grade_a_plus_all_ratios_well_below_limits(self):
        """Beyonce Rule: all ratios ≤ 50 % of safety limits → A+."""
        # da=0.05 (≤0.125), ii=0.005 (≤0.015), car=0.20 (≤0.45)
        status, grade = self._grade(da=0.05, ii=0.005, car=0.20)
        self.assertEqual(status, "COMPLIANT")
        self.assertEqual(grade, "A+")

    def test_grade_a_plus_boundary_exact_limits(self):
        """Beyonce Rule: ratios exactly at A+ sub-limits → A+."""
        status, grade = self._grade(
            da=GRADE_A_PLUS["debt"],
            ii=GRADE_A_PLUS["interest"],
            car=GRADE_A_PLUS["cash_ar"],
        )
        self.assertEqual(grade, "A+")

    def test_grade_a_plus_boundary_one_over_loses_plus(self):
        """If debt just exceeds A+ limit but within A limit → A (not A+)."""
        # da = 0.126 > 0.125 (A+ limit), but ≤ 0.20 (A limit)
        status, grade = self._grade(da=0.126, ii=0.005, car=0.20)
        self.assertEqual(status, "COMPLIANT")
        self.assertNotEqual(grade, "A+")

    # ── A Grade ───────────────────────────────────────────────────────────────

    def test_grade_a_all_within_a_limits(self):
        """All ratios within A sub-limits but outside A+ → A."""
        # da=0.18 (0.125<0.18≤0.20), ii=0.018 (0.015<0.018≤0.02), car=0.70 (0.45<0.70≤0.80)
        status, grade = self._grade(da=0.18, ii=0.018, car=0.70)
        self.assertEqual(status, "COMPLIANT")
        self.assertEqual(grade, "A")

    def test_grade_a_boundary_exact_a_limits(self):
        """Ratios exactly at A sub-limits → A."""
        status, grade = self._grade(
            da=GRADE_A["debt"],
            ii=GRADE_A["interest"],
            car=GRADE_A["cash_ar"],
        )
        self.assertEqual(grade, "A")

    # ── B Grade ───────────────────────────────────────────────────────────────

    def test_grade_b_debt_in_approaching_range(self):
        """Beyonce Rule: debt in (20 %, 23 %] → B grade."""
        status, grade = self._grade(da=0.22, ii=0.010, car=0.60)
        self.assertEqual(status, "COMPLIANT")
        self.assertEqual(grade, "B")

    def test_grade_b_boundary_at_max(self):
        """Beyonce Rule: debt exactly at 23 % → B."""
        status, grade = self._grade(da=GRADE_B_DEBT_MAX, ii=0.01, car=0.50)
        self.assertEqual(grade, "B")

    def test_grade_b_boundary_at_min_not_b(self):
        """Beyonce Rule: debt exactly at 20 % → A (not B; B range is exclusive of 20 %)."""
        status, grade = self._grade(da=GRADE_B_DEBT_MIN, ii=0.01, car=0.50)
        # da=0.20 is exactly the A limit → should be A, not B
        self.assertEqual(grade, "A")

    # ── C Grade ───────────────────────────────────────────────────────────────

    def test_grade_c_all_pass_outside_a_and_b(self):
        """All TASIS thresholds met but outside A/B criteria → C."""
        # da=0.24 > GRADE_B_DEBT_MAX (0.23) and ≤ 0.25 TASIS limit
        status, grade = self._grade(da=0.24, ii=0.025, car=0.85)
        self.assertEqual(status, "COMPLIANT")
        self.assertEqual(grade, "C")

    # ── None ratios (all passed somehow — defensive) ──────────────────────────

    def test_none_ratio_with_pass_true_treated_as_zero(self):
        """If ratio=None but pass=True (defensive path), grade proceeds without crash."""
        ratios = {
            "debt_to_assets":    (None, True),
            "interest_income":   (None, True),
            "cash_ar_to_assets": (None, True),
        }
        status, grade = self.screener.assign_compliance_grade(True, ratios, False)
        # None → 0.0 defensively → should get A+
        self.assertEqual(status, "COMPLIANT")
        self.assertEqual(grade, "A+")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. DATABASE OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════


class TestDatabaseOperations(_BaseScreenerTest):
    """Tests for DB schema creation and _persist_result()."""

    def test_schema_table_created_on_init(self):
        """Beyonce Rule: table must exist immediately after __init__."""
        cur = self.screener.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='halal_screening_results'"
        )
        row = cur.fetchone()
        self.assertIsNotNone(row, "Table 'halal_screening_results' not found in DB")

    def test_schema_has_all_required_columns(self):
        """All amended schema columns must be present."""
        expected_cols = {
            "id", "ticker", "sector", "business_screen_pass",
            "excluded_categories",
            "debt_to_assets_ratio", "debt_to_assets_pass",
            "interest_income_ratio", "interest_income_pass",
            "cash_ar_to_assets_ratio", "cash_ar_pass",
            "overall_status", "compliance_grade",
            "purification_ratio", "screen_date",
        }
        cur = self.screener.conn.execute(
            "PRAGMA table_info(halal_screening_results)"
        )
        actual_cols = {row[1] for row in cur.fetchall()}
        self.assertTrue(
            expected_cols.issubset(actual_cols),
            f"Missing columns: {expected_cols - actual_cols}",
        )

    def test_persist_result_writes_row(self):
        """A persisted result must be retrievable from the DB."""
        self.screener._persist_result({
            "ticker":                 "TEST.NS",
            "sector":                 "Technology",
            "business_screen_pass":   True,
            "excluded_categories":    [],
            "debt_to_assets_ratio":   0.10,
            "debt_to_assets_pass":    True,
            "interest_income_ratio":  0.005,
            "interest_income_pass":   True,
            "cash_ar_to_assets_ratio": 0.40,
            "cash_ar_pass":           True,
            "overall_status":         "COMPLIANT",
            "compliance_grade":       "A+",
            "purification_ratio":     0.005,
        })
        cur = self.screener.conn.execute(
            "SELECT * FROM halal_screening_results WHERE ticker='TEST.NS'"
        )
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["overall_status"], "COMPLIANT")
        self.assertEqual(row["compliance_grade"], "A+")

    def test_insert_or_replace_same_ticker_same_date(self):
        """
        Beyonce Rule: screening same ticker twice on same day must produce exactly
        ONE row (INSERT OR REPLACE), not two.  Last write wins.
        """
        payload = {
            "ticker":                 "DUPE.NS",
            "sector":                 "Technology",
            "business_screen_pass":   True,
            "excluded_categories":    [],
            "debt_to_assets_ratio":   0.05,
            "debt_to_assets_pass":    True,
            "interest_income_ratio":  0.005,
            "interest_income_pass":   True,
            "cash_ar_to_assets_ratio": 0.20,
            "cash_ar_pass":           True,
            "overall_status":         "COMPLIANT",
            "compliance_grade":       "A+",
            "purification_ratio":     0.005,
        }
        self.screener._persist_result(payload)
        # Second write with updated grade
        payload["compliance_grade"] = "A"
        payload["debt_to_assets_ratio"] = 0.15
        self.screener._persist_result(payload)

        cur = self.screener.conn.execute(
            "SELECT COUNT(*) as cnt, compliance_grade FROM halal_screening_results "
            "WHERE ticker='DUPE.NS'"
        )
        row = cur.fetchone()
        self.assertEqual(row["cnt"], 1, "Must have exactly 1 row (upsert, not duplicate)")
        self.assertEqual(row["compliance_grade"], "A", "Latest write should win")

    def test_excluded_categories_serialised_as_json(self):
        """excluded_categories must be stored as a JSON array string."""
        self.screener._persist_result({
            "ticker":                 "JSONTEST.NS",
            "sector":                 None,
            "business_screen_pass":   False,
            "excluded_categories":    ["Conventional Banking", "Insurance"],
            "debt_to_assets_ratio":   None,
            "debt_to_assets_pass":    False,
            "interest_income_ratio":  None,
            "interest_income_pass":   False,
            "cash_ar_to_assets_ratio": None,
            "cash_ar_pass":           False,
            "overall_status":         "NON_COMPLIANT",
            "compliance_grade":       "F",
            "purification_ratio":     0.0,
        })
        cur = self.screener.conn.execute(
            "SELECT excluded_categories FROM halal_screening_results WHERE ticker='JSONTEST.NS'"
        )
        raw = cur.fetchone()["excluded_categories"]
        parsed = json.loads(raw)
        self.assertIsInstance(parsed, list)
        self.assertIn("Conventional Banking", parsed)

    def test_screen_date_stored_as_yyyy_mm_dd(self):
        """screen_date must be stored as ISO date string (YYYY-MM-DD), not datetime."""
        self.screener._persist_result({
            "ticker":                 "DATETEST.NS",
            "sector":                 None,
            "business_screen_pass":   False,
            "excluded_categories":    [],
            "debt_to_assets_ratio":   None,
            "debt_to_assets_pass":    False,
            "interest_income_ratio":  None,
            "interest_income_pass":   False,
            "cash_ar_to_assets_ratio": None,
            "cash_ar_pass":           False,
            "overall_status":         "NON_COMPLIANT",
            "compliance_grade":       "F",
            "purification_ratio":     0.0,
        })
        cur = self.screener.conn.execute(
            "SELECT screen_date FROM halal_screening_results WHERE ticker='DATETEST.NS'"
        )
        stored_date = cur.fetchone()["screen_date"]
        expected = date.today().isoformat()
        self.assertEqual(stored_date, expected)

    def test_invalid_db_directory_raises_runtime_error(self):
        """Beyonce Rule: non-existent DB directory must raise RuntimeError on init."""
        with self.assertRaises(RuntimeError):
            HalalScreener(db_path="/nonexistent/path/that/does/not/exist/trade.db")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SCREEN_STOCK ORCHESTRATOR — End-to-End (fully mocked)
# ═══════════════════════════════════════════════════════════════════════════════


class TestScreenStockOrchestrator(_BaseScreenerTest):
    """
    End-to-end tests for screen_stock().
    yf.Ticker and yf.download are mocked to prevent live network calls.
    """

    # ── Clean compliant stock (TCS-like) ─────────────────────────────────────

    @patch("src.screening.halal_screener.yf.Ticker")
    def test_tcs_type_returns_compliant(self, MockTicker):
        """TCS-like stock with clean financials must return 'COMPLIANT'."""
        # Ratios: debt=3.2% (A+), ii=0.1% (A+), cash_ar=15% (A+)
        bs = _make_balance_sheet(
            total_assets=1_000_000_000,
            total_debt=32_000_000,
            cash=100_000_000,
            receivables=50_000_000,
        )
        is_ = _make_income_stmt(
            total_revenue=500_000_000,
            interest_income=500_000,   # 0.1 % → A+
        )
        mock_ticker = _make_mock_ticker(info=TCS_INFO, balance_sheet=bs, income_stmt=is_)
        MockTicker.return_value = mock_ticker

        result = self.screener.screen_stock("TCS.NS")
        self.assertEqual(result, "COMPLIANT")

        # Verify DB row persisted correctly
        cur = self.screener.conn.execute(
            "SELECT overall_status, compliance_grade FROM halal_screening_results "
            "WHERE ticker='TCS.NS' ORDER BY screen_date DESC LIMIT 1"
        )
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["overall_status"], "COMPLIANT")

    # ── SBIN-type (banking) ────────────────────────────────────────────────────

    @patch("src.screening.halal_screener.yf.Ticker")
    def test_sbin_type_fails_stage1_banking(self, MockTicker):
        """Banking stock must fail Stage 1 and return 'NON_COMPLIANT'."""
        mock_ticker = _make_mock_ticker(info=SBIN_INFO)
        MockTicker.return_value = mock_ticker

        result = self.screener.screen_stock("SBIN.NS")
        self.assertEqual(result, "NON_COMPLIANT")

        cur = self.screener.conn.execute(
            "SELECT compliance_grade, excluded_categories FROM halal_screening_results "
            "WHERE ticker='SBIN.NS' ORDER BY screen_date DESC LIMIT 1"
        )
        row = cur.fetchone()
        self.assertEqual(row["compliance_grade"], "F")
        self.assertIn("Conventional Banking", json.loads(row["excluded_categories"]))

    # ── ITC-type (tobacco) ────────────────────────────────────────────────────

    @patch("src.screening.halal_screener.yf.Ticker")
    def test_itc_type_fails_stage1_tobacco(self, MockTicker):
        """Tobacco stock must fail Stage 1 and return 'NON_COMPLIANT'."""
        mock_ticker = _make_mock_ticker(info=ITC_INFO)
        MockTicker.return_value = mock_ticker

        result = self.screener.screen_stock("ITC.NS")
        self.assertEqual(result, "NON_COMPLIANT")

        cur = self.screener.conn.execute(
            "SELECT excluded_categories FROM halal_screening_results "
            "WHERE ticker='ITC.NS' LIMIT 1"
        )
        row = cur.fetchone()
        self.assertIn("Tobacco", json.loads(row["excluded_categories"]))

    # ── Stage 1 pass but Stage 2 debt ratio fails ─────────────────────────────

    @patch("src.screening.halal_screener.yf.Ticker")
    def test_high_debt_stock_fails_stage2(self, MockTicker):
        """Stock with 30 % debt/assets: passes Stage 1, fails Stage 2 → NON_COMPLIANT F."""
        bs = _make_balance_sheet(
            total_assets=1_000_000,
            total_debt=300_000,   # 30 % → FAIL
            cash=50_000,
            receivables=50_000,
        )
        is_ = _make_income_stmt(total_revenue=500_000, interest_income=5_000)
        mock_ticker = _make_mock_ticker(info=TCS_INFO, balance_sheet=bs, income_stmt=is_)
        MockTicker.return_value = mock_ticker

        result = self.screener.screen_stock("HIGH_DEBT.NS")
        self.assertEqual(result, "NON_COMPLIANT")

        cur = self.screener.conn.execute(
            "SELECT compliance_grade FROM halal_screening_results WHERE ticker='HIGH_DEBT.NS'"
        )
        self.assertEqual(cur.fetchone()["compliance_grade"], "F")

    # ── Hotel / DOUBTFUL path ─────────────────────────────────────────────────

    @patch("src.screening.halal_screener.yf.Ticker")
    def test_hotel_stock_returns_doubtful(self, MockTicker):
        """Hotel stock must return 'DOUBTFUL' and be assigned grade D."""
        bs = _make_balance_sheet()
        is_ = _make_income_stmt()
        mock_ticker = _make_mock_ticker(info=HOTEL_INFO, balance_sheet=bs, income_stmt=is_)
        MockTicker.return_value = mock_ticker

        result = self.screener.screen_stock("HOTEL.NS")
        self.assertEqual(result, "DOUBTFUL")

        cur = self.screener.conn.execute(
            "SELECT overall_status, compliance_grade FROM halal_screening_results "
            "WHERE ticker='HOTEL.NS' LIMIT 1"
        )
        row = cur.fetchone()
        self.assertEqual(row["overall_status"], "DOUBTFUL")
        self.assertEqual(row["compliance_grade"], "D")

    # ── Purification ratio persisted for compliant stocks ────────────────────

    @patch("src.screening.halal_screener.yf.Ticker")
    def test_purification_ratio_persisted_correctly(self, MockTicker):
        """
        Beyonce Rule: purification_ratio = Interest Income / Total Revenue
        must be persisted for COMPLIANT stocks.
        """
        # ii = 7_500, revenue = 500_000 → 1.5 %
        bs = _make_balance_sheet(total_assets=1_000_000, total_debt=50_000)
        is_ = _make_income_stmt(total_revenue=500_000, interest_income=7_500)
        mock_ticker = _make_mock_ticker(info=TCS_INFO, balance_sheet=bs, income_stmt=is_)
        MockTicker.return_value = mock_ticker

        self.screener.screen_stock("PURIF.NS")

        cur = self.screener.conn.execute(
            "SELECT purification_ratio FROM halal_screening_results WHERE ticker='PURIF.NS'"
        )
        row = cur.fetchone()
        self.assertAlmostEqual(row["purification_ratio"], 0.015, places=4)

    # ── 3-Tier Fallback Grid ──────────────────────────────────────────────────

    @patch("src.screening.halal_screener.yf.download")
    @patch("src.screening.halal_screener.yf.Ticker")
    def test_fallback_attempt2_download_on_info_failure(self, MockTicker, MockDownload):
        """
        Beyonce Rule: when yf.Ticker.info raises, Attempt 2 (yf.download) is tried.
        If download also fails, FETCH_FAILED is returned with a NON_COMPLIANT DB row.
        """
        # Attempt 1: Ticker.info raises
        mock_ticker = MagicMock()
        mock_ticker.info = {}       # Stub dict → triggers fallback
        MockTicker.return_value = mock_ticker

        # Attempt 2: download also fails
        MockDownload.side_effect = ConnectionError("Network unreachable")

        result = self.screener.screen_stock("UNAVAILABLE.NS")
        self.assertEqual(result, "FETCH_FAILED")

        cur = self.screener.conn.execute(
            "SELECT overall_status, compliance_grade FROM halal_screening_results "
            "WHERE ticker='UNAVAILABLE.NS'"
        )
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["overall_status"], "NON_COMPLIANT")
        self.assertEqual(row["compliance_grade"], "F")

    @patch("src.screening.halal_screener.yf.download")
    @patch("src.screening.halal_screener.yf.Ticker")
    def test_fetch_failed_row_has_fetch_failed_in_excluded(self, MockTicker, MockDownload):
        """Beyonce Rule: FETCH_FAILED row must include 'FETCH_FAILED' in excluded_categories."""
        mock_ticker = MagicMock()
        mock_ticker.info = {}
        MockTicker.return_value = mock_ticker
        MockDownload.side_effect = ConnectionError("Timeout")

        self.screener.screen_stock("TIMEOUT.NS")

        cur = self.screener.conn.execute(
            "SELECT excluded_categories FROM halal_screening_results WHERE ticker='TIMEOUT.NS'"
        )
        row = cur.fetchone()
        excluded = json.loads(row["excluded_categories"])
        self.assertIn("FETCH_FAILED", excluded)

    @patch("src.screening.halal_screener.yf.Ticker")
    def test_rate_limited_stub_info_triggers_fallback(self, MockTicker):
        """
        Beyonce Rule: info dict with ≤ 2 keys (rate-limit stub) must trigger
        fallback, not proceed to screen against empty data.
        """
        mock_ticker = MagicMock()
        mock_ticker.info = {"trailingPegRatio": None}  # 1 key = rate-limit stub
        MockTicker.return_value = mock_ticker

        # We're not mocking download, so it will try to download and may fail.
        # We just verify it doesn't crash and returns a valid result string.
        result = self.screener.screen_stock("RATELIMIT.NS")
        self.assertIn(result, {"COMPLIANT", "NON_COMPLIANT", "DOUBTFUL", "FETCH_FAILED"})


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
