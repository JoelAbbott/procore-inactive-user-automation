import os
import csv
import time
import random
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any
from requests.exceptions import RequestException
from dotenv import load_dotenv
from oauth_manager import OAuthManager, OAuthTokenError
import json

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
        return str(user_id) in DMSA_USER_IDS

    def deactivate_user_with_retry(self, person_id: str) -> Dict[str, Any]:
        access_token = self.oauth_manager.get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Procore-Company-Id": str(self.company_id)
        }
        body = {"person": {"active": False}}
        url = f"{PROCORE_BASE_URL}/rest/v1.0/companies/{self.company_id}/people/{person_id}"

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.patch(url, headers=headers, json=body, timeout=10)

                if response.status_code == 200:
                    logger.info(f"Successfully deactivated user {person_id}")
                    return {"status": "success", "error_message": ""}
                elif response.status_code == 404:
                    logger.info(f"User {person_id} already inactive (404)")
                    return {"status": "already_inactive", "error_message": ""}
                elif response.status_code in (429, 500, 502, 503, 504):
                    logger.warning(f"Transient error deactivating user {person_id}: {response.status_code}. Retrying (attempt {attempt}/{MAX_RETRIES})...")
                else:
                    logger.error(f"Permanent error deactivating user {person_id}: {response.status_code} {response.text}")
                    return {"status": "error", "error_message": f"HTTP {response.status_code}: {response.text}"}

            except RequestException as e:
                logger.warning(f"RequestException deactivating user {person_id}: {e}. Retrying (attempt {attempt}/{MAX_RETRIES})...")

            if attempt < MAX_RETRIES:
                sleep_time = BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 0.5 * BACKOFF_BASE)
                time.sleep(sleep_time)

        logger.error(f"Failed to deactivate user {person_id} after maximum retries.")
        return {"status": "error", "error_message": "Maximum retries exceeded"}

    def write_deactivation_log(self, deactivation_results: List[Dict[str, Any]], users_cache, projects_cache):
        if not deactivation_results:
            logger.warning("No deactivation results to log.")
            return

        try:
            self.deactivation_log_dir.mkdir(parents=True, exist_ok=True)
            with open(self.deactivation_log_file, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = [
                    'first_name', 'last_name', 'email_address', 'vendor_name',
                    'project_name', 'last_activity_date', 'deactivation_status', 'error_message'
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                for result in deactivation_results:
                    user_id = result.get('user_id', '')
                    user = users_cache.get(str(user_id), {})
                    first_name = user.get('first_name', '')
                    last_name = user.get('last_name', '')
                    email_address = user.get('email_address', '')
                    vendor_name = user.get('vendor', {}).get('name', '') if user.get('vendor') else ''
                    project_id = result.get('project_id', '')
                    project = projects_cache.get(str(project_id), {}) if project_id else {}
                    project_name = project.get('name', '') if project else ''
                    writer.writerow({
                        'first_name': first_name,
                        'last_name': last_name,
                        'email_address': email_address,
                        'vendor_name': vendor_name,
                        'project_name': project_name,
                        'last_activity_date': result.get('last_activity_date', ''),
                        'deactivation_status': result.get('deactivation_status', ''),
                        'error_message': result.get('error_message', '')
                    })
            logger.info(f"Deactivation log written to {self.deactivation_log_file}")
        except Exception as e:
            logger.error(f"Failed to write deactivation log: {e}")
            from audit_logging import log_error
            log_error(__name__, e, 'Writing deactivation log')

    def run_deactivation_pipeline(self):
        logger.info("Starting user deactivation pipeline.")

        inactive_users = self.load_inactive_users()
        if not inactive_users:
            logger.info("No inactive users to process.")
            return

        users_to_deactivate = [user for user in inactive_users if not self.is_dmsa_user(user['user_id'])]
        if not users_to_deactivate:
            logger.info("No non-DMSA inactive users to process.")
            return

        logger.info(f"Processing {len(users_to_deactivate)} users for deactivation.")

        access_token = self.oauth_manager.get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        }
        users_cache = self.fetch_users_metadata(headers)
        projects_cache = self.fetch_projects_metadata(headers)

        deactivation_results = []
        for user in users_to_deactivate:
            user_id = user['user_id']
            last_activity_date = user.get('last_activity_date', '')
            project_id = user.get('project_id', '')

            logger.info(f"Deactivating user {user_id} (last activity: {last_activity_date})")

            result = self.deactivate_user_with_retry(user_id)
            deactivation_results.append({
                'user_id': user_id,
                'last_activity_date': last_activity_date,
                'deactivation_status': result['status'],
                'error_message': result['error_message'],
                'project_id': project_id
            })

        self.write_deactivation_log(deactivation_results, users_cache, projects_cache)

        success_count = sum(1 for r in deactivation_results if r['deactivation_status'] == 'success')
        already_inactive_count = sum(1 for r in deactivation_results if r['deactivation_status'] == 'already_inactive')
        error_count = sum(1 for r in deactivation_results if r['deactivation_status'] == 'error')

        logger.info(f"Deactivation pipeline completed. Success: {success_count}, Already inactive: {already_inactive_count}, Errors: {error_count}")

    def fetch_users_metadata(self, headers):
        USERS_CACHE = './data/intermediate/users_cache.json'
        if os.path.exists(USERS_CACHE):
            try:
                with open(USERS_CACHE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                from audit_logging import log_error
                log_error(__name__, e, 'Loading users_cache.json')
        return {}

    def fetch_projects_metadata(self, headers):
        PROJECTS_CACHE = './data/intermediate/projects_cache.json'
        if os.path.exists(PROJECTS_CACHE):
            try:
                with open(PROJECTS_CACHE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                from audit_logging import log_error
                log_error(__name__, e, 'Loading projects_cache.json')
        return {}

if __name__ == "__main__":
    try:
        pipeline = UserDeactivationPipeline()
        pipeline.run_deactivation_pipeline()
    except (OAuthTokenError, RuntimeError) as e:
        logger.error(f"Deactivation pipeline failed: {e}")
        exit(1)
