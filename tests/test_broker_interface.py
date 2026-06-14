import time
from unittest.mock import patch
import pytest

from src.execution.broker_interface import KiteExecutionEngine, MarketSessionExpiredException

class MockPersistenceEngine:
    def log_telemetry(self, component, data):
        pass

def test_rate_limiter_stress_test():
    """
    Fire 15 mock orders in a tight loop. 
    Assert total execution time takes more than 1 second to stay under 10 orders/sec threshold.
    """
    engine = KiteExecutionEngine(MockPersistenceEngine(), "dummy_api", "dummy_token", paper_mode=True)
    
    order_intent = {
        'product': 'CNC',
        'algo_id': 'SEBI-ALGO-20231010-TEST',
        'symbol': 'TCS',
        'entry_price': 100.0,
        'qty': 10
    }
    
    start_time = time.time()
    for _ in range(15):
        # Mocking time check so tests do not fail on real clock time
        with patch.object(engine, 'enforce_daily_session_reset', return_value=None):
            engine.place_cnc_order(order_intent)
            
    end_time = time.time()
    elapsed = end_time - start_time
    
    assert elapsed >= 1.0

def test_product_type_guardrail():
    """
    Pass an order with product 'MIS' or 'MARGIN' and assert that it throws a ValueError.
    """
    engine = KiteExecutionEngine(MockPersistenceEngine(), "dummy_api", "dummy_token", paper_mode=True)
    
    order_intent_mis = {
        'product': 'MIS',
        'algo_id': 'SEBI-ALGO-20231010-TEST',
        'symbol': 'TCS',
        'entry_price': 100.0,
        'qty': 10
    }
    with patch.object(engine, 'enforce_daily_session_reset', return_value=None):
        with pytest.raises(ValueError, match="SEBI Violation: Only CNC allowed"):
            engine.place_cnc_order(order_intent_mis)
            
    order_intent_margin = {
        'product': 'MARGIN',
        'algo_id': 'SEBI-ALGO-20231010-TEST',
        'symbol': 'TCS',
        'entry_price': 100.0,
        'qty': 10
    }
    with patch.object(engine, 'enforce_daily_session_reset', return_value=None):
        with pytest.raises(ValueError, match="SEBI Violation: Only CNC allowed"):
            engine.place_cnc_order(order_intent_margin)

def test_algo_id_missing_guardrail():
    """
    Ensure the SEBI Algo ID is required for execution.
    """
    engine = KiteExecutionEngine(MockPersistenceEngine(), "dummy_api", "dummy_token", paper_mode=True)
    order_intent = {
        'product': 'CNC',
        'symbol': 'TCS',
        'entry_price': 100.0,
        'qty': 10
    }
    with patch.object(engine, 'enforce_daily_session_reset', return_value=None):
        with pytest.raises(ValueError, match="SEBI Violation: Missing Algo ID"):
            engine.place_cnc_order(order_intent)

def test_session_expiry_gate():
    """
    Mock system time to 15:31:00 IST and assert MarketSessionExpiredException is raised,
    credentials are wiped, and active flag is set to False.
    """
    engine = KiteExecutionEngine(MockPersistenceEngine(), "dummy_api", "dummy_token", paper_mode=True)
    
    import datetime
    from zoneinfo import ZoneInfo
    mock_time = datetime.datetime(2023, 10, 10, 15, 31, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    
    with patch('src.execution.broker_interface.datetime') as mock_datetime:
        mock_datetime.now.return_value = mock_time
        
        with pytest.raises(MarketSessionExpiredException, match="SEBI Violation: Market Session Expired at 15:30 IST"):
            engine.enforce_daily_session_reset()
            
        assert engine.active is False
        assert engine.api_key is None
        assert engine.access_token is None

def test_brokerage_slippage_math():
    """
    Verify CNC brokerage is 0.0, STT is exactly 0.1%, and execution price has 
    0.02% to 0.05% slippage applied accurately.
    """
    engine = KiteExecutionEngine(MockPersistenceEngine(), "dummy_api", "dummy_token", paper_mode=True)
    
    metrics = engine.simulate_brokerage_and_slippage("TCS", 100.0, 100)
    
    trade_value = 10000.0
    expected_stt = 0.001 * trade_value
    expected_trans = 0.0000345 * trade_value
    expected_gst = 0.18 * expected_trans
    expected_total_charges = expected_stt + expected_trans + expected_gst
    
    assert metrics['total_charges'] == pytest.approx(expected_total_charges)
    assert metrics['executed_price'] > 100.0
    assert metrics['executed_price'] <= 100.0 * 1.0005
    assert metrics['executed_price'] >= 100.0 * 1.0002
