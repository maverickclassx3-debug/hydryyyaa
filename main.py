import uvicorn
import logging
import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Import the existing SentimentEngine
from src.agents.sentiment_engine import SentimentEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI(title="AI Trade Brain API", version="1.0")

class AnalyzeRequest(BaseModel):
    symbol: str

# Initialize the SentimentEngine once at startup
sentiment_engine = SentimentEngine()

@app.get("/")
def health_check():
    return {"status": "Brain is online and ready for n8n"}

@app.post("/analyze")
def analyze_symbol(req: AnalyzeRequest):
    logging.info(f"Received analysis request from n8n for: {req.symbol}")
    try:
        # Run the real Sentiment/AI logic
        analysis = sentiment_engine.analyze_ticker(req.symbol)
        
        # Format the result for n8n
        result = {
            "symbol": req.symbol,
            "action": analysis.get("signal", "Neutral"),
            "sentiment_score": analysis.get("sentiment_score", 0.0),
            "ai_logic": f"Analyzed {analysis.get('headlines', 0)} recent headlines."
        }
        
        logging.info(f"Analysis complete for {req.symbol}: {result['action']}")
        return result
    except Exception as e:
        logging.error(f"Analysis failed for {req.symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    logging.info(f"Starting API Brain on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
