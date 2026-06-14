import traceback
import yfinance as yf
import pandas as pd

class SystemOrchestrator:
    def __init__(self, persistence_engine, screener, alpha_engine, broker):
        self.persistence = persistence_engine
        self.screener = screener
        self.alpha_engine = alpha_engine
        self.broker = broker

    def run_pipeline_iteration(self, symbols: list, market_regime: str):
        """
        Master Execution Loop Iteration.
        Designed to be run repeatedly by a standalone daemon/service.
        """
        for symbol in symbols:
            try:
                # Step A: Run HalalScreener and update screening DB
                self.screener.screen_stock(symbol)
                
                # Check DB to verify if status is COMPLIANT
                conn = self.persistence._get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT overall_status FROM halal_screening_results WHERE ticker = ? ORDER BY id DESC LIMIT 1", 
                    (symbol,)
                )
                row = cursor.fetchone()
                conn.close()

                if not row or row[0] != 'COMPLIANT':
                    continue

                # Step B: Fetch market data for compliant symbol
                ticker = yf.Ticker(symbol)
                # Fetching enough history for 200-EMA and other indicators
                df = ticker.history(period="1y")

                if df.empty:
                    continue

                # Pass to AlphaEngine to get OrderIntents
                signals = self.alpha_engine.orchestrate_signals({symbol: df}, market_regime=market_regime)

                # Step C: Route generated OrderIntent dictionaries to broker execution
                for intent in signals:
                    self.broker.place_cnc_order(intent)

            except Exception as e:
                # Graceful Error Recovery: Catch any crash and write trace to telemetry
                error_trace = traceback.format_exc()
                if self.persistence:
                    try:
                        self.persistence.log_telemetry('ORCHESTRATOR', {
                            'symbol': symbol,
                            'error': str(e),
                            'traceback': error_trace
                        })
                    except Exception:
                        pass
