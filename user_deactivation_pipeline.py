import os
import csv
import time
import random
import logging
import requests
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
from requests.exceptions import RequestException
from dotenv import load_dotenv
from oauth_manager import OAuthManager, OAuthTokenError

# Load environment variables from .env file
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] %(levelname)s %(name)s: %(message)s')
handler.setFormatter(formatter)
if not logger.hasHandlers():
    logger.addHandler(handler)

# Constants
PROCORE_BASE_URL = os.getenv('PROCORE_BASE_URL', 'https://sandbox.procore.com')
PROCORE_COMPANY_ID = os.getenv('PROCORE_COMPANY_ID')
MAX_RETRIES = 5
BACKOFF_BASE = 1  # seconds

# Paths
REPORTS_FILE = Path("./data/reports/inactive_users_report.csv")
DEACTIVATION_LOGS_BASE = Path("./data/deactivation_logs")

# DMSA user IDs to skip (can be loaded from env or config)
DMSA_USER_IDS = os.getenv('DMSA_USER_IDS', '').split(',') if os.getenv('DMSA_USER_IDS') else []

class UserDeactivationPipeline:
    def __init__(self):
        self.oauth_manager = OAuthManager()
        self.company_id = PROCORE_COMPANY_ID
        self.today = datetime.now().strftime('%Y-%m-%d')
        self.deactivation_log_dir = DEACTIVATION_LOGS_BASE / self.today
        self.deactivation_log_file = self.deactivation_log_dir / "deactivation_log.csv"
        
        if not self.company_id:
            raise RuntimeError("PROCORE_COMPANY_ID environment variable is required.")

    def load_inactive_users(self) -> List[Dict[str, Any]]:
        """Load inactive users from the report CSV file."""
        if not REPORTS_FILE.exists():
            logger.warning(f"Inactive users report file {REPORTS_FILE} does not exist.")
            return []
        
        try:
            with open(REPORTS_FILE, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                users = [row for row in reader if row.get('status') == 'Inactive > 12 months']
            logger.info(f"Loaded {len(users)} inactive users from report.")
            return users
        except Exception as e:
            logger.error(f"Failed to load inactive users report: {e}")
            return []

    def is_dmsa_user(self, user_id: str) -> bool:
        """Check if user is a DMSA user that should be skipped."""
        return str(user_id) in DMSA_USER_IDS

    def deactivate_user_with_retry(self, user_id: str) -> Dict[str, Any]:
        """Deactivate a user with retry logic and exponential backoff."""
        access_token = self.oauth_manager.get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Procore-Company-Id": str(self.company_id)
        }
        body = {"is_active": False}
        url = f"{PROCORE_BASE_URL}/rest/v1.0/users/{user_id}"
        
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.patch(url, headers=headers, json=body, timeout=10)
                
                if response.status_code == 200:
                    logger.info(f"Successfully deactivated user {user_id}")
                    return {"status": "success", "error_message": ""}
                elif response.status_code == 404:
                    logger.info(f"User {user_id} already inactive (404)")
                    return {"status": "already_inactive", "error_message": ""}
                elif response.status_code in (429, 500, 502, 503, 504):
                    logger.warning(f"Transient error deactivating user {user_id}: {response.status_code}. Retrying (attempt {attempt}/{MAX_RETRIES})...")
                else:
                    logger.error(f"Permanent error deactivating user {user_id}: {response.status_code} {response.text}")
                    return {"status": "error", "error_message": f"HTTP {response.status_code}: {response.text}"}
                    
            except RequestException as e:
                logger.warning(f"RequestException deactivating user {user_id}: {e}. Retrying (attempt {attempt}/{MAX_RETRIES})...")
            
            # Exponential backoff with jitter
            if attempt < MAX_RETRIES:
                sleep_time = BACKOFF_BASE * (2 ** (attempt - 1))
                sleep_time = sleep_time + random.uniform(0, 0.5 * sleep_time)
                time.sleep(sleep_time)
        
        logger.error(f"Failed to deactivate user {user_id} after maximum retries.")
        return {"status": "error", "error_message": "Maximum retries exceeded"}

    def write_deactivation_log(self, deactivation_results: List[Dict[str, Any]]):
        """Write deactivation results to daily CSV log."""
        if not deactivation_results:
            logger.warning("No deactivation results to log.")
            return
        
        try:
            self.deactivation_log_dir.mkdir(parents=True, exist_ok=True)
            
            with open(self.deactivation_log_file, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['user_id', 'last_activity_date', 'deactivation_status', 'error_message']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                for result in deactivation_results:
                    writer.writerow(result)
            
            logger.info(f"Deactivation log written to {self.deactivation_log_file}")
        except Exception as e:
            logger.error(f"Failed to write deactivation log: {e}")

    def run_deactivation_pipeline(self):
        """Main function to run the user deactivation pipeline."""
        logger.info("Starting user deactivation pipeline.")
        
        # Load inactive users
        inactive_users = self.load_inactive_users()
        if not inactive_users:
            logger.info("No inactive users to process.")
            return
        
        # Filter out DMSA users
        users_to_deactivate = [user for user in inactive_users if not self.is_dmsa_user(user['user_id'])]
        if not users_to_deactivate:
            logger.info("No non-DMSA inactive users to process.")
            return
        
        logger.info(f"Processing {len(users_to_deactivate)} users for deactivation.")
        
        # Process each user
        deactivation_results = []
        for user in users_to_deactivate:
            user_id = user['user_id']
            last_activity_date = user['last_activity_date']
            
            logger.info(f"Deactivating user {user_id} (last activity: {last_activity_date})")
            
            result = self.deactivate_user_with_retry(user_id)
            deactivation_results.append({
                'user_id': user_id,
                'last_activity_date': last_activity_date,
                'deactivation_status': result['status'],
                'error_message': result['error_message']
            })
        
        # Write deactivation log
        self.write_deactivation_log(deactivation_results)
        
        # Summary
        success_count = sum(1 for r in deactivation_results if r['deactivation_status'] == 'success')
        already_inactive_count = sum(1 for r in deactivation_results if r['deactivation_status'] == 'already_inactive')
        error_count = sum(1 for r in deactivation_results if r['deactivation_status'] == 'error')
        
        logger.info(f"Deactivation pipeline completed. Success: {success_count}, Already inactive: {already_inactive_count}, Errors: {error_count}")

if __name__ == "__main__":
    try:
        pipeline = UserDeactivationPipeline()
        pipeline.run_deactivation_pipeline()
    except (OAuthTokenError, RuntimeError) as e:
        logger.error(f"Deactivation pipeline failed: {e}")
        exit(1) 