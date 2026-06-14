"""
persistence_engine.py
=====================
Layer 1, Part B — Unified Persistence Engine & Telemetry Ledger
Halal AI World Monitor · Phase 1 · Week 1

Central, thread-safe persistence layer shared by all platform agents:
    SCREENER, RISK_MGR, KITE_EXEC, and future components.

Architecture:
    PersistenceEngine(db_path)
        │
        ├── run_migration()
        │       → Additive CREATE TABLE IF NOT EXISTS (never DROP)
        │       → active_positions   (strict CNC: long-only, SEBI algo_id)
        │       → system_telemetry   (append-only audit stream)
        │
        ├── upsert_position(position_dict)
        │       → 7 pre-DB validation gates (Doubt-Driven)
        │       → threading.Lock + with conn: atomic write
        │       → INSERT OR REPLACE (last-write-wins for same symbol)
        │
        ├── close_position(symbol) → bool
        │       → DELETE + telemetry audit log
        │
        ├── get_position(symbol) → Optional[dict]
        ├── get_all_positions() → List[dict]
        │
        ├── log_telemetry(component, level, message, payload)
        │       → 4 pre-DB validation gates
        │       → threading.Lock + with conn: atomic INSERT
        │
        ├── query_telemetry(component, level, limit) → List[dict]
        │       → Read-only SELECT; limit capped at 10_000
        │
        └── close()
                → Idempotent connection teardown

Thread-Safety Model:
    SQLite is opened with check_same_thread=False and PRAGMA journal_mode=WAL.
    WAL allows concurrent reads; writes are serialized via self._write_lock
    (threading.Lock). This prevents sqlite3.OperationalError("database is locked")
    under parallel agent access patterns.

SEBI 2026 Compliance:
    algo_id is mandatory on every active_positions record. upsert_position()
    raises ValueError before touching the DB if algo_id is absent or empty.
    This is a hard pre-commit compliance gate, not merely a DB constraint.

CNC (Cash-and-Carry) Discipline:
    quantity  must be > 0  (long-only enforcement — no short selling)
    stop_loss must be > 0 and < entry_price
    profit_target must be > entry_price
    These are Python-level pre-checks, applied before the SQL layer.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import date, datetime
from typing import Any, Dict, List, Optional

# ─── Structured logger ────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# MODULE-LEVEL CONSTANTS  (whitelists used by pre-DB validation gates)
# ═══════════════════════════════════════════════════════════════════════════════

COMPONENT_NAMES: frozenset = frozenset({
    "SCREENER",
    "RISK_MGR",
    "KITE_EXEC",
    "PERSISTENCE",
    "SYSTEM",
    "DATA_FEED",
    "PORTFOLIO_MGR",
    "NOTIFIER",
})

LOG_LEVELS: frozenset = frozenset({
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
})

COMPLIANCE_GRADES: frozenset = frozenset({
    "A+", "A", "B", "C", "D", "F",
})

TELEMETRY_QUERY_LIMIT_MAX: int = 10_000  # Hard ceiling on query result size

# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA DDL — Additive migration only.  Never DROP or ALTER existing tables.
# ═══════════════════════════════════════════════════════════════════════════════

_DDL_ACTIVE_POSITIONS: str = """
CREATE TABLE IF NOT EXISTS active_positions (
    symbol                  TEXT    PRIMARY KEY,
    entry_date              DATE    NOT NULL,
    entry_price             REAL    NOT NULL CHECK(entry_price > 0),
    quantity                INTEGER NOT NULL CHECK(quantity > 0),
    total_capital           REAL    NOT NULL CHECK(total_capital > 0),
    current_stop_loss       REAL    NOT NULL CHECK(current_stop_loss > 0),
    trailing_profit_target  REAL    NOT NULL CHECK(trailing_profit_target > 0),
    algo_id                 TEXT    NOT NULL,
    compliance_grade        TEXT    NOT NULL
        CHECK(compliance_grade IN ('A+', 'A', 'B', 'C', 'D', 'F'))
);
"""

_DDL_SYSTEM_TELEMETRY: str = """
CREATE TABLE IF NOT EXISTS system_telemetry (
    id          INTEGER   PRIMARY KEY AUTOINCREMENT,
    timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    component   TEXT      NOT NULL,
    log_level   TEXT      CHECK(log_level IN ('DEBUG','INFO','WARNING','ERROR','CRITICAL')),
    message     TEXT      NOT NULL,
    payload     TEXT
);
"""

# WAL mode is set at connection time — not a DDL statement, but pragmas run once.
_PRAGMA_WAL: str      = "PRAGMA journal_mode=WAL;"
_PRAGMA_FK: str       = "PRAGMA foreign_keys=ON;"
_PRAGMA_CACHE: str    = "PRAGMA cache_size=-8000;"   # 8 MB page cache


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER — validate ISO date string
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_iso_date(value: Any, field_name: str) -> str:
    """
    Validate and normalize an ISO 8601 date string (YYYY-MM-DD).

    Args:
        value: Input to validate.
        field_name: Name of the field for error messages.

    Returns:
        Validated date string.

    Raises:
        ValueError: If value is not a valid YYYY-MM-DD string.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"[PersistenceEngine] '{field_name}' must be a non-empty string; "
            f"got {type(value).__name__!r}: {value!r}"
        )
    try:
        datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError:
        raise ValueError(
            f"[PersistenceEngine] '{field_name}' must be ISO date (YYYY-MM-DD); "
            f"got {value!r}"
        )
    return value.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class PersistenceEngine:
    """
    Thread-safe, centralized persistence layer for the Halal AI World Monitor.

    Manages two new tables inside the existing trade_data.db:
        active_positions  — real-time CNC position tracking
        system_telemetry  — structured operational audit stream

    The engine does NOT modify or access halal_screening_results or any other
    pre-existing tables. It is a pure additive extension.

    Thread-Safety:
        self._write_lock (threading.Lock) serializes all write operations.
        Reads are lock-free (SQLite WAL allows concurrent reads).

    Usage:
        pe = PersistenceEngine("trade_data.db")
        pe.upsert_position({...})
        pe.log_telemetry("SCREENER", "INFO", "TCS.NS screened", {"grade": "A+"})
        pe.close()
    """

    def __init__(self, db_path: str = "trade_data.db") -> None:
        """
        Initialize the persistence engine and run the additive schema migration.

        Args:
            db_path: Path to the SQLite database file.
                     The file will be created if it does not exist.
                     The parent directory must already exist.

        Raises:
            RuntimeError: If the parent directory of db_path does not exist.
        """
        # ── Guard: parent directory must exist ───────────────────────────────
        db_dir = os.path.dirname(os.path.abspath(db_path))
        if not os.path.isdir(db_dir):
            raise RuntimeError(
                f"[PersistenceEngine.__init__] Database directory does not exist: "
                f"'{db_dir}'. Create the directory before initializing PersistenceEngine."
            )

        self.db_path = db_path
        self._closed = False

        # ── Write-serialization lock ──────────────────────────────────────────
        # All write operations (INSERT / UPDATE / DELETE) acquire this lock.
        # Read operations are lock-free; WAL mode handles read-write isolation.
        self._write_lock = threading.Lock()

        # ── Open connection (shared across threads) ───────────────────────────
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        # ── Apply connection-level PRAGMAs ────────────────────────────────────
        # Must be run outside a transaction (executescript auto-commits).
        self.conn.executescript(
            f"{_PRAGMA_WAL}\n{_PRAGMA_FK}\n{_PRAGMA_CACHE}"
        )

        # ── Run additive schema migration ─────────────────────────────────────
        self.run_migration()

        logger.info(
            "[PersistenceEngine] Initialized. DB='%s' | WAL=ON | Lock=READY",
            db_path,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # §4 — Schema Migration (Additive, Idempotent)
    # ──────────────────────────────────────────────────────────────────────────

    def run_migration(self) -> None:
        """
        Apply additive schema migration to the database.

        Creates active_positions and system_telemetry tables if they do not
        already exist. Safe to call multiple times — fully idempotent.
        Does NOT touch any pre-existing tables (halal_screening_results, etc.).

        Raises:
            sqlite3.DatabaseError: If the DDL itself is malformed (should never
                happen in production — indicates a code-level bug).
        """
        with self._write_lock:
            with self.conn:
                self.conn.executescript(
                    _DDL_ACTIVE_POSITIONS + "\n" + _DDL_SYSTEM_TELEMETRY
                )

        logger.info("[PersistenceEngine] Schema migration complete (additive).")

        # Bootstrap: write a telemetry entry confirming the migration ran.
        # Use a direct INSERT to avoid circular calls through the public API
        # during the first-ever initialization (before system_telemetry exists).
        try:
            with self._write_lock:
                with self.conn:
                    self.conn.execute(
                        """
                        INSERT INTO system_telemetry
                            (component, log_level, message, payload)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            "PERSISTENCE",
                            "INFO",
                            "PersistenceEngine migration applied — tables ready.",
                            json.dumps({"db_path": self.db_path}),
                        ),
                    )
        except sqlite3.Error as exc:
            # Non-fatal: telemetry table may already hold rows — log and continue.
            logger.warning("[PersistenceEngine] Bootstrap telemetry insert failed: %s", exc)

    # ──────────────────────────────────────────────────────────────────────────
    # §5 — upsert_position
    # ──────────────────────────────────────────────────────────────────────────

    def upsert_position(self, position: Dict[str, Any]) -> None:
        """
        Insert or replace an active CNC equity position.

        Uses INSERT OR REPLACE, so calling with an existing symbol will
        overwrite the previous row (last-write-wins). Only one open position
        per symbol is tracked at any time.

        Args:
            position: Dictionary with the following mandatory keys:
                symbol                 (str)   — NSE ticker, e.g. "TCS.NS"
                entry_date             (str)   — ISO date "YYYY-MM-DD"
                entry_price            (float) — must be > 0
                quantity               (int)   — must be > 0 (long-only)
                total_capital          (float) — must be > 0
                current_stop_loss      (float) — must be > 0 AND < entry_price
                trailing_profit_target (float) — must be > entry_price
                algo_id                (str)   — SEBI 2026 token, non-empty
                compliance_grade       (str)   — one of A+ / A / B / C / D / F

        Raises:
            ValueError: If any validation gate fails (pre-DB, no DB write occurs).
            KeyError:   If a mandatory key is missing from position dict.
            sqlite3.DatabaseError: On unexpected DB-layer errors.
        """
        self._validate_position(position)

        sql = """
        INSERT OR REPLACE INTO active_positions (
            symbol, entry_date, entry_price, quantity,
            total_capital, current_stop_loss, trailing_profit_target,
            algo_id, compliance_grade
        ) VALUES (
            :symbol, :entry_date, :entry_price, :quantity,
            :total_capital, :current_stop_loss, :trailing_profit_target,
            :algo_id, :compliance_grade
        )
        """
        params = {
            "symbol":                 position["symbol"],
            "entry_date":             position["entry_date"],
            "entry_price":            float(position["entry_price"]),
            "quantity":               int(position["quantity"]),
            "total_capital":          float(position["total_capital"]),
            "current_stop_loss":      float(position["current_stop_loss"]),
            "trailing_profit_target": float(position["trailing_profit_target"]),
            "algo_id":                position["algo_id"],
            "compliance_grade":       position["compliance_grade"],
        }

        with self._write_lock:
            with self.conn:
                self.conn.execute(sql, params)

        logger.info(
            "[PersistenceEngine] Position upserted: %s | qty=%s | algo_id=%s",
            position["symbol"], position["quantity"], position["algo_id"],
        )

    def _validate_position(self, position: Dict[str, Any]) -> None:
        """
        Run all pre-DB validation gates for a position dict.

        Gate order:
            1. algo_id non-empty (SEBI 2026 compliance gate — first-fail-fast)
            2. entry_price > 0
            3. quantity > 0 (CNC long-only enforcement)
            4. total_capital > 0
            5. current_stop_loss > 0 AND < entry_price
            6. trailing_profit_target > entry_price
            7. compliance_grade in COMPLIANCE_GRADES
            8. entry_date is valid ISO date

        Raises:
            ValueError: On first failed gate.
            KeyError:   If a mandatory key is missing.
        """
        # Gate 1 — SEBI 2026: algo_id is mandatory
        algo_id = position.get("algo_id")
        if not algo_id or not str(algo_id).strip():
            raise ValueError(
                "[PersistenceEngine.upsert_position] 'algo_id' is required and must "
                "be non-empty — SEBI 2026 algorithmic trading compliance mandate."
            )

        # Gate 2 — entry_price
        entry_price = position.get("entry_price")
        if entry_price is None or float(entry_price) <= 0:
            raise ValueError(
                f"[PersistenceEngine.upsert_position] 'entry_price' must be > 0; "
                f"got {entry_price!r}"
            )
        entry_price = float(entry_price)

        # Gate 3 — quantity (long-only CNC enforcement)
        quantity = position.get("quantity")
        if quantity is None or int(quantity) <= 0:
            raise ValueError(
                f"[PersistenceEngine.upsert_position] 'quantity' must be > 0 "
                f"(CNC long-only; short selling is prohibited); got {quantity!r}"
            )

        # Gate 4 — total_capital
        total_capital = position.get("total_capital")
        if total_capital is None or float(total_capital) <= 0:
            raise ValueError(
                f"[PersistenceEngine.upsert_position] 'total_capital' must be > 0; "
                f"got {total_capital!r}"
            )

        # Gate 5 — stop_loss: must be > 0 AND strictly below entry_price
        stop_loss = position.get("current_stop_loss")
        if stop_loss is None or float(stop_loss) <= 0:
            raise ValueError(
                f"[PersistenceEngine.upsert_position] 'current_stop_loss' must be > 0; "
                f"got {stop_loss!r}"
            )
        if float(stop_loss) >= entry_price:
            raise ValueError(
                f"[PersistenceEngine.upsert_position] 'current_stop_loss' ({stop_loss}) "
                f"must be strictly less than 'entry_price' ({entry_price}). "
                "A stop-loss at or above entry price is a CNC risk violation."
            )

        # Gate 6 — trailing_profit_target: must be strictly above entry_price
        profit_target = position.get("trailing_profit_target")
        if profit_target is None or float(profit_target) <= entry_price:
            raise ValueError(
                f"[PersistenceEngine.upsert_position] 'trailing_profit_target' "
                f"({profit_target}) must be strictly greater than 'entry_price' "
                f"({entry_price})."
            )

        # Gate 7 — compliance_grade whitelist
        grade = position.get("compliance_grade")
        if grade not in COMPLIANCE_GRADES:
            raise ValueError(
                f"[PersistenceEngine.upsert_position] 'compliance_grade' must be one of "
                f"{sorted(COMPLIANCE_GRADES)}; got {grade!r}"
            )

        # Gate 8 — entry_date ISO format
        _parse_iso_date(position.get("entry_date", ""), "entry_date")

    # ──────────────────────────────────────────────────────────────────────────
    # §6 — close_position
    # ──────────────────────────────────────────────────────────────────────────

    def close_position(self, symbol: str) -> bool:
        """
        Remove an active position from tracking (exit / stop-loss trigger).

        Args:
            symbol: NSE ticker of the position to close, e.g. "TCS.NS".

        Returns:
            True  — position existed and was deleted.
            False — symbol was not found in active_positions.
        """
        if not symbol or not isinstance(symbol, str):
            raise ValueError(
                f"[PersistenceEngine.close_position] 'symbol' must be a non-empty str; "
                f"got {symbol!r}"
            )

        with self._write_lock:
            with self.conn:
                cursor = self.conn.execute(
                    "DELETE FROM active_positions WHERE symbol = ?",
                    (symbol,),
                )
                deleted = cursor.rowcount > 0

        if deleted:
            logger.info("[PersistenceEngine] Position closed: %s", symbol)
            # Audit the close event in telemetry (uses public API — lock already released)
            self.log_telemetry(
                component="PERSISTENCE",
                level="INFO",
                message=f"Position closed: {symbol}",
                payload={"symbol": symbol, "closed_at": date.today().isoformat()},
            )
        else:
            logger.warning(
                "[PersistenceEngine] close_position called for unknown symbol: %s", symbol
            )

        return deleted

    # ──────────────────────────────────────────────────────────────────────────
    # §7 — get_position
    # ──────────────────────────────────────────────────────────────────────────

    def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a single active position by symbol.

        Args:
            symbol: NSE ticker, e.g. "TCS.NS".

        Returns:
            Dict with all position fields, or None if not found.
        """
        cursor = self.conn.execute(
            "SELECT * FROM active_positions WHERE symbol = ?",
            (symbol,),
        )
        row = cursor.fetchone()
        return dict(row) if row is not None else None

    # ──────────────────────────────────────────────────────────────────────────
    # §8 — get_all_positions
    # ──────────────────────────────────────────────────────────────────────────

    def get_all_positions(self) -> List[Dict[str, Any]]:
        """
        Retrieve all active positions.

        Returns:
            List of dicts (one per position). Empty list if no positions open.
        """
        cursor = self.conn.execute(
            "SELECT * FROM active_positions ORDER BY entry_date ASC"
        )
        return [dict(row) for row in cursor.fetchall()]

    # ──────────────────────────────────────────────────────────────────────────
    # §9 — log_telemetry
    # ──────────────────────────────────────────────────────────────────────────

    def log_telemetry(
        self,
        component: str,
        level: str,
        message: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Append a structured audit entry to the system_telemetry table.

        Pre-DB validation gates:
            1. component must be in COMPONENT_NAMES
            2. level must be in LOG_LEVELS
            3. message must be a non-empty string
            4. payload (if provided) must be JSON-serializable

        Args:
            component: Originating system component (e.g. "SCREENER").
            level:     Severity level (e.g. "INFO", "ERROR").
            message:   Human-readable log message.
            payload:   Optional dict of structured context data (JSON-serialized).

        Raises:
            ValueError: If any validation gate fails (pre-DB).
        """
        # Gate 1 — component whitelist
        if component not in COMPONENT_NAMES:
            raise ValueError(
                f"[PersistenceEngine.log_telemetry] 'component' must be one of "
                f"{sorted(COMPONENT_NAMES)}; got {component!r}"
            )

        # Gate 2 — log level whitelist
        if level not in LOG_LEVELS:
            raise ValueError(
                f"[PersistenceEngine.log_telemetry] 'level' must be one of "
                f"{sorted(LOG_LEVELS)}; got {level!r}"
            )

        # Gate 3 — message non-empty
        if not message or not str(message).strip():
            raise ValueError(
                "[PersistenceEngine.log_telemetry] 'message' must be a non-empty string."
            )

        # Gate 4 — payload JSON-serializable
        payload_json: Optional[str] = None
        if payload is not None:
            try:
                payload_json = json.dumps(payload)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"[PersistenceEngine.log_telemetry] 'payload' must be JSON-serializable; "
                    f"error: {exc}"
                ) from exc

        with self._write_lock:
            with self.conn:
                self.conn.execute(
                    """
                    INSERT INTO system_telemetry
                        (component, log_level, message, payload)
                    VALUES (?, ?, ?, ?)
                    """,
                    (component, level, message, payload_json),
                )

        logger.debug(
            "[Telemetry] [%s] [%s] %s", component, level, message
        )

    # ──────────────────────────────────────────────────────────────────────────
    # §10 — query_telemetry
    # ──────────────────────────────────────────────────────────────────────────

    def query_telemetry(
        self,
        component: Optional[str] = None,
        level: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Query the telemetry audit stream with optional filters.

        Args:
            component: If set, filter to rows from this component.
            level:     If set, filter to rows with this log level.
            limit:     Maximum number of rows to return.
                       Capped at TELEMETRY_QUERY_LIMIT_MAX (10_000).
                       Rows are returned newest-first (ORDER BY id DESC).

        Returns:
            List of dicts, each representing one telemetry row.
            payload field is deserialized from JSON string to dict/None.

        Raises:
            ValueError: If component or level is provided but invalid.
        """
        # Validate filters if provided (avoid silently returning empty results)
        if component is not None and component not in COMPONENT_NAMES:
            raise ValueError(
                f"[PersistenceEngine.query_telemetry] Invalid 'component': {component!r}. "
                f"Must be one of {sorted(COMPONENT_NAMES)}."
            )
        if level is not None and level not in LOG_LEVELS:
            raise ValueError(
                f"[PersistenceEngine.query_telemetry] Invalid 'level': {level!r}. "
                f"Must be one of {sorted(LOG_LEVELS)}."
            )

        # Cap limit — Doubt-Driven: prevent runaway memory allocation
        capped_limit = min(int(limit), TELEMETRY_QUERY_LIMIT_MAX)
        if capped_limit <= 0:
            return []

        # Build query dynamically with optional WHERE clauses
        clauses: List[str] = []
        params: List[Any] = []

        if component is not None:
            clauses.append("component = ?")
            params.append(component)
        if level is not None:
            clauses.append("log_level = ?")
            params.append(level)

        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT id, timestamp, component, log_level, message, payload
            FROM system_telemetry
            {where_sql}
            ORDER BY id DESC
            LIMIT ?
        """
        params.append(capped_limit)

        cursor = self.conn.execute(sql, params)
        rows = cursor.fetchall()

        results: List[Dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            # Deserialize payload JSON string → dict (or leave None)
            if d.get("payload") is not None:
                try:
                    d["payload"] = json.loads(d["payload"])
                except (json.JSONDecodeError, TypeError):
                    pass  # Leave as raw string if malformed
            results.append(d)

        return results

    # ──────────────────────────────────────────────────────────────────────────
    # §11 — close
    # ──────────────────────────────────────────────────────────────────────────

    def close(self) -> None:
        """
        Close the SQLite connection cleanly.

        Idempotent — calling close() on an already-closed engine is a no-op.
        """
        if self._closed:
            return
        try:
            self.conn.close()
            self._closed = True
            logger.info("[PersistenceEngine] Connection closed: '%s'", self.db_path)
        except sqlite3.Error as exc:
            logger.error("[PersistenceEngine] Error closing connection: %s", exc)

    # ──────────────────────────────────────────────────────────────────────────
    # Dunder helpers
    # ──────────────────────────────────────────────────────────────────────────

    def __enter__(self) -> "PersistenceEngine":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __repr__(self) -> str:
        status = "closed" if self._closed else "open"
        return f"PersistenceEngine(db='{self.db_path}', connection={status})"
