"""
tests/test_alpha_engine.py
==========================
Complete test suite for AlphaEngine (Phase 2).
Enforces the Beyonce Rule: strict testing of mathematical bounds, zero-division
handling, data resilience, and strategy capacity lockouts.
"""

from __future__ import annotations

import math
from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from src.agents.alpha_engine import AlphaEngine, InvalidOrderException


class MockPersistenceEngine:
    def __init__(self, positions: list = None):
        self.positions = positions or []

    def get_all_positions(self):
        return self.positions


@pytest.fixture
def base_engine():
    return AlphaEngine(MockPersistenceEngine(), current_aum=100_000.0)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. RISK MATH & POSITION SIZING
# ═══════════════════════════════════════════════════════════════════════════════

def test_position_size_math_standard(base_engine):
    """
    Given 100k AUM, buying at 100 with 95 SL.
    Risk is 1% of 100k = 1000. Risk per share = 5.
    Shares = 1000 / 5 = 200.
    """
    qty = base_engine.calculate_position_size(100.0, 95.0)
    assert qty == 200

def test_position_size_zero_division(base_engine):
    """If entry == stop_loss, raise InvalidOrderException."""
    with pytest.raises(InvalidOrderException):
        base_engine.calculate_position_size(100.0, 100.0)

def test_position_size_invalid_sl(base_engine):
    """If stop_loss > entry_price, raise InvalidOrderException."""
    with pytest.raises(InvalidOrderException):
        base_engine.calculate_position_size(100.0, 105.0)

def test_position_sizing_high_parity(base_engine):
    """
    Boundary Check: High parity (entry=100.01, SL=100.00).
    Risk per share is 0.01. Shares should be large.
    """
    qty = base_engine.calculate_position_size(100.01, 100.00)
    # 1000 / 0.01 = 100,000
    assert qty == 100_000


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CENTRALIZED RISK CAP (CAPACITY & EXPOSURE LOCKOUTS)
# ═══════════════════════════════════════════════════════════════════════════════

def test_capacity_lockout():
    """
    Force 3 active positions and assert orchestrate_signals blocks execution instantly.
    """
    pe = MockPersistenceEngine([
        {'symbol': 'A', 'total_capital': 1000},
        {'symbol': 'B', 'total_capital': 1000},
        {'symbol': 'C', 'total_capital': 1000}
    ])
    engine = AlphaEngine(pe, current_aum=100_000.0)
    
    # Pass dummy compliant data; since active positions >= 3, it should return []
    dummy_data = {'TCS.NS': pd.DataFrame()}
    signals = engine.orchestrate_signals(dummy_data, market_regime='BULL')
    
    assert signals == []

def test_exposure_limit_lockout():
    """
    If total capital across active positions >= 60% AUM, block execution.
    For 100k AUM, limit is 60k.
    """
    pe = MockPersistenceEngine([
        {'symbol': 'A', 'total_capital': 60_000.0}
    ])
    engine = AlphaEngine(pe, current_aum=100_000.0)
    
    dummy_data = {'TCS.NS': pd.DataFrame()}
    signals = engine.orchestrate_signals(dummy_data, market_regime='BULL')
    
    assert signals == []

def test_orchestration_high_parity_exposure_block():
    """
    Test that a generated signal causing capital to exceed 60% is blocked.
    If entry=100.01 and SL=100.00, qty=100,000. Capital required = 10,001,000.
    This wildly exceeds 60% of 100k AUM (60k), so it should be discarded.
    """
    pe = MockPersistenceEngine([])
    engine = AlphaEngine(pe, current_aum=100_000.0)
    
    # Mock mean reversion to return this high parity signal
    engine.strategy_mean_reversion = MagicMock(return_value={
        'symbol': 'TCS.NS',
        'strategy': 'TEST',
        'action': 'BUY',
        'entry_price': 100.01,
        'stop_loss': 100.00
    })
    
    dummy_data = {'TCS.NS': pd.DataFrame()}
    signals = engine.orchestrate_signals(dummy_data, market_regime='BEAR') # Bear to skip HMM
    
    assert signals == []


# ═══════════════════════════════════════════════════════════════════════════════
# 3. DATA HORIZON RESILIENCE
# ═══════════════════════════════════════════════════════════════════════════════

