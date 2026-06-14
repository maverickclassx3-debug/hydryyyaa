import os
import logging
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

class SupabasePortfolioManager:
    def __init__(self):
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        
        if not url or not key:
            logging.warning("Supabase credentials missing. Cloud broker running in offline simulation mode.")
            self.client = None
        else:
            self.client: Client = create_client(url, key)

    def sync_manual_position(self, data: dict) -> bool:
        """
        Pushes row definitions to the cloud table manual_portfolio.
        """
        if not self.client:
            return False
            
        try:
            response = self.client.table("manual_portfolio").insert(data).execute()
            return len(response.data) > 0
        except Exception as e:
            logging.error(f"Failed to sync position to Supabase: {e}")
            return False

    def fetch_active_positions(self) -> list:
        """
        Queries the database dynamically where status == 'ACTIVE'.
        """
        if not self.client:
            return []
            
        try:
            response = self.client.table("manual_portfolio").select("*").eq("status", "ACTIVE").execute()
            return response.data
        except Exception as e:
            logging.error(f"Failed to fetch active positions from Supabase: {e}")
            return []

    def update_position_status(self, row_id: int, status: str) -> bool:
        """
        Commits instant mutations updating rows safely.
        """
        if not self.client:
            return False
            
        try:
            response = self.client.table("manual_portfolio").update({"status": status}).eq("id", row_id).execute()
            return len(response.data) > 0
        except Exception as e:
            logging.error(f"Failed to update position {row_id} status in Supabase: {e}")
            return False
