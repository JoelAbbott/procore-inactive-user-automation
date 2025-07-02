import streamlit as st
import pandas as pd
import os
import json
import glob
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from oauth_manager import OAuthManager # Assuming this is available and handles authentication
from audit_logging import log_error, log_audit, log_operation_summary # For logging
import requests
import time
import logging

# Configure logging for the Streamlit dashboard
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] %(levelname)s %(name)s: %(message)s')
handler.setFormatter(formatter)
if not logger.hasHandlers():
    logger.addHandler(handler)

# --- Streamlit Page Configuration ---
st.set_page_config(page_title="Procore Governance Dashboard", layout="wide")

# --- Configuration Constants (can also be pulled from a config.py module) ---
MAX_PAGE_SIZE = 300  # Procore API limit for records per page
INACTIVE_THRESHOLD_DAYS = 365  # Number of days after which a user is considered inactive
NEVER_LOGGED_IN_THRESHOLD_DAYS = 180  # Number of days after creation to consider a user 'never logged in'

# --- File Paths ---
USERS_CACHE = './data/intermediate/users_cache.json' # Path to cached user metadata
PROJECTS_CACHE = './data/intermediate/projects_cache.json' # Path to cached project metadata
ACTIVITY_LOGS_DIR = './data/logs' # Directory containing daily activity log CSVs
INACTIVE_USERS_REPORT_CSV = './data/intermediate/inactive_users_raw.csv' # Output from ETL pipeline
NEVER_LOGGED_IN_REPORT_CSV = './data/reports/never_logged_in_users_report.csv' # Output for specific report
MODULE = "governance_dashboard" # Module name for audit logging

# --- Environment Variable Loading ---
# Loads environment variables from a .env file (e.g., PROCORE_COMPANY_ID, COMPANY_EMAIL_DOMAIN)
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)
COMPANY_ID = os.getenv('PROCORE_COMPANY_ID')
BASE_URL = os.getenv('PROCORE_BASE_URL', 'https://sandbox.procore.com')
COMPANY_EMAIL_DOMAIN = os.getenv('COMPANY_EMAIL_DOMAIN', 'compassdatacenters.com').lower()

# --- Load ENV for DRY_RUN_MODE ---
DRY_RUN_MODE = os.getenv('DRY_RUN_MODE', 'false').lower() == 'true'

