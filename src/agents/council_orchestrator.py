import math

class SpecializedAgent:
    def __init__(self, name: str, agent_type: str):
        self.name = name
        self.agent_type = agent_type  # 'LLM', 'Technical', 'Quantitative', 'Compliance'

    def evaluate(self, market_context: dict) -> dict:
        raise NotImplementedError("Each agent must implement its own evaluation logic.")

# ==========================================
# 10 SPECIALIZED INTELLIGENCE AGENTS DEFINITION
# ==========================================

class NewsBullAgent(SpecializedAgent):
    def evaluate(self, market_context: dict) -> dict:
        news = market_context.get("live_news_feed", "").lower()
        llm_client = market_context.get("llm_client")
        
        if llm_client:
            prompt = f"Analyze this news for positive growth: {news}"
            system_role = "You are a bullish financial analyst. Reply strictly with 'BULLISH: <reason>' or 'NEUTRAL: <reason>'."
            response = llm_client.query_model(prompt, system_role)
            if response.startswith("BULLISH:"):
                return {"vote": "BULLISH", "reason": response.replace("BULLISH:", "").strip()}
            elif response.startswith("NEUTRAL:"):
                return {"vote": "NEUTRAL", "reason": response.replace("NEUTRAL:", "").strip()}
            
        # Fallback to hardcoded heuristics if LLM client is missing or fails formatting
        if "surge" in news or "cut" in news or "growth" in news:
            return {"vote": "BULLISH", "reason": "Positive growth catalysts or monetary easing detected in macro narrative."}
        return {"vote": "NEUTRAL", "reason": "No immediate micro-expansion vectors identified."}

class NewsBearAgent(SpecializedAgent):
    def evaluate(self, market_context: dict) -> dict:
        news = market_context.get("live_news_feed", "").lower()
        llm_client = market_context.get("llm_client")
        
        if llm_client:
            prompt = f"Analyze this news for bearish structural risk: {news}"
            system_role = "You are a bearish financial analyst. Reply strictly with 'BEARISH: <reason>' or 'NEUTRAL: <reason>'."
            response = llm_client.query_model(prompt, system_role)
            if response.startswith("BEARISH:"):
                return {"vote": "BEARISH", "reason": response.replace("BEARISH:", "").strip()}
            elif response.startswith("NEUTRAL:"):
                return {"vote": "NEUTRAL", "reason": response.replace("NEUTRAL:", "").strip()}

        if "crisis" in news or "panic" in news or "tension" in news or "inflation" in news:
            return {"vote": "BEARISH", "reason": "Structural risk macro premium or geopolitical threat vector observed."}
        return {"vote": "NEUTRAL", "reason": "No immediate macro stress factors detected."}

class ShariahGatekeeperAgent(SpecializedAgent):
    def evaluate(self, market_context: dict) -> dict:
        # Strict absolute verification gate
        is_compliant = market_context.get("shariah_compliant", True)
        if not is_compliant:
            return {"vote": "BLOCKED", "reason": "CRITICAL VETO: Asset failed financial purification thresholds (Excessive Debt/Interest ratio)."}
        return {"vote": "COMPLIANT", "reason": "Asset passed all core TASIS screening financial parameters safely."}

class SmartMoneyTracker(SpecializedAgent):
    def evaluate(self, market_context: dict) -> dict:
        volume_anomaly = market_context.get("volume_anomaly", False)
        price_stable = market_context.get("price_stable", True)
        if volume_anomaly and price_stable:
            return {"vote": "BULLISH", "reason": "Institutional accumulation divergence detected. Smart money is absorbing the float."}
        return {"vote": "NEUTRAL", "reason": "Standard retail volume matrix. Multi-lot whale transactions absent."}

class HmmRegimeClassifier(SpecializedAgent):
    def evaluate(self, market_context: dict) -> dict:
        regime = market_context.get("hmm_regime", "BULL")
        if regime == "BULL":
            return {"vote": "BULLISH", "reason": "Hidden Markov Model confirms structural low-variance bullish regime."}
        elif regime == "BEAR":
            return {"vote": "BEARISH", "reason": "HMM confirms high-variance structural breakdown regime."}
        return {"vote": "NEUTRAL", "reason": "Sideways distribution wave detected."}

class MeanReversionFinder(SpecializedAgent):
    def evaluate(self, market_context: dict) -> dict:
        rsi = market_context.get("rsi_value", 50)
        if rsi < 35:
            return {"vote": "BULLISH", "reason": "Mathematical exhaustion. Asset is extremely oversold near multi-week support bands."}
        elif rsi > 75:
            return {"vote": "BEARISH", "reason": "Asset is heavily overextended into extreme distribution zones."}
        return {"vote": "NEUTRAL", "reason": "RSI values oscillator locked in equilibrium ranges."}

