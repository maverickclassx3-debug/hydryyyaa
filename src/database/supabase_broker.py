import os
import logging
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

class SupabasePortfolioManager:
    def __init__(self):
        raw_url = os.getenv("SUPABASE_URL", "")
        raw_key = os.getenv("SUPABASE_KEY", "")
        
        # Strict Multi-Pass Sanitization Wrapper
        def sanitize(val: str) -> str:
            if not val:
                return ""
            # Strip accidental prefixes if pasted along with the key
            if "SUPABASE_KEY=" in val:
                val = val.split("SUPABASE_KEY=")[-1]
            if "SUPABASE_URL=" in val:
                val = val.split("SUPABASE_URL=")[-1]
            # Strip backticks, quotes, and whitespace bounds
            return val.replace("`", "").replace("'", "").replace('"', "").strip()

        self.url = sanitize(raw_url)
        self.key = sanitize(raw_key)
        
        if not self.url or not self.key:
            logging.error("CRITICAL: Sanitized Supabase credentials are empty!")
            self.client = None
            # Raising an error may crash sentinel depending on handling, 
            # but user explicitly requested to raise ValueError
            raise ValueError("Invalid configuration boundaries.")
        else:
            # Bypass Supabase JWT validation for sb_publishable_* keys
            import supabase._sync.client
            original_match = supabase._sync.client.re.match
            
            def patched_match(pattern, string, flags=0):
                if string.startswith("sb_publishable_"):
                    return True
                return original_match(pattern, string, flags)
                
            supabase._sync.client.re.match = patched_match
            try:
                self.client: Client = create_client(self.url, self.key)
            finally:
                supabase._sync.client.re.match = original_match

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
