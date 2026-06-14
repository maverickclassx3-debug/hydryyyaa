"""
halal_screener.py
=================
Layer 1 Upstream Gateway Filter — Halal AI World Monitor
Phase 1 · Week 1

Screens NSE equities against AAOIFI/TASIS Shariah compliance criteria.
Every equity must clear all three compliance gates before any downstream
agent receives a trade signal. No partial approvals reach the order engine.

Pipeline Architecture:
    screen_stock(symbol)
        │
        ├── Fallback Grid (3-tier network resilience)
        │       Attempt 1: yf.Ticker(symbol).info   (live HTTP)
        │       Attempt 2: yf.download()             (alt endpoint)
        │       Attempt 3: FETCH_FAILED              (safe-fail DB row)
        │
        ├── Stage 1: check_business_activity()
        │       → 7 Prohibited Verticals + Hotel/DOUBTFUL gate
        │       → NON_COMPLIANT on any definitive match
        │
        ├── Stage 2: check_financial_ratios()
        │       → TASIS Primary Thresholds (Debt, Interest, Cash+AR)
        │       → Division-by-zero guards on all denominators
        │       → Debt fallback: Short-term + Long-term if Total Debt absent
        │
        ├── Stage 3: assign_compliance_grade()
        │       → Hard-gate: Stage 1 FAIL or ANY ratio FAIL → F
        │       → A+ / A / B / C for compliant stocks
        │       → D for DOUBTFUL (hotel/unverified segment)
        │
        └── _persist_result()
                → Atomic SQLite transaction via context manager
                → INSERT OR REPLACE for daily idempotency

Dependencies:
    yfinance==0.2.37
    pandas==2.2.1

Usage:
    python -m src.screening.halal_screener
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import warnings
from datetime import date
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf

# ─── Suppress noisy yfinance / pandas FutureWarning chatter ──────────────────
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

# ─── Structured logger ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# TASIS / AAOIFI PRIMARY THRESHOLDS
# ═══════════════════════════════════════════════════════════════════════════════

THRESHOLD_DEBT_TO_ASSETS: float = 0.25   # 25 % max interest-bearing debt
THRESHOLD_INTEREST_INCOME: float = 0.03  # 3 %  max interest income / revenue
THRESHOLD_CASH_AR: float = 0.90          # 90 % max (cash + AR) / total assets

# Sub-thresholds for grading (proximity to safety limits)
GRADE_A_PLUS: Dict[str, float] = {
    "debt":      0.125,   # ≤ 12.5 % (50 % of 25 %)
    "interest":  0.015,   # ≤  1.5 % (50 % of 3 %)
    "cash_ar":   0.45,    # ≤ 45 %   (50 % of 90 %)
}
GRADE_A: Dict[str, float] = {
    "debt":      0.20,    # ≤ 20 %
    "interest":  0.02,    # ≤  2 %
    "cash_ar":   0.80,    # ≤ 80 %
}
GRADE_B_DEBT_MIN: float = 0.20  # Approaching threshold range: (20 %, 23 %]
GRADE_B_DEBT_MAX: float = 0.23

# ═══════════════════════════════════════════════════════════════════════════════
# YFINANCE LABEL ALIAS TABLES
# Order matters: most common / canonical labels listed first.
# Adding alternatives insulates against schema drift across yfinance versions.
# ═══════════════════════════════════════════════════════════════════════════════

BS_TOTAL_ASSETS: List[str] = [
    "Total Assets",
    "TotalAssets",
    "Assets",
]
# Direct total-debt label (preferred)
BS_TOTAL_DEBT_DIRECT: List[str] = [
    "Total Debt",
]
# Fallback: Short-term debt components (summed when Total Debt is absent/NaN)
BS_SHORT_TERM_DEBT: List[str] = [
    "Current Debt And Capital Lease Obligation",
    "Current Debt",
    "Short Long Term Debt",
    "Short Term Borrowings",
    "Current Portion Of Long Term Debt",
]
# Fallback: Long-term debt components
BS_LONG_TERM_DEBT: List[str] = [
    "Long Term Debt And Capital Lease Obligation",
    "Long Term Debt Capital Lease Obligation",
    "Long Term Debt",
]
BS_CASH: List[str] = [
    "Cash And Cash Equivalents",
    "Cash Cash Equivalents And Short Term Investments",
    "Cash And Short Term Investments",
    "Cash",
]
BS_RECEIVABLES: List[str] = [
    "Net Receivables",
    "Accounts Receivable",
    "Gross Accounts Receivable",
    "Trade And Other Receivables Current",
]
IS_TOTAL_REVENUE: List[str] = [
    "Total Revenue",
    "Operating Revenue",
    "Revenue",
]
IS_INTEREST_INCOME: List[str] = [
    "Interest Income",
    "Net Interest Income",
    "Interest And Dividend Income",
    "Interest Income Non Operating",
]

# ═══════════════════════════════════════════════════════════════════════════════
# PROHIBITED BUSINESS VERTICALS (7 Core Shariah Categories)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Matching priority:
#   1. industry keyword match (sector-gated where unambiguous)
#   2. longBusinessSummary keyword match (for sectors that lack distinct industries)
#
# NOTE: Weapons/Defense uses `sector="industrials"` gate to prevent false
# positives on civilian aerospace companies classified in other sectors.
# ═══════════════════════════════════════════════════════════════════════════════

PROHIBITED_VERTICALS: Dict[str, Dict] = {
    "Conventional Banking": {
        "sector": "financial services",   # Required sector context
        "industry_keywords": ["bank", "savings & mortgage", "savings and mortgage"],
        "summary_keywords": [],           # Deliberately empty: avoid FP on "savings"
    },
    "Insurance": {
        "sector": "financial services",
        "industry_keywords": ["insurance"],
        "summary_keywords": [],
    },
    "Alcohol": {
        "sector": None,   # Can appear in multiple sectors
        "industry_keywords": ["alcoholic", "brewer", "distill", "winer"],
        "summary_keywords": [
            "produces alcohol", "manufactures alcohol", "beer production",
            "wine production", "spirits distillation",
        ],
    },
    "Tobacco": {
        "sector": None,
        "industry_keywords": ["tobacco"],
        "summary_keywords": ["tobacco products", "cigarette manufactur", "cigar manufactur"],
    },
    "Gambling": {
        "sector": None,
        "industry_keywords": ["gambling", "casino", "gaming"],
        "summary_keywords": ["casino operations", "gambling operations", "sports betting"],
    },
    "Pork": {
        "sector": None,
        "industry_keywords": [],          # No dedicated yfinance industry label
        "summary_keywords": [
            "pork processing", "pork products", "swine farming",
            "hog farming", "pig farming",
        ],
    },
    "Adult Entertainment": {
        "sector": None,
        "industry_keywords": [],
        "summary_keywords": ["adult entertainment", "pornograph"],
    },
    "Weapons/Defense": {
        "sector": "industrials",
        "industry_keywords": ["defense", "defence"],
        "summary_keywords": ["weapons manufacturer", "munitions manufacturer", "armaments"],
    },
}

# Hotel/hospitality industry keywords that trigger the DOUBTFUL path
HOTEL_INDUSTRY_KEYWORDS: List[str] = [
    "hotel", "resort", "lodging", "hospitality", "accommodation",
]

# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE SCHEMA
# ═══════════════════════════════════════════════════════════════════════════════

DB_SCHEMA: str = """
CREATE TABLE IF NOT EXISTS halal_screening_results (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker               TEXT    NOT NULL,
    sector               TEXT,
    business_screen_pass BOOLEAN NOT NULL,
    excluded_categories  TEXT,
    debt_to_assets_ratio      REAL,
    debt_to_assets_pass       BOOLEAN,
    interest_income_ratio     REAL,
    interest_income_pass      BOOLEAN,
    cash_ar_to_assets_ratio   REAL,
    cash_ar_pass              BOOLEAN,
    overall_status       TEXT CHECK (overall_status IN ('COMPLIANT', 'DOUBTFUL', 'NON_COMPLIANT')),
    compliance_grade     TEXT CHECK (compliance_grade IN ('A+', 'A', 'B', 'C', 'D', 'F')),
    purification_ratio   REAL DEFAULT 0.0,
    screen_date          DATE NOT NULL,
    UNIQUE(ticker, screen_date)
);
"""

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN CLASS
# ═══════════════════════════════════════════════════════════════════════════════


class HalalScreener:
    """
    Layer 1 Upstream Gateway Filter for Shariah-compliant equity screening.

    Applies AAOIFI/TASIS Primary Thresholds across three sequential gates:
        Stage 1 — Business Activity (7 prohibited verticals + hotel DOUBTFUL)
        Stage 2 — Financial Ratios  (3 TASIS thresholds with resilient extraction)
        Stage 3 — Compliance Grading (hard-gate rules: A+ through F)

    All results are persisted atomically to SQLite with daily idempotency
    (INSERT OR REPLACE on UNIQUE(ticker, screen_date)).
    """

    def __init__(self, db_path: str = "trade_data.db") -> None:
        """
        Initialise screener and ensure the production DB schema exists.

        Args:
            db_path: Path to the SQLite database file.

        Raises:
            RuntimeError: If the parent directory of db_path does not exist.
        """
        db_dir = os.path.dirname(os.path.abspath(db_path))
        if not os.path.isdir(db_dir):
            raise RuntimeError(
                f"[HalalScreener.__init__] Database directory does not exist: '{db_dir}'. "
                "Please create the directory before initialising HalalScreener."
            )

        self.db_path = db_path
        # check_same_thread=False permits downstream multi-agent access patterns
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        # Create schema atomically; safe to call multiple times (IF NOT EXISTS)
        with self.conn:
            self.conn.executescript(DB_SCHEMA)

        logger.info("[DB] Schema ready at '%s'", db_path)

    # ──────────────────────────────────────────────────────────────────────────
    # Stage 1 — Business Activity Gate
    # ──────────────────────────────────────────────────────────────────────────

    def check_business_activity(
        self, info: dict
    ) -> Tuple[bool, List[str]]:
        """
        Screen business activity against 7 Shariah-prohibited verticals.

        Matching precedence:
            1. Industry keyword match (sector-gated where unambiguous)
            2. longBusinessSummary keyword match

        Hotel/hospitality companies are flagged with a sentinel category
        "Hotel (Unverified Segment)" when segment revenue data is not available
        via the yfinance API. They receive overall_status='DOUBTFUL' and grade
        'D' rather than hard NON_COMPLIANT, pending manual revenue audit.

        Args:
            info: Raw dict from yf.Ticker(symbol).info

        Returns:
            Tuple[bool, List[str]]:
                - (True,  [])                               → Clean pass
                - (True,  ["Hotel (Unverified Segment)"])   → DOUBTFUL gate
                - (False, ["Conventional Banking", ...])    → Hard NON_COMPLIANT
        """
        sector_raw   = info.get("sector", "") or ""
        industry_raw = info.get("industry", "") or ""
        summary_raw  = info.get("longBusinessSummary", "") or ""

        sector_l   = sector_raw.lower().strip()
        industry_l = industry_raw.lower().strip()
        summary_l  = summary_raw.lower()

        flagged: List[str] = []

        for vertical_name, config in PROHIBITED_VERTICALS.items():
            matched = False
            required_sector   = config.get("sector")       # None = any sector
            industry_keywords = config.get("industry_keywords", [])
            summary_keywords  = config.get("summary_keywords", [])

            # ── Primary: industry keyword match (sector-gated) ────────────────
            if industry_keywords and industry_l:
                sector_ok = (required_sector is None) or (required_sector in sector_l)
                if sector_ok and any(kw in industry_l for kw in industry_keywords):
                    matched = True

            # ── Secondary: longBusinessSummary keyword match ───────────────────
            if not matched and summary_keywords:
                for kw in summary_keywords:
                    if kw in summary_l:
                        matched = True
                        break

            if matched and vertical_name not in flagged:
                flagged.append(vertical_name)

        # ── Hotel / Hospitality DOUBTFUL check ────────────────────────────────
        # yfinance does not expose segment revenue breakdowns for NSE tickers.
        # A hotel company may legally derive <5 % revenue from alcohol/gambling,
        # but we cannot verify this programmatically. Mark as DOUBTFUL.
        if any(kw in industry_l for kw in HOTEL_INDUSTRY_KEYWORDS):
            if "Hotel (Unverified Segment)" not in flagged:
                flagged.append("Hotel (Unverified Segment)")

        # business_pass is True only if no *definitive* prohibited vertical matched
        definitive_fails = [f for f in flagged if f != "Hotel (Unverified Segment)"]
        business_pass = len(definitive_fails) == 0

        return business_pass, flagged

    # ──────────────────────────────────────────────────────────────────────────
    # Stage 2 — Financial Ratio Gate (TASIS Primary Thresholds)
    # ──────────────────────────────────────────────────────────────────────────

    def check_financial_ratios(
        self, ticker_obj: yf.Ticker
    ) -> Dict[str, Tuple[Optional[float], bool]]:
        """
        Compute TASIS Primary Threshold ratios from yfinance financials.

        Resilience strategies:
            - Label alias tables survive yfinance schema drift
            - Total Debt fallback: Short-term + Long-term if direct label absent
            - Division-by-zero guard on all denominators
            - Missing numerators default to 0.0 (absence of debt/interest = compliant)
            - Missing denominators → (None, False) → hard fail

        Returns:
            Dict with mandatory keys: "debt_to_assets", "interest_income",
            "cash_ar_to_assets". Each value: (ratio: Optional[float], pass: bool)
            Optional key "_data_errors" present when extraction errors occurred.
        """
        data_errors: List[str] = []

        # ── Fetch financial statement DataFrames ──────────────────────────────
        try:
            balance_sheet = ticker_obj.balance_sheet
        except Exception as exc:
            logger.warning("[RATIOS] balance_sheet fetch error: %s", exc)
            balance_sheet = pd.DataFrame()

        try:
            income_stmt = ticker_obj.income_stmt
        except Exception as exc:
            logger.warning("[RATIOS] income_stmt fetch error: %s", exc)
            income_stmt = pd.DataFrame()

        # Most-recent reporting period is always the first column
        bs = self._latest_series(balance_sheet)
        is_ = self._latest_series(income_stmt)

        # ── Extract: Total Assets (denominator for D/A and C+AR ratios) ───────
        total_assets = self._get_value(bs, BS_TOTAL_ASSETS)
        # total_assets=None triggers division-by-zero guard → (None, False)

        # ── Extract: Total Debt (with resilient fallback) ─────────────────────
        total_debt_raw = self._get_value(bs, BS_TOTAL_DEBT_DIRECT)
        if total_debt_raw is None:
            # Fallback: manually sum short-term + long-term components
            short_debt = self._get_value(bs, BS_SHORT_TERM_DEBT) or 0.0
            long_debt  = self._get_value(bs, BS_LONG_TERM_DEBT)  or 0.0
            total_debt = short_debt + long_debt
            if total_debt:
                logger.debug(
                    "[RATIOS] Total Debt (fallback): ST=%.0f + LT=%.0f = %.0f",
                    short_debt, long_debt, total_debt,
                )
            # total_debt=0.0 means no debt found → legitimate 0 % ratio
        else:
            total_debt = total_debt_raw

        # ── Extract: Cash + Accounts Receivable ───────────────────────────────
        cash        = self._get_value(bs, BS_CASH)        or 0.0
        receivables = self._get_value(bs, BS_RECEIVABLES) or 0.0
        cash_ar     = cash + receivables

        # ── Extract: Total Revenue (denominator for Interest ratio) ───────────
        total_revenue = self._get_value(is_, IS_TOTAL_REVENUE)

        # ── Extract: Interest Income ──────────────────────────────────────────
        # Absence of an "Interest Income" line in the income statement is
        # interpreted as 0 interest income (standard for non-financial companies).
        interest_income = self._get_value(is_, IS_INTEREST_INCOME) or 0.0

        # ── Compute Ratios with Division-by-Zero Guards ───────────────────────
        da_ratio, da_pass = self._safe_ratio(
            numerator=total_debt,
            denominator=total_assets,
            threshold=THRESHOLD_DEBT_TO_ASSETS,
            label="Debt-to-Assets",
            data_errors=data_errors,
        )
        ii_ratio, ii_pass = self._safe_ratio(
            numerator=interest_income,
            denominator=total_revenue,
            threshold=THRESHOLD_INTEREST_INCOME,
            label="Interest Income / Revenue",
            data_errors=data_errors,
        )
        car_ratio, car_pass = self._safe_ratio(
            numerator=cash_ar,
            denominator=total_assets,
            threshold=THRESHOLD_CASH_AR,
            label="(Cash + AR) / Assets",
            data_errors=data_errors,
        )

        results: Dict[str, Tuple[Optional[float], bool]] = {
            "debt_to_assets":   (da_ratio,  da_pass),
            "interest_income":  (ii_ratio,  ii_pass),
            "cash_ar_to_assets": (car_ratio, car_pass),
        }

        if data_errors:
            results["_data_errors"] = (None, False)           # type: ignore[assignment]
            results["_error_list"]  = data_errors              # type: ignore[assignment]

        return results

    # ──────────────────────────────────────────────────────────────────────────
    # Stage 3 — Compliance Grading (Hard-Gate Rules)
    # ──────────────────────────────────────────────────────────────────────────

    def assign_compliance_grade(
        self,
        business_pass: bool,
        ratios: Dict[str, Tuple[Optional[float], bool]],
        is_doubtful: bool = False,
    ) -> Tuple[str, str]:
        """
        Map screening results to (overall_status, compliance_grade).

        Hard-gate rules (non-negotiable per amended spec):
            - Stage 1 FAIL (business_pass=False) → ('NON_COMPLIANT', 'F')
            - ANY Stage 2 ratio fails             → ('NON_COMPLIANT', 'F')
            - Hotel / unverified segment          → ('DOUBTFUL', 'D')

        Compliant grade assignment (all gates pass):
            - 'A+': All ratios ≤ 50 % of safety limits
            - 'A' : All ratios within tighter A sub-limits
            - 'B' : Debt in (20 %, 23 %] — approaching threshold
            - 'C' : All TASIS thresholds met, outside A/B criteria

        Args:
            business_pass: Result of Stage 1 check.
            ratios: Result dict from check_financial_ratios().
            is_doubtful: True when hotel/unverified segment sentinel present.

        Returns:
            Tuple[str, str]: (overall_status, compliance_grade)
        """
        # ── Hard gate 1: Stage 1 business screen failure ──────────────────────
        if not business_pass:
            return ("NON_COMPLIANT", "F")

        # ── Hard gate 2: Hotel / unverified conglomerate segment ─────────────
        if is_doubtful:
            return ("DOUBTFUL", "D")

        # ── Hard gate 3: Any Stage 2 ratio fails ─────────────────────────────
        _, da_pass  = ratios.get("debt_to_assets",    (None, False))
        _, ii_pass  = ratios.get("interest_income",   (None, False))
        _, car_pass = ratios.get("cash_ar_to_assets", (None, False))

        if not da_pass or not ii_pass or not car_pass:
            return ("NON_COMPLIANT", "F")

        # ── All gates passed — determine grade by proximity to thresholds ─────
        da_ratio,  _ = ratios["debt_to_assets"]
        ii_ratio,  _ = ratios["interest_income"]
        car_ratio, _ = ratios["cash_ar_to_assets"]

        # Defensive: if ratio is somehow None but passed (shouldn't happen), use 0
        da  = da_ratio  if da_ratio  is not None else 0.0
        ii  = ii_ratio  if ii_ratio  is not None else 0.0
        car = car_ratio if car_ratio is not None else 0.0

        # A+: All ratios ≤ 50 % of their respective safety limits
        if (
            da  <= GRADE_A_PLUS["debt"]
            and ii  <= GRADE_A_PLUS["interest"]
            and car <= GRADE_A_PLUS["cash_ar"]
        ):
            return ("COMPLIANT", "A+")

        # A: All ratios within tighter sub-limits
        if (
            da  <= GRADE_A["debt"]
            and ii  <= GRADE_A["interest"]
            and car <= GRADE_A["cash_ar"]
        ):
            return ("COMPLIANT", "A")

        # B: Debt is approaching the TASIS limit (20 %–23 %)
        if GRADE_B_DEBT_MIN < da <= GRADE_B_DEBT_MAX:
            return ("COMPLIANT", "B")

        # C: All TASIS thresholds met but outside A/B criteria
        return ("COMPLIANT", "C")

    # ──────────────────────────────────────────────────────────────────────────
    # Orchestrator — screen_stock
    # ──────────────────────────────────────────────────────────────────────────

    def screen_stock(self, symbol: str) -> str:
        """
        Full screening pipeline: fetch → Stage 1 → Stage 2 → Grade → Persist.

        3-tier Fallback Grid:
            Attempt 1: yf.Ticker(symbol).info  — standard live HTTP endpoint
            Attempt 2: yf.download()           — alternate price endpoint (validates ticker)
            Attempt 3: FETCH_FAILED            — safe-fail; NON_COMPLIANT row persisted

        Args:
            symbol: NSE ticker symbol, e.g. "TCS.NS"

        Returns:
            One of: 'COMPLIANT' | 'DOUBTFUL' | 'NON_COMPLIANT' | 'FETCH_FAILED'
        """
        logger.info("[SCREEN] ▶ Starting: %s", symbol)

        ticker_obj: Optional[yf.Ticker] = None
        info: dict = {}

        # ── Fallback Grid ─────────────────────────────────────────────────────
        # Attempt 1 — yf.Ticker.info (primary live HTTP)
        try:
            ticker_obj = yf.Ticker(symbol)
            info = ticker_obj.info or {}
            # A minimal stub dict (2 keys) is returned when rate-limited
            if len(info) <= 2:
                raise ValueError(
                    f"Stub info dict returned ({len(info)} keys) — possible rate-limit"
                )
            logger.info(
                json.dumps({
                    "event": "fetch_success", "attempt": 1,
                    "symbol": symbol, "sector": info.get("sector"),
                    "industry": info.get("industry"),
                })
            )
        except Exception as exc_1:
            logger.warning(
                json.dumps({
                    "event": "fallback", "attempt": 1,
                    "symbol": symbol, "reason": str(exc_1),
                })
            )

            # Attempt 2 — yf.download (alternate CDN endpoint)
            try:
                df_price = yf.download(symbol, period="5d", progress=False, timeout=15)
                if df_price.empty:
                    raise ValueError("yf.download returned empty DataFrame — invalid ticker?")
                # Price data exists but financial metadata unavailable;
                # cannot reliably screen → treat as FETCH_FAILED
                logger.warning(
                    json.dumps({
                        "event": "fallback", "attempt": 2,
                        "symbol": symbol,
                        "reason": "Price data available but financial metadata absent; "
                                  "cannot screen. Persisting safe-fail row.",
                    })
                )
                # Fall through to Attempt 3 behaviour (no info = safe-fail)
                info = {}
                ticker_obj = None

            except Exception as exc_2:
                logger.error(
                    json.dumps({
                        "event": "fetch_failed", "attempt": 3,
                        "symbol": symbol, "reason": str(exc_2),
                    })
                )
                # Attempt 3 — hard safe-fail: persist NON_COMPLIANT and exit
                self._persist_result({
                    "ticker":                 symbol,
                    "sector":                 None,
                    "business_screen_pass":   False,
                    "excluded_categories":    ["FETCH_FAILED"],
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
                return "FETCH_FAILED"

        # If Attempt 2 succeeded (price only, no metadata) we still can't screen
        if not info:
            self._persist_result({
                "ticker":                 symbol,
                "sector":                 None,
                "business_screen_pass":   False,
                "excluded_categories":    ["METADATA_UNAVAILABLE"],
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
            return "FETCH_FAILED"

        # ── Stage 1: Business Activity ─────────────────────────────────────────
        business_pass, excluded_categories = self.check_business_activity(info)
        is_hotel_doubtful = "Hotel (Unverified Segment)" in excluded_categories

        if not business_pass:
            logger.info("[SCREEN] %s → Stage 1 FAIL | %s", symbol, excluded_categories)
            self._persist_result({
                "ticker":                 symbol,
                "sector":                 info.get("sector"),
                "business_screen_pass":   False,
                "excluded_categories":    excluded_categories,
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
            return "NON_COMPLIANT"

        # ── Stage 2: Financial Ratios ──────────────────────────────────────────
        ratios: Dict[str, Tuple[Optional[float], bool]] = {
            "debt_to_assets":    (None, False),
            "interest_income":   (None, False),
            "cash_ar_to_assets": (None, False),
        }
        purification_ratio: float = 0.0

        if ticker_obj is not None:
            try:
                ratios = self.check_financial_ratios(ticker_obj)
                # Purification ratio = Interest Income / Total Revenue
                ii_r, _ = ratios.get("interest_income", (0.0, True))
                purification_ratio = round(ii_r, 6) if ii_r is not None else 0.0
            except Exception as exc:
                logger.error("[RATIOS] Unexpected extraction error for %s: %s", symbol, exc)
                # Ratios already initialised to (None, False) — will hard-fail grade

        # ── Append any ratio data errors to excluded_categories ───────────────
        ratio_errors: List[str] = ratios.get("_error_list", [])   # type: ignore[assignment]
        if ratio_errors:
            excluded_categories = excluded_categories + list(ratio_errors)

        # ── Stage 3: Assign Grade ─────────────────────────────────────────────
        overall_status, compliance_grade = self.assign_compliance_grade(
            business_pass=business_pass,
            ratios=ratios,
            is_doubtful=is_hotel_doubtful,
        )

        # ── Persist ────────────────────────────────────────────────────────────
        da_ratio,  da_pass  = ratios.get("debt_to_assets",    (None, False))
        ii_ratio,  ii_pass  = ratios.get("interest_income",   (None, False))
        car_ratio, car_pass = ratios.get("cash_ar_to_assets", (None, False))

        self._persist_result({
            "ticker":                 symbol,
            "sector":                 info.get("sector"),
            "business_screen_pass":   business_pass,
            "excluded_categories":    excluded_categories,
            "debt_to_assets_ratio":   da_ratio,
            "debt_to_assets_pass":    da_pass,
            "interest_income_ratio":  ii_ratio,
            "interest_income_pass":   ii_pass,
            "cash_ar_to_assets_ratio": car_ratio,
            "cash_ar_pass":           car_pass,
            "overall_status":         overall_status,
            "compliance_grade":       compliance_grade,
            "purification_ratio":     purification_ratio,
        })

        logger.info(
            "[SCREEN] %s → %s (%s) | D/A=%.2f%% | I/I=%.3f%% | C+AR=%.2f%% | Purif=%.4f%%",
            symbol, overall_status, compliance_grade,
            (da_ratio or 0.0) * 100,
            (ii_ratio or 0.0) * 100,
            (car_ratio or 0.0) * 100,
            purification_ratio * 100,
        )

        return overall_status

    # ──────────────────────────────────────────────────────────────────────────
    # Private Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _latest_series(self, df: Optional[pd.DataFrame]) -> pd.Series:
        """
        Extract the most-recent reporting-period column as a Series.
        yfinance orders columns newest → oldest (index 0 = latest).
        Returns empty Series if the DataFrame is None, empty, or malformed.
        """
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return pd.Series(dtype=float)
        try:
            return df.iloc[:, 0]
        except Exception:
            return pd.Series(dtype=float)

    def _get_value(
        self, series: pd.Series, labels: List[str]
    ) -> Optional[float]:
        """
        Scan the alias list and return the first non-NaN numeric value found.

        Args:
            series: A pandas Series with financial-statement row labels as index.
            labels: Ordered list of candidate label strings.

        Returns:
            float value, or None if no alias matches or all values are NaN/null.
        """
        if series is None or series.empty:
            return None
        for label in labels:
            if label in series.index:
                val = series[label]
                # Guard against NaN, None, and non-numeric types
                if val is None:
                    continue
                try:
                    f = float(val)
                    if not pd.isna(f):
                        return f
                except (TypeError, ValueError):
                    continue
        return None

    def _safe_ratio(
        self,
        numerator: float,
        denominator: Optional[float],
        threshold: float,
        label: str,
        data_errors: List[str],
    ) -> Tuple[Optional[float], bool]:
        """
        Safely compute numerator/denominator with explicit zero-division guard.

        Design contract:
            - Caller guarantees numerator is a float (0.0 if absent; see rationale above).
            - denominator=None or denominator<=0 → (None, False) and error logged.
            - Returns (ratio rounded to 6dp, ratio <= threshold).
        """
        if denominator is None or denominator <= 0:
            data_errors.append(f"{label}: denominator is {'None' if denominator is None else denominator} (≤ 0)")
            logger.warning("[RATIOS] Division-by-zero guard triggered: %s", label)
            return (None, False)

        ratio = numerator / denominator
        passes = ratio <= threshold
        return (round(ratio, 6), passes)

    def _persist_result(self, data: dict) -> None:
        """
        Atomically persist one screening result to SQLite.

        Uses INSERT OR REPLACE so that re-screening the same ticker on the
        same calendar day overwrites the previous result (last-write-wins).
        The `with self.conn:` context manager provides atomic commit/rollback.
        """
        screen_date   = date.today().isoformat()          # YYYY-MM-DD
        excluded_json = json.dumps(data.get("excluded_categories") or [])

        sql = """
        INSERT OR REPLACE INTO halal_screening_results (
            ticker, sector, business_screen_pass,
            excluded_categories,
            debt_to_assets_ratio,   debt_to_assets_pass,
            interest_income_ratio,  interest_income_pass,
            cash_ar_to_assets_ratio, cash_ar_pass,
            overall_status, compliance_grade,
            purification_ratio, screen_date
        ) VALUES (
            :ticker, :sector, :business_screen_pass,
            :excluded_categories,
            :debt_to_assets_ratio,  :debt_to_assets_pass,
            :interest_income_ratio, :interest_income_pass,
            :cash_ar_to_assets_ratio, :cash_ar_pass,
            :overall_status, :compliance_grade,
            :purification_ratio, :screen_date
        )
        """

        with self.conn:
            self.conn.execute(sql, {
                "ticker":                  data["ticker"],
                "sector":                  data.get("sector"),
                "business_screen_pass":    int(bool(data["business_screen_pass"])),
                "excluded_categories":     excluded_json,
                "debt_to_assets_ratio":    data.get("debt_to_assets_ratio"),
                "debt_to_assets_pass":     int(bool(data.get("debt_to_assets_pass"))),
                "interest_income_ratio":   data.get("interest_income_ratio"),
                "interest_income_pass":    int(bool(data.get("interest_income_pass"))),
                "cash_ar_to_assets_ratio": data.get("cash_ar_to_assets_ratio"),
                "cash_ar_pass":            int(bool(data.get("cash_ar_pass"))),
                "overall_status":          data["overall_status"],
                "compliance_grade":        data["compliance_grade"],
                "purification_ratio":      data.get("purification_ratio", 0.0) or 0.0,
                "screen_date":             screen_date,
            })

        logger.info(
            "[DB] Persisted %s → %s (%s) on %s",
            data["ticker"], data["overall_status"], data["compliance_grade"], screen_date,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CLI HELPERS — ASCII VERIFICATION TABLE
# ═══════════════════════════════════════════════════════════════════════════════

def _fmt_pct(val: Optional[float], dp: int = 2) -> str:
    """Format an optional float as a percentage string."""
    if val is None:
        return "N/A"
    return f"{val * 100:.{dp}f}%"


def _render_verification_table(rows: List[dict]) -> None:
    """
    Render screening results as a UTF-8 bordered ASCII table to stdout.

    Expected row dict keys:
        symbol, status, grade, excluded, da, ii, car, purif
    """
    # Column definitions: (header, width)
    cols = [
        ("Symbol",   10),
        ("Status",   15),
        ("Gr",        4),
        ("Excluded Categories",          30),
        ("D/A %",     8),
        ("I/I %",     8),
        ("C+AR %",    8),
        ("Purif. %",  9),
    ]

    def _hr(left: str, mid: str, right: str, fill: str = "─") -> str:
        return left + mid.join(fill * (w + 2) for _, w in cols) + right

    header_cells = "│".join(f" {h:^{w}} " for h, w in cols)

    print()
    print("  HALAL AI WORLD MONITOR — Layer 1 Screening Results")
    print(f"  Run Date: {date.today().isoformat()}")
    print()
    print(_hr("┌", "┬", "┐"))
    print(f"│{header_cells}│")
    print(_hr("├", "┼", "┤"))

    for r in rows:
        excluded_str = ", ".join(r.get("excluded", []) or []) or "—"
        if len(excluded_str) > 28:
            excluded_str = excluded_str[:25] + "…"

        status_icon = {"COMPLIANT": "✅", "NON_COMPLIANT": "❌", "DOUBTFUL": "⚠️"}.get(
            r["status"], ""
        )
        status_cell = f"{status_icon} {r['status']}"

        cells = [
            f" {r['symbol']:<{cols[0][1]}} ",
            f" {status_cell:^{cols[1][1]}} ",
            f" {r['grade']:^{cols[2][1]}} ",
            f" {excluded_str:<{cols[3][1]}} ",
            f" {_fmt_pct(r.get('da')):^{cols[4][1]}} ",
            f" {_fmt_pct(r.get('ii'), dp=3):^{cols[5][1]}} ",
            f" {_fmt_pct(r.get('car')):^{cols[6][1]}} ",
            f" {_fmt_pct(r.get('purif'), dp=4):^{cols[7][1]}} ",
        ]
        print("│" + "│".join(cells) + "│")

    print(_hr("└", "┴", "┘"))
    print()
    print("  Legend:")
    print("    D/A   = Interest-Bearing Debt / Total Assets    (TASIS threshold: ≤ 25 %)")
    print("    I/I   = Interest Income / Total Revenue          (TASIS threshold: ≤  3 %)")
    print("    C+AR  = (Cash + Accounts Receivable) / Assets   (TASIS threshold: ≤ 90 %)")
    print("    Purif = Interest Income / Total Revenue         (Purification reference)")
    print()


# ═══════════════════════════════════════════════════════════════════════════════
# __main__ — VALIDATION CLUSTER
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    # Force UTF-8 output on Windows (cp1252 cannot encode box-drawing characters)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    VALIDATION_CLUSTER = [
        {
            "symbol":   "TCS.NS",
            "expected": "COMPLIANT",
            "note":     "IT services — should pass all gates cleanly",
        },
        {
            "symbol":   "SBIN.NS",
            "expected": "NON_COMPLIANT",
            "note":     "State Bank of India — Stage 1 FAIL via Conventional Banking",
        },
        {
            "symbol":   "ITC.NS",
            "expected": "NON_COMPLIANT",
            "note":     "ITC Ltd — Stage 1 FAIL via Tobacco",
        },
    ]

    print("\n" + "═" * 80)
    print("  HALAL AI WORLD MONITOR — Phase 1 Week 1 Validation Run")
    print("  Layer 1 Gateway Filter · TASIS Primary Thresholds")
    print("═" * 80)

    screener = HalalScreener(db_path="trade_data.db")
    table_rows: List[dict] = []
    all_pass = True

    for spec in VALIDATION_CLUSTER:
        sym = spec["symbol"]
        expected = spec["expected"]
        print(f"\n  Screening {sym} ({spec['note']}) …")

        actual = screener.screen_stock(sym)

        # Pull the persisted row for display metrics
        cur = screener.conn.execute(
            "SELECT * FROM halal_screening_results WHERE ticker = ? "
            "ORDER BY screen_date DESC LIMIT 1",
            (sym,),
        )
        row = cur.fetchone()

        da_val     = row["debt_to_assets_ratio"]     if row else None
        ii_val     = row["interest_income_ratio"]    if row else None
        car_val    = row["cash_ar_to_assets_ratio"]  if row else None
        purif_val  = row["purification_ratio"]       if row else None
        grade      = row["compliance_grade"]         if row else "F"
        excluded   = json.loads(row["excluded_categories"]) if row else []

        verdict = "✅ PASS" if actual == expected else f"❌ FAIL (got {actual}, expected {expected})"
        if actual != expected:
            all_pass = False
        print(f"  Validation: {verdict}")

        table_rows.append({
            "symbol":   sym,
            "status":   actual,
            "grade":    grade,
            "excluded": excluded,
            "da":       da_val,
            "ii":       ii_val,
            "car":      car_val,
            "purif":    purif_val,
        })

    _render_verification_table(table_rows)

    print("═" * 80)
    if all_pass:
        print("  ✅  ALL VALIDATION TESTS PASSED — Layer 1 is GO for pipeline integration")
    else:
        print("  ❌  ONE OR MORE VALIDATION TESTS FAILED — Review output above")
        sys.exit(1)
    print("═" * 80 + "\n")
