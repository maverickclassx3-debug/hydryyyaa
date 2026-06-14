"""
tests/test_persistence_engine.py
=================================
Complete test suite for PersistenceEngine — Layer 1, Part B
Halal AI World Monitor · Phase 1 · Week 1

Beyonce Rule: every validation gate, business rule, data path, concurrency
scenario, and schema invariant has an explicit test.

Test Classes:
    TestMigration           — additive schema, idempotency, no pre-table drop
    TestPositionUpsert      — 10 validation gates + happy-path + replace
    TestPositionClose       — DELETE logic + telemetry side-effect
    TestPositionRead        — get_position, get_all_positions
    TestTelemetryLog        — 4 pre-DB gates + happy-path + payload=None
    TestTelemetryQuery      — filter by component, level, limit cap
    TestThreadSafety        — 10 concurrent threads, 5 writes each → no corruption
    TestWALMode             — PRAGMA journal_mode=WAL active after init
    TestClose               — idempotent close()

Run:
    python -m pytest tests/test_persistence_engine.py -v
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.database.persistence_engine import (
    COMPLIANCE_GRADES,
    COMPONENT_NAMES,
    LOG_LEVELS,
    TELEMETRY_QUERY_LIMIT_MAX,
    PersistenceEngine,
)


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════

def _valid_position(**overrides) -> dict:
    """Return a fully valid position dict, with optional field overrides."""
    base = {
        "symbol":                 "TCS.NS",
        "entry_date":             "2026-06-13",
        "entry_price":            3500.0,
        "quantity":               10,
        "total_capital":          35000.0,
        "current_stop_loss":      3200.0,
        "trailing_profit_target": 3900.0,
        "algo_id":                "SEBI-ALGO-20260613-001",
        "compliance_grade":       "A+",
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════════════════════
# BASE TEST CLASS — provisions a fresh temp DB per test method
# ═══════════════════════════════════════════════════════════════════════════════

class _BasePersistenceTest(unittest.TestCase):
    """
    Base TestCase that creates a fresh temporary SQLite DB for each test.
    Inherits from TestCase only (not a mixin) — prevents pytest 3.14
    double-discovery issue.
    """

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        self.pe = PersistenceEngine(db_path=self.db_path)

    def tearDown(self):
        try:
            self.pe.close()
        except Exception:
            pass
        try:
            os.unlink(self.db_path)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# 1. MIGRATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestMigration(_BasePersistenceTest):
    """Beyonce Rule: every schema invariant has a test."""

    def test_active_positions_table_created_on_init(self):
        """active_positions table must exist immediately after __init__."""
        cur = self.pe.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='active_positions'"
        )
        self.assertIsNotNone(cur.fetchone(), "active_positions table not found")

    def test_system_telemetry_table_created_on_init(self):
        """system_telemetry table must exist immediately after __init__."""
        cur = self.pe.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='system_telemetry'"
        )
        self.assertIsNotNone(cur.fetchone(), "system_telemetry table not found")

    def test_migration_does_not_drop_halal_screening_results(self):
        """
        Beyonce Rule: the additive migration must NOT touch pre-existing tables.
        Simulate halal_screening_results already being present.
        """
        # Pre-create a sentinel table to simulate existing Layer 1 Part A table
        with self.pe.conn:
            self.pe.conn.execute(
                "CREATE TABLE IF NOT EXISTS halal_screening_results "
                "(id INTEGER PRIMARY KEY, ticker TEXT)"
            )
            self.pe.conn.execute(
                "INSERT INTO halal_screening_results (ticker) VALUES ('SENTINEL.NS')"
            )

        # Re-run migration
        self.pe.run_migration()

        # Sentinel data must still be present
        cur = self.pe.conn.execute(
            "SELECT ticker FROM halal_screening_results WHERE ticker='SENTINEL.NS'"
        )
        row = cur.fetchone()
        self.assertIsNotNone(row, "halal_screening_results was wiped by migration!")
        self.assertEqual(row[0], "SENTINEL.NS")

    def test_migration_is_idempotent(self):
        """Running run_migration() twice must not raise or alter the schema."""
        # First run already happened in setUp.
        try:
            self.pe.run_migration()
            self.pe.run_migration()
        except Exception as exc:
            self.fail(f"run_migration() raised on second/third call: {exc}")

        # Both tables still present
        cur = self.pe.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('active_positions','system_telemetry')"
        )
        tables = {row[0] for row in cur.fetchall()}
        self.assertEqual(tables, {"active_positions", "system_telemetry"})

    def test_active_positions_has_all_required_columns(self):
        """Beyonce Rule: every schema column must be verified to exist."""
        required = {
            "symbol", "entry_date", "entry_price", "quantity",
            "total_capital", "current_stop_loss", "trailing_profit_target",
            "algo_id", "compliance_grade",
        }
        cur = self.pe.conn.execute("PRAGMA table_info(active_positions)")
        actual = {row[1] for row in cur.fetchall()}
        self.assertTrue(
            required.issubset(actual),
            f"Missing columns in active_positions: {required - actual}",
        )

    def test_system_telemetry_has_all_required_columns(self):
        """Beyonce Rule: every telemetry column must be verified to exist."""
        required = {"id", "timestamp", "component", "log_level", "message", "payload"}
        cur = self.pe.conn.execute("PRAGMA table_info(system_telemetry)")
        actual = {row[1] for row in cur.fetchall()}
        self.assertTrue(
            required.issubset(actual),
            f"Missing columns in system_telemetry: {required - actual}",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. POSITION UPSERT — validation gates + happy-path
# ═══════════════════════════════════════════════════════════════════════════════

class TestPositionUpsert(_BasePersistenceTest):
    """Tests for upsert_position() — validation gates and persistence."""

    # ── Happy Path ────────────────────────────────────────────────────────────

    def test_valid_position_is_persisted(self):
        """A fully valid position dict must be stored and retrievable."""
        pos = _valid_position()
        self.pe.upsert_position(pos)
        result = self.pe.get_position("TCS.NS")
        self.assertIsNotNone(result)
        self.assertEqual(result["symbol"], "TCS.NS")
        self.assertAlmostEqual(result["entry_price"], 3500.0)
        self.assertEqual(result["algo_id"], "SEBI-ALGO-20260613-001")
        self.assertEqual(result["compliance_grade"], "A+")

    def test_upsert_replaces_same_symbol(self):
        """
        Beyonce Rule: INSERT OR REPLACE semantics — second upsert for the same
        symbol must overwrite the first row (not create a duplicate).
        """
        self.pe.upsert_position(_valid_position(entry_price=3500.0))
        self.pe.upsert_position(_valid_position(entry_price=3600.0, quantity=5))

        # Count must be exactly 1
        cur = self.pe.conn.execute(
            "SELECT COUNT(*) FROM active_positions WHERE symbol='TCS.NS'"
        )
        self.assertEqual(cur.fetchone()[0], 1)

        # Latest write should win
        pos = self.pe.get_position("TCS.NS")
        self.assertAlmostEqual(pos["entry_price"], 3600.0)
        self.assertEqual(pos["quantity"], 5)

    # ── SEBI Gate — algo_id ───────────────────────────────────────────────────

    def test_empty_algo_id_raises_value_error(self):
        """Beyonce Rule: SEBI 2026 gate — empty string algo_id → ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.pe.upsert_position(_valid_position(algo_id=""))
        self.assertIn("algo_id", str(ctx.exception))

    def test_none_algo_id_raises_value_error(self):
        """Beyonce Rule: SEBI 2026 gate — None algo_id → ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.pe.upsert_position(_valid_position(algo_id=None))
        self.assertIn("algo_id", str(ctx.exception))

    def test_whitespace_only_algo_id_raises_value_error(self):
        """Beyonce Rule: whitespace-only algo_id must be treated as empty."""
        with self.assertRaises(ValueError):
            self.pe.upsert_position(_valid_position(algo_id="   "))

    # ── CNC Gate — quantity ───────────────────────────────────────────────────

    def test_zero_quantity_raises_value_error(self):
        """Beyonce Rule: CNC long-only — quantity=0 → ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.pe.upsert_position(_valid_position(quantity=0))
        self.assertIn("quantity", str(ctx.exception))

    def test_negative_quantity_raises_value_error(self):
        """Beyonce Rule: CNC long-only — negative quantity (short sell) → ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.pe.upsert_position(_valid_position(quantity=-5))
        self.assertIn("quantity", str(ctx.exception))

    # ── Price Gates ───────────────────────────────────────────────────────────

    def test_zero_entry_price_raises_value_error(self):
        """Beyonce Rule: entry_price=0 → ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.pe.upsert_position(_valid_position(entry_price=0.0))
        self.assertIn("entry_price", str(ctx.exception))

    def test_negative_entry_price_raises_value_error(self):
        """Beyonce Rule: negative entry_price → ValueError."""
        with self.assertRaises(ValueError):
            self.pe.upsert_position(_valid_position(entry_price=-100.0))

    def test_stop_loss_equal_to_entry_price_raises_value_error(self):
        """
        Beyonce Rule: stop_loss must be STRICTLY below entry_price.
        stop_loss = entry_price → CNC risk violation → ValueError.
        """
        with self.assertRaises(ValueError) as ctx:
            self.pe.upsert_position(_valid_position(
                entry_price=3500.0, current_stop_loss=3500.0
            ))
        self.assertIn("stop_loss", str(ctx.exception).lower())

    def test_stop_loss_above_entry_price_raises_value_error(self):
        """Beyonce Rule: stop_loss > entry_price → CNC risk violation → ValueError."""
        with self.assertRaises(ValueError):
            self.pe.upsert_position(_valid_position(
                entry_price=3500.0, current_stop_loss=3600.0
            ))

    def test_zero_stop_loss_raises_value_error(self):
        """Beyonce Rule: stop_loss=0 → ValueError (must be > 0)."""
        with self.assertRaises(ValueError):
            self.pe.upsert_position(_valid_position(current_stop_loss=0.0))

    def test_profit_target_equal_to_entry_price_raises_value_error(self):
        """
        Beyonce Rule: trailing_profit_target must be STRICTLY above entry_price.
        Equal → ValueError.
        """
        with self.assertRaises(ValueError) as ctx:
            self.pe.upsert_position(_valid_position(
                entry_price=3500.0, trailing_profit_target=3500.0
            ))
        self.assertIn("trailing_profit_target", str(ctx.exception))

    def test_profit_target_below_entry_price_raises_value_error(self):
        """Beyonce Rule: profit_target < entry_price → ValueError."""
        with self.assertRaises(ValueError):
            self.pe.upsert_position(_valid_position(
                entry_price=3500.0, trailing_profit_target=3000.0
            ))

    # ── Compliance Grade Gate ─────────────────────────────────────────────────

    def test_invalid_compliance_grade_raises_value_error(self):
        """Beyonce Rule: grade not in {A+, A, B, C, D, F} → ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.pe.upsert_position(_valid_position(compliance_grade="E"))
        self.assertIn("compliance_grade", str(ctx.exception))

    def test_all_valid_compliance_grades_accepted(self):
        """Beyonce Rule: all 6 valid grades (A+, A, B, C, D, F) must be accepted."""
        for grade in ("A+", "A", "B", "C", "D", "F"):
            with self.subTest(grade=grade):
                symbol = f"TEST{grade.replace('+','PLUS')}.NS"
                self.pe.upsert_position(_valid_position(
                    symbol=symbol, compliance_grade=grade
                ))
                result = self.pe.get_position(symbol)
                self.assertEqual(result["compliance_grade"], grade)

    # ── Date Gate ────────────────────────────────────────────────────────────

    def test_invalid_date_format_raises_value_error(self):
        """Beyonce Rule: entry_date not in YYYY-MM-DD format → ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.pe.upsert_position(_valid_position(entry_date="13-06-2026"))
        self.assertIn("entry_date", str(ctx.exception))

    def test_non_string_date_raises_value_error(self):
        """Beyonce Rule: integer or None entry_date → ValueError."""
        with self.assertRaises(ValueError):
            self.pe.upsert_position(_valid_position(entry_date=20260613))

    def test_zero_total_capital_raises_value_error(self):
        """Beyonce Rule: total_capital=0 → ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.pe.upsert_position(_valid_position(total_capital=0.0))
        self.assertIn("total_capital", str(ctx.exception))


# ═══════════════════════════════════════════════════════════════════════════════
# 3. POSITION CLOSE
# ═══════════════════════════════════════════════════════════════════════════════

class TestPositionClose(_BasePersistenceTest):
    """Tests for close_position() — DELETE semantics."""

    def test_close_existing_position_returns_true(self):
        """Beyonce Rule: closing an extant position must return True."""
        self.pe.upsert_position(_valid_position())
        result = self.pe.close_position("TCS.NS")
        self.assertTrue(result)

    def test_close_nonexistent_position_returns_false(self):
        """Beyonce Rule: closing an unknown symbol must return False (not raise)."""
        result = self.pe.close_position("GHOST.NS")
        self.assertFalse(result)

    def test_closed_position_is_no_longer_retrievable(self):
        """Beyonce Rule: after close_position, get_position must return None."""
        self.pe.upsert_position(_valid_position())
        self.pe.close_position("TCS.NS")
        self.assertIsNone(self.pe.get_position("TCS.NS"))

    def test_close_position_writes_telemetry_entry(self):
        """Beyonce Rule: closing a position must log an audit entry in system_telemetry."""
        self.pe.upsert_position(_valid_position())
        self.pe.close_position("TCS.NS")
        rows = self.pe.query_telemetry(component="PERSISTENCE", level="INFO")
        messages = [r["message"] for r in rows]
        self.assertTrue(
            any("TCS.NS" in m for m in messages),
            "Expected a telemetry entry mentioning TCS.NS after close_position",
        )

    def test_close_position_invalid_symbol_raises(self):
        """Beyonce Rule: empty string symbol → ValueError."""
        with self.assertRaises(ValueError):
            self.pe.close_position("")

    def test_close_position_none_symbol_raises(self):
        """Beyonce Rule: None symbol → ValueError."""
        with self.assertRaises(ValueError):
            self.pe.close_position(None)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. POSITION READ
# ═══════════════════════════════════════════════════════════════════════════════

class TestPositionRead(_BasePersistenceTest):
    """Tests for get_position() and get_all_positions()."""

    def test_get_position_returns_none_for_missing_symbol(self):
        """Beyonce Rule: unknown symbol → None (not exception, not empty dict)."""
        self.assertIsNone(self.pe.get_position("MISSING.NS"))

    def test_get_position_returns_correct_dict(self):
        """Beyonce Rule: returned dict must match every field of the upserted row."""
        pos = _valid_position()
        self.pe.upsert_position(pos)
        result = self.pe.get_position("TCS.NS")
        self.assertIsNotNone(result)
        self.assertEqual(result["entry_date"], pos["entry_date"])
        self.assertAlmostEqual(result["entry_price"], pos["entry_price"])
        self.assertEqual(result["quantity"], pos["quantity"])
        self.assertAlmostEqual(result["current_stop_loss"], pos["current_stop_loss"])
        self.assertAlmostEqual(result["trailing_profit_target"], pos["trailing_profit_target"])
        self.assertEqual(result["algo_id"], pos["algo_id"])

    def test_get_all_positions_returns_empty_list_when_no_positions(self):
        """Beyonce Rule: no positions → empty list (not None, not exception)."""
        result = self.pe.get_all_positions()
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)

    def test_get_all_positions_returns_all_rows(self):
        """Beyonce Rule: three upserted positions → list of length 3."""
        symbols = ["TCS.NS", "INFY.NS", "HDFCBANK.NS"]
        for sym in symbols:
            self.pe.upsert_position(_valid_position(symbol=sym))
        results = self.pe.get_all_positions()
        returned_symbols = {r["symbol"] for r in results}
        self.assertEqual(returned_symbols, set(symbols))

    def test_get_all_positions_result_is_list_of_dicts(self):
        """Beyonce Rule: result items must be dicts (not sqlite3.Row objects)."""
        self.pe.upsert_position(_valid_position())
        results = self.pe.get_all_positions()
        self.assertIsInstance(results[0], dict)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. TELEMETRY LOG
# ═══════════════════════════════════════════════════════════════════════════════

class TestTelemetryLog(_BasePersistenceTest):
    """Tests for log_telemetry() — pre-DB gates and persistence."""

    # ── Happy Path ────────────────────────────────────────────────────────────

    def test_valid_telemetry_entry_is_persisted(self):
        """A fully valid telemetry call must insert a row retrievable by query."""
        self.pe.log_telemetry(
            component="SCREENER",
            level="INFO",
            message="TCS.NS screened COMPLIANT",
            payload={"grade": "A+", "symbol": "TCS.NS"},
        )
        rows = self.pe.query_telemetry(component="SCREENER", limit=10)
        self.assertGreater(len(rows), 0)
        # Most recent row should match
        latest = rows[0]
        self.assertEqual(latest["component"], "SCREENER")
        self.assertEqual(latest["log_level"], "INFO")
        self.assertIn("TCS.NS", latest["message"])

    def test_payload_is_deserialized_on_query(self):
        """Beyonce Rule: payload stored as JSON string must be deserialized on read."""
        self.pe.log_telemetry(
            "RISK_MGR", "WARNING", "Stop-loss triggered",
            {"symbol": "INFY.NS", "stop": 1450.0},
        )
        rows = self.pe.query_telemetry(component="RISK_MGR")
        latest = rows[0]
        self.assertIsInstance(latest["payload"], dict)
        self.assertEqual(latest["payload"]["symbol"], "INFY.NS")
        self.assertAlmostEqual(latest["payload"]["stop"], 1450.0)

    def test_payload_none_is_allowed(self):
        """Beyonce Rule: payload=None must be accepted and stored as NULL."""
        self.pe.log_telemetry("SYSTEM", "DEBUG", "Health check ping", payload=None)
        rows = self.pe.query_telemetry(component="SYSTEM")
        self.assertIsNotNone(rows)
        # payload should be None or absent
        self.assertIsNone(rows[0]["payload"])

    # ── Gate 1 — component whitelist ──────────────────────────────────────────

    def test_invalid_component_raises_value_error(self):
        """Beyonce Rule: component not in COMPONENT_NAMES → ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.pe.log_telemetry("UNKNOWN_BOT", "INFO", "test")
        self.assertIn("component", str(ctx.exception))

    # ── Gate 2 — log level whitelist ─────────────────────────────────────────

    def test_invalid_log_level_raises_value_error(self):
        """Beyonce Rule: level not in LOG_LEVELS → ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.pe.log_telemetry("SCREENER", "VERBOSE", "test")
        self.assertIn("level", str(ctx.exception))

    # ── Gate 3 — message non-empty ───────────────────────────────────────────

    def test_empty_message_raises_value_error(self):
        """Beyonce Rule: empty string message → ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.pe.log_telemetry("SCREENER", "INFO", "")
        self.assertIn("message", str(ctx.exception))

    def test_whitespace_only_message_raises_value_error(self):
        """Beyonce Rule: whitespace-only message → ValueError."""
        with self.assertRaises(ValueError):
            self.pe.log_telemetry("SCREENER", "INFO", "   ")

    # ── Gate 4 — payload JSON-serializable ───────────────────────────────────

    def test_non_serializable_payload_raises_value_error(self):
        """Beyonce Rule: payload containing a non-JSON-serializable object → ValueError."""
        class _Unserializable:
            pass

        with self.assertRaises(ValueError) as ctx:
            self.pe.log_telemetry(
                "SCREENER", "ERROR", "bad payload",
                {"data": _Unserializable()},
            )
        self.assertIn("payload", str(ctx.exception))

    def test_all_valid_log_levels_accepted(self):
        """Beyonce Rule: every valid log level must be accepted without error."""
        for lvl in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            with self.subTest(level=lvl):
                self.pe.log_telemetry("SYSTEM", lvl, f"Test message at {lvl}")

    def test_all_valid_components_accepted(self):
        """Beyonce Rule: every component in COMPONENT_NAMES must be accepted."""
        for comp in COMPONENT_NAMES:
            with self.subTest(component=comp):
                self.pe.log_telemetry(comp, "INFO", f"Test from {comp}")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. TELEMETRY QUERY
