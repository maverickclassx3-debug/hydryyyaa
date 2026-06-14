import sqlite3
import pytest
import os
import tempfile

from src.database.paper_ledger import PaperLedgerManager
from src.agents.performance_reviewer import PerformanceReviewer

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)

def test_safe_vs_aggressive_isolation(temp_db):
    """
    Insert active trades for both profiles. Assert data queries return isolated datasets matching specific constraint types.
    """
    ledger = PaperLedgerManager(db_path=temp_db)
    ledger.log_manual_entry('AGGRESSIVE', 'BTC-USD', 60000.0, 1, 58000.0, 65000.0, 'crypto spot breakout')
    ledger.log_manual_entry('SAFE', 'RELIANCE.NS', 3000.0, 10, 2900.0, 3200.0, 'energy sector strength')
    
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    cursor.execute("SELECT symbol FROM paper_trades WHERE portfolio_type = 'AGGRESSIVE'")
    aggressive_symbols = [row[0] for row in cursor.fetchall()]
    
    cursor.execute("SELECT symbol FROM paper_trades WHERE portfolio_type = 'SAFE'")
    safe_symbols = [row[0] for row in cursor.fetchall()]
    
    conn.close()
    
    assert 'BTC-USD' in aggressive_symbols
    assert 'RELIANCE.NS' not in aggressive_symbols
    assert 'RELIANCE.NS' in safe_symbols
    assert 'BTC-USD' not in safe_symbols

def test_mathematical_winrate_evaluation(temp_db):
    """
    Mock 6 trades matching 'PROFIT_EXIT' and 4 matching 'STOP_LOSS_EXIT' inside the 'AGGRESSIVE' segment. 
    Run generate_strategy_efficiency_report and assert that the output values match exactly 60.0 percent win rate.
    """
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    # Explicitly create tables via ledger init
    ledger = PaperLedgerManager(db_path=temp_db)
    
    # Manually insert historical trades to mock the scenario
    # 6 wins
    for _ in range(6):
        cursor.execute("INSERT INTO paper_trades (portfolio_type, symbol, entry_price, quantity, stop_loss, target, entry_reason, status, realized_pnl) VALUES ('AGGRESSIVE', 'T1', 100, 1, 90, 110, 'crypto tech', 'PROFIT_EXIT', 10)")
    # 4 losses
    for _ in range(4):
        cursor.execute("INSERT INTO paper_trades (portfolio_type, symbol, entry_price, quantity, stop_loss, target, entry_reason, status, realized_pnl) VALUES ('AGGRESSIVE', 'T2', 100, 1, 90, 110, 'crypto macro', 'STOP_LOSS_EXIT', -10)")
        
    conn.commit()
    conn.close()
    
    reviewer = PerformanceReviewer(db_path=temp_db)
    report = reviewer.generate_strategy_efficiency_report()
    
    agg_stats = report['AGGRESSIVE']
    assert agg_stats['total_trades'] == 10
    assert agg_stats['winning_trades'] == 6
    assert agg_stats['losing_trades'] == 4
    assert agg_stats['win_rate'] == 60.0

def test_compounding_balance_calculation(temp_db):
    """
    Verify that a combination of wins and losses mathematically sums the running capital balance accurately 
    above or below the 7,000 base tier framework.
    """
    ledger = PaperLedgerManager(db_path=temp_db)
    
    # Safe Trade 1: Target hit
    # Entry: 100, Qty: 10. Target: 110 -> PnL = (110 - 100) * 10 = +100
    ledger.log_manual_entry('SAFE', 'TCS', 100.0, 10, 90.0, 110.0, 'tech energy')
    
    # Safe Trade 2: Stop Loss hit
    # Entry: 50, Qty: 20. Stop Loss: 45 -> PnL = (45 - 50) * 20 = -100
    ledger.log_manual_entry('SAFE', 'INFY', 50.0, 20, 45.0, 60.0, 'tech energy')
    
    # Safe Trade 3: Target hit
    # Entry: 200, Qty: 5. Target: 250 -> PnL = (250 - 200) * 5 = +250
    ledger.log_manual_entry('SAFE', 'WIPRO', 200.0, 5, 180.0, 250.0, 'macro trend tech')

    # Evaluate all live exits where they hit the trigger bounds
    market_prices = {
        'TCS': 110.0,    # Hits target (+100)
        'INFY': 45.0,    # Hits SL (-100)
        'WIPRO': 250.0   # Hits target (+250)
    }
    
    exits = ledger.evaluate_live_exits(market_prices)
    assert len(exits) == 3
    
    reviewer = PerformanceReviewer(db_path=temp_db)
    report = reviewer.generate_strategy_efficiency_report()
    
    safe_stats = report['SAFE']
    # Initial balance 7000.0 + 100 - 100 + 250 = 7250.0
    assert safe_stats['current_balance'] == 7250.0