def test_data_horizon_resilience(base_engine):
    """
    Ensure each strategy handles short data arrays (e.g., < 20 rows)
    gracefully by returning {} without crashing.
    """
    df_short = pd.DataFrame({'Close': [100] * 15, 'Volume': [1000] * 15, 'High': [105] * 15, 'Low': [95] * 15})
    
    assert base_engine.strategy_hmm_trend_following('TCS.NS', df_short, 'BULL') == {}
    assert base_engine.strategy_mean_reversion('TCS.NS', df_short) == {}
    assert base_engine.strategy_volume_breakout('TCS.NS', df_short) == {}


# ═══════════════════════════════════════════════════════════════════════════════
# 4. STRATEGY LOGIC CHECKS
# ═══════════════════════════════════════════════════════════════════════════════

def test_strategy_hmm_trend_following_regime_lockout(base_engine):
    """HMM strategy must instantly skip if regime is not BULL."""
    df = pd.DataFrame({'Close': [100]*250})
    signal = base_engine.strategy_hmm_trend_following('TCS.NS', df, 'BEAR')
    assert signal == {}

def test_strategy_hmm_trend_following_signal(base_engine):
    """HMM strategy logic check."""
    # Create 250 days of data.
    # EMA200 will be ~100. EMA50 will be ~100.
    # To trigger: close[-1] > EMA200 and close[-1] > EMA50, but close[-2] <= EMA50.
    closes = [100.0] * 248
    closes.extend([99.0, 105.0]) # cross above 50-EMA and 200-EMA
    
    df = pd.DataFrame({
        'Close': closes,
        'High': [c + 2 for c in closes],
        'Low': [c - 2 for c in closes]
    })
    
    signal = base_engine.strategy_hmm_trend_following('TCS.NS', df, 'BULL')
    assert signal != {}
    assert signal['action'] == 'BUY'
    assert signal['entry_price'] == 105.0
    # ATR will be roughly 4. SL = 105 - (2*4) = 97
    assert 'stop_loss' in signal

def test_strategy_mean_reversion_signal(base_engine):
    """Mean reversion logic check."""
    # Need 20 days. Close < Lower BB AND RSI < 30.
    closes = np.linspace(150, 100, 30)
    closes[-1] = 80  # Sudden crash to pierce lower BB
    df = pd.DataFrame({'Close': closes})
    
    signal = base_engine.strategy_mean_reversion('TCS.NS', df)
    assert signal != {}
    assert signal['action'] == 'BUY'
    assert signal['entry_price'] == 80.0
    assert signal['stop_loss'] == 80.0 * 0.95

def test_strategy_volume_breakout_signal(base_engine):
    """Volume breakout logic check."""
    # Need 30 days.
    np.random.seed(42)
    volumes = np.random.normal(1000, 100, 30)
    volumes[-1] = 100000  # Massive anomaly
    
    closes = np.linspace(100, 110, 30)
    closes[-1] = 130  # Breakout above previous highs (max was ~109.6)
    
    df = pd.DataFrame({
        'Volume': volumes,
        'Close': closes,
        'High': closes + 2,
        'Low': closes - 2
    })
    
    signal = base_engine.strategy_volume_breakout('TCS.NS', df)
    assert signal != {}
    assert signal['action'] == 'BUY'
    assert signal['entry_price'] == 130.0
    # Lowest low of last 5: lows are ~ [106.5, 107.5, 108.5, 109.5, 128]. min is ~106.5
    assert 'stop_loss' in signal
    assert signal['stop_loss'] < 130.0

def test_sebi_algo_id_generation():
    """Ensure orchestrate_signals generates correct algo_ids."""
    pe = MockPersistenceEngine([])
    engine = AlphaEngine(pe, current_aum=100_000.0)
    
    # Mock mean reversion to return a valid basic signal
    engine.strategy_mean_reversion = MagicMock(return_value={
        'symbol': 'TCS.NS',
        'strategy': 'MEAN_REVERSION',
        'action': 'BUY',
        'entry_price': 100.0,
        'stop_loss': 95.0
    })
    engine.strategy_hmm_trend_following = MagicMock(return_value={})
    engine.strategy_volume_breakout = MagicMock(return_value={})
    
    dummy_data = {'TCS.NS': pd.DataFrame()}
    signals = engine.orchestrate_signals(dummy_data, market_regime='BULL')
    
    assert len(signals) == 1
    intent = signals[0]
    
    today_str = date.today().strftime('%Y%m%d')
    expected_algo_id = f"SEBI-ALGO-{today_str}-MEAN_REVERSION"
    
    assert intent['algo_id'] == expected_algo_id
    assert intent['qty'] == 200