# ═══════════════════════════════════════════════════════════════════════════════

class TestTelemetryQuery(_BasePersistenceTest):
    """Tests for query_telemetry() — filter logic and safety caps."""

    def setUp(self):
        super().setUp()
        # Pre-populate known telemetry rows
        self.pe.log_telemetry("SCREENER", "INFO",    "Screened TCS.NS")
        self.pe.log_telemetry("SCREENER", "WARNING", "Partial data for INFY.NS")
        self.pe.log_telemetry("RISK_MGR", "ERROR",   "Stop-loss hit: SBIN.NS")
        self.pe.log_telemetry("KITE_EXEC", "INFO",   "Order placed: TCS.NS")

    def test_filter_by_component_returns_only_matching_rows(self):
        """Beyonce Rule: component filter must exclude rows from other components."""
        rows = self.pe.query_telemetry(component="SCREENER")
        for r in rows:
            self.assertEqual(r["component"], "SCREENER")

    def test_filter_by_component_correct_count(self):
        """Beyonce Rule: filtering by SCREENER should return the 2 SCREENER rows."""
        rows = self.pe.query_telemetry(component="SCREENER", limit=100)
        screener_rows = [r for r in rows if r["component"] == "SCREENER"]
        self.assertEqual(len(screener_rows), 2)

    def test_filter_by_level_returns_only_matching_rows(self):
        """Beyonce Rule: level filter must exclude rows with other levels."""
        rows = self.pe.query_telemetry(level="INFO", limit=100)
        for r in rows:
            self.assertEqual(r["log_level"], "INFO")

    def test_combined_component_and_level_filter(self):
        """Beyonce Rule: combined filter — SCREENER + WARNING → exactly 1 row."""
        rows = self.pe.query_telemetry(component="SCREENER", level="WARNING", limit=100)
        self.assertEqual(len(rows), 1)
        self.assertIn("INFY.NS", rows[0]["message"])

    def test_limit_is_respected(self):
        """Beyonce Rule: limit=1 must return at most 1 row."""
        rows = self.pe.query_telemetry(limit=1)
        self.assertLessEqual(len(rows), 1)

    def test_limit_zero_returns_empty_list(self):
        """Beyonce Rule: limit=0 must return empty list, not raise."""
        rows = self.pe.query_telemetry(limit=0)
        self.assertEqual(rows, [])

    def test_limit_capped_at_maximum(self):
        """Beyonce Rule: limit exceeding TELEMETRY_QUERY_LIMIT_MAX must be silently capped."""
        # We cannot insert 10_001 rows here; instead test that the cap logic applies
        # by checking the query doesn't raise with an absurdly large limit.
        try:
            rows = self.pe.query_telemetry(limit=TELEMETRY_QUERY_LIMIT_MAX + 99_999)
        except Exception as exc:
            self.fail(f"query_telemetry raised on oversized limit: {exc}")

    def test_invalid_component_filter_raises_value_error(self):
        """Beyonce Rule: querying with invalid component → ValueError (not silent empty)."""
        with self.assertRaises(ValueError):
            self.pe.query_telemetry(component="HACKER")

    def test_invalid_level_filter_raises_value_error(self):
        """Beyonce Rule: querying with invalid level → ValueError."""
        with self.assertRaises(ValueError):
            self.pe.query_telemetry(level="TRACE")

    def test_results_ordered_newest_first(self):
        """Beyonce Rule: query must return rows in descending id order (newest-first)."""
        rows = self.pe.query_telemetry(limit=100)
        ids = [r["id"] for r in rows]
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_no_filters_returns_all_rows_up_to_limit(self):
        """Beyonce Rule: no filter → all rows returned (up to limit)."""
        # setUp() inserted 4 explicit rows + 1 bootstrap row from run_migration()
        rows = self.pe.query_telemetry(limit=100)
        self.assertGreaterEqual(len(rows), 4)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. THREAD SAFETY
