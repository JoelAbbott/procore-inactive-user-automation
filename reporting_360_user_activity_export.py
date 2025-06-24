import os
import pandas as pd
import logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

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
PROCORE_COMPANY_ID = os.getenv('PROCORE_COMPANY_ID')
LOGS_BASE = Path("./data/logs")
OUTPUT_FILE = Path("./data/reports/reporting_360_user_activity_export.csv")

# Output columns
EXPORT_COLUMNS = ['company_id', 'user_id', 'total_events', 'total_projects', 'first_activity_date', 'last_activity_date']

def load_activity_logs():
    """Load all available activity log CSV files from daily folders."""
    all_data = []
    total_files = 0
    processed_files = 0
    error_files = 0
    
    if not LOGS_BASE.exists():
        logger.warning(f"Logs directory {LOGS_BASE} does not exist.")
        return pd.DataFrame()
    
    for day_dir in sorted(LOGS_BASE.iterdir()):
        if not day_dir.is_dir() or len(day_dir.name) != 10:  # Skip non-date directories
            continue
        
        csv_file = day_dir / "activity_log.csv"
        total_files += 1
        
        if not csv_file.exists():
            logger.warning(f"Activity log file {csv_file} does not exist.")
            continue
        
        try:
            df = pd.read_csv(csv_file)
            if not df.empty:
                # Add log date for tracking
                df['log_date'] = day_dir.name
                all_data.append(df)
                processed_files += 1
                logger.info(f"Loaded {len(df)} records from {csv_file}")
            else:
                logger.info(f"Empty file: {csv_file}")
        except Exception as e:
            logger.error(f"Failed to load {csv_file}: {e}")
            error_files += 1
            continue
    
    logger.info(f"File processing summary: {total_files} total, {processed_files} processed, {error_files} errors")
    
    if not all_data:
        logger.warning("No activity log data found.")
        return pd.DataFrame()
    
    combined_df = pd.concat(all_data, ignore_index=True)
    logger.info(f"Combined {len(combined_df)} total records from all log files.")
    return combined_df

def parse_timestamp(timestamp_str):
    """Parse timestamp string to UTC datetime."""
    if pd.isna(timestamp_str) or timestamp_str == '':
        return None
    
    try:
        # Try parsing ISO 8601 format
        return pd.to_datetime(timestamp_str, utc=True)
    except Exception:
        try:
            # Try parsing without timezone info (assume UTC)
            dt = pd.to_datetime(timestamp_str)
            return dt.tz_localize('UTC')
        except Exception as e:
            logger.warning(f"Failed to parse timestamp '{timestamp_str}': {e}")
            return None

def aggregate_user_activity(df):
    """Aggregate activity data by user_id."""
    if df.empty:
        logger.warning("No data to aggregate.")
        return pd.DataFrame()
    
    # Filter out rows with missing user_id
    df = df.dropna(subset=['user_id'])
    if df.empty:
        logger.warning("No records with valid user_id found.")
        return pd.DataFrame()
    
    # Parse timestamps
    df['parsed_timestamp'] = df['timestamp'].apply(parse_timestamp)
    df = df.dropna(subset=['parsed_timestamp'])
    
    if df.empty:
        logger.warning("No records with valid timestamps found.")
        return pd.DataFrame()
    
    # Aggregate by user_id
    user_summary = df.groupby('user_id').agg({
        'event_id': 'count',  # Total events
        'project_id': 'nunique',  # Distinct projects
        'parsed_timestamp': ['min', 'max']  # First and last activity
    }).reset_index()
    
    # Flatten column names
    user_summary.columns = ['user_id', 'total_events', 'total_projects', 'first_activity_date', 'last_activity_date']
    
    # Convert datetime to string format for CSV
    user_summary['first_activity_date'] = user_summary['first_activity_date'].dt.strftime('%Y-%m-%d %H:%M:%S UTC')
    user_summary['last_activity_date'] = user_summary['last_activity_date'].dt.strftime('%Y-%m-%d %H:%M:%S UTC')
    
    logger.info(f"Aggregated activity for {len(user_summary)} unique users.")
    return user_summary

def enrich_with_company_id(df):
    """Add company_id to the dataframe."""
    if df.empty:
        return df
    
    df['company_id'] = PROCORE_COMPANY_ID
    return df[EXPORT_COLUMNS]  # Reorder columns to match export format

def export_user_activity_summary():
    """Main function to export user activity summary for Reporting 360."""
    logger.info("Starting user activity summary export for Reporting 360.")
    
    # Validate environment
    if not PROCORE_COMPANY_ID:
        logger.error("PROCORE_COMPANY_ID environment variable is required.")
        return False
    
    # Load activity logs
    df = load_activity_logs()
    
    # Aggregate user activity
    user_summary = aggregate_user_activity(df)
    
    if user_summary.empty:
        logger.warning("No user activity data to export.")
        return False
    
    # Enrich with company_id
    export_df = enrich_with_company_id(user_summary)
    
    # Create output directory if it doesn't exist
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    # Write to CSV
    try:
        export_df.to_csv(OUTPUT_FILE, index=False)
        logger.info(f"Successfully exported {len(export_df)} user activity records to {OUTPUT_FILE}")
        return True
    except Exception as e:
        logger.error(f"Failed to write export file: {e}")
        return False

def get_export_summary():
    """Get a summary of the export file if it exists."""
    if not OUTPUT_FILE.exists():
        return "Export file does not exist."
    
    try:
        df = pd.read_csv(OUTPUT_FILE)
        return f"Export file contains {len(df)} user activity records."
    except Exception as e:
        return f"Error reading export file: {e}"

if __name__ == "__main__":
    success = export_user_activity_summary()
    if success:
        logger.info("User activity summary export completed successfully.")
        print(get_export_summary())
    else:
        logger.error("User activity summary export failed.")
        exit(1) 