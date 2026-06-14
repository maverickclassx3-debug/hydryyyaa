import pytest
from unittest.mock import patch, MagicMock
from src.agents.council_orchestrator import TheFinalBossOrchestrator
from src.utils.api_clients import MarketDataClient, LLMInferenceClient

@patch("src.utils.api_clients.yf.Ticker")
def test_shariah_gatekeeper_veto(mock_ticker):
    """
    Pass a mock dictionary that is incredibly bullish (low RSI, high volume, bullish news) 
    but flag shariah_compliant = False. Assert that the Final Boss overrides the other 
    agents and returns STRICT HARAM BLOCK.
    """
    # Mock yfinance response
    mock_history = MagicMock()
    mock_history.empty = False
    mock_ticker.return_value.history.return_value = mock_history
    
    md_client = MarketDataClient()
    # Override fetch to return deterministic data
    md_client.fetch_ticker_data = MagicMock(return_value={
        "volume_anomaly": True, "price_stable": True,
        "hmm_regime": "BULL",
        "rsi_value": 30,
    })
    
    llm_client = LLMInferenceClient(api_key="mock_key")
    llm_client.query_model = MagicMock(return_value="BULLISH: Massive growth surge expected")
    
    orchestrator = TheFinalBossOrchestrator(market_data_client=md_client, llm_client=llm_client)
    
    base_context = {
        "shariah_compliant": False,  # The Veto
        "corporate_guidance": "AGGRESSIVE_GROWTH",
        "strategy_win_rate": 0.55,
        "is_crypto": False,
        "bid_ask_ratio": 2.0
    }
    
    result = orchestrator.synthesize_council_intelligence("AAPL", "Massive growth surge expected", base_context=base_context)
    
    assert result["final_signal"] == "STRICT HARAM BLOCK"
    assert result["allocation_pct"] == 0.0
    assert "Execution halted by Shariah Gatekeeper" in result["reason"]

@patch("src.utils.api_clients.yf.Ticker")
def test_buy_advice_consensus(mock_ticker):
    """
    Pass a bullish context meeting the >= 4 Bull votes threshold and <= 1 Bear votes.
    Assert BUY_ADVICE.
    """
    md_client = MarketDataClient()
    md_client.fetch_ticker_data = MagicMock(return_value={
        "volume_anomaly": True, "price_stable": True,
        "hmm_regime": "BULL",
        "rsi_value": 30,
    })
    
    llm_client = LLMInferenceClient(api_key="mock_key")
    # Return BULLISH for NewsBullAgent
    llm_client.query_model = MagicMock(return_value="BULLISH: Massive growth surge expected")
    
    orchestrator = TheFinalBossOrchestrator(market_data_client=md_client, llm_client=llm_client)
    
    base_context = {
        "shariah_compliant": True, 
        "corporate_guidance": "POSITIVE",
        "strategy_win_rate": 0.55,
        "is_crypto": False,
        "bid_ask_ratio": 1.0
    }
    
    result = orchestrator.synthesize_council_intelligence("AAPL", "Massive growth surge expected", base_context=base_context)
    
    assert result["final_signal"] == "BUY_ADVICE"
    assert "Total Bullish Consensus:" in result["reason"]

def test_hold_avoid_consensus():
    """
    Pass a neutral context. Assert HOLD / AVOID.
    """
    orchestrator = TheFinalBossOrchestrator()
    
    neutral_context = {
        "shariah_compliant": True, 
        "volume_anomaly": False, "price_stable": True,  # Neutral
        "hmm_regime": "NEUTRAL",  # Neutral
        "rsi_value": 50,  # Neutral
        "corporate_guidance": "POSITIVE",  # Neutral
        "strategy_win_rate": 0.55,
        "is_crypto": False,
        "bid_ask_ratio": 1.0  # Neutral
    }
    
    result = orchestrator.synthesize_council_intelligence("AAPL", "Nothing happening", base_context=neutral_context)
    
    assert result["final_signal"] == "HOLD / AVOID"
    assert "Total Bullish Consensus: 0/10" in result["reason"]

def test_kelly_position_sizer():
    """
    Verify that strategy_win_rate correctly modifies the Kelly output text preventing over-allocation.
    """
    orchestrator = TheFinalBossOrchestrator()
    
    context = {
        "shariah_compliant": True, 
        "strategy_win_rate": 0.55
    }
    
    result = orchestrator.synthesize_council_intelligence("AAPL", "Test", base_context=context)
    
    # Check that Kelly allocator hit the 15.00% cap
    assert "15.00%" in result["allocation_pct"]
    
    context["strategy_win_rate"] = 0.35
    result = orchestrator.synthesize_council_intelligence("AAPL", "Test", base_context=context)
    
    assert "2.50%" in result["allocation_pct"]