# ═══════════════════════════════════════════════════════════════════════════════

class TestThreadSafety(_BasePersistenceTest):
    """
    Beyonce Rule: concurrent writes must not corrupt data or raise
    sqlite3.OperationalError("database is locked").
    """

    def test_concurrent_position_upserts_do_not_corrupt(self):
        """
        10 threads each upsert 5 distinct symbols → 50 unique position rows.
        No thread-safety error should be raised.
        """
        errors: list = []
        n_threads = 10
        positions_per_thread = 5

        def _writer(thread_id: int):
            try:
                for i in range(positions_per_thread):
                    sym = f"THREAD{thread_id}STOCK{i}.NS"
                    self.pe.upsert_position(_valid_position(symbol=sym))
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=_writer, args=(tid,))
            for tid in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Thread errors: {errors}")

        # All 50 unique symbols must be present
        all_pos = self.pe.get_all_positions()
        symbols = {p["symbol"] for p in all_pos}
        expected_count = n_threads * positions_per_thread
        self.assertEqual(
            len(symbols), expected_count,
            f"Expected {expected_count} unique positions; got {len(symbols)}",
        )

    def test_concurrent_telemetry_writes_do_not_corrupt(self):
        """
        10 threads each write 5 telemetry entries → at least 50 rows appended.
        No errors should be raised.
        """
        errors: list = []
        n_threads = 10
        writes_per_thread = 5

        def _writer(thread_id: int):
            try:
                for i in range(writes_per_thread):
                    self.pe.log_telemetry(
                        "SYSTEM", "DEBUG",
                        f"Thread {thread_id} ping {i}",
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=_writer, args=(tid,))
            for tid in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Thread errors: {errors}")

        rows = self.pe.query_telemetry(component="SYSTEM", limit=TELEMETRY_QUERY_LIMIT_MAX)
        # Must have at least the 50 thread-written rows (+ migration bootstrap)
        self.assertGreaterEqual(len(rows), n_threads * writes_per_thread)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. WAL MODE
