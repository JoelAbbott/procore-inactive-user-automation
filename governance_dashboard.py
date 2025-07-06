import streamlit as st
import pandas as pd
import os
import json
import glob
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from oauth_manager import OAuthManager # Assuming this is available
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

# --- File Paths (CRITICAL: Corrected paths and types) ---
USERS_CACHE = './data/intermediate/users_cache.json' 
PROJECTS_CACHE = './data/intermediate/projects_cache.json' 
ACTIVITY_LOGS_DIR = './data/logs' # Corrected to data/logs
INACTIVE_USERS_REPORT_CSV = './data/intermediate/inactive_users_raw.csv' # Output from ETL pipeline
NON_COMPANY_EMAIL_REPORT_CSV = './data/reports/non_company_email_users_report.csv' # Output for specific report
# REMOVED: NEVER_LOGGED_IN_REPORT_CSV as it's filtered from inactive_users_raw.csv
MODULE = "governance_dashboard" 

# --- Environment Variable Loading ---
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)
COMPANY_ID = os.getenv('PROCORE_COMPANY_ID')
BASE_URL = os.getenv('PROCORE_BASE_URL', 'https://sandbox.procore.com')
COMPANY_EMAIL_DOMAIN = os.getenv('COMPANY_EMAIL_DOMAIN', 'compassdatacenters.com').lower()
DRY_RUN_MODE = os.getenv('DRY_RUN_MODE', 'false').lower() == 'true' # For display purposes

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
    except pd.errors.EmptyDataError: # Handle empty CSVs explicitly
        logger.warning(f"CSV file '{path}' is empty or malformed. Returning empty DataFrame.")
        return pd.DataFrame()
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
    except FileNotFoundError: # Handle cache file not found gracefully
        logger.error(f"Cache file not found: {cache_path}. Please run ETL pipeline to generate it.")
        return pd.DataFrame()
    except Exception as e:
        log_error('governance_dashboard', e, f'Loading {cache_path}')
        return pd.DataFrame()

# Load caches globally for enrichment
users_cache_df = load_cache_df(USERS_CACHE, 'user_id')
projects_cache_df = load_cache_df(PROJECTS_CACHE, 'project_id')


# --- Helper: Enrich DataFrame with User/Project/Vendor Info ---
def enrich_with_user_project_vendor(df):
    try:
        if df.empty:
            return df # Return empty df if input is empty

        # Ensure user_id column is string for merge
        if 'user_id' in df.columns:
            df['user_id'] = df['user_id'].astype(str)
            if not users_cache_df.empty:
                # Merge user details directly
                df = df.merge(users_cache_df[['first_name', 'last_name', 'email_address', 'vendor_name', 'created_at']], 
                              left_on='user_id', right_index=True, how='left', suffixes=('', '_cache'))
                # Handle cases where created_at might come from report and cache, prioritize report if present
                if 'created_at' in df.columns and 'created_at_cache' in df.columns:
                    df['created_at'] = df['created_at'].fillna(df['created_at_cache'])
                    df = df.drop(columns=['created_at_cache'])
                elif 'created_at_cache' in df.columns and 'created_at' not in df.columns:
                     df = df.rename(columns={'created_at_cache': 'created_at'})


        # Ensure project_id column is string for merge and handle float conversion
        if 'project_id' in df.columns:
            df['project_id_str_for_merge'] = df['project_id'].apply(lambda x: str(int(float(x))) if pd.notna(x) and str(x).replace('.','',1).isdigit() else '')
            if not projects_cache_df.empty:
                df = df.merge(projects_cache_df[['name']].rename(columns={'name': 'project_name'}), 
                              left_on='project_id_str_for_merge', right_index=True, how='left')
            df = df.drop(columns=['project_id_str_for_merge']) # Clean up helper column


        # Fill NaNs and ensure columns exist
        for col in ['first_name', 'last_name', 'email_address', 'vendor_name', 'project_name', 'last_activity_date', 'status', 'inactivity_days', 'deactivation_status', 'error_message', 'created_at']:
            if col not in df.columns:
                df[col] = ''
            df[col] = df[col].fillna('')
        
        # Ensure 'N/A' for blank project names
        if 'project_name' in df.columns:
            df['project_name'] = df['project_name'].replace({'': 'N/A', None: 'N/A', pd.NA: 'N/A'})

        return df
    except Exception as e:
        st.error(f"Error enriching data: {e}")
        log_error(MODULE, e, 'Enrichment')
        # Return original DataFrame if enrichment fails, after trying to add basic columns
        for col in ['first_name', 'last_name', 'email_address', 'vendor_name', 'project_name']:
            if col not in df.columns:
                df[col] = ''
            df[col] = df[col].fillna('')
        return df


