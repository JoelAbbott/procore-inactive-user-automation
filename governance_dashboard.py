
import streamlit as st
import pandas as pd
import os
import json
import glob
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from oauth_manager import OAuthManager
from audit_logging import log_error, log_audit
import requests
import time

# --- Streamlit Page Config ---
st.set_page_config(page_title="Procore Governance Dashboard", layout="wide")

# --- Configuration ---
MAX_PAGE_SIZE = 300  # API limit
INACTIVE_THRESHOLD_DAYS = 365  # 12 months
NEVER_LOGGED_IN_THRESHOLD_DAYS = 180  # 6 months

# --- Constants ---
USERS_CACHE = './data/intermediate/users_cache.json'
PROJECTS_CACHE = './data/intermediate/projects_cache.json'
ACTIVITY_LOGS_DIR = './data/activity_logs'
REPORT_CSV = './data/reports/never_logged_in_users_report.csv'
MODULE = "never_logged_in_detector"

# --- ENV ---
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)
COMPANY_ID = os.getenv('PROCORE_COMPANY_ID')
BASE_URL = os.getenv('PROCORE_BASE_URL', 'https://sandbox.procore.com')
COMPANY_EMAIL_DOMAIN = os.getenv('COMPANY_EMAIL_DOMAIN', 'compassdatacenters.com').lower()
DMSA_USER_IDS = set(u.strip() for u in os.getenv('DMSA_USER_IDS', '').split(',') if u.strip())

# Ensure reports directory exists
os.makedirs('./data/reports', exist_ok=True)

# --- Helper Functions ---
def get_latest_file(pattern):
    """Get the most recent file matching the pattern."""
    files = glob.glob(pattern)
    if not files:
        return None, []
    files = sorted(files, key=os.path.getmtime, reverse=True)
    return files[0], files

def load_json_file(path):
    """Safely load a JSON file."""
    if not path or not os.path.exists(path):
        st.error(f"File not found: {path}")
        return []
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else [data] if data else []
    except json.JSONDecodeError as e:
        st.error(f"Invalid JSON in file {path}: {e}")
        return []
    except Exception as e:
        st.error(f"Failed to load JSON file {path}: {e}")
        return []

def load_csv_file(path):
    """Safely load a CSV file."""
    if not path or not os.path.exists(path):
        st.warning(f"CSV file not found: {path}")
        return pd.DataFrame(columns=["user_id", "last_active"])
    
    try:
        df = pd.read_csv(path)
        if df.empty or len(df.columns) == 0:
            return pd.DataFrame(columns=["user_id", "last_active"])
        return df
    except Exception as e:
        st.error(f"Failed to load CSV file {path}: {e}")
        return pd.DataFrame(columns=["user_id", "last_active"])

def summary_card(label, value, color, icon=None):
    """Create a summary card widget."""
    style = f"""
        background: #fff;
        box-shadow: 0 2px 8px rgba(0,0,0,0.07);
        border-radius: 12px;
        padding: 1.5rem 2rem;
        margin: 0.5rem;
        display: flex;
        flex-direction: column;
        align-items: center;
        font-size: 1.5rem;
        color: {color};
    """
    icon_html = f"<span style='font-size:2rem'>{icon}</span>" if icon else ""
    st.markdown(f"""
        <div style='{style}'>
            {icon_html}
            <span style='font-size:1.1rem;font-weight:600'>{label}</span>
            <span style='font-size:2.2rem;font-weight:700'>{value}</span>
        </div>
    """, unsafe_allow_html=True)

