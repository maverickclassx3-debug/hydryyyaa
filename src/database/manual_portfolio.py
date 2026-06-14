import sqlite3
from datetime import date

class ManualPortfolioManager:
    def __init__(self, db_path='trade_data.db'):
        self.db_path = db_path
        self._initialize_tables()

    def _get_connection(self):
        return sqlite3.connect(self.db_path, timeout=10.0)

    def _initialize_tables(self):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS manual_portfolio (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                entry_price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                entry_date DATE NOT NULL,
                stop_loss REAL NOT NULL,
                target REAL NOT NULL,
                status TEXT CHECK(status IN ('ACTIVE', 'CLOSED')) DEFAULT 'ACTIVE'
            )
        ''')
        conn.commit()
        conn.close()

    def add_manual_position(self, symbol: str, price: float, qty: int, stop_loss: float, target: float):
        conn = self._get_connection()
        cursor = conn.cursor()
        today = date.today().isoformat()
        cursor.execute('''
            INSERT INTO manual_portfolio (symbol, entry_price, quantity, entry_date, stop_loss, target, status)
            VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE')
        ''', (symbol, price, qty, today, stop_loss, target))
        conn.commit()
        conn.close()

    def evaluate_manual_exits(self, current_prices: dict) -> list:
        """
        Loops through 'ACTIVE' trades. 
        If current price <= stop_loss OR current price >= target, generates an EXIT SIGNAL.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, symbol, stop_loss, target FROM manual_portfolio WHERE status = 'ACTIVE'")
        active_trades = cursor.fetchall()
        
        exit_signals = []
        for trade_id, symbol, stop_loss, target in active_trades:
            if symbol in current_prices:
                current_price = current_prices[symbol]
                reason = None
                
                if current_price <= stop_loss:
                    reason = f"Boundary crossed. Price {current_price} hit stop loss {stop_loss}."
                elif current_price >= target:
                    reason = f"Boundary crossed. Price {current_price} hit target {target}."
                    
                if reason:
                    # Update status to CLOSED
                    cursor.execute("UPDATE manual_portfolio SET status = 'CLOSED' WHERE id = ?", (trade_id,))
                    exit_signals.append({
                        'symbol': symbol,
                        'action': 'EXIT',
                        'reason': 'Boundary crossed. Triggering exit advice.'
                    })
        
        conn.commit()
        conn.close()
        
        return exit_signals
