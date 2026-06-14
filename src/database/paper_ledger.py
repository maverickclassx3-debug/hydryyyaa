import sqlite3
from datetime import date

class PaperLedgerManager:
    def __init__(self, db_path="trade_data.db"):
        self.db_path = db_path
        self._initialize_tables()

    def _get_connection(self):
        return sqlite3.connect(self.db_path, timeout=10.0)

    def _initialize_tables(self):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                portfolio_type TEXT CHECK(portfolio_type IN ('AGGRESSIVE', 'SAFE')),
                symbol TEXT NOT NULL,
                entry_price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                stop_loss REAL NOT NULL,
                target REAL NOT NULL,
                entry_reason TEXT NOT NULL,
                status TEXT CHECK(status IN ('ACTIVE', 'PROFIT_EXIT', 'STOP_LOSS_EXIT')) DEFAULT 'ACTIVE',
                realized_pnl REAL DEFAULT 0.0,
                entry_date DATE DEFAULT CURRENT_DATE,
                exit_date DATE
            )
        ''')
        conn.commit()
        conn.close()

    def log_manual_entry(self, portfolio_type: str, symbol: str, entry_price: float, qty: int, stop_loss: float, target: float, reason: str) -> bool:
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            today = date.today().isoformat()
            cursor.execute('''
                INSERT INTO paper_trades 
                (portfolio_type, symbol, entry_price, quantity, stop_loss, target, entry_reason, status, entry_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?)
            ''', (portfolio_type, symbol, entry_price, qty, stop_loss, target, reason, today))
            conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"Database error during insert: {e}")
            return False
        finally:
            if conn:
                conn.close()

    def evaluate_live_exits(self, current_prices: dict) -> list:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, portfolio_type, symbol, entry_price, quantity, stop_loss, target 
            FROM paper_trades WHERE status = 'ACTIVE'
        ''')
        active_trades = cursor.fetchall()
        
        exit_signals = []
        today = date.today().isoformat()

        for trade_id, portfolio_type, symbol, entry_price, qty, stop_loss, target in active_trades:
            if symbol in current_prices:
                current_price = current_prices[symbol]
                
                if current_price <= stop_loss:
                    realized_pnl = (stop_loss - entry_price) * qty
                    cursor.execute('''
                        UPDATE paper_trades 
                        SET status = 'STOP_LOSS_EXIT', realized_pnl = ?, exit_date = ? 
                        WHERE id = ?
                    ''', (realized_pnl, today, trade_id))
                    
                    exit_signals.append({
                        'symbol': symbol,
                        'portfolio_type': portfolio_type,
                        'action': 'EXIT',
                        'reason': 'STOP_LOSS_EXIT',
                        'realized_pnl': realized_pnl
                    })
                    
                elif current_price >= target:
                    realized_pnl = (target - entry_price) * qty
                    cursor.execute('''
                        UPDATE paper_trades 
                        SET status = 'PROFIT_EXIT', realized_pnl = ?, exit_date = ? 
                        WHERE id = ?
                    ''', (realized_pnl, today, trade_id))
                    
                    exit_signals.append({
                        'symbol': symbol,
                        'portfolio_type': portfolio_type,
                        'action': 'EXIT',
                        'reason': 'PROFIT_EXIT',
                        'realized_pnl': realized_pnl
                    })
        
        conn.commit()
        conn.close()
        return exit_signals
