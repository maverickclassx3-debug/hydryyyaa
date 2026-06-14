import pytest
from unittest.mock import MagicMock, patch
from src.orchestrator import SystemOrchestrator

class MockPersistenceEngine:
    def _get_connection(self):
        conn = MagicMock()
        cursor = MagicMock()
        # Mock database read returning 'COMPLIANT'
        cursor.fetchone.return_value = ('COMPLIANT',)
        conn.cursor.return_value = cursor
        return conn
        
    def log_telemetry(self, component, data):
        self.last_log = (component, data)

def test_run_pipeline_iteration_full_pass():
    """
    Mock successful data pipeline outputs to ensure that valid tickers pass safely 
    from screening filters to active broker queue execution instances.
    """
    screener = MagicMock()
    alpha = MagicMock()
    
    # Mocking order intent return from AlphaEngine
    intent = {
        'product': 'CNC',
        'algo_id': 'SEBI-ALGO-20231010-TEST',
        'symbol': 'TCS',
        'entry_price': 100.0,
        'qty': 10
    }
    alpha.orchestrate_signals.return_value = [intent]
    
    broker = MagicMock()
    pe = MockPersistenceEngine()
    
    orchestrator = SystemOrchestrator(pe, screener, alpha, broker)
    
    with patch('src.orchestrator.yf.Ticker') as mock_ticker:
        # Mock historical dataframe return
        mock_df = MagicMock()
        mock_df.empty = False
        mock_ticker.return_value.history.return_value = mock_df
        
        # Execute the iteration for one symbol
        orchestrator.run_pipeline_iteration(['TCS'], 'BULL')
    
    # Verify screen_stock was called to update DB compliance
    screener.screen_stock.assert_called_once_with('TCS')
    
    # Verify alpha_engine received the data mapping
    alpha.orchestrate_signals.assert_called_once_with({'TCS': mock_df}, market_regime='BULL')
    
    # Verify broker correctly received the parsed order intent
    broker.place_cnc_order.assert_called_once_with(intent)

def test_run_pipeline_iteration_error_handling():
    """
    Ensure internal component crashes are caught and written to system_telemetry
    without killing the master loop.
    """
    screener = MagicMock()
    screener.screen_stock.side_effect = ValueError("Screener crashed during network call")
    
    alpha = MagicMock()
    broker = MagicMock()
    pe = MockPersistenceEngine()
    
    orchestrator = SystemOrchestrator(pe, screener, alpha, broker)
    
    # Executing against a symbol that will trigger the side_effect
    orchestrator.run_pipeline_iteration(['RELIANCE'], 'BEAR')
    
    # The error should be gracefully caught and logged to telemetry
    assert hasattr(pe, 'last_log')
    assert pe.last_log[0] == 'ORCHESTRATOR'
    assert 'Screener crashed' in pe.last_log[1]['error']
    assert 'RELIANCE' == pe.last_log[1]['symbol']
