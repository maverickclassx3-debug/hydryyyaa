import yfinance as yf
import pandas as pd
import requests
import logging

class MarketDataClient:
    def __init__(self):
        pass

    def fetch_ticker_data(self, symbol: str) -> dict:
        """
        Fetches 60 days of daily history from yfinance and calculates:
        - current_price
        - rsi_14
        - volume_anomaly (Today's vol > 2x 20-day avg)
        - 50_sma, 200_sma -> hmm_regime ('BULL' if 50 > 200 else 'BEAR')
        """
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="60d")
            
            if hist.empty or len(hist) < 20:
                return {}

            # Calculate Simple Moving Averages
            hist['50_sma'] = hist['Close'].rolling(window=50, min_periods=1).mean()
            hist['200_sma'] = hist['Close'].rolling(window=200, min_periods=1).mean()
            
            # Calculate RSI (Wilder's Smoothing)
            delta = hist['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            hist['rsi_14'] = 100 - (100 / (1 + rs))
            
            # Calculate Volume Anomaly
            hist['20d_vol_avg'] = hist['Volume'].rolling(window=20).mean()
            
            latest = hist.iloc[-1]
            
            current_price = float(latest['Close'])
            rsi_value = float(latest['rsi_14']) if not pd.isna(latest['rsi_14']) else 50.0
            
            vol_avg = float(latest['20d_vol_avg']) if not pd.isna(latest['20d_vol_avg']) else 1.0
            volume_anomaly = float(latest['Volume']) > (2.0 * vol_avg)
            
            hmm_regime = "BULL" if latest['50_sma'] > latest['200_sma'] else "BEAR"
            
            return {
                "current_price": current_price,
                "rsi_value": rsi_value,
                "volume_anomaly": volume_anomaly,
                "hmm_regime": hmm_regime,
                "price_stable": True,  # Defaulting as requested
            }
            
        except Exception as e:
            logging.error(f"MarketDataClient Error fetching {symbol}: {e}")
            return {}

class LLMInferenceClient:
    def __init__(self, api_key: str = None, provider: str = "openrouter"):
        self.api_key = api_key
        self.provider = provider
        self.endpoint = "https://openrouter.ai/api/v1/chat/completions"

    def query_model(self, prompt: str, system_role: str) -> str:
        """
        Hits the REST API to infer the result. Returns a safe fallback if key is missing.
        """
        if not self.api_key:
            return "API_KEY_MISSING: Neutral structural consensus."

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "NousResearch/Hermes-3-Llama-3.1-8B",
            "messages": [
                {"role": "system", "content": system_role},
                {"role": "user", "content": prompt}
            ]
        }
        
        try:
            response = requests.post(self.endpoint, headers=headers, json=payload, timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                return data['choices'][0]['message']['content'].strip()
            else:
                logging.warning(f"LLM API returned status {response.status_code}. Fallback triggered.")
                return "API_ERROR: Neutral structural consensus."
        except Exception as e:
            logging.error(f"LLMInferenceClient Error: {e}")
            return "NETWORK_ERROR: Neutral structural consensus."
