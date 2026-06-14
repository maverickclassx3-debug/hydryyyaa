from textblob import TextBlob

class GlobalNewsAnalyzer:
    def __init__(self, persistence_engine, halal_screener):
        self.persistence = persistence_engine
        self.screener = halal_screener

    def fetch_global_intelligence_feeds(self) -> list:
        """
        High-fidelity structural mock returning macro events.
        """
        return [
            "Crude Oil surges due to regional tensions in the Middle East",
            "Central bank drops structural base borrowing rate causing banking sector rally",
            "Major supply chain disruption bolsters tech manufacturing demand"
        ]

    def _determine_bullish_sector(self, event_text: str) -> str:
        """
        Simple NLP keyword map to sector.
        """
        blob = TextBlob(event_text.lower())
        words = blob.words
        
        if 'tensions' in words or 'crude' in words or 'war' in words:
            return 'Energy'
        if 'rate' in words or 'bank' in words or 'borrowing' in words:
            return 'Banking'
        if 'tech' in words or 'manufacturing' in words:
            return 'Technology'
            
        return 'Unknown'

    def generate_event_driven_signals(self, intelligence_strings: list) -> list:
        """
        Analyzes geopolitical NLP sentiment, queries DB for compliant sector stocks,
        and enforces the Banking filter explicit blockade.
        """
        signals = []
        
        conn = self.persistence._get_connection()
        cursor = conn.cursor()
        
        for event in intelligence_strings:
            sector = self._determine_bullish_sector(event)
            
            # The Hard Haram Ignorer
            if sector == 'Banking' or sector == 'Financials':
                # Track velocity but explicitly enforce the filter gate
                try:
                    self.persistence.log_telemetry('NEWS_ANALYZER', {
                        'sector': sector,
                        'trend': 'UP',
                        'action': 'IGNORE - NON-COMPLIANT',
                        'event': event
                    })
                except AttributeError:
                    pass
                continue
                
            # Filter Compliant Stocks for the identified sector
            cursor.execute('''
                SELECT ticker FROM halal_screening_results 
                WHERE overall_status = 'COMPLIANT' AND sector = ?
                ORDER BY id DESC
            ''', (sector,))
            
            compliant_tickers = set(row[0] for row in cursor.fetchall())
            
            for ticker in compliant_tickers:
                signals.append({
                    'symbol': ticker,
                    'action': 'BUY',
                    'reason': f"Event: {event} | Impact: {sector} sector is bullish. Shariah Status: Compliant."
                })
                
        conn.close()
        return signals
