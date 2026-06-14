import pytest
from unittest.mock import patch, MagicMock
from src.agents.telegram_sentinel import TelegramSentinel
from src.database.supabase_broker import SupabasePortfolioManager

@patch("src.agents.telegram_sentinel.requests.post")
@patch("src.agents.telegram_sentinel.SupabasePortfolioManager.fetch_active_positions")
@patch("src.agents.telegram_sentinel.SupabasePortfolioManager.update_position_status")
@patch("src.agents.telegram_sentinel.MarketDataClient.fetch_ticker_data")
@patch("src.agents.telegram_sentinel.os.getenv")
def test_telegram_sentinel_breach_alerting(mock_getenv, mock_fetch_ticker, mock_update_status, mock_fetch_active, mock_requests_post):
    """
    Mock a simulated breach event generating a properly formatted payload string to Telegram 
    with clean symbol data, tracking dates, and explicit exit pricing indicators.
    """
    # Force dotenv vars to mock values
    def mock_env(key, default=None):
        if key == "TELEGRAM_BOT_TOKEN": return "mock_token"
        if key == "TELEGRAM_CHAT_ID": return "123"
        return default
    mock_getenv.side_effect = mock_env

    # Setup the mock environment for active positions
    mock_fetch_active.return_value = [
        {"id": 1, "symbol": "RELIANCE.NS", "stop_loss": 2800.0, "target": 3000.0, "status": "ACTIVE"}
    ]
    
    # Simulate current price plunging through the stop loss
    mock_fetch_ticker.return_value = {
        "current_price": 2795.0,  # Below 2800
        "rsi_value": 30.0,
        "volume_anomaly": False,
        "hmm_regime": "BEAR"
    }
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_requests_post.return_value = mock_response
    
    sentinel = TelegramSentinel()
    sentinel.check_breaches()
    
    # Assert Supabase state was inverted
    mock_update_status.assert_called_once_with(1, "CLOSED")
    
    # Assert Telegram was called exactly once
    mock_requests_post.assert_called_once()
    
    # Validate payload JSON formatting
    called_url = mock_requests_post.call_args[0][0]
    called_json = mock_requests_post.call_args[1]["json"]
    
    assert "mock_token" in called_url
    assert called_json["chat_id"] == "123"
    assert "🚨 *URGENT EXIT ADVICE*: RELIANCE.NS breached STOP-LOSS at 2795.00!" in called_json["text"]
    assert called_json["parse_mode"] == "Markdown"