# --- Helper: Compare IDs in Data vs Cache (keep as is) ---
def compare_ids(df, cache_df, id_col, cache_name):
    if df.empty or cache_df.empty or id_col not in df.columns:
        return []
    data_ids = set(df[id_col].astype(str).unique())
    cache_ids = set(cache_df.index.astype(str).unique())
    missing = sorted(list(data_ids - cache_ids))
    return missing

# --- Data File Discovery (CRITICAL: Corrected patterns) ---
# Activity log pattern: now looks for CSVs in data/logs
activity_log_pattern = f'{ACTIVITY_LOGS_DIR}/activity_log_*.csv' 
# Inactive users report pattern: points to raw CSV from ETL
inactive_users_report_pattern = f'{INACTIVE_USERS_REPORT_CSV}' 
# Deactivation log pattern: still in dated subfolders
deactivated_users_path = './data/deactivation_logs/*/deactivation_log.csv'
# Non-company email report path
non_company_email_report_path = NON_COMPANY_EMAIL_REPORT_CSV # Direct path


# Helper to get the latest file (keep as is)
def get_latest_file(pattern):
    files = glob.glob(pattern)
    if not files:
        return None, []
    files = sorted(files, key=os.path.getmtime, reverse=True)
    return files[0], files

# Discover files
latest_activity_log, all_activity_logs = get_latest_file(activity_log_pattern)
# Note: inactive_users_report_pattern is a direct path to the one raw CSV
latest_deactivated_users, all_deactivated_users_logs = get_latest_file(deactivated_users_path)


# --- Dashboard Layout ---
st.title("Procore Governance Dashboard")

if DRY_RUN_MODE:
    st.warning("ℹ️ **DRY RUN MODE is ENABLED.** Actual user deactivations are not being performed.")

# --- Tabs Navigation ---
tabs = st.tabs([
    "Activity Overview",
    "Inactive Users",
    "Never Logged In", # This tab will now filter from inactive_users_raw.csv
    "Non-Company Email Users", # Added a dedicated tab for this
    "Deactivated Users"
])

# --- Activity Overview Tab ---
with tabs[0]:
    st.header("Activity Overview")
    if not all_activity_logs: # Use all_activity_logs to populate selectbox
        st.info("No activity log files found. Please run `activity_logger.py`.")
    else:
        # Load the latest activity log CSV
        selected_log_file = st.selectbox("Select Activity Log File", all_activity_logs, index=0, format_func=lambda x: os.path.basename(x))
        df = load_csv_file(selected_log_file) # CRITICAL: Now loads as CSV
        
        if df.empty:
            st.warning(f"No activity records found in selected log file: {os.path.basename(selected_log_file)}.")
        else:
            df = enrich_with_user_project_vendor(df)

            # Compare user_ids (keep as is)
            missing_users = compare_ids(df, users_cache_df, 'user_id', 'users_cache')
            if missing_users:
                st.warning(f"⚠️ {len(missing_users)} user_id(s) in activity log not found in users_cache: {missing_users[:10]}{'...' if len(missing_users) > 10 else ''}")
            
            # Compare project_ids (keep as is)
            if 'project_id' in df.columns:
                missing_projects = compare_ids(df, projects_cache_df, 'project_id', 'projects_cache')
                if missing_projects:
                    st.warning(f"⚠️ {len(missing_projects)} project_id(s) in activity log not found in projects_cache: {missing_projects[:10]}{'...' if len(missing_projects) > 10 else ''}")
            
            # Display columns (adjust order for better readability)
            display_cols = [
                'first_name', 'last_name', 'email_address', 'vendor_name', 'project_name',
                'event_type', 'resource_name', 'timestamp', 'user_id', 'project_id' # Added user_id, project_id for clarity
            ]
            # Filter to only include columns that exist in the DataFrame
            display_cols_filtered = [col for col in display_cols if col in df.columns]
            st.dataframe(df[display_cols_filtered], use_container_width=True)
            st.download_button("Export Activity Log to CSV", df[display_cols_filtered].to_csv(index=False), file_name="activity_log_export.csv", mime="text/csv")
            log_audit(MODULE, 'Loaded Activity Overview', record_count=len(df))

