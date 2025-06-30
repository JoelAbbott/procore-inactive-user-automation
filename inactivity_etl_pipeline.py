
import os
import json
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from oauth_manager import OAuthManager
from audit_logging import log_error, log_audit, log_operation_summary

import requests
import time

# --- Constants ---
LOGS_BASE = './data/logs'
OUTPUT_DIR = './data/intermediate'
OUTPUT_CSV = os.path.join(OUTPUT_DIR, 'inactive_users_raw.csv')
USERS_CACHE = os.path.join(OUTPUT_DIR, 'users_cache.json')
PROJECTS_CACHE = os.path.join(OUTPUT_DIR, 'projects_cache.json')
MODULE = "inactivity_etl_pipeline"

# --- Configuration ---
MAX_PAGE_SIZE = 300  # API limit is 300, not 1000
INACTIVE_THRESHOLD_DAYS = 365  # 12 months
NEVER_LOGGED_IN_THRESHOLD_DAYS = 180  # 6 months

# --- ENV ---
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)
ADMIN_USER_IDS = set(u.strip() for u in os.getenv('ADMIN_USER_IDS', '').split(',') if u.strip())
COMPANY_ID = os.getenv('PROCORE_COMPANY_ID')
BASE_URL = os.getenv('PROCORE_BASE_URL', 'https://sandbox.procore.com')

# --- Ensure output dir exists ---
os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- Helper: Retry logic with better error handling ---
def api_get_with_retry(url, headers, params=None, max_retries=3, context=None):
    """API GET with retry logic and proper error handling."""
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 400:
                error_msg = f"HTTP 400: {resp.text}"
                log_error(MODULE, Exception(error_msg), context or url)
                if "max page size" in resp.text.lower():
                    raise ValueError(f"Page size too large. API response: {resp.text}")
                raise Exception(error_msg)
            elif resp.status_code in [401, 403]:
                error_msg = f"HTTP {resp.status_code}: Authentication/Authorization error: {resp.text}"
                log_error(MODULE, Exception(error_msg), context or url)
                raise Exception(error_msg)
            elif resp.status_code in [429, 500, 502, 503, 504]:
                error_msg = f"HTTP {resp.status_code}: {resp.text}"
                log_error(MODULE, Exception(error_msg), context or url)
                if attempt == max_retries:
                    raise Exception(error_msg)
            else:
                error_msg = f"HTTP {resp.status_code}: {resp.text}"
                log_error(MODULE, Exception(error_msg), context or url)
                raise Exception(error_msg)
        except requests.RequestException as e:
            log_error(MODULE, e, context or url)
            if attempt == max_retries:
                raise
        
        # Exponential backoff with jitter
        sleep_time = (2 ** (attempt - 1)) + (time.time() % 1)
        time.sleep(sleep_time)

