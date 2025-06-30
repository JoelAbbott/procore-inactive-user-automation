
import os
import csv
import time
import random
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from requests.exceptions import RequestException
from dotenv import load_dotenv
from oauth_manager import OAuthManager, OAuthTokenError
from audit_logging import log_error, log_audit, log_operation_summary
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
MAX_PAGE_SIZE = 300  # API limit

# Paths
REPORTS_FILE = Path("./data/reports/inactive_users_report.csv")
DEACTIVATION_LOGS_BASE = Path("./data/deactivation_logs")
USERS_CACHE = Path("./data/intermediate/users_cache.json")
PROJECTS_CACHE = Path("./data/intermediate/projects_cache.json")

# DMSA user IDs to skip (properly handle whitespace)
DMSA_USER_IDS = set(u.strip() for u in os.getenv('DMSA_USER_IDS', '').split(',') if u.strip())

class UserDeactivationPipeline:
    """Enhanced user deactivation pipeline with proper error handling and API consistency."""
    
    def __init__(self):
        self.oauth_manager = OAuthManager()
        self.company_id = PROCORE_COMPANY_ID
        self.today = datetime.now().strftime('%Y-%m-%d')
        self.deactivation_log_dir = DEACTIVATION_LOGS_BASE / self.today
        self.deactivation_log_file = self.deactivation_log_dir / "deactivation_log.csv"
        self.module = "user_deactivation_pipeline"

        if not self.company_id:
            raise RuntimeError("PROCORE_COMPANY_ID environment variable is required.")

    def load_inactive_users(self) -> List[Dict[str, Any]]:
        """Load inactive users from the report file."""
        if not REPORTS_FILE.exists():
            logger.warning(f"Inactive users report file {REPORTS_FILE} does not exist.")
            log_audit(self.module, "Load Inactive Users", record_count=0, 
                     notes="Report file not found")
            return []

        try:
            with open(REPORTS_FILE, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                users = [row for row in reader if row.get('status') == 'Inactive > 12 months']
            
            logger.info(f"Loaded {len(users)} inactive users from report.")
            log_audit(self.module, "Load Inactive Users", record_count=len(users))
            return users
            
        except Exception as e:
            log_error(self.module, e, f"Loading {REPORTS_FILE}")
            return []

    def is_dmsa_user(self, user_id: str) -> bool:
        """Check if user is a DMSA user that should be skipped."""
        return str(user_id).strip() in DMSA_USER_IDS

    def fetch_user_details(self, user_id: str, headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """
        Fetch detailed user information to get the correct person_id for deactivation.
        This resolves the /users/ vs /people/ endpoint mismatch.
        """
        try:
            # First try to get user details from /users endpoint
            url = f"{PROCORE_BASE_URL}/rest/v1.0/users/{user_id}"
            params = {'company_id': self.company_id}
            
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code == 200:
                user_data = response.json()
                # The contact_id from users endpoint maps to person_id in people endpoint
                person_id = user_data.get('contact_id') or user_data.get('id')
                return {
                    'user_id': user_id,
                    'person_id': person_id,
                    'user_data': user_data
                }
            elif response.status_code == 404:
                logger.warning(f"User {user_id} not found in /users endpoint")
                return None
            else:
                log_error(self.module, Exception(f"HTTP {response.status_code}: {response.text}"), 
                         f"Fetching user details for {user_id}")
                return None
                
        except Exception as e:
            log_error(self.module, e, f"Fetching user details for {user_id}")
            return None

    def deactivate_user_with_retry(self, user_id: str, person_id: str) -> Dict[str, Any]:
        """
        Deactivate user with retry logic using the correct person_id.
        Fixed to use proper endpoint and ID mapping.
        """
        access_token = self.oauth_manager.get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Procore-Company-Id": str(self.company_id)
        }
        
        # Use the people endpoint with the correct person_id
        body = {"person": {"active": False}}
        url = f"{PROCORE_BASE_URL}/rest/v1.0/companies/{self.company_id}/people/{person_id}"

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.patch(url, headers=headers, json=body, timeout=30)

                if response.status_code == 200:
                    logger.info(f"Successfully deactivated user {user_id} (person_id: {person_id})")
                    return {"status": "success", "error_message": ""}
                    
                elif response.status_code == 404:
                    logger.info(f"User {user_id} not found or already inactive (404)")
                    return {"status": "already_inactive", "error_message": "User not found (404)"}
                    
                elif response.status_code == 400:
                    # Check if user is already inactive
                    response_text = response.text.lower()
                    if "already" in response_text and "inactive" in response_text:
                        logger.info(f"User {user_id} already inactive")
                        return {"status": "already_inactive", "error_message": "Already inactive"}
                    else:
                        logger.error(f"Bad request deactivating user {user_id}: {response.text}")
                        return {"status": "error", "error_message": f"HTTP 400: {response.text}"}
                        
                elif response.status_code in (429, 500, 502, 503, 504):
                    logger.warning(f"Transient error deactivating user {user_id}: {response.status_code}. Retrying (attempt {attempt}/{MAX_RETRIES})...")
                    
                else:
                    logger.error(f"Permanent error deactivating user {user_id}: {response.status_code} {response.text}")
                    return {"status": "error", "error_message": f"HTTP {response.status_code}: {response.text}"}

            except RequestException as e:
                logger.warning(f"RequestException deactivating user {user_id}: {e}. Retrying (attempt {attempt}/{MAX_RETRIES})...")
                log_error(self.module, e, f"Deactivating user {user_id}, attempt {attempt}")

            # Exponential backoff with jitter
            if attempt < MAX_RETRIES:
                sleep_time = BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 0.5 * BACKOFF_BASE)
                time.sleep(sleep_time)

        logger.error(f"Failed to deactivate user {user_id} after maximum retries.")
        return {"status": "error", "error_message": "Maximum retries exceeded"}

    def write_deactivation_log(self, deactivation_results: List[Dict[str, Any]], 
                              users_cache: Dict, projects_cache: Dict):
        """Write deactivation results to CSV log with enhanced error handling."""
        if not deactivation_results:
            logger.warning("No deactivation results to log.")
            log_audit(self.module, "Write Deactivation Log", record_count=0)
            return

        try:
            # Ensure directory exists
            self.deactivation_log_dir.mkdir(parents=True, exist_ok=True)
            
            fieldnames = [
                'first_name', 'last_name', 'email_address', 'vendor_name',
                'project_name', 'last_activity_date', 'deactivation_status', 'error_message'
            ]
            
            with open(self.deactivation_log_file, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                for result in deactivation_results:
                    user_id = result.get('user_id', '')
                    user = users_cache.get(str(user_id), {})
                    
                    # Extract user information safely
                    first_name = user.get('first_name', '')
                    last_name = user.get('last_name', '')
                    email_address = user.get('email_address', '')
                    vendor_name = ''
                    if user.get('vendor') and isinstance(user['vendor'], dict):
                        vendor_name = user['vendor'].get('name', '')
                    
                    # Extract project information safely
                    project_id = result.get('project_id', '')
                    project_name = ''
                    if project_id and str(project_id) in projects_cache:
                        project = projects_cache[str(project_id)]
                        project_name = project.get('name', '') if isinstance(project, dict) else ''
                    
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
            log_audit(self.module, "Write Deactivation Log", record_count=len(deactivation_results))
            
        except Exception as e:
            log_error(self.module, e, f'Writing deactivation log to {self.deactivation_log_file}')
            raise

    def fetch_users_metadata(self, headers: Dict[str, str]) -> Dict[str, Any]:
        """Load users metadata from cache or return empty dict."""
        if USERS_CACHE.exists():
            try:
                with open(USERS_CACHE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    log_audit(self.module, "Users Cache Loaded", record_count=len(data))
                    return data
            except Exception as e:
                log_error(self.module, e, f'Loading {USERS_CACHE}')
        
        logger.warning(f"Users cache not found at {USERS_CACHE}")
        return {}

    def fetch_projects_metadata(self, headers: Dict[str, str]) -> Dict[str, Any]:
        """Load projects metadata from cache or return empty dict."""
        if PROJECTS_CACHE.exists():
            try:
                with open(PROJECTS_CACHE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    log_audit(self.module, "Projects Cache Loaded", record_count=len(data))
                    return data
            except Exception as e:
                log_error(self.module, e, f'Loading {PROJECTS_CACHE}')
        
        logger.warning(f"Projects cache not found at {PROJECTS_CACHE}")
        return {}

    def run_deactivation_pipeline(self):
        """Main deactivation pipeline with enhanced error handling and logging."""
        logger.info("Starting user deactivation pipeline.")
        log_audit(self.module, "Deactivation Pipeline Started")

        try:
            # Step 1: Load inactive users
            inactive_users = self.load_inactive_users()
            if not inactive_users:
                logger.info("No inactive users to process.")
                log_audit(self.module, "Deactivation Pipeline Completed", 
                         record_count=0, notes="No inactive users found")
                return

            # Step 2: Filter out DMSA users
            users_to_deactivate = [user for user in inactive_users if not self.is_dmsa_user(user['user_id'])]
            dmsa_filtered_count = len(inactive_users) - len(users_to_deactivate)
            
            if not users_to_deactivate:
                logger.info("No non-DMSA inactive users to process.")
                log_audit(self.module, "Deactivation Pipeline Completed", 
                         record_count=0, notes=f"All {dmsa_filtered_count} users were DMSA users")
                return

            logger.info(f"Processing {len(users_to_deactivate)} users for deactivation (filtered {dmsa_filtered_count} DMSA users).")

            # Step 3: Get OAuth token and setup headers
            access_token = self.oauth_manager.get_access_token()
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json"
            }

            # Step 4: Load metadata caches
            users_cache = self.fetch_users_metadata(headers)
            projects_cache = self.fetch_projects_metadata(headers)

            # Step 5: Process each user for deactivation
            deactivation_results = []
            successful_deactivations = 0
            already_inactive_count = 0
            error_count = 0

            for i, user in enumerate(users_to_deactivate, 1):
                user_id = user['user_id']
                last_activity_date = user.get('last_activity_date', '')
                project_id = user.get('project_id', '')

                logger.info(f"Processing user {i}/{len(users_to_deactivate)}: {user_id} (last activity: {last_activity_date})")

                # Step 5a: Get user details to resolve person_id
                user_details = self.fetch_user_details(user_id, headers)
                
                if not user_details:
                    error_count += 1
                    deactivation_results.append({
                        'user_id': user_id,
                        'last_activity_date': last_activity_date,
                        'deactivation_status': 'error',
                        'error_message': 'Could not fetch user details',
                        'project_id': project_id
                    })
                    continue

                person_id = user_details['person_id']
                if not person_id:
                    error_count += 1
                    deactivation_results.append({
                        'user_id': user_id,
                        'last_activity_date': last_activity_date,
                        'deactivation_status': 'error',
                        'error_message': 'No person_id found for user',
                        'project_id': project_id
                    })
                    continue

                # Step 5b: Attempt deactivation
                logger.info(f"Deactivating user {user_id} (person_id: {person_id})")
                result = self.deactivate_user_with_retry(user_id, person_id)
                
                # Count results
                if result['status'] == 'success':
                    successful_deactivations += 1
                elif result['status'] == 'already_inactive':
                    already_inactive_count += 1
                else:
                    error_count += 1

                deactivation_results.append({
                    'user_id': user_id,
                    'last_activity_date': last_activity_date,
                    'deactivation_status': result['status'],
                    'error_message': result['error_message'],
                    'project_id': project_id
                })

                # Small delay to be nice to the API
                time.sleep(0.5)

            # Step 6: Write deactivation log
            self.write_deactivation_log(deactivation_results, users_cache, projects_cache)

            # Step 7: Log final summary
            log_operation_summary(
                self.module, 
                "Deactivation Pipeline Completed",
                total_processed=len(users_to_deactivate),
                successful=successful_deactivations,
                failed=error_count,
                notes=f"Already inactive: {already_inactive_count}, DMSA filtered: {dmsa_filtered_count}"
            )

            logger.info(f"Deactivation pipeline completed successfully.")
            logger.info(f"Results - Success: {successful_deactivations}, Already inactive: {already_inactive_count}, Errors: {error_count}")

        except Exception as e:
            log_error(self.module, e, "Deactivation pipeline main execution")
            log_audit(self.module, "Deactivation Pipeline Failed", notes=str(e))
            logger.error(f"Deactivation pipeline failed: {e}")
            raise


def main():
    """Main entry point with proper error handling."""
    try:
        pipeline = UserDeactivationPipeline()
        pipeline.run_deactivation_pipeline()
        
    except OAuthTokenError as e:
        logger.error(f"OAuth authentication failed: {e}")
        return 1
        
    except RuntimeError as e:
        logger.error(f"Configuration error: {e}")
        return 1
        
    except Exception as e:
        logger.error(f"Unexpected error in deactivation pipeline: {e}")
        return 1
        
    return 0


if __name__ == "__main__":
    exit_code = main()
    exit(exit_code)
