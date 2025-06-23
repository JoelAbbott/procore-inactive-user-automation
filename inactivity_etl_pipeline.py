import pandas as pd
import logging
from pathlib import Path
from datetime import datetime, timezone
import os

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] %(levelname)s %(name)s: %(message)s')
handler.setFormatter(formatter)
if not logger.hasHandlers():
    logger.addHandler(handler)

# Paths
LOGS_BASE = Path("./data/logs")
REPORTS_BASE = Path("./data/reports")
OUTPUT_FILE = REPORTS_BASE / "inactive_users_report.csv"

# Constants
INACTIVITY_THRESHOLD_DAYS = 365  # 12 months
CURRENT_TIME = datetime.now(timezone.utc)

def load_activity_logs():
    """Load all available CSV log files from the logs directory."""
    all_data = []
    
    if not LOGS_BASE.exists():
        logger.warning(f"Logs directory {LOGS_BASE} does not exist.")
        return pd.DataFrame()
    
    for day_dir in sorted(LOGS_BASE.iterdir()):
        if not day_dir.is_dir() or len(day_dir.name) != 10:  # Skip non-date directories
            continue
            
        csv_file = day_dir / "activity_log.csv"
        if not csv_file.exists():
            logger.warning(f"Activity log file {csv_file} does not exist.")
            continue
            
        try:
            df = pd.read_csv(csv_file)
            if not df.empty:
                all_data.append(df)
                logger.info(f"Loaded {len(df)} records from {csv_file}")
        except Exception as e:
            logger.error(f"Failed to load {csv_file}: {e}")
            continue
    
    if not all_data:
        logger.warning("No activity log data found.")
        return pd.DataFrame()
    
    combined_df = pd.concat(all_data, ignore_index=True)
    logger.info(f"Combined {len(combined_df)} total records from all log files.")
    return combined_df

def parse_timestamp(timestamp_str):
    """Parse timestamp string to datetime object, handling various formats."""
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

def process_inactivity_data(df):
    """Process activity data to identify inactive users."""
    if df.empty:
        logger.warning("No data to process.")
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
    
    # Group by user_id and find latest activity
    user_activity = df.groupby('user_id')['parsed_timestamp'].max().reset_index()
    user_activity.columns = ['user_id', 'last_activity_date']
    
    # Calculate inactivity days
    user_activity['inactivity_days'] = (CURRENT_TIME - user_activity['last_activity_date']).dt.days
    
    # Add status column
    user_activity['status'] = user_activity['inactivity_days'].apply(
        lambda days: "Inactive > 12 months" if days >= INACTIVITY_THRESHOLD_DAYS else "Active"
    )
    
    # Filter for inactive users only
    inactive_users = user_activity[user_activity['inactivity_days'] >= INACTIVITY_THRESHOLD_DAYS].copy()
    
    # Convert datetime to string for CSV output
    inactive_users['last_activity_date'] = inactive_users['last_activity_date'].dt.strftime('%Y-%m-%d %H:%M:%S UTC')
    
    logger.info(f"Found {len(inactive_users)} inactive users out of {len(user_activity)} total users.")
    return inactive_users

def save_report(df):
    """Save the inactive users report to CSV."""
    if df.empty:
        logger.warning("No inactive users to report.")
        return
    
    try:
        REPORTS_BASE.mkdir(parents=True, exist_ok=True)
        df.to_csv(OUTPUT_FILE, index=False)
        logger.info(f"Saved inactive users report to {OUTPUT_FILE}")
    except Exception as e:
        logger.error(f"Failed to save report to {OUTPUT_FILE}: {e}")

def run_inactivity_analysis():
    """Main function to run the inactivity analysis pipeline."""
    logger.info("Starting inactivity analysis pipeline.")
    
    # Load activity logs
    df = load_activity_logs()
    
    # Process data
    inactive_users = process_inactivity_data(df)
    
    # Save report
    save_report(inactive_users)
    
    logger.info("Inactivity analysis pipeline completed.")

if __name__ == "__main__":
    run_inactivity_analysis() 