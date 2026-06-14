import sqlite3

class PerformanceReviewer:
    def __init__(self, db_path="trade_data.db"):
        self.db_path = db_path

    def _get_connection(self):
        return sqlite3.connect(self.db_path, timeout=10.0)

    def generate_strategy_efficiency_report(self) -> dict:
        conn = self._get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        report = {
            'AGGRESSIVE': {'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0, 'win_rate': 0.0, 'current_balance': 7000.0, 'catalysts': {}},
            'SAFE': {'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0, 'win_rate': 0.0, 'current_balance': 7000.0, 'catalysts': {}}
        }

        # 1. Fetch all closed trades to build primary metrics
        cursor.execute("SELECT portfolio_type, status, realized_pnl, entry_reason FROM paper_trades WHERE status IN ('PROFIT_EXIT', 'STOP_LOSS_EXIT')")
        closed_trades = cursor.fetchall()
        
        for row in closed_trades:
            ptype = row['portfolio_type']
            status = row['status']
            pnl = row['realized_pnl']
            reason = row['entry_reason'].lower()
            
            # Simple keyword extraction for sector tracking (mock catalyst logic based on NLP words)
            catalyst = 'unknown'
            if 'energy' in reason or 'crude' in reason:
                catalyst = 'energy'
            elif 'tech' in reason or 'manufacturing' in reason:
                catalyst = 'technology'
            elif 'bank' in reason or 'financial' in reason:
                catalyst = 'banking'
            elif 'crypto' in reason:
                catalyst = 'crypto'
            elif 'macro' in reason or 'regime' in reason:
                catalyst = 'macro_trend'

            stats = report[ptype]
            stats['total_trades'] += 1
            stats['current_balance'] += pnl
            
            if catalyst not in stats['catalysts']:
                stats['catalysts'][catalyst] = {'wins': 0, 'losses': 0, 'win_rate': 0.0}
            
            if status == 'PROFIT_EXIT':
                stats['winning_trades'] += 1
                stats['catalysts'][catalyst]['wins'] += 1
            elif status == 'STOP_LOSS_EXIT':
                stats['losing_trades'] += 1
                stats['catalysts'][catalyst]['losses'] += 1

        # 2. Compute final win rates avoiding Division by Zero
        for ptype in ['AGGRESSIVE', 'SAFE']:
            stats = report[ptype]
            if stats['total_trades'] > 0:
                stats['win_rate'] = (stats['winning_trades'] / stats['total_trades']) * 100.0
                
            # Compute catalyst win rates
            for cat, c_stats in stats['catalysts'].items():
                total_cat = c_stats['wins'] + c_stats['losses']
                if total_cat > 0:
                    c_stats['win_rate'] = (c_stats['wins'] / total_cat) * 100.0

        conn.close()
        return report