def api_get_with_retry(url, headers, params=None, max_retries=3, context=None):
    """API GET with retry logic and proper error handling."""
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 400 and "max page size" in resp.text.lower():
                # Handle page size errors gracefully
                st.error(f"API Error: {resp.text}")
                log_error(MODULE, Exception(f"HTTP 400: {resp.text}"), context or url)
                return None
            else:
                raise Exception(f"HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            log_error(MODULE, e, context or url)
            if attempt == max_retries:
                raise
            sleep_time = 2 ** (attempt - 1)
            time.sleep(sleep_time)

def load_or_fetch_users(headers):
    """Load users from cache or fetch from API with proper pagination."""
    if os.path.exists(USERS_CACHE):
        try:
            with open(USERS_CACHE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log_error(MODULE, e, "Loading users_cache.json")
    
    users_cache = {}
    try:
        url = f"{BASE_URL}/rest/v1.0/users"
        params = {'company_id': COMPANY_ID, 'per_page': MAX_PAGE_SIZE, 'page': 1}
        all_users = []
        
        with st.spinner("Fetching users from API..."):
            while True:
                data = api_get_with_retry(url, headers, params, context="GET /users bulk")
                if not data:
                    break
                    
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
                
                # Safety check
                if params['page'] > 1000:
                    st.error("Too many pages while fetching users")
                    break
        
        # Build cache dictionary
        for user in all_users:
            users_cache[str(user.get('id'))] = user
        
        # Save cache
        with open(USERS_CACHE, 'w', encoding='utf-8') as f:
            json.dump(users_cache, f, indent=2)
            
    except Exception as e:
        log_error(MODULE, e, "Fetching users metadata")
        st.error(f"Failed to fetch users: {e}")
    
    return users_cache

def load_or_fetch_projects(headers):
    """Load projects from cache or fetch from API with proper pagination."""
    if os.path.exists(PROJECTS_CACHE):
        try:
            with open(PROJECTS_CACHE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log_error(MODULE, e, "Loading projects_cache.json")
    
    projects_cache = {}
    try:
        url = f"{BASE_URL}/rest/v1.1/projects"
        params = {'company_id': COMPANY_ID, 'per_page': MAX_PAGE_SIZE, 'page': 1}
        all_projects = []
        
        with st.spinner("Fetching projects from API..."):
            while True:
                data = api_get_with_retry(url, headers, params, context="GET /projects bulk")
                if not data:
                    break
                    
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
                
                # Safety check
                if params['page'] > 1000:
                    st.error("Too many pages while fetching projects")
                    break
        
        # Build cache dictionary
        for project in all_projects:
            projects_cache[str(project.get('id'))] = project
        
        # Save cache
        with open(PROJECTS_CACHE, 'w', encoding='utf-8') as f:
            json.dump(projects_cache, f, indent=2)
            
    except Exception as e:
        log_error(MODULE, e, "Fetching projects metadata")
        st.error(f"Failed to fetch projects: {e}")
    
    return projects_cache

def get_latest_project_ids():
    """Find the most recent project_id for each user from activity logs."""
    latest_project_ids = {}
    try:
        if not os.path.exists(ACTIVITY_LOGS_DIR):
            return latest_project_ids
            
        all_files = []
        for fname in os.listdir(ACTIVITY_LOGS_DIR):
            if fname.startswith('activity_log_') and fname.endswith('.json'):
                all_files.append(os.path.join(ACTIVITY_LOGS_DIR, fname))
        
        if not all_files:
            return latest_project_ids
        
        # Sort by date in filename descending
        all_files.sort(reverse=True)
        
        for file_path in all_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    events = json.load(f)
                    if isinstance(events, dict):
                        events = [events]
                    
                    for event in events:
                        user_id = str(event.get('user_id', ''))
                        project_id = str(event.get('project_id', '')) if event.get('project_id') else ''
                        
                        if user_id and project_id and user_id not in latest_project_ids:
                            latest_project_ids[user_id] = project_id
                            
            except Exception as e:
                log_error(MODULE, e, f"Reading {file_path}")
                
        return latest_project_ids
        
    except Exception as e:
        log_error(MODULE, e, "Finding latest project_id per user")
        return latest_project_ids

def is_non_company_email(email):
    """Check if email is from outside the company domain."""
    if not isinstance(email, str) or '@' not in email:
        return False
    return not email.lower().endswith(f"@{COMPANY_EMAIL_DOMAIN}")

def parse_timestamp_safe(timestamp_str):
    """Safely parse timestamp to timezone-aware datetime."""
    if pd.isna(timestamp_str) or timestamp_str == '':
        return None
    try:
        # Parse as UTC if no timezone info
        parsed = pd.to_datetime(timestamp_str, utc=True)
        return parsed
    except Exception:
        try:
            # Fallback: parse as naive then localize to UTC
            parsed = pd.to_datetime(timestamp_str)
            if parsed.tz is None:
                parsed = parsed.tz_localize('UTC')
            else:
                parsed = parsed.tz_convert('UTC')
            return parsed
        except Exception as e:
            log_error(MODULE, e, f"Parsing timestamp: {timestamp_str}")
            return None

def generate_never_logged_in_report():
    """Generate report of users who never logged in."""
    try:
        oauth = OAuthManager()
        token = oauth.get_access_token()
        headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json'
        }
        
        users_cache = load_or_fetch_users(headers)
        
        # Use timezone-aware datetime - FIXED
        threshold_date = datetime.now(timezone.utc) - timedelta(days=NEVER_LOGGED_IN_THRESHOLD_DAYS)
        
        rows = []
        for user_id, user in users_cache.items():
            if user_id in DMSA_USER_IDS:
                continue
            
            last_login = user.get('last_login_at')
            created_at = user.get('created_at')
            
            # If no last_login or null, and created_at > threshold
            if last_login not in [None, '', 'null']:
                continue
            
            if not created_at:
                continue
            
            try:
                created_at_dt = parse_timestamp_safe(created_at)
                if not created_at_dt:
                    continue
                    
            except Exception:
                continue
            
            if created_at_dt > threshold_date:
                continue
            
            row = {
                'first_name': user.get('first_name', ''),
                'last_name': user.get('last_name', ''),
                'email_address': user.get('email_address', ''),
                'vendor_name': user.get('vendor', {}).get('name', '') if user.get('vendor') else '',
                'created_at': created_at_dt.isoformat(),
                'status': 'Never Logged In'
            }
            rows.append(row)
        
        df = pd.DataFrame(rows, columns=[
            'first_name', 'last_name', 'email_address', 'vendor_name', 'created_at', 'status'
        ])
        
        df.to_csv(REPORT_CSV, index=False)
        log_audit(MODULE, "Never Logged In Report Generated", record_count=len(df))
        return True, len(df)
        
    except Exception as e:
        log_error(MODULE, e, "generate_never_logged_in_report")
        return False, 0

# --- Data File Discovery ---
activity_log_pattern = './data/activity_logs/activity_log_*.json'
inactive_users_pattern = './data/deactivation_candidates/inactive_users_*.csv'
audit_log_pattern = './data/audit_logs/deactivation_audit_*.json'
non_company_email_users_path = './data/reports/non_company_email_users_report.csv'

latest_activity_log, activity_logs = get_latest_file(activity_log_pattern)
latest_inactive_users, inactive_users_files = get_latest_file(inactive_users_pattern)
latest_audit_log, audit_logs = get_latest_file(audit_log_pattern)

# --- Load Data ---
activity_events = load_json_file(latest_activity_log) if latest_activity_log else []
inactive_users_df = load_csv_file(latest_inactive_users) if latest_inactive_users else pd.DataFrame(columns=["user_id", "last_active"])
audit_events = load_json_file(latest_audit_log) if latest_audit_log else []

# --- Summary Metrics ---
total_events = len(activity_events)
unique_users = len(set(e.get('user_id') for e in activity_events if e.get('user_id'))) if activity_events else 0
total_inactive = len(inactive_users_df) if not inactive_users_df.empty else 0
total_deactivations = len(audit_events)

# --- Dashboard Layout ---
st.title("Procore Governance Dashboard")
st.markdown("""
<style>
    .block-container {padding-top: 2rem;}
    .stTabs [data-baseweb="tab-list"] {justify-content: left;}
</style>
""", unsafe_allow_html=True)

# --- Summary Cards ---
st.markdown("<div style='display:flex;gap:2rem;'>", unsafe_allow_html=True)
summary_card("Webhook Events Ingested", total_events, "#0072C6", "📥")
summary_card("Unique Users", unique_users, "#0072C6", "👤")
summary_card("Inactive Users Detected", total_inactive, "#b30000", "🛑")
summary_card("Deactivation Events Logged", total_deactivations, "#b30000", "📝")
st.markdown("</div>", unsafe_allow_html=True)

st.markdown('---')

# --- Tabs Navigation ---
tabs = st.tabs(["Activity Logs", "Inactive Users", "Audit Logs", "Non-Company Email Users", "Generate Reports"])

# --- Activity Logs Tab ---
with tabs[0]:
    st.header("Activity Logs")
    if not activity_logs:
        st.info("No activity log files found.")
    else:
        selected_log = st.selectbox("Select Activity Log File", activity_logs, index=0, format_func=lambda x: os.path.basename(x))
        events = load_json_file(selected_log)
        if not events:
            st.warning("No events found in selected log.")
        else:
            df = pd.DataFrame(events)
            st.dataframe(df, use_container_width=True)
            st.markdown('---')
            
            # Analytics
            col1, col2 = st.columns(2)
            with col1:
                if 'object_type' in df.columns:
                    obj_counts = df['object_type'].value_counts().reset_index()
                    obj_counts.columns = ['Object Type', 'Count']
                    st.bar_chart(obj_counts.set_index('Object Type'))
            
            with col2:
                if 'user_id' in df.columns:
                    st.metric("Distinct Active Users", df['user_id'].nunique())
                    st.metric("Total Events", len(df))

# --- Inactive Users Tab ---
with tabs[1]:
    st.header("Inactive Users")
    if not inactive_users_files:
        st.info("No inactive user files found.")
    else:
        selected_csv = st.selectbox("Select Inactive Users File", inactive_users_files, index=0, format_func=lambda x: os.path.basename(x))
        df = load_csv_file(selected_csv)
        if df.empty:
            st.warning("No inactive users in selected file.")
        else:
            def highlight_row(row):
                try:
                    if 'last_active' in row and row['last_active']:
                        last_active = parse_timestamp_safe(row['last_active'])
                        if last_active:
                            threshold = datetime.now(timezone.utc) - timedelta(days=INACTIVE_THRESHOLD_DAYS)
                            if last_active < threshold:
                                return ['background-color: #ffe6e6; color: #b30000'] * len(row)
                            else:
                                return ['background-color: #e6ffe6; color: #006600'] * len(row)
                except:
                    pass
                return [''] * len(row)
            
            st.dataframe(df.style.apply(highlight_row, axis=1), use_container_width=True)
            st.download_button("Export to CSV", df.to_csv(index=False), file_name="inactive_users_export.csv")

# --- Audit Logs Tab ---
with tabs[2]:
    st.header("Deactivation Audit Logs")
    if not audit_logs:
        st.info("No audit log files found.")
    else:
        selected_audit = st.selectbox("Select Audit Log File", audit_logs, index=0, format_func=lambda x: os.path.basename(x))
        events = load_json_file(selected_audit)
        if not events:
            st.warning("No audit events in selected file.")
        else:
            df = pd.DataFrame(events)
            st.dataframe(df, use_container_width=True)
            st.download_button("Export to CSV", df.to_csv(index=False), file_name="deactivation_audit_export.csv")

# --- Non-Company Email Users Tab ---
with tabs[3]:
    st.header("Non-Company Email Users")
    try:
        non_company_df = load_csv_file(non_company_email_users_path)
    except Exception:
        non_company_df = pd.DataFrame(columns=["user_id", "first_name", "last_name", "email_address", "vendor_name", "project_name", "last_active"])
    
    if non_company_df is None or non_company_df.empty:
        st.warning("No non-company email users found or report file is missing.")
    else:
        st.dataframe(non_company_df, use_container_width=True)
        st.download_button("Export to CSV", non_company_df.to_csv(index=False), file_name="non_company_users_export.csv")

# --- Generate Reports Tab ---
with tabs[4]:
    st.header("Generate Reports")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Never Logged In Users Report")
        st.write(f"Generate report for users created more than {NEVER_LOGGED_IN_THRESHOLD_DAYS} days ago who never logged in.")
        
        if st.button("Generate Never Logged In Report"):
            with st.spinner("Generating report..."):
                success, count = generate_never_logged_in_report()
                if success:
                    st.success(f"✅ Report generated successfully! Found {count} users who never logged in.")
                else:
                    st.error("❌ Failed to generate report. Check logs for details.")
    
    with col2:
        st.subheader("Configuration")
        st.write("Current settings:")
        st.write(f"- Company Domain: `{COMPANY_EMAIL_DOMAIN}`")
        st.write(f"- Inactive Threshold: `{INACTIVE_THRESHOLD_DAYS} days`")
        st.write(f"- Never Logged In Threshold: `{NEVER_LOGGED_IN_THRESHOLD_DAYS} days`")
        st.write(f"- API Page Size: `{MAX_PAGE_SIZE}`")

st.markdown('---')
st.caption("Compass Governance Dashboard • Procore Inactive User Automation • For audit and compliance use only.")