# --- Inactive Users Tab ---
with tabs[1]:
    st.header("Inactive Users")
    df_inactive_raw = load_csv_file(INACTIVE_USERS_REPORT_CSV) # Load the raw inactive users report
    
    if df_inactive_raw.empty:
        st.info("No inactive users found. Please run `inactivity_etl_pipeline.py`.")
    else:
        # Filter for only 'Inactive' status for this tab
        df = df_inactive_raw[df_inactive_raw['status'] == 'Inactive'].copy() 
        df = enrich_with_user_project_vendor(df)

        missing_users = compare_ids(df, users_cache_df, 'user_id', 'users_cache')
        if missing_users:
            st.warning(f"⚠️ {len(missing_users)} user_id(s) in inactive users report not found in users_cache: {missing_users[:10]}{'...' if len(missing_users) > 10 else ''}")
        
        if 'project_id' in df.columns:
            missing_projects = compare_ids(df, projects_cache_df, 'project_id', 'projects_cache')
            if missing_projects:
                st.warning(f"⚠️ {len(missing_projects)} project_id(s) in inactive users report not found in projects_cache: {missing_projects[:10]}{'...' if len(missing_projects) > 10 else ''}")
        
        display_cols = [
            'first_name', 'last_name', 'email_address', 'vendor_name', 'project_name', 
            'last_activity_date', 'inactivity_days', 'status', 'user_id', 'created_at'
        ]
        display_cols_filtered = [col for col in display_cols if col in df.columns]

        if df.empty:
            st.info("No users currently flagged as 'Inactive > 365 days'.")
        else:
            def highlight_inactive_row(row): # Specific highlight function for this tab
                if 'status' in row and row['status'] == 'Inactive':
                    return ['background-color: #ffe6e6; color: #b30000'] * len(row)
                return [''] * len(row)
            
            st.dataframe(df[display_cols_filtered].style.apply(highlight_inactive_row, axis=1), use_container_width=True)
            st.download_button("Export Inactive Users to CSV", df[display_cols_filtered].to_csv(index=False), file_name="inactive_users_export.csv", mime="text/csv")
            log_audit(MODULE, 'Loaded Inactive Users', record_count=len(df))

# --- Never Logged In Tab ---
with tabs[2]: # CRITICAL: This tab now filters from inactive_users_raw.csv
    st.header("Never Logged In Users")
    df_inactive_raw = load_csv_file(INACTIVE_USERS_REPORT_CSV) # Load the raw inactive users report again

    if df_inactive_raw.empty:
        st.info("No users found. Please run `inactivity_etl_pipeline.py`.")
    else:
        # Filter for only 'Never Logged In' status for this tab
        df = df_inactive_raw[df_inactive_raw['status'] == 'Never Logged In'].copy()
        df = enrich_with_user_project_vendor(df)

        missing_users = compare_ids(df, users_cache_df, 'user_id', 'users_cache')
        if missing_users:
            st.warning(f"⚠️ {len(missing_users)} user_id(s) in 'Never Logged In' report not found in users_cache: {missing_users[:10]}{'...' if len(missing_users) > 10 else ''}")
        
        display_cols = [
            'first_name', 'last_name', 'email_address', 'vendor_name', 'created_at', 'status', 'user_id', 'inactivity_days'
        ]
        display_cols_filtered = [col for col in display_cols if col in df.columns]

        if df.empty:
            st.info("No users currently flagged as 'Never Logged In'.")
        else:
            def highlight_never_loggedIn_row(row): # Specific highlight for this tab
                if 'status' in row and row['status'] == 'Never Logged In':
                    return ['background-color: #fffacd; color: #cc9900'] * len(row) # Light yellow
                return [''] * len(row)
            st.dataframe(df[display_cols_filtered].style.apply(highlight_never_loggedIn_row, axis=1), use_container_width=True)
            st.download_button("Export Never Logged In Users to CSV", df[display_cols_filtered].to_csv(index=False), file_name="never_logged_in_export.csv", mime="text/csv")
            log_audit(MODULE, 'Loaded Never Logged In', record_count=len(df))

