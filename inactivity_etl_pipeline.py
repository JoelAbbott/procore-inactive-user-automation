import os
import json
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from oauth_manager import OAuthManager
from audit_logging import log_error

import requests
import time

# --- Constants ---
LOGS_BASE = './data/logs'
OUTPUT_DIR = './data/intermediate'
OUTPUT_CSV = os.path.join(OUTPUT_DIR, 'inactive_users_raw.csv')
USERS_CACHE = os.path.join(OUTPUT_DIR, 'users_cache.json')
PROJECTS_CACHE = os.path.join(OUTPUT_DIR, 'projects_cache.json')
MODULE = "inactivity_etl_pipeline"

# --- ENV ---
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)
ADMIN_USER_IDS = set(u.strip() for u in os.getenv('ADMIN_USER_IDS', '').split(',') if u.strip())
COMPANY_ID = os.getenv('PROCORE_COMPANY_ID')
BASE_URL = os.getenv('PROCORE_BASE_URL', 'https://sandbox.procore.com')

# --- Ensure output dir exists ---
os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- Helper: Retry logic ---
def api_get_with_retry(url, headers, params=None, max_retries=3, context=None):
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            else:
                raise Exception(f"HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            log_error(MODULE, e, context or url)
            if attempt == max_retries:
                raise
            sleep_time = 2 ** (attempt - 1)
            time.sleep(sleep_time)

# --- Step 1: Load all activity logs ---
def load_activity_logs():
    all_events = []
    logs_path = Path(LOGS_BASE)
    if not logs_path.exists():
        return pd.DataFrame()
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
            except Exception as e:
                log_error(MODULE, e, f"Loading {json_file}")
    if not all_events:
        return pd.DataFrame()
    return pd.DataFrame(all_events)

# --- Step 2: Fetch user metadata (with caching) ---
def fetch_users_metadata(oauth, headers):
    if os.path.exists(USERS_CACHE):
        try:
            with open(USERS_CACHE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log_error(MODULE, e, "Loading users_cache.json")
    users_cache = {}
    try:
        url = f"{BASE_URL}/rest/v1.0/users"
        params = {'company_id': COMPANY_ID, 'per_page': 1000, 'page': 1}
        all_users = []
        while True:
            data = api_get_with_retry(url, headers, params, context="GET /users bulk")
            if isinstance(data, dict) and 'users' in data:
                users = data['users']
            else:
                users = data
            if not users:
                break
            all_users.extend(users)
            if len(users) < 1000:
                break
            params['page'] += 1
        for user in all_users:
            users_cache[str(user.get('id'))] = user
        with open(USERS_CACHE, 'w', encoding='utf-8') as f:
            json.dump(users_cache, f)
    except Exception as e:
        log_error(MODULE, e, "Fetching users metadata")
    return users_cache

# --- Step 3: Fetch project metadata (with caching) ---
def fetch_projects_metadata(oauth, headers):
    if os.path.exists(PROJECTS_CACHE):
        try:
            with open(PROJECTS_CACHE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log_error(MODULE, e, "Loading projects_cache.json")
    projects_cache = {}
    try:
        url = f"{BASE_URL}/rest/v1.1/projects"
        params = {'company_id': COMPANY_ID, 'per_page': 1000, 'page': 1}
        all_projects = []
        while True:
            data = api_get_with_retry(url, headers, params, context="GET /projects bulk")
            if isinstance(data, dict) and 'projects' in data:
                projects = data['projects']
            else:
                projects = data
            if not projects:
                break
            all_projects.extend(projects)
            if len(projects) < 1000:
                break
            params['page'] += 1
        for project in all_projects:
            projects_cache[str(project.get('id'))] = project
        with open(PROJECTS_CACHE, 'w', encoding='utf-8') as f:
            json.dump(projects_cache, f)
    except Exception as e:
        log_error(MODULE, e, "Fetching projects metadata")
    return projects_cache

# --- Step 4: Identify inactive users ---
def identify_inactive_users(activity_df, users_cache):
    # Build last activity per user
    activity_df = activity_df.dropna(subset=['user_id'])
    activity_df['occurred_at'] = pd.to_datetime(activity_df['occurred_at'], errors='coerce')
    last_active_map = activity_df.groupby('user_id')['occurred_at'].max().to_dict()
    # For each user, determine inactivity
    now = datetime.now(timezone.utc)
    inactive_users = []
    for user_id, user in users_cache.items():
        if user_id in ADMIN_USER_IDS:
            continue
        # Get last activity
        last_active = last_active_map.get(user_id)
        # If user never logged in, check created_at
        created_at = user.get('created_at')
        created_at_dt = pd.to_datetime(created_at, errors='coerce') if created_at else None
        # Inactive if last activity > 12 months ago
        if last_active and (now - last_active.to_pydatetime()).days >= 365:
            inactive_users.append((user_id, last_active, user))
        # Or never logged in and created 6+ months ago
        elif not last_active and created_at_dt and (now - created_at_dt.to_pydatetime()).days >= 180:
            inactive_users.append((user_id, None, user))
    return inactive_users

# --- Step 5: Write output CSV ---
def write_inactive_users_csv(inactive_users, projects_cache, activity_df):
    rows = []
    for user_id, last_active, user in inactive_users:
        # Find a project_id from activity logs if available
        user_acts = activity_df[activity_df['user_id'] == user_id]
        project_id = user_acts['project_id'].dropna().astype(str).iloc[0] if not user_acts.empty and 'project_id' in user_acts else ''
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
    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT_CSV, index=False)

# --- Main ETL Pipeline ---
if __name__ == "__main__":
    try:
        activity_df = load_activity_logs()
        if activity_df.empty or 'user_id' not in activity_df.columns:
            raise ValueError("No valid activity logs found or missing 'user_id' field.")
        oauth = OAuthManager()
        token = oauth.get_access_token()
        headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json'
        }
        users_cache = fetch_users_metadata(oauth, headers)
        projects_cache = fetch_projects_metadata(oauth, headers)
        inactive_users = identify_inactive_users(activity_df, users_cache)
        write_inactive_users_csv(inactive_users, projects_cache, activity_df)
        print(f"✅ Inactive users written to {OUTPUT_CSV}")
    except Exception as e:
        log_error(MODULE, e, "ETL pipeline main")
        print(f"❌ ETL pipeline failed: {e}") 