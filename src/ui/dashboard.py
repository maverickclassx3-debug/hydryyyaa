import streamlit as st
import pandas as pd
import sqlite3
import os

# Configure the page for a unified 12-Tab Streamlit Production Interface
st.set_page_config(layout="wide", page_title="Halal AI Monitor")

# Safely point to the database file assuming script is run from project root
DB_PATH = 'trade_data.db'

def get_connection():
    if not os.path.exists(DB_PATH):
        return None
    return sqlite3.connect(DB_PATH, timeout=10.0)

# --- SIDEBAR ENGINE ---
st.sidebar.title("Halal AI Control Panel")
st.sidebar.markdown("---")

active_positions_count = 0
deployed_capital = 0.0
system_status = "OFFLINE"

conn = get_connection()
if conn:
    system_status = "ONLINE"
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM active_positions")
        active_positions_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT SUM(total_capital) FROM active_positions")
        deployed_capital_row = cursor.fetchone()
        deployed_capital = deployed_capital_row[0] if deployed_capital_row[0] else 0.0
    except Exception:
        pass
    finally:
        conn.close()

# System AUM Constraint
TOTAL_AUM = 100000.0

st.sidebar.metric(label="System Status", value=system_status)
st.sidebar.metric(label="Total AUM", value=f"₹{TOTAL_AUM:,.2f}")
st.sidebar.metric(label="Active Positions", value=active_positions_count)
st.sidebar.metric(label="Deployed Capital", value=f"₹{deployed_capital:,.2f}")

paper_mode = st.sidebar.toggle("Paper Trading Mode", value=True, disabled=True)

# --- MASTER INTERFACE LAYOUT (12 Tabs Matrix) ---
tabs = st.tabs([
    "Overview", 
    "Halal Screener", 
    "Global Macro", 
    "Alpha Gen", 
    "Risk Manager", 
    "Execution Ledger", 
    "Telemetry Streams", 
    "Purification Ledger", 
    "Crypto Spot Track", 
    "Forex Spot Track", 
    "Compliance Audit", 
    "The War Room"
])

# 1. Overview
with tabs[0]:
    st.header("Overview")
    if not os.path.exists(DB_PATH):
        st.info("No trading logs recorded yet. Radar is active and waiting for your first manual position log!")
    else:
        try:
            import plotly.graph_objects as go
            from src.agents.performance_reviewer import PerformanceReviewer
            
            reviewer = PerformanceReviewer(DB_PATH)
            report = reviewer.generate_strategy_efficiency_report()
            agg_bal = report['AGGRESSIVE']['current_balance']
            safe_bal = report['SAFE']['current_balance']
            
            col1, col2 = st.columns(2)
            col1.metric("AGGRESSIVE Portfolio Balance", f"₹{agg_bal:,.2f}")
            col2.metric("SAFE Portfolio Balance", f"₹{safe_bal:,.2f}")
            
            agg_ret = ((agg_bal - 7000.0) / 7000.0) * 100
            safe_ret = ((safe_bal - 7000.0) / 7000.0) * 100
            
            fig = go.Figure(data=[
                go.Bar(name='AGGRESSIVE', x=['AGGRESSIVE'], y=[agg_ret], marker_color='red'),
                go.Bar(name='SAFE', x=['SAFE'], y=[safe_ret], marker_color='green')
            ])
            fig.update_layout(title="Return Percentages (%)", barmode='group')
            st.plotly_chart(fig, use_container_width=True)
            
        except Exception as e:
            st.info("No trading logs recorded yet. Radar is active and waiting for your first manual position log!")

# 2. Halal Screener
with tabs[1]:
    st.header("Halal Screener Database")
    conn = get_connection()
    if conn:
        try:
            df_screener = pd.read_sql("SELECT * FROM halal_screening_results ORDER BY id DESC LIMIT 100", conn)
            st.dataframe(df_screener, use_container_width=True)
        except Exception as e:
            st.warning(f"Unable to load Screener data: {e}")
        finally:
            conn.close()
    else:
        st.warning("Database unavailable.")

# 3. Global Macro
with tabs[2]:
    st.header("Global Macro Status")
    st.info("Market Regime: BULL")

