import sqlite3
import pytest
from unittest.mock import MagicMock

from src.database.manual_portfolio import ManualPortfolioManager
from src.agents.news_analyzer import GlobalNewsAnalyzer

class MockPersistenceEngine:
    def __init__(self):
        self.last_log = None
        # Setup an in-memory db just for the mock to test queries safely
        self.conn = sqlite3.connect(':memory:')
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE halal_screening_results (
                id INTEGER PRIMARY KEY,
                ticker TEXT,
                sector TEXT,
                overall_status TEXT
            )
        ''')
        # Insert a compliant Energy stock
        cursor.execute("INSERT INTO halal_screening_results (ticker, sector, overall_status) VALUES ('ONGC.NS', 'Energy', 'COMPLIANT')")
        self.conn.commit()

    def _get_connection(self):
        return self.conn
        
    def log_telemetry(self, component, data):
        self.last_log = (component, data)

# --- NLP Narrative & Banking Filter Execution Gate Tests ---

def test_haram_event_ignorer():
    """
    Inject a banking sector news update and assert generate_event_driven_signals 
    emits an empty list while logging a tracking event.
    """
    pe = MockPersistenceEngine()
    screener = MagicMock()
    analyzer = GlobalNewsAnalyzer(pe, screener)
    
    events = ["Central bank drops structural base borrowing rate causing banking sector rally"]
    signals = analyzer.generate_event_driven_signals(events)
    
    # Must emit strictly ZERO buy signals for banking events
    assert len(signals) == 0
    
    # Must log an interactive track warning to telemetry
    assert pe.last_log is not None
    assert pe.last_log[0] == 'NEWS_ANALYZER'
    assert pe.last_log[1]['sector'] == 'Banking'
    assert pe.last_log[1]['action'] == 'IGNORE - NON-COMPLIANT'

def test_reason_appended():
    """
    Verify that valid Halal energy stocks receive complete string configurations 
    in their reason elements matching the geopolitical raw arrays.
    """
    pe = MockPersistenceEngine()
    screener = MagicMock()
    analyzer = GlobalNewsAnalyzer(pe, screener)
    
    event_str = "Crude Oil surges due to regional tensions in the Middle East"
    signals = analyzer.generate_event_driven_signals([event_str])
    
    assert len(signals) == 1
    sig = signals[0]
    assert sig['symbol'] == 'ONGC.NS'
    assert sig['action'] == 'BUY'
    assert 'reason' in sig
    assert event_str in sig['reason']
    assert 'Energy sector is bullish' in sig['reason']
    assert 'Compliant' in sig['reason']

# --- Manual Advisory Exit Parity Tests ---

def test_manual_exit_trigger():
    """
    Mock an active stock row at entry 100, SL 90. Pass a current price of 89.5 
    and assert evaluate_manual_exits flags the target exit signal cleanly.
    """
    import tempfile
    import os
    
    # Use an isolated file DB for portfolio manager so we don't hit closed memory connections
    fd, temp_db_path = tempfile.mkstemp()
    os.close(fd)
    
    try:
        mgr = ManualPortfolioManager(db_path=temp_db_path)
        mgr.add_manual_position('TCS', 100.0, 10, 90.0, 120.0)
        
        # Current price falls strictly below the stop_loss
        market_data = {'TCS': 89.5}
        exits = mgr.evaluate_manual_exits(market_data)
        
        assert len(exits) == 1
        assert exits[0]['symbol'] == 'TCS'
        assert exits[0]['action'] == 'EXIT'
        assert exits[0]['reason'] == 'Boundary crossed. Triggering exit advice.'
        
        # Verify the status was updated to CLOSED
        conn = sqlite3.connect(temp_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM manual_portfolio WHERE symbol = 'TCS'")
        status = cursor.fetchone()[0]
        conn.close()
        
        assert status == 'CLOSED'
    finally:
        if os.path.exists(temp_db_path):
            os.remove(temp_db_path)
