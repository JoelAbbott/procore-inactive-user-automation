import os
import json
import csv
from datetime import datetime
from pathlib import Path
import logging

# Configure logging for the activity logger module
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] %(levelname)s %(name)s: %(message)s')
handler.setFormatter(formatter)
if not logger.hasHandlers():
    logger.addHandler(handler)

# Define the mapping between desired CSV column names and their corresponding JSON paths
# Each tuple contains (CSV_COLUMN_NAME, JSON_PATH_TUPLE)
# The JSON_PATH_TUPLE specifies the nested keys to access in the incoming webhook payload
FIELD_MAP = [
    ("event_id", ("id",)),                  # Unique identifier for the webhook event
    ("timestamp", ("occurred_at",)),       # When the event occurred (UTC)
    ("event_type", ("event",)),            # Type of event (e.g., "user_updated", "rfi_created")
    ("resource_name", ("object_type",)),   # Type of object involved (e.g., "RFI", "User")
    ("user_id", ("details", "user_id")),   # ID of the user performing the action
    ("project_id", ("details", "project_id")), # ID of the project related to the event.
                                            # IMPORTANT: Ensure Procore webhooks actually include 'project_id' in 'details'.
                                            # If this field is consistently missing from raw webhook JSONs,
                                            # the issue is upstream (Procore webhook configuration) or requires
                                            # a later enrichment step using other data.
    ("source_user_id", ("metadata", "source_user_id")), # Original user ID if applicable (e.g., for API calls)
    ("source_project_id", ("metadata", "source_project_id")), # Original project ID if applicable
]

# Create a list of column names for the CSV output based on the FIELD_MAP
CSV_COLUMNS = [col for col, _ in FIELD_MAP]

# Define base paths for storing raw webhooks and processed activity logs
WEBHOOKS_BASE = Path("./data/webhooks")
LOGS_BASE = Path("./data/logs")


def extract_field(payload, path):
    """
    Safely extracts a nested field from a dictionary (webhook payload).
    Returns an empty string if any part of the path is missing or if the value is None.
    This prevents KeyErrors or TypeErrors when processing inconsistent webhook payloads.
    Non-string values are converted to string for CSV consistency.
    """
    try:
        value = payload
        for key in path:
            if isinstance(value, dict):
                value = value.get(key) # Use .get() to avoid KeyError
            else:
                return '' # Path segment is not a dictionary, cannot proceed
            if value is None:
                return '' # Found a None value in the path
        # Convert non-string values (like int IDs) to string for consistency in CSV
        return str(value) if value is not None else ''
    except Exception as e:
        logger.debug(f"Failed to extract field with path {path}: {e}. Payload snippet: {str(payload)[:200]}...")
        return ''


def process_webhook_day(date_str: str):
    """
    Processes all raw webhook JSON files for a specific date and converts them into
    a single, structured CSV file. The CSV is named 'activity_log_YYYY-MM-DD.csv'
    and stored directly in the LOGS_BASE directory.
    
    Args:
        date_str (str): The date string in 'YYYY-MM-DD' format (e.g., '2025-06-30').
    """
    webhook_dir = WEBHOOKS_BASE / date_str
    # IMPORTANT CHANGE: CSV is now directly in LOGS_BASE with specific date in name
    csv_path = LOGS_BASE / f"activity_log_{date_str}.csv" 
    rows = []

    # Ensure the logs directory exists where the CSV will be saved
    LOGS_BASE.mkdir(parents=True, exist_ok=True)

    if not webhook_dir.exists() or not webhook_dir.is_dir():
        logger.warning(f"Webhook directory {webhook_dir} does not exist or is not a directory. Skipping.")
        return

    logger.info(f"Processing webhook files in {webhook_dir}")
    for json_file in webhook_dir.glob("*.json"):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                payload = json.load(f)
            # Extract fields based on FIELD_MAP, ensuring all values are strings
            row = {col: extract_field(payload, path) for col, path in FIELD_MAP}
            rows.append(row)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to decode JSON from file {json_file}: {e}. Skipping.")
            continue
        except Exception as e:
            logger.warning(f"Failed to process file {json_file}: {e}. Skipping.")
            continue

    if not rows:
        logger.info(f"No valid webhook events found for {date_str}. No CSV created.")
        return

    try:
        with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows) # Use writerows for efficiency
        logger.info(f"Successfully wrote {len(rows)} events to {csv_path}")
    except Exception as e:
        logger.error(f"Failed to write CSV log {csv_path}: {e}")


def process_all_days():
    """
    Iterates through all daily webhook directories and processes them.
    This function ensures that all historical webhook data can be converted to activity logs.
    """
    if not WEBHOOKS_BASE.exists() or not WEBHOOKS_BASE.is_dir():
        logger.error(f"Webhooks base directory {WEBHOOKS_BASE} does not exist or is not a directory. Aborting.")
        return

    # Get all subdirectories (which are assumed to be date strings) and sort them
    all_dates = sorted([d.name for d in WEBHOOKS_BASE.iterdir() if d.is_dir() and len(d.name) == 10])
    if not all_dates:
        logger.info(f"No daily webhook directories found in {WEBHOOKS_BASE}.")
        return

    logger.info(f"Starting to process activity logs for {len(all_dates)} days.")
    for date_str in all_dates:
        logger.info(f"Processing activity for date: {date_str}")
        process_webhook_day(date_str)
    logger.info("Finished processing all available daily webhooks.")


if __name__ == "__main__":
    # Example usage:
    # To process all existing webhook directories into daily activity logs:
    process_all_days()

    # To process only a specific day (e.g., '2025-06-30'):
    # process_webhook_day('2025-06-30')