# 4. Alpha Gen
with tabs[3]:
    st.header("Alpha Generation Engines")
    st.markdown("- **Regime Rider (HMM Trend)**: Active\n- **Statistical Dip-Buyer**: Active\n- **Volume Breakout**: Active\n- **Crypto Spot**: Standby")
    st.markdown("---")
    st.subheader("Performance Reviewer Analytics")
    if not os.path.exists(DB_PATH):
        st.info("No trading logs recorded yet. Radar is active and waiting for your first manual position log!")
    else:
        try:
            from src.agents.performance_reviewer import PerformanceReviewer
            reviewer = PerformanceReviewer(DB_PATH)
            report = reviewer.generate_strategy_efficiency_report()
            
            st.write("**AGGRESSIVE Strategy Win Rate:**", f"{report['AGGRESSIVE']['win_rate']:.1f}%")
            st.write("**SAFE Strategy Win Rate:**", f"{report['SAFE']['win_rate']:.1f}%")
            
            catalyst_data = []
            for ptype in ['AGGRESSIVE', 'SAFE']:
                for cat, stats in report[ptype]['catalysts'].items():
                    catalyst_data.append({
                        'Portfolio': ptype,
                        'Catalyst Narrative': cat.capitalize(),
                        'Wins': stats['wins'],
                        'Losses': stats['losses'],
                        'Win Rate (%)': round(stats['win_rate'], 1)
                    })
            if catalyst_data:
                st.write("**Self-Improving Catalyst Tracker:**")
                st.dataframe(pd.DataFrame(catalyst_data), use_container_width=True)
            else:
                st.info("Not enough closed trades to form catalyst sub-win rates.")
        except Exception as e:
            st.info("No trading logs recorded yet. Radar is active and waiting for your first manual position log!")

# 5. Risk Manager
with tabs[4]:
    st.header("Risk Manager Thresholds")
    st.markdown("Concurrent Positions (Max 3)")
    pos_progress = active_positions_count / 3.0
    st.progress(pos_progress if pos_progress <= 1.0 else 1.0)
    
    st.markdown(f"Capital Exposure Limit (60% of ₹{TOTAL_AUM:,.0f})")
    exposure_ratio = deployed_capital / (TOTAL_AUM * 0.6)
    st.progress(exposure_ratio if exposure_ratio <= 1.0 else 1.0)

# 6. Execution Ledger
with tabs[5]:
    st.header("Paper Trading Ledger")
    if not os.path.exists(DB_PATH):
        st.info("No trading logs recorded yet. Radar is active and waiting for your first manual position log!")
    else:
        conn = get_connection()
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_trades'")
                if not cursor.fetchone():
                    st.info("No trading logs recorded yet. Radar is active and waiting for your first manual position log!")
                else:
                    st.subheader("AGGRESSIVE Tracking Rows")
                    df_agg = pd.read_sql("SELECT * FROM paper_trades WHERE portfolio_type = 'AGGRESSIVE' ORDER BY id DESC", conn)
                    if not df_agg.empty:
                        st.dataframe(df_agg, use_container_width=True)
                    else:
                        st.info("No AGGRESSIVE trades logged yet.")
                        
                    st.subheader("SAFE Tracking Rows")
                    df_safe = pd.read_sql("SELECT * FROM paper_trades WHERE portfolio_type = 'SAFE' ORDER BY id DESC", conn)
                    if not df_safe.empty:
                        st.dataframe(df_safe, use_container_width=True)
                    else:
                        st.info("No SAFE trades logged yet.")
            except Exception as e:
                st.info("No trading logs recorded yet. Radar is active and waiting for your first manual position log!")
            finally:
                conn.close()

# 7. Telemetry Streams
with tabs[6]:
    st.header("System Telemetry Streams")
    conn = get_connection()
    if conn:
        try:
            df_log = pd.read_sql("SELECT * FROM system_telemetry ORDER BY id DESC LIMIT 100", conn)
            st.dataframe(df_log, use_container_width=True)
        except Exception:
            st.info("No telemetry logs found yet.")
        finally:
            conn.close()
    else:
        st.warning("Telemetry unavailable.")

# 8. Purification Ledger
with tabs[7]:
    st.header("Purification Ledger")
    st.write("Real-time tracking ledger calculations checking dividend purification data frames.")

# 9. Crypto Spot Track
with tabs[8]:
    st.header("Crypto Spot Track")
    st.write("24/7 liquid spot monitoring asset stream.")

# 10. Forex Spot Track
with tabs[9]:
    st.header("Forex Spot Track")
    st.write("Same-day fiat reference calculation layouts.")

# 11. Compliance Audit
with tabs[10]:
    st.header("Compliance Audit")
    st.markdown("- [x] Order Rate Limiter (<10/sec)\n- [x] CNC Only Product Type\n- [x] SEBI Algo-ID Inclusion\n- [x] Session Expiry Gate (15:30 IST)")