# --- Non-Company Email Users Tab (NEWLY ADDED) ---
with tabs[3]: # This is the 4th tab (index 3)
    st.header("Non-Company Email Users")
    non_company_df = load_csv_file(NON_COMPANY_EMAIL_REPORT_CSV) # Load report from correct path
    
    if non_company_df.empty:
        st.info("No non-company email users found. Please run `non_company_email_report_generator.py`.")
    else:
        non_company_df = enrich_with_user_project_vendor(non_company_df) # Enrich for display

        missing_users = compare_ids(non_company_df, users_cache_df, 'user_id', 'users_cache')
        if missing_users:
            st.warning(f"⚠️ {len(missing_users)} user_id(s) in non-company email report not found in users_cache: {missing_users[:10]}{'...' if len(missing_users) > 10 else ''}")
        
        display_cols = [
            'first_name', 'last_name', 'email_address', 'vendor_name', 'created_at', 'last_active', 'user_id'
        ]
        display_cols_filtered = [col for col in display_cols if col in non_company_df.columns]

        st.dataframe(non_company_df[display_cols_filtered], use_container_width=True)
        st.download_button("Export Non-Company Email Users to CSV", non_company_df[display_cols_filtered].to_csv(index=False), file_name="non_company_email_export.csv", mime="text/csv")
        log_audit(MODULE, 'Loaded Non-Company Email Users', record_count=len(non_company_df))


# --- Deactivated Users Tab ---
with tabs[4]: # This is the 5th tab (index 4)
    st.header("Deactivated Users")
    if DRY_RUN_MODE:
        st.info("ℹ️ **DRY RUN MODE is ENABLED.** Deactivations require manual approval and are not performed automatically.")
    
    if not all_deactivated_users_logs: # Use all_deactivated_users_logs
        st.info("No deactivated user log files found. Please run `user_deactivation_pipeline.py`.")
    else:
        # Load the latest deactivation log CSV
        selected_deact_log_file = st.selectbox("Select Deactivated Users Log File", all_deactivated_users_logs, index=0, format_func=lambda x: os.path.basename(x))
        df = load_csv_file(selected_deact_log_file)
        
        if df.empty:
            st.warning(f"No deactivation records found in selected log file: {os.path.basename(selected_deact_log_file)}.")
        else:
            df = enrich_with_user_project_vendor(df)

            missing_users = compare_ids(df, users_cache_df, 'user_id', 'users_cache')
            if missing_users:
                st.warning(f"⚠️ {len(missing_users)} user_id(s) in deactivated users log not found in users_cache: {missing_users[:10]}{'...' if len(missing_users) > 10 else ''}")
            
            if 'project_id' in df.columns:
                missing_projects = compare_ids(df, projects_cache_df, 'project_id', 'projects_cache')
                if missing_projects:
                    st.warning(f"⚠️ {len(missing_projects)} project_id(s) in deactivated users log not found in projects_cache: {missing_projects[:10]}{'...' if len(missing_projects) > 10 else ''}")
            
            display_cols = [
                'first_name', 'last_name', 'email_address', 'vendor_name', 'project_name', 
                'last_activity_date', 'deactivation_status', 'error_message', 'user_id'
            ]
            display_cols_filtered = [col for col in display_cols if col in df.columns]

            def highlight_deactivation_status(row): # Highlight success/error
                if 'deactivation_status' in row:
                    if row['deactivation_status'] == 'success':
                        return ['background-color: #d4edda; color: #155724'] * len(row) # Green
                    elif row['deactivation_status'] == 'error':
                        return ['background-color: #f8d7da; color: #721c24'] * len(row) # Red
                    elif row['deactivation_status'] == 'dry_run':
                        return ['background-color: #fff3cd; color: #856404'] * len(row) # Yellow
                return [''] * len(row)

            st.dataframe(df[display_cols_filtered].style.apply(highlight_deactivation_status, axis=1), use_container_width=True)
            st.download_button("Export Deactivated Users Log to CSV", df[display_cols_filtered].to_csv(index=False), file_name="deactivation_log_export.csv", mime="text/csv")
            log_audit(MODULE, 'Loaded Deactivated Users', record_count=len(df))


st.markdown('---')
st.caption("Compass Governance Dashboard • Phase10 Inactive User Automation • For audit and compliance use only.")