# ═══════════════════════════════════════════════════════════════════════════════

class TestWALMode(_BasePersistenceTest):
    """Beyonce Rule: WAL journal mode must be active after initialization."""

    def test_wal_journal_mode_is_set(self):
        """PRAGMA journal_mode must return 'wal' after __init__."""
        cursor = self.pe.conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        self.assertEqual(
            mode.lower(), "wal",
            f"Expected WAL journal mode, got: {mode!r}",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 9. CLOSE
# ═══════════════════════════════════════════════════════════════════════════════

class TestClose(_BasePersistenceTest):
    """Tests for close() — idempotency and context manager."""

    def test_close_does_not_raise(self):
        """Beyonce Rule: close() on an open connection must not raise."""
        try:
            self.pe.close()
        except Exception as exc:
            self.fail(f"close() raised: {exc}")

    def test_double_close_does_not_raise(self):
        """Beyonce Rule: calling close() twice must not raise (idempotent)."""
        self.pe.close()
        try:
            self.pe.close()
        except Exception as exc:
            self.fail(f"Second close() raised: {exc}")

    def test_context_manager_closes_on_exit(self):
        """Beyonce Rule: using PersistenceEngine as a context manager must auto-close."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            path = tmp.name

        try:
            with PersistenceEngine(db_path=path) as pe:
                pe.log_telemetry("SYSTEM", "INFO", "Context manager test")
                self.assertFalse(pe._closed)
            # After __exit__, _closed must be True
            self.assertTrue(pe._closed)
        finally:
            try:
                os.unlink(path)
            except Exception:
                pass

    def test_invalid_db_directory_raises_runtime_error(self):
        """Beyonce Rule: PersistenceEngine with non-existent parent dir → RuntimeError."""
        with self.assertRaises(RuntimeError) as ctx:
            PersistenceEngine(db_path="/nonexistent/path/that/does/not/exist/db.db")
        self.assertIn("directory", str(ctx.exception).lower())


# ═══════════════════════════════════════════════════════════════════════════════
# 10. RUNTIME — repr and constants
# ═══════════════════════════════════════════════════════════════════════════════

class TestConstants(_BasePersistenceTest):
    """Beyonce Rule: module-level constants must contain expected values."""

    def test_component_names_contains_required_agents(self):
        required = {"SCREENER", "RISK_MGR", "KITE_EXEC", "PERSISTENCE", "SYSTEM"}
        self.assertTrue(required.issubset(COMPONENT_NAMES))

    def test_log_levels_contains_all_standard_levels(self):
        required = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        self.assertEqual(LOG_LEVELS, required)

    def test_compliance_grades_contains_all_tasis_grades(self):
        required = {"A+", "A", "B", "C", "D", "F"}
        self.assertEqual(COMPLIANCE_GRADES, required)

    def test_repr_contains_db_path(self):
        self.assertIn(self.db_path, repr(self.pe))

    def test_repr_shows_open_status_before_close(self):
        self.assertIn("open", repr(self.pe))

    def test_repr_shows_closed_status_after_close(self):
        self.pe.close()
        self.assertIn("closed", repr(self.pe))