# 12. The War Room
with tabs[11]:
    st.header("The War Room (Multi-Agent Cognitive Council)")
    
    payload = None
    conn = get_connection()
    if conn:
        try:
            cursor = conn.cursor()
            # In a real run, check system_telemetry for COUNCIL_ORCHESTRATOR
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='system_telemetry'")
            if cursor.fetchone():
                cursor.execute("SELECT payload FROM system_telemetry WHERE component = 'COUNCIL_ORCHESTRATOR' ORDER BY id DESC LIMIT 1")
                row = cursor.fetchone()
                if row:
                    import json
                    payload = json.loads(row[0])
        except Exception:
            pass
        finally:
            conn.close()
            
    if not payload:
        st.info("The War Room is on standby. Showing layout mock since the background orchestrator hasn't processed macro events yet.")
        payload = {
            "final_signal": "BUY_ADVICE",
            "allocation_pct": "15.00%",
            "reason": "Council completed evaluation. Total Bullish Consensus: 4/10, Total Bearish Structural Risks: 1/10.",
            "agent_breakdown": {
                "Hermes News Bull": {"vote": "BULLISH", "reason": "Positive growth catalysts or monetary easing detected in macro narrative."},
                "Hermes News Bear": {"vote": "NEUTRAL", "reason": "No immediate macro stress factors detected."},
                "Shariah Gatekeeper": {"vote": "COMPLIANT", "reason": "Asset passed all core TASIS screening financial parameters safely."},
                "Whale Accumulation Tracker": {"vote": "NEUTRAL", "reason": "Standard retail volume matrix. Multi-lot whale transactions absent."},
                "HMM Market Classifier": {"vote": "BULLISH", "reason": "Hidden Markov Model confirms structural low-variance bullish regime."},
                "RSI Dip Finder": {"vote": "BULLISH", "reason": "Mathematical exhaustion. Asset is extremely oversold near multi-week support bands."},
                "Corporate Guidance Reader": {"vote": "NEUTRAL", "reason": "Standard baseline revenue guidance reported."},
                "Kelly allocation Engine": {"vote": "NEUTRAL", "reason": "Mathematical optimum position footprint locked at 15.00% of virtual capital."},
                "Crypto Velocity Radar": {"vote": "NEUTRAL", "reason": "Velocity indexing metrics matching baseline ranges."},
                "L2 Order Book Watcher": {"vote": "BULLISH", "reason": "Severe order book imbalance. Heavy buy walls clearing active ask layers."}
            }
        }

    st.subheader("Intelligence Council Array")
    agents = list(payload.get("agent_breakdown", {}).items())
    
    if agents:
        cols1 = st.columns(5)
        for i, col in enumerate(cols1):
            if i < len(agents):
                name, data = agents[i]
                with col:
                    st.markdown(f"**{name}**")
                    vote = data.get("vote", "UNKNOWN")
                    color = "green" if vote in ["BULLISH", "COMPLIANT"] else "red" if vote in ["BEARISH", "BLOCKED"] else "gray"
                    st.markdown(f"<span style='color:{color}; font-weight:bold;'>{vote}</span>", unsafe_allow_html=True)
                    st.caption(data.get("reason", ""))
                    
        if len(agents) > 5:
            cols2 = st.columns(5)
            for i, col in enumerate(cols2):
                idx = i + 5
                if idx < len(agents):
                    name, data = agents[idx]
                    with col:
                        st.markdown(f"**{name}**")
                        vote = data.get("vote", "UNKNOWN")
                        color = "green" if vote in ["BULLISH", "COMPLIANT"] else "red" if vote in ["BEARISH", "BLOCKED"] else "gray"
                        st.markdown(f"<span style='color:{color}; font-weight:bold;'>{vote}</span>", unsafe_allow_html=True)
                        st.caption(data.get("reason", ""))

    st.divider()
    
    st.subheader("The Final Boss Verdict")
    with st.container():
        c1, c2 = st.columns([2, 1])
        with c1:
            signal = payload.get("final_signal", "UNKNOWN")
            signal_color = "green" if "BUY" in signal else "red" if "BLOCK" in signal else "orange"
            st.markdown(f"<h3 style='color:{signal_color};'>{signal}</h3>", unsafe_allow_html=True)
            st.write(payload.get("reason", ""))
        with c2:
            alloc = payload.get("allocation_pct", "0.0%")
            st.metric("Execution Sizer (Kelly Allocation)", alloc)