# --- Step 1: Load all activity logs ---
def load_activity_logs():
    """Load and combine all activity logs from daily directories."""
    all_events = []
    logs_path = Path(LOGS_BASE)
    
    if not logs_path.exists():
        log_error(MODULE, FileNotFoundError(f"Logs directory {LOGS_BASE} not found"), "load_activity_logs")
        return pd.DataFrame()
    
    processed_files = 0
    failed_files = 0
    
    for day_dir in sorted(logs_path.iterdir()):
        if not day_dir.is_dir():
            continue
            
        for json_file in day_dir.glob("*.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Accept both single event and list of events
                    if isinstance(data, dict):
                        all_events.append(data)
                    elif isinstance(data, list):
                        all_events.extend(data)
                processed_files += 1
            except Exception as e:
                log_error(MODULE, e, f"Loading {json_file}")
                failed_files += 1
    
    log_audit(MODULE, "Activity Logs Loaded", 
              record_count=len(all_events), 
              success_count=processed_files,
              failure_count=failed_files)
    
    if not all_events:
        return pd.DataFrame()
    
    return pd.DataFrame(all_events)

# --- Step 2: Fetch user metadata (with caching and pagination) ---
def fetch_users_metadata(oauth, headers):
    """Fetch user metadata with proper pagination and caching."""
    if os.path.exists(USERS_CACHE):
        try:
            with open(USERS_CACHE, 'r', encoding='utf-8') as f:
                cached_data = json.load(f)
                log_audit(MODULE, "Users Cache Loaded", record_count=len(cached_data))
                return cached_data
        except Exception as e:
            log_error(MODULE, e, "Loading users_cache.json")
    
    users_cache = {}
    api_calls = 0
    
    try:
        url = f"{BASE_URL}/rest/v1.0/users"
        params = {
            'company_id': COMPANY_ID, 
            'per_page': MAX_PAGE_SIZE,  # Fixed: Use API limit
            'page': 1
        }
        all_users = []
        
        while True:
            api_calls += 1
            data = api_get_with_retry(url, headers, params, context="GET /users bulk")
            
            # Handle different response formats
            if isinstance(data, dict) and 'users' in data:
                users = data['users']
            else:
                users = data if isinstance(data, list) else []
            
            if not users:
                break
                
            all_users.extend(users)
            
            # Check if we got fewer results than requested (last page)
            if len(users) < MAX_PAGE_SIZE:
                break
                
            params['page'] += 1
            
            # Safety check to prevent infinite loops
            if params['page'] > 1000:
                log_error(MODULE, Exception("Too many pages, possible infinite loop"), "fetch_users_metadata")
                break
        
        # Build cache dictionary
        for user in all_users:
            users_cache[str(user.get('id'))] = user
        
        # Save cache
        with open(USERS_CACHE, 'w', encoding='utf-8') as f:
            json.dump(users_cache, f, indent=2)
            
        log_operation_summary(MODULE, "Users Metadata Fetched", 
                            len(all_users), len(all_users), 0, api_calls)
        
    except Exception as e:
        log_error(MODULE, e, "Fetching users metadata")
        
    return users_cache

# --- Step 3: Fetch project metadata (with caching and pagination) ---
def fetch_projects_metadata(oauth, headers):
    """Fetch project metadata with proper pagination and caching."""
    if os.path.exists(PROJECTS_CACHE):
        try:
            with open(PROJECTS_CACHE, 'r', encoding='utf-8') as f:
                cached_data = json.load(f)
                log_audit(MODULE, "Projects Cache Loaded", record_count=len(cached_data))
                return cached_data
        except Exception as e:
            log_error(MODULE, e, "Loading projects_cache.json")
    
    projects_cache = {}
    api_calls = 0
    
    try:
        url = f"{BASE_URL}/rest/v1.1/projects"
        params = {
            'company_id': COMPANY_ID, 
            'per_page': MAX_PAGE_SIZE,  # Fixed: Use API limit
            'page': 1
        }
        all_projects = []
        
        while True:
            api_calls += 1
            data = api_get_with_retry(url, headers, params, context="GET /projects bulk")
            
            # Handle different response formats
            if isinstance(data, dict) and 'projects' in data:
                projects = data['projects']
            else:
                projects = data if isinstance(data, list) else []
            
            if not projects:
                break
                
            all_projects.extend(projects)
            
            # Check if we got fewer results than requested (last page)
            if len(projects) < MAX_PAGE_SIZE:
                break
                
            params['page'] += 1
            
            # Safety check to prevent infinite loops
            if params['page'] > 1000:
                log_error(MODULE, Exception("Too many pages, possible infinite loop"), "fetch_projects_metadata")
                break
        
        # Build cache dictionary
        for project in all_projects:
            projects_cache[str(project.get('id'))] = project
        
        # Save cache
        with open(PROJECTS_CACHE, 'w', encoding='utf-8') as f:
            json.dump(projects_cache, f, indent=2)
            
        log_operation_summary(MODULE, "Projects Metadata Fetched", 
                            len(all_projects), len(all_projects), 0, api_calls)
        
    except Exception as e:
        log_error(MODULE, e, "Fetching projects metadata")
        
    return projects_cache

# --- Step 4: Identify inactive users with proper timezone handling ---
def identify_inactive_users(activity_df, users_cache):
    """Identify inactive users with proper timezone-aware datetime handling."""
    if activity_df.empty or 'user_id' not in activity_df.columns:
        log_error(MODULE, ValueError("No activity data or missing user_id column"), "identify_inactive_users")
        return []
    
    # Clean and prepare activity data
    activity_df = activity_df.dropna(subset=['user_id'])
    
    # Parse timestamps with timezone awareness - FIXED
    def parse_timestamp_safe(ts):
        """Safely parse timestamp to timezone-aware datetime."""
        if pd.isna(ts) or ts == '':
            return None
        try:
            # Parse as UTC if no timezone info
            parsed = pd.to_datetime(ts, utc=True)
            return parsed
        except Exception:
            try:
                # Fallback: parse as naive then localize to UTC
                parsed = pd.to_datetime(ts)
                if parsed.tz is None:
                    parsed = parsed.tz_localize('UTC')
                else:
                    parsed = parsed.tz_convert('UTC')
                return parsed
            except Exception as e:
                log_error(MODULE, e, f"Parsing timestamp: {ts}")
                return None
    
    activity_df['occurred_at_parsed'] = activity_df['occurred_at'].apply(parse_timestamp_safe)
    activity_df = activity_df.dropna(subset=['occurred_at_parsed'])
    
    # Build last activity per user
    if activity_df.empty:
        log_error(MODULE, ValueError("No valid timestamps in activity data"), "identify_inactive_users")
        return []
    
    last_active_map = activity_df.groupby('user_id')['occurred_at_parsed'].max().to_dict()
    
    # Current time in UTC - FIXED
    now = datetime.now(timezone.utc)
    
    inactive_users = []
    never_logged_in_users = []
    
    for user_id, user in users_cache.items():
        if user_id in ADMIN_USER_IDS:
            continue
        
        # Get last activity (timezone-aware)
        last_active = last_active_map.get(user_id)
        
        # Parse created_at with timezone awareness
        created_at = user.get('created_at')
        created_at_dt = None
        if created_at:
            try:
                created_at_dt = pd.to_datetime(created_at, utc=True)
            except Exception as e:
                log_error(MODULE, e, f"Parsing created_at for user {user_id}: {created_at}")
        
        # Check inactivity - FIXED timezone handling
        if last_active:
            # User has activity, check if it's old enough
            time_diff = now - last_active
            if time_diff.days >= INACTIVE_THRESHOLD_DAYS:
                inactive_users.append((user_id, last_active, user))
        elif created_at_dt:
            # User never logged in, check if account is old enough
            time_diff = now - created_at_dt
            if time_diff.days >= NEVER_LOGGED_IN_THRESHOLD_DAYS:
                never_logged_in_users.append((user_id, None, user))
    
    log_audit(MODULE, "Inactive Users Identified", 
              record_count=len(inactive_users + never_logged_in_users),
              notes=f"Inactive: {len(inactive_users)}, Never logged in: {len(never_logged_in_users)}")
    
    return inactive_users + never_logged_in_users

# --- Step 5: Write output CSV with better error handling ---
def write_inactive_users_csv(inactive_users, projects_cache, activity_df):
    """Write inactive users to CSV with proper error handling."""
    if not inactive_users:
        log_audit(MODULE, "No Inactive Users", record_count=0)
        # Still create an empty CSV with headers
        empty_df = pd.DataFrame(columns=[
            'user_id', 'last_active', 'project_id', 'first_name', 'last_name',
            'email_address', 'vendor_name', 'project_name'
        ])
        empty_df.to_csv(OUTPUT_CSV, index=False)
        return
    
    rows = []
    for user_id, last_active, user in inactive_users:
        # Find a project_id from activity logs if available
        project_id = ''
        if not activity_df.empty and 'project_id' in activity_df.columns:
            user_acts = activity_df[activity_df['user_id'] == user_id]
            if not user_acts.empty:
                project_ids = user_acts['project_id'].dropna().astype(str)
                if not project_ids.empty:
                    project_id = project_ids.iloc[0]
        
        project = projects_cache.get(str(project_id), {}) if project_id else {}
        
        row = {
            'user_id': user_id,
            'last_active': last_active.isoformat() if last_active else '',
            'project_id': project_id,
            'first_name': user.get('first_name', ''),
            'last_name': user.get('last_name', ''),
            'email_address': user.get('email_address', ''),
            'vendor_name': user.get('vendor', {}).get('name', '') if user.get('vendor') else '',
            'project_name': project.get('name', '') if project else ''
        }
        rows.append(row)
    
    try:
        df = pd.DataFrame(rows)
        df.to_csv(OUTPUT_CSV, index=False)
        log_audit(MODULE, "Inactive Users CSV Written", record_count=len(rows))
    except Exception as e:
        log_error(MODULE, e, f"Writing CSV to {OUTPUT_CSV}")
        raise

# --- Main ETL Pipeline ---
if __name__ == "__main__":
    try:
        log_audit(MODULE, "ETL Pipeline Started")
        
        # Step 1: Load activity logs
        activity_df = load_activity_logs()
        if activity_df.empty or 'user_id' not in activity_df.columns:
            raise ValueError("No valid activity logs found or missing 'user_id' field.")
        
        # Step 2: Get OAuth token and setup headers
        oauth = OAuthManager()
        token = oauth.get_access_token()
        headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json'
        }
        
        # Step 3: Fetch metadata
        users_cache = fetch_users_metadata(oauth, headers)
        projects_cache = fetch_projects_metadata(oauth, headers)
        
        # Step 4: Identify inactive users
        inactive_users = identify_inactive_users(activity_df, users_cache)
        
        # Step 5: Write results
        write_inactive_users_csv(inactive_users, projects_cache, activity_df)
        
        log_audit(MODULE, "ETL Pipeline Completed Successfully", 
                 record_count=len(inactive_users))
        print(f"✅ Inactive users written to {OUTPUT_CSV}")
        
    except Exception as e:
        log_error(MODULE, e, "ETL pipeline main")
        log_audit(MODULE, "ETL Pipeline Failed", notes=str(e))
        print(f"❌ ETL pipeline failed: {e}")
        raise