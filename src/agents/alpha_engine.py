"""
alpha_engine.py
===============
Layer 2 — Intelligence Engine (AlphaEngine)
Halal AI World Monitor · Phase 2 · Week 5

Generates buy/sell signals from mathematical strategies, constrained by centralized
risk management gates and persistence tracking.

Architectural Constraints:
    - NO leverage, NO short selling (Long-only CNC).
    - MAX 3 concurrent open positions across the system.
    - MAX 60% total capital exposure across the system.
    - STRICT 1% Risk per trade rule.
    - SEBI 2026 compliance: every order requires an algo_id.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Dict, List

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
import ta

# Assuming PersistenceEngine provides get_all_positions() as per spec
from src.database.persistence_engine import PersistenceEngine


class InvalidOrderException(Exception):
    """Raised when risk math breaks or an order violates strict constraints."""
    pass


class AlphaEngine:
    """
    Intelligence Layer for generating Shariah-compliant trade signals.
    """

    def __init__(self, persistence_engine: PersistenceEngine, current_aum: float):
        self.pe = persistence_engine
        self.current_aum = float(current_aum)

        if self.current_aum <= 0:
            raise ValueError("AUM must be strictly positive.")

    def calculate_position_size(self, entry_price: float, stop_loss: float) -> int:
        """
        Calculates strict CNC position sizing based on 1% Risk per trade.
        
        Args:
            entry_price: The intended execution price.
            stop_loss: The intended stop loss price.
            
        Returns:
            int: Number of shares to buy.
            
        Raises:
            InvalidOrderException: If entry_price <= stop_loss (division by zero or inverted risk).
        """
        if stop_loss >= entry_price:
            raise InvalidOrderException(
                f"Stop loss ({stop_loss}) must be strictly less than entry price ({entry_price})."
            )

        capital_at_risk = self.current_aum * 0.01
        risk_per_share = round(entry_price - stop_loss, 4)
        
        shares = math.floor(capital_at_risk / risk_per_share)
        return shares

    def strategy_hmm_trend_following(self, symbol: str, df: pd.DataFrame, current_regime: str) -> dict:
        """
        The Regime Rider. 
        Only operates in 'BULL' regimes.
        """
        if current_regime != 'BULL':
            return {}

        if len(df) < 200:
            return {}

        # Calculate EMAs
        ema50 = ta.trend.ema_indicator(df['Close'], window=50)
        ema200 = ta.trend.ema_indicator(df['Close'], window=200)

        # Get latest values
        current_close = df['Close'].iloc[-1]
        prev_close = df['Close'].iloc[-2]
        current_ema50 = ema50.iloc[-1]
        prev_ema50 = ema50.iloc[-2]
        current_ema200 = ema200.iloc[-1]

        # Logic: Close > 200 EMA AND Close crosses above 50 EMA
        is_above_200 = current_close > current_ema200
        cross_above_50 = (current_close > current_ema50) and (prev_close <= prev_ema50)

        if is_above_200 and cross_above_50:
            # Calculate ATR for SL
            atr = ta.volatility.average_true_range(df['High'], df['Low'], df['Close'], window=14)
            current_atr = atr.iloc[-1]
            
            stop_loss = current_close - (2 * current_atr)
            
            return {
                'symbol': symbol,
                'strategy': 'HMM_TREND_FOLLOWING',
                'action': 'BUY',
                'entry_price': float(current_close),
                'stop_loss': float(stop_loss)
            }
            
        return {}

    def strategy_mean_reversion(self, symbol: str, df: pd.DataFrame) -> dict:
        """
        Statistical Dip-Buyer.
        RSI < 30 and Close < Lower BB.
        """
        if len(df) < 20:
            return {}

        # Calculate indicators
        rsi = ta.momentum.rsi(df['Close'], window=14)
        bb = ta.volatility.BollingerBands(df['Close'], window=20, window_dev=2)
        lower_bb = bb.bollinger_lband()

        current_close = df['Close'].iloc[-1]
        current_rsi = rsi.iloc[-1]
        current_lower_bb = lower_bb.iloc[-1]

        if current_close < current_lower_bb and current_rsi < 30:
            stop_loss = current_close * 0.95
            
            return {
                'symbol': symbol,
                'strategy': 'MEAN_REVERSION',
                'action': 'BUY',
                'entry_price': float(current_close),
                'stop_loss': float(stop_loss)
            }

        return {}

    def strategy_volume_breakout(self, symbol: str, df: pd.DataFrame) -> dict:
        """
        Isolation Forest Breakout.
        Finds volume anomalies coupled with a 20-day high breakout.
        """
        if len(df) < 30:
            return {}

        volumes = df[['Volume']].values
        
        # Fit Isolation Forest
        try:
            iso_forest = IsolationForest(contamination=0.05, random_state=42)
            preds = iso_forest.fit_predict(volumes)
            is_anomaly = preds[-1] == -1
        except Exception:
            return {}

        current_close = df['Close'].iloc[-1]
        
        # 20-day High of the previous 20 candles (excluding current)
        past_20_high = df['High'].iloc[-21:-1].max()

        if is_anomaly and current_close > past_20_high:
            # SL: Lowest low of the last 5 execution candles
            lowest_5_low = df['Low'].iloc[-5:].min()
            
            return {
                'symbol': symbol,
                'strategy': 'VOLUME_BREAKOUT',
                'action': 'BUY',
                'entry_price': float(current_close),
                'stop_loss': float(lowest_5_low)
            }
            
        return {}

    def orchestrate_signals(self, compliant_symbols_data: Dict[str, pd.DataFrame], market_regime: str) -> List[dict]:
        """
        Core orchestrator loop checking database active records.
        Applies capacity lockouts and risk limits.
        """
        # Centralized Risk Cap Testing
        active_positions = self.pe.get_all_positions()
        
        if len(active_positions) >= 3:
            return []
            
        total_capital_deployed = sum(float(p.get('total_capital', 0.0)) for p in active_positions)
        if total_capital_deployed >= (0.60 * self.current_aum):
            return []

        intents = []
        
        # Keep track of simulated state during orchestration to not exceed limits within the same batch
        simulated_active_count = len(active_positions)
        simulated_capital_deployed = total_capital_deployed
        
        for symbol, df in compliant_symbols_data.items():
            if simulated_active_count >= 3:
                break
                
            # Gather raw signals from all strategies
            raw_signals = [
                self.strategy_hmm_trend_following(symbol, df, market_regime),
                self.strategy_mean_reversion(symbol, df),
                self.strategy_volume_breakout(symbol, df)
            ]
            
            for signal in raw_signals:
                if not signal:
                    continue
                    
                if simulated_active_count >= 3:
                    break

                try:
                    qty = self.calculate_position_size(signal['entry_price'], signal['stop_loss'])
                except InvalidOrderException:
                    continue
                    
                if qty <= 0:
                    continue
                    
                capital_required = qty * signal['entry_price']
                
                # Check capital cap dynamically
                if (simulated_capital_deployed + capital_required) > (0.60 * self.current_aum):
                    continue
                    
                # Generate SEBI algo_id
                today_str = date.today().strftime('%Y%m%d')
                algo_id = f"SEBI-ALGO-{today_str}-{signal['strategy']}"
                
                # Construct intent
                intent = {
                    'symbol': symbol,
                    'strategy': signal['strategy'],
                    'action': 'BUY',
                    'qty': qty,
                    'entry_price': signal['entry_price'],
                    'stop_loss': signal['stop_loss'],
                    'total_capital': capital_required,
                    'algo_id': algo_id
                }
                
                intents.append(intent)
                
                # Update simulated state to reflect this intent
                simulated_active_count += 1
                simulated_capital_deployed += capital_required
                
                # Only take one signal per symbol in a run
                break

        return intents
