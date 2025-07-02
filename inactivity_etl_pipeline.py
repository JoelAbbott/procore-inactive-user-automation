import os
import json
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from oauth_manager import OAuthManager # Assuming this is available
from audit_logging import log_error, log_audit, log_operation_summary

import requests
import time
import logging

# Configure logging for the ETL pipeline module
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] %(levelname)s %(name)s: %(message)s')
handler.setFormatter(formatter)
if not logger.hasHandlers():
    logger.addHandler(handler)

# --- Constants and Configuration Paths ---
LOGS_BASE = './data/logs' # Directory where daily activity logs (CSVs) are stored
OUTPUT_DIR = './data/intermediate' # Directory for intermediate cache files and raw reports
OUTPUT_CSV = os.path.join(OUTPUT_DIR, 'inactive_users_raw.csv') # Output CSV for inactive users
USERS_CACHE = os.path.join(OUTPUT_DIR, 'users_cache.json') # Cache for Procore user metadata
PROJECTS_CACHE = os.path.join(OUTPUT_DIR, 'projects_cache.json') # Cache for Procore project metadata
MODULE = "inactivity_etl_pipeline" # Module name for audit logging

# --- ETL Specific Configuration ---
MAX_PAGE_SIZE = 300  # Procore API limit for records per page
INACTIVE_THRESHOLD_DAYS = 365  # Users inactive for this many days are considered inactive (12 months)
NEVER_LOGGED_IN_THRESHOLD_DAYS = 180  # Users created this many days ago who never logged in

# --- Environment Variable Loading ---
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)
ADMIN_USER_IDS = set(u.strip() for u in os.getenv('ADMIN_USER_IDS', '').split(',') if u.strip())
COMPANY_ID = os.getenv('PROCORE_COMPANY_ID')
BASE_URL = os.getenv('PROCORE_BASE_URL', 'https://sandbox.procore.com')

# Ensure output directory exists
Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)


# --- Helper Functions for Data Loading and API Calls ---