# --- Caching for Performance ---
@st.cache_data
def load_json_file(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log_error('governance_dashboard', e, f'Loading {path}')
        return []

@st.cache_data
def load_csv_file(path):
    try:
        df = pd.read_csv(path)
        if df.empty or len(df.columns) == 0:
            return pd.DataFrame()
        return df
    except Exception as e:
        log_error('governance_dashboard', e, f'Loading {path}')
        return pd.DataFrame()

@st.cache_data
def load_cache_df(cache_path, index_col):
    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            cache = json.load(f)
        df = pd.DataFrame.from_dict(cache, orient='index')
        df.index.name = index_col
        df.index = df.index.astype(str)
        return df
    except Exception as e:
        log_error('governance_dashboard', e, f'Loading {cache_path}')
        return pd.DataFrame()

users_cache_path = './data/intermediate/users_cache.json'
projects_cache_path = './data/intermediate/projects_cache.json'
users_cache_df = load_cache_df(users_cache_path, 'user_id')
projects_cache_df = load_cache_df(projects_cache_path, 'project_id')

# --- Helper: Enrich DataFrame with User/Project/Vendor Info ---
def enrich_with_user_project_vendor(df):
    try:
        if not df.empty and 'user_id' in df.columns and not users_cache_df.empty:
            df['user_id'] = df['user_id'].astype(str)
            df = df.merge(users_cache_df[['first_name', 'last_name', 'email_address', 'vendor_name']], left_on='user_id', right_index=True, how='left')
        if not df.empty and 'project_id' in df.columns and not projects_cache_df.empty:
            df['project_id'] = df['project_id'].astype(str)
            df = df.merge(projects_cache_df[['name']].rename(columns={'name': 'project_name'}), left_on='project_id', right_index=True, how='left')
        if 'project_name' not in df.columns:
            df['project_name'] = 'N/A'
        else:
            df['project_name'] = df['project_name'].replace({None: 'N/A', pd.NA: 'N/A', '': 'N/A'})
        if 'vendor_name' not in df.columns:
            df['vendor_name'] = ''
        df = df.fillna('')
        return df
    except Exception as e:
        st.error(f"Error enriching data: {e}")
        log_error('governance_dashboard', e, 'Enrichment')
        for col in ['first_name', 'last_name', 'email_address', 'vendor_name', 'project_name']:
            if col not in df.columns:
                df[col] = ''
        return df

# --- Helper: Compare IDs in Data vs Cache ---
def compare_ids(df, cache_df, id_col, cache_name):
    if df.empty or cache_df.empty or id_col not in df.columns:
        return []
    data_ids = set(df[id_col].astype(str).unique())
    cache_ids = set(cache_df.index.astype(str).unique())
    missing = sorted(list(data_ids - cache_ids))
    return missing

# --- Data File Discovery ---
activity_log_pattern = './data/activity_logs/activity_log_*.json'
inactive_users_pattern = './data/deactivation_candidates/inactive_users_*.csv'
audit_log_pattern = './data/audit_logs/deactivation_audit_*.json'
non_company_email_users_path = './data/reports/non_company_email_users_report.csv'
deactivated_users_path = './data/deactivation_logs/*/deactivation_log.csv'
never_logged_in_path = './data/reports/never_logged_in_users_report.csv'

def get_latest_file(pattern):
    files = glob.glob(pattern)
    if not files:
        return None, []
    files = sorted(files, key=os.path.getmtime, reverse=True)
    return files[0], files

latest_activity_log, activity_logs = get_latest_file(activity_log_pattern)
latest_inactive_users, inactive_users_files = get_latest_file(inactive_users_pattern)
latest_audit_log, audit_logs = get_latest_file(audit_log_pattern)
latest_deactivated_users, deactivated_users_files = get_latest_file(deactivated_users_path)

# --- Dashboard Layout ---
st.title("Procore Governance Dashboard")

# --- Summary Cards (optional, not changed) ---
# ... (keep your summary card logic here) ...

# --- Tabs Navigation ---
tabs = st.tabs([
    "Activity Overview",
    "Inactive Users",
    "Never Logged In",
    "Deactivated Users",
    "Non-Company Email Users"
])

# --- Activity Overview Tab ---
with tabs[0]:
    st.header("Activity Overview")
    if not activity_logs:
        st.info("No activity log files found.")
    else:
        selected_log = st.selectbox("Select Activity Log File", activity_logs, index=0, format_func=lambda x: os.path.basename(x))
        events = load_json_file(selected_log)
        if not events:
            st.warning("No events found in selected log.")
        else:
            df = pd.DataFrame(events)
            df = enrich_with_user_project_vendor(df)
            # Compare user_ids
            missing_users = compare_ids(df, users_cache_df, 'user_id', 'users_cache')
            if missing_users:
                st.warning(f"{len(missing_users)} user_id(s) in activity log not found in users_cache: {missing_users[:10]}{'...' if len(missing_users) > 10 else ''}")
            # Compare project_ids if present
            if 'project_id' in df.columns:
                missing_projects = compare_ids(df, projects_cache_df, 'project_id', 'projects_cache')
                if missing_projects:
                    st.warning(f"{len(missing_projects)} project_id(s) in activity log not found in projects_cache: {missing_projects[:10]}{'...' if len(missing_projects) > 10 else ''}")
            display_cols = [
                'first_name', 'last_name', 'email_address', 'vendor_name', 'project_name',
                'event_type', 'object_type', 'occurred_at'
            ]
            for col in display_cols:
                if col not in df.columns:
                    df[col] = ''
            st.dataframe(df[display_cols], use_container_width=True)
            log_audit('governance_dashboard', 'Loaded Activity Overview', record_count=len(df))

# --- Inactive Users Tab ---
with tabs[1]:
    st.header("Inactive Users")
    if not inactive_users_files:
        st.info("No inactive user files found.")
    else:
        selected_csv = st.selectbox("Select Inactive Users File", inactive_users_files, index=0, format_func=lambda x: os.path.basename(x))
        df = load_csv_file(selected_csv)
        df = enrich_with_user_project_vendor(df)
        if 'last_active' in df.columns:
            df['last_activity_date'] = df['last_active']
        missing_users = compare_ids(df, users_cache_df, 'user_id', 'users_cache')
        if missing_users:
            st.warning(f"{len(missing_users)} user_id(s) in inactive users not found in users_cache: {missing_users[:10]}{'...' if len(missing_users) > 10 else ''}")
        if 'project_id' in df.columns:
            missing_projects = compare_ids(df, projects_cache_df, 'project_id', 'projects_cache')
            if missing_projects:
                st.warning(f"{len(missing_projects)} project_id(s) in inactive users not found in projects_cache: {missing_projects[:10]}{'...' if len(missing_projects) > 10 else ''}")
        display_cols = [
            'first_name', 'last_name', 'email_address', 'vendor_name', 'project_name', 'last_activity_date'
        ]
        for col in display_cols:
            if col not in df.columns:
                df[col] = ''
        if df.empty:
            st.warning("No inactive users in selected file.")
        else:
            def highlight_row(row):
                try:
                    last_active = pd.to_datetime(row['last_activity_date'])
                    if last_active < datetime.datetime.now() - datetime.timedelta(days=365):
                        return ['background-color: #ffe6e6; color: #b30000'] * len(row)
                    else:
                        return ['background-color: #e6ffe6; color: #006600'] * len(row)
                except:
                    return [''] * len(row)
            st.dataframe(df[display_cols].style.apply(highlight_row, axis=1), use_container_width=True)
            st.download_button("Export to CSV", df[display_cols].to_csv(index=False), file_name="inactive_users_export.csv")
            log_audit('governance_dashboard', 'Loaded Inactive Users', record_count=len(df))

# --- Never Logged In Tab ---
with tabs[2]:
    st.header("Never Logged In Users")
    try:
        never_logged_df = load_csv_file(never_logged_in_path)
        never_logged_df = enrich_with_user_project_vendor(never_logged_df)
    except Exception:
        never_logged_df = pd.DataFrame(columns=["first_name", "last_name", "email_address", "vendor_name", "created_at", "status"])
    display_cols = ["first_name", "last_name", "email_address", "vendor_name", "created_at", "status"]
    for col in display_cols:
        if col not in never_logged_df.columns:
            never_logged_df[col] = ''
    if never_logged_df is None or never_logged_df.empty:
        st.warning("No never-logged-in users found or report file is missing.")
    else:
        st.dataframe(never_logged_df[display_cols], use_container_width=True)
        st.download_button("Export to CSV", never_logged_df[display_cols].to_csv(index=False), file_name="never_logged_in_export.csv")
        log_audit('governance_dashboard', 'Loaded Never Logged In', record_count=len(never_logged_df))

# --- Deactivated Users Tab ---
with tabs[3]:
    st.header("Deactivated Users")
    if DRY_RUN_MODE:
        st.info("DRY_RUN_MODE is enabled. Deactivations require manual approval and are not performed automatically.")
    if not deactivated_users_files:
        st.info("No deactivated user log files found.")
    else:
        selected_csv = st.selectbox("Select Deactivated Users File", deactivated_users_files, index=0, format_func=lambda x: os.path.basename(x))
        df = load_csv_file(selected_csv)
        df = enrich_with_user_project_vendor(df)
        missing_users = compare_ids(df, users_cache_df, 'user_id', 'users_cache')
        if missing_users:
            st.warning(f"{len(missing_users)} user_id(s) in deactivated users not found in users_cache: {missing_users[:10]}{'...' if len(missing_users) > 10 else ''}")
        if 'project_id' in df.columns:
            missing_projects = compare_ids(df, projects_cache_df, 'project_id', 'projects_cache')
            if missing_projects:
                st.warning(f"{len(missing_projects)} project_id(s) in deactivated users not found in projects_cache: {missing_projects[:10]}{'...' if len(missing_projects) > 10 else ''}")
        display_cols = [
            'first_name', 'last_name', 'email_address', 'vendor_name', 'project_name', 'last_activity_date', 'deactivation_status', 'error_message'
        ]
        for col in display_cols:
            if col not in df.columns:
                df[col] = ''
        if df.empty:
            st.warning("No deactivated users in selected file.")
        else:
            st.dataframe(df[display_cols], use_container_width=True)
            st.download_button("Export to CSV", df[display_cols].to_csv(index=False), file_name="deactivated_users_export.csv")
            log_audit('governance_dashboard', 'Loaded Deactivated Users', record_count=len(df))

# --- Non-Company Email Users Tab ---
with tabs[4]:
    st.header("Non-Company Email Users")
    if DRY_RUN_MODE:
        st.info("DRY_RUN_MODE is enabled. Deactivation of non-company email users requires manual approval.")
    try:
        non_company_df = load_csv_file(non_company_email_users_path)
        non_company_df = enrich_with_user_project_vendor(non_company_df)
    except Exception:
        non_company_df = pd.DataFrame(columns=["first_name", "last_name", "email_address", "vendor_name", "project_name", "last_active"])
    missing_users = compare_ids(non_company_df, users_cache_df, 'user_id', 'users_cache')
    if missing_users:
        st.warning(f"{len(missing_users)} user_id(s) in non-company email users not found in users_cache: {missing_users[:10]}{'...' if len(missing_users) > 10 else ''}")
    if 'project_id' in non_company_df.columns:
        missing_projects = compare_ids(non_company_df, projects_cache_df, 'project_id', 'projects_cache')
        if missing_projects:
            st.warning(f"{len(missing_projects)} project_id(s) in non-company email users not found in projects_cache: {missing_projects[:10]}{'...' if len(missing_projects) > 10 else ''}")
    display_cols = ["first_name", "last_name", "email_address", "vendor_name", "project_name", "last_active"]
    for col in display_cols:
        if col not in non_company_df.columns:
            non_company_df[col] = ''
    if non_company_df is None or non_company_df.empty:
        st.warning("No non-company email users found or report file is missing.")
    else:
        st.dataframe(non_company_df[display_cols], use_container_width=True)
        st.download_button("Export to CSV", non_company_df[display_cols].to_csv(index=False), file_name="non_company_users_export.csv")
        log_audit('governance_dashboard', 'Loaded Non-Company Email Users', record_count=len(non_company_df))

st.markdown('---')
st.caption("Compass Governance Dashboard • Phase10 Inactive User Automation • For audit and compliance use only.")