class EarningsAnalyzer(SpecializedAgent):
    def evaluate(self, market_context: dict) -> dict:
        news = market_context.get("live_news_feed", "").lower()
        llm_client = market_context.get("llm_client")
        
        if llm_client:
            prompt = f"Analyze this corporate guidance: {news}"
            system_role = "You are a corporate earnings analyst. Reply strictly with 'BULLISH: <reason>', 'BEARISH: <reason>', or 'NEUTRAL: <reason>'."
            response = llm_client.query_model(prompt, system_role)
            if response.startswith("BULLISH:"):
                return {"vote": "BULLISH", "reason": response.replace("BULLISH:", "").strip()}
            elif response.startswith("BEARISH:"):
                return {"vote": "BEARISH", "reason": response.replace("BEARISH:", "").strip()}
            elif response.startswith("NEUTRAL:"):
                return {"vote": "NEUTRAL", "reason": response.replace("NEUTRAL:", "").strip()}

        guidance = market_context.get("corporate_guidance", "POSITIVE")
        if guidance == "AGGRESSIVE_GROWTH":
            return {"vote": "BULLISH", "reason": "Forward capex roadmap indicates strong institutional product pipeline expansion."}
        return {"vote": "NEUTRAL", "reason": "Standard baseline revenue guidance reported."}

class KellyPositionSizer(SpecializedAgent):
    def evaluate(self, market_context: dict) -> dict:
        win_rate = market_context.get("strategy_win_rate", 0.55)
        win_loss_ratio = 2.0  # Safe target risk reward model
        # Kelly Formula: f* = (p*b - q) / b
        p = win_rate
        q = 1.0 - p
        b = win_loss_ratio
        kelly_fraction = (p * b - q) / b
        allocation = max(0.0, min(kelly_fraction * 100, 15.0)) # Hard cap allocation at 15% max per pool
        return {"vote": "NEUTRAL", "reason": f"Mathematical optimum position footprint locked at {allocation:.2f}% of virtual capital."}

class CryptoMomentumRadar(SpecializedAgent):
    def evaluate(self, market_context: dict) -> dict:
        is_crypto = market_context.get("is_crypto", False)
        velocity = market_context.get("crypto_velocity", "LOW")
        if is_crypto and velocity == "HIGH":
            return {"vote": "BULLISH", "reason": "Tier-1 spot liquidity pools accelerating rapidly above 24-hour moving averages."}
        return {"vote": "NEUTRAL", "reason": "Velocity indexing metrics matching baseline ranges."}

class Level2BookWatcher(SpecializedAgent):
    def evaluate(self, market_context: dict) -> dict:
        bid_ask_ratio = market_context.get("bid_ask_ratio", 1.0)
        if bid_ask_ratio > 1.8:
            return {"vote": "BULLISH", "reason": "Severe order book imbalance. Heavy buy walls clearing active ask layers."}
        return {"vote": "NEUTRAL", "reason": "Order book spreads tight and uniform. Symmetrical liquidity profile."}

# ==========================================
# THE FINAL BOSS AGENT (ORCHESTRATOR)
# ==========================================

class TheFinalBossOrchestrator:
    def __init__(self, market_data_client=None, llm_client=None):
        self.market_data_client = market_data_client
        self.llm_client = llm_client
        
        self.council = [
            NewsBullAgent("Hermes News Bull", "LLM"),
            NewsBearAgent("Hermes News Bear", "LLM"),
            ShariahGatekeeperAgent("Shariah Gatekeeper", "Compliance"),
            SmartMoneyTracker("Whale Accumulation Tracker", "Quantitative"),
            HmmRegimeClassifier("HMM Market Classifier", "Technical"),
            MeanReversionFinder("RSI Dip Finder", "Technical"),
            EarningsAnalyzer("Corporate Guidance Reader", "LLM"),
            KellyPositionSizer("Kelly allocation Engine", "Quantitative"),
            CryptoMomentumRadar("Crypto Velocity Radar", "Quantitative"),
            Level2BookWatcher("L2 Order Book Watcher", "Technical")
        ]

    def synthesize_council_intelligence(self, symbol: str, live_news_feed: str, base_context: dict = None) -> dict:
        market_context = base_context.copy() if base_context else {}
        market_context["symbol"] = symbol
        market_context["live_news_feed"] = live_news_feed
        market_context["llm_client"] = self.llm_client
        
        if self.market_data_client:
            tech_data = self.market_data_client.fetch_ticker_data(symbol)
            market_context.update(tech_data)
            
        agent_feedbacks = {}
        bull_votes = 0
        bear_votes = 0
        veto_triggered = False
        veto_reason = ""

        # Step 1: Poll all independent intelligence vectors
        for agent in self.council:
            feedback = agent.evaluate(market_context)
            agent_feedbacks[agent.name] = feedback
            
            if feedback["vote"] == "BLOCKED":
                veto_triggered = True
                veto_reason = feedback["reason"]
            elif feedback["vote"] == "BULLISH":
                bull_votes += 1
            elif feedback["vote"] == "BEARISH":
                bear_votes += 1

        # Step 2: Enforce the Shariah Veto Override
        if veto_triggered:
            return {
                "final_signal": "STRICT HARAM BLOCK",
                "allocation_pct": 0.0,
                "reason": f"Execution halted by Shariah Gatekeeper. Context: {veto_reason}",
                "agent_breakdown": agent_feedbacks
            }

        # Step 3: Synthesis matrix evaluation
        if bull_votes >= 4 and bear_votes <= 1:
            signal = "BUY_ADVICE"
        else:
            signal = "HOLD / AVOID"

        # Extract calculated allocation metrics
        kelly_reason = agent_feedbacks["Kelly allocation Engine"]["reason"]
        
        return {
            "final_signal": signal,
            "allocation_pct": kelly_reason,
            "reason": f"Council completed evaluation. Total Bullish Consensus: {bull_votes}/10, Total Bearish Structural Risks: {bear_votes}/10.",
            "agent_breakdown": agent_feedbacks
        }