def load_json_cache(filepath: str) -> dict:
    """Loads a JSON file into a dictionary, handling file not found errors."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"Cache file not found: {filepath}. Returning empty dictionary.")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from {filepath}: {e}. Returning empty dictionary.")
        return {}


def save_json_cache(data: dict, filepath: str):
    """Saves a dictionary to a JSON file."""
    try:
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Cache saved to {filepath}")
    except Exception as e:
        logger.error(f"Failed to save cache to {filepath}: {e}")


def _make_api_call(session, url, headers, params=None):
    """Internal helper to make API calls with retry logic."""
    for attempt in range(3): # Simple retry logic
        try:
            response = session.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.warning(f"API call to {url} failed (attempt {attempt+1}/3): {e}")
            time.sleep(2 ** attempt) # Exponential backoff
    raise ConnectionError(f"Failed to fetch data from {url} after multiple attempts.")


def fetch_users_metadata(oauth_manager: OAuthManager, headers: dict) -> dict:
    """
    Fetches all user metadata from Procore API and caches it.
    Returns a dictionary mapping user_id (as string) to user details, flattened for dashboard use.
    """
    users_cache = load_json_cache(USERS_CACHE)
    if users_cache:
        logger.info(f"Loaded {len(users_cache)} users from cache: {USERS_CACHE}")
        return users_cache # Return cached data if available

    logger.info("Fetching all users from Procore API...")
    users_data = {}
    session = requests.Session() # Use a session for connection pooling

    # Procore API requires company_id for /users endpoint
    if not COMPANY_ID:
        logger.error("PROCORE_COMPANY_ID is not set. Cannot fetch user metadata.")
        return {}

    page = 1
    while True:
        url = f"{BASE_URL}/rest/v1.1/users?company_id={COMPANY_ID}&page={page}&per_page={MAX_PAGE_SIZE}"
        try:
            current_users = _make_api_call(session, url, headers)
            if not current_users:
                break
            for user in current_users:
                # Flatten and extract only the required fields
                users_data[str(user.get('id'))] = {
                    'first_name': user.get('first_name', ''),
                    'last_name': user.get('last_name', ''),
                    'email_address': user.get('email_address', ''),
                    'vendor_name': user.get('vendor', {}).get('name', '') if user.get('vendor') else '',
                    'created_at': user.get('created_at', ''),
                    # Add more fields as needed
                }
            logger.info(f"Fetched page {page} with {len(current_users)} users. Total: {len(users_data)}")
            if len(current_users) < MAX_PAGE_SIZE:
                break # No more pages
            page += 1
            time.sleep(0.1) # Small delay to be polite to the API
        except Exception as e:
            log_error(MODULE, e, f"Error fetching users metadata (page {page})")
            break

    save_json_cache(users_data, USERS_CACHE)
    logger.info(f"Successfully fetched and cached {len(users_data)} users.")
    return users_data


def fetch_projects_metadata(oauth_manager: OAuthManager, headers: dict) -> dict:
    """
    Fetches all project metadata from Procore API and caches it.
    Returns a dictionary mapping project_id (as string) to project details, flattened for dashboard use.
    """
    projects_cache = load_json_cache(PROJECTS_CACHE)
    if projects_cache:
        logger.info(f"Loaded {len(projects_cache)} projects from cache: {PROJECTS_CACHE}")
        return projects_cache # Return cached data if available

    logger.info("Fetching all projects from Procore API...")
    projects_data = {}
    session = requests.Session() # Use a session for connection pooling

    # Procore API requires company_id for /projects endpoint
    if not COMPANY_ID:
        logger.error("PROCORE_COMPANY_ID is not set. Cannot fetch project metadata.")
        return {}

    page = 1
    while True:
        url = f"{BASE_URL}/rest/v1.0/projects?company_id={COMPANY_ID}&page={page}&per_page={MAX_PAGE_SIZE}"
        try:
            current_projects = _make_api_call(session, url, headers)
            if not current_projects:
                break
            for project in current_projects:
                # Flatten and extract only the required fields
                projects_data[str(project.get('id'))] = {
                    'name': project.get('name', ''),
                    # Add more fields as needed
                }
            logger.info(f"Fetched page {page} with {len(current_projects)} projects. Total: {len(projects_data)}")
            if len(current_projects) < MAX_PAGE_SIZE:
                break # No more pages
            page += 1
            time.sleep(0.1) # Small delay
        except Exception as e:
            log_error(MODULE, e, f"Error fetching projects metadata (page {page})")
            break

    save_json_cache(projects_data, PROJECTS_CACHE)
    logger.info(f"Successfully fetched and cached {len(projects_data)} projects.")
    return projects_data


def load_activity_logs() -> pd.DataFrame:
    """
    Loads all available activity log CSV files into a single Pandas DataFrame.
    It expects CSVs named 'activity_log_YYYY-MM-DD.csv' directly in the LOGS_BASE directory.
    Ensures 'user_id' and 'project_id' columns are treated as strings for consistency.
    """
    all_data = []
    total_files = 0
    processed_files = 0
    error_files = 0

    if not Path(LOGS_BASE).exists():
        logger.warning(f"Activity logs directory {LOGS_BASE} does not exist.")
        return pd.DataFrame()

    logger.info(f"Loading activity logs from {LOGS_BASE}...")
    # Look for CSV files named activity_log_YYYY-MM-DD.csv directly in LOGS_BASE
    for log_file in Path(LOGS_BASE).glob(f'activity_log_*.csv'): 
        total_files += 1
        try:
            df = pd.read_csv(log_file)
            if not df.empty:
                # Ensure 'user_id' and 'project_id' columns are strings for reliable merging
                if 'user_id' in df.columns:
                    df['user_id'] = df['user_id'].astype(str)
                if 'project_id' in df.columns:
                    df['project_id'] = df['project_id'].astype(str)
                all_data.append(df)
                processed_files += 1
            else:
                logger.info(f"Skipping empty activity log file: {log_file.name}")
        except pd.errors.EmptyDataError:
            logger.warning(f"Activity log file {log_file.name} is empty. Skipping.")
            error_files += 1
        except Exception as e:
            logger.error(f"Error loading activity log file {log_file.name}: {e}")
            error_files += 1

    if not all_data:
        logger.warning(f"No activity logs found or processed successfully from {LOGS_BASE}.")
        return pd.DataFrame()

    combined_df = pd.concat(all_data, ignore_index=True)
    
    # Convert 'timestamp' to datetime objects for time-based calculations
    if 'timestamp' in combined_df.columns:
        combined_df['timestamp'] = pd.to_datetime(combined_df['timestamp'], errors='coerce', utc=True)
        # Drop rows where timestamp conversion failed
        combined_df.dropna(subset=['timestamp'], inplace=True)
        
    logger.info(f"Successfully loaded {processed_files}/{total_files} activity log files. Total records: {len(combined_df)}")
    return combined_df


def identify_inactive_users(activity_df: pd.DataFrame, users_cache: dict) -> pd.DataFrame:
    """
    Identifies inactive users based on activity logs and user metadata.
    Enriches with user details from the cache and filters out admin/DMSA users.
    
    Args:
        activity_df (pd.DataFrame): DataFrame containing user activity logs.
        users_cache (dict): Dictionary mapping user_id to user details.
        
    Returns:
        pd.DataFrame: DataFrame of identified inactive users with enriched details.
    """
    if activity_df.empty:
        logger.warning("Activity DataFrame is empty, cannot identify inactive users.")
        return pd.DataFrame()

    logger.info("Identifying inactive users...")
    current_time = datetime.now(timezone.utc)

    # Calculate last activity for each user
    # Ensure 'user_id' is string for groupby
    activity_df['user_id'] = activity_df['user_id'].astype(str) 
    last_activity = activity_df.groupby('user_id')['timestamp'].max().reset_index()
    last_activity.rename(columns={'timestamp': 'last_activity_date'}, inplace=True)

    # Convert all user IDs in cache keys to string for reliable lookup
    string_keyed_users_cache = {str(k): v for k, v in users_cache.items()}

    # Get all users from cache
    all_users_df = pd.DataFrame.from_dict(string_keyed_users_cache, orient='index')
    # Ensure the 'id' column in all_users_df is also string type for merging
    if 'id' in all_users_df.columns:
        all_users_df['id'] = all_users_df['id'].astype(str)
    all_users_df.rename(columns={'id': 'user_id'}, inplace=True) # Rename 'id' to 'user_id' for merging

    # Merge with user metadata to get creation date and status
    # Use 'left' merge to keep all users from the cache, even if they have no activity
    inactive_potential_df = pd.merge(
        all_users_df,
        last_activity,
        on='user_id',
        how='left'
    )

    # Convert 'created_at' to datetime and drop rows where conversion fails
    if 'created_at' in inactive_potential_df.columns:
        inactive_potential_df['created_at'] = pd.to_datetime(inactive_potential_df['created_at'], errors='coerce', utc=True)
        inactive_potential_df.dropna(subset=['created_at'], inplace=True)
    else:
        logger.warning("Missing 'created_at' column in user data. Cannot identify 'never logged in' users precisely.")
        inactive_potential_df['created_at'] = pd.NaT # Set to Not a Time if missing

    # Calculate inactivity_days
    inactive_potential_df['inactivity_days'] = (current_time - inactive_potential_df['last_activity_date']).dt.days

    # Identify "Never Logged In" users
    # A user is "never logged in" if last_activity_date is NaT AND created_at is older than threshold
    never_logged_in_mask = (inactive_potential_df['last_activity_date'].isna()) & \
                           ((current_time - inactive_potential_df['created_at']).dt.days > NEVER_LOGGED_IN_THRESHOLD_DAYS)

    # Identify "Inactive" users
    # A user is "inactive" if last_activity_date is not NaT AND inactivity_days exceeds threshold
    inactive_mask = (inactive_potential_df['last_activity_date'].notna()) & \
                    (inactive_potential_df['inactivity_days'] > INACTIVE_THRESHOLD_DAYS)

    # Combine masks for all inactive users (both truly inactive and never logged in)
    inactive_users_df = inactive_potential_df[inactive_mask | never_logged_in_mask].copy()

    # Add status for clarity
    inactive_users_df['status'] = 'Inactive'
    inactive_users_df.loc[never_logged_in_mask, 'status'] = 'Never Logged In'

    # Filter out users who are already inactive or suspended (if 'status' field exists and relevant)
    if 'status' in inactive_users_df.columns:
        inactive_users_df = inactive_users_df[
            ~inactive_users_df['status'].isin(['inactive', 'suspended'])
        ]
    
    # Filter out admin users and DMSA users (assuming 'is_admin' field or similar)
    # Ensure ADMIN_USER_IDS is a set of strings
    inactive_users_df = inactive_users_df[
        ~inactive_users_df['user_id'].isin(ADMIN_USER_IDS)
    ]
    
    # Further filter for DMSA users if such a list or attribute exists on the user object
    # For example, if you had a DMSA_USER_IDS list from config:
    # inactive_users_df = inactive_users_df[~inactive_users_df['user_id'].isin(DMSA_USER_IDS)]
    
    # Select and reorder relevant columns
    display_cols = [
        'user_id', 'first_name', 'last_name', 'email_address',
        'status', 'inactivity_days', 'last_activity_date', 'created_at'
    ]
    # Filter out columns that don't exist in the DataFrame
    display_cols = [col for col in display_cols if col in inactive_users_df.columns]
    inactive_users_df = inactive_users_df[display_cols]

    logger.info(f"Identified {len(inactive_users_df)} potential inactive users.")
    return inactive_users_df


def write_inactive_users_csv(inactive_users_df: pd.DataFrame, projects_cache: dict, activity_df: pd.DataFrame):
    """
    Writes the identified inactive users to a CSV file, enriching them with
    project names from their last activity (if available).
    
    Args:
        inactive_users_df (pd.DataFrame): DataFrame of inactive users.
        projects_cache (dict): Dictionary mapping project_id to project details.
        activity_df (pd.DataFrame): Full activity log DataFrame for last project lookup.
    """
    if inactive_users_df.empty:
        logger.warning("No inactive users to write to CSV.")
        return

    logger.info(f"Writing {len(inactive_users_df)} inactive users to {OUTPUT_CSV}")

    # Ensure projects_cache keys are strings
    string_keyed_projects_cache = {str(k): v for k, v in projects_cache.items()}

    # Get the last project ID for each user from activity_df
    # Ensure 'user_id' and 'project_id' are strings for this merge
    activity_df['user_id'] = activity_df['user_id'].astype(str)
    if 'project_id' in activity_df.columns:
        activity_df['project_id'] = activity_df['project_id'].astype(str)
        # Sort by timestamp descending to easily get the last project
        activity_df_sorted = activity_df.sort_values(by='timestamp', ascending=False)
        # Drop duplicates based on user_id, keeping the first (most recent)
        last_project_for_user = activity_df_sorted.drop_duplicates(subset='user_id')[[
            'user_id', 'project_id'
        ]].copy()
        last_project_for_user.rename(columns={'project_id': 'last_project_id'}, inplace=True)
    else:
        logger.warning("No 'project_id' column in activity logs. Cannot enrich with last project names.")
        last_project_for_user = pd.DataFrame(columns=['user_id', 'last_project_id'])


    # Merge inactive users with their last project ID
    # Ensure 'user_id' is string for merge
    inactive_users_df['user_id'] = inactive_users_df['user_id'].astype(str)
    inactive_users_with_project = pd.merge(
        inactive_users_df,
        last_project_for_user,
        on='user_id',
        how='left'
    )

    # Add 'last_project_name' column
    inactive_users_with_project['last_project_name'] = inactive_users_with_project['last_project_id'].apply(
        lambda x: string_keyed_projects_cache.get(str(x), {}).get('name', 'N/A') if pd.notna(x) else 'N/A'
    )
    
    # Define final columns for the CSV output
    final_cols = [
        'user_id', 'first_name', 'last_name', 'email_address', 'status',
        'inactivity_days', 'last_activity_date', 'last_project_name', 'created_at'
    ]
    
    # Filter to only include columns that actually exist in the DataFrame
    final_cols_filtered = [col for col in final_cols if col in inactive_users_with_project.columns]
    output_df = inactive_users_with_project[final_cols_filtered]

    # Ensure output directory exists before writing
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    
    try:
        output_df.to_csv(OUTPUT_CSV, index=False, encoding='utf-8')
        logger.info(f"Successfully wrote inactive user report to {OUTPUT_CSV}")
        log_operation_summary(
            MODULE,
            "Inactive Users Report Generation",
            total_processed=len(inactive_users_df),
            successful=len(inactive_users_df),
            failed=0,
            notes=f"Report generated with {len(inactive_users_df)} inactive users."
        )
    except Exception as e:
        logger.error(f"Failed to write inactive user report to CSV: {e}")
        log_error(MODULE, e, "Failed to write inactive user report CSV")


# --- Main ETL Pipeline Execution ---
if __name__ == "__main__":
    try:
        log_audit(MODULE, "ETL Pipeline Started")
        
        # Step 1: Load activity logs from CSVs
        # This function now ensures 'user_id' and 'project_id' are strings
        activity_df = load_activity_logs()
        if activity_df.empty or 'user_id' not in activity_df.columns:
            raise ValueError("No valid activity logs found or missing 'user_id' field. Aborting ETL.")
        
        # Step 2: Get OAuth token and setup headers for Procore API calls
        oauth = OAuthManager()
        token = oauth.get_access_token()
        headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json'
        }
        
        # Step 3: Fetch and cache user and project metadata from Procore API
        # Caching prevents repeated API calls and handles JSON key types
        users_cache = fetch_users_metadata(oauth, headers)
        projects_cache = fetch_projects_metadata(oauth, headers)
        
        # Step 4: Identify inactive users based on activity data and user metadata
        # This function uses the string-keyed user cache
        inactive_users = identify_inactive_users(activity_df, users_cache)
        
        # Step 5: Write the identified inactive users to a CSV report
        # This function handles enriching with project names and saves the output
        write_inactive_users_csv(inactive_users, projects_cache, activity_df)
        
        log_audit(MODULE, "ETL Pipeline Completed Successfully", 
                 record_count=len(inactive_users))
        print(f"✅ Inactive users report generated and written to {OUTPUT_CSV}")
        
    except Exception as e:
        log_error(MODULE, e, "Main ETL Pipeline Execution Failed")
        log_audit(MODULE, "ETL Pipeline Failed", notes=str(e))
        logger.error(f"ETL pipeline failed: {e}", exc_info=True)
        # Re-raise the exception if you want the program to terminate on error
        raise