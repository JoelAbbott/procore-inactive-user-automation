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
# Updated to handle various webhook payload structures
FIELD_MAP = [
    ("event_id", ("id",)),
    ("timestamp", ("occurred_at",)),
    ("event_type", ("event",)),
    ("resource_name", ("object_type",)),
    ("user_id", ("user", "id")),  # Try nested user object first
    ("user_id", ("details", "user_id")),  # Fallback to details.user_id
    ("user_id", ("metadata", "performer_id")),  # Another fallback
    ("project_id", ("project", "id")),  # Try nested project object first
    ("project_id", ("details", "project_id")),  # Fallback to details.project_id
    ("project_id", ("metadata", "project_id")),  # Another fallback
    ("project_id", ("resource_id",)),  # Sometimes project_id is stored as resource_id
    ("source_user_id", ("metadata", "source_user_id")),
    ("source_project_id", ("metadata", "source_project_id")),
]

# Create a list of column names for the CSV output
CSV_COLUMNS = ["event_id", "timestamp", "event_type", "resource_name", "user_id", "project_id", "source_user_id", "source_project_id"]

# Define base paths for storing raw webhooks and processed activity logs
WEBHOOKS_BASE = Path("./data/webhooks")
LOGS_BASE = Path("./data/logs")


def extract_field_multiple_paths(payload, paths):
    """
    Try multiple paths to extract a field from the payload.
    This handles different webhook structures from Procore.
    """
    for path in paths:
        value = extract_field(payload, path)
        if value and value != '':
            return value
    return ''


def extract_field(payload, path):
    """
    Safely extracts a nested field from a dictionary (webhook payload).
    Returns an empty string if any part of the path is missing or if the value is None.
    """
    try:
        value = payload
        for key in path:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return ''
            if value is None:
                return ''
        return str(value) if value is not None else ''
    except Exception as e:
        logger.debug(f"Failed to extract field with path {path}: {e}")
        return ''


def extract_user_and_project_from_resource(payload):
    """
    Special extraction logic for user and project IDs based on resource type.
    Some webhooks store IDs differently based on the resource being modified.
    """
    user_id = ''
    project_id = ''
    
    # Get resource type
    resource_type = payload.get('object_type', '').lower()
    
    # For user-related events, the resource_id might be the user_id
    if 'user' in resource_type and payload.get('resource_id'):
        user_id = str(payload['resource_id'])
    
    # For project-related events, extract from the resource
    if payload.get('resource') and isinstance(payload['resource'], dict):
        # Try to get project_id from resource
        project_id = str(payload['resource'].get('project_id', '')) if payload['resource'].get('project_id') else ''
        # Try to get user_id from resource if not already found
        if not user_id and payload['resource'].get('created_by_id'):
            user_id = str(payload['resource']['created_by_id'])
    
    return user_id, project_id


def process_webhook_day(date_str: str):
    """
    Processes all raw webhook JSON files for a specific date and converts them into
    a single, structured CSV file with improved ID extraction.
    """
    webhook_dir = WEBHOOKS_BASE / date_str
    csv_path = LOGS_BASE / f"activity_log_{date_str}.csv"
    rows = []

    # Ensure the logs directory exists
    LOGS_BASE.mkdir(parents=True, exist_ok=True)

    if not webhook_dir.exists() or not webhook_dir.is_dir():
        logger.warning(f"Webhook directory {webhook_dir} does not exist or is not a directory. Skipping.")
        return

    logger.info(f"Processing webhook files in {webhook_dir}")
    for json_file in webhook_dir.glob("*.json"):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                payload = json.load(f)
            
            # Extract standard fields
            row = {}
            row['event_id'] = extract_field(payload, ("id",)) or f"evt_{json_file.stem}"
            row['timestamp'] = extract_field(payload, ("occurred_at",)) or extract_field(payload, ("created_at",))
            row['event_type'] = extract_field(payload, ("event",)) or extract_field(payload, ("event_type",))
            row['resource_name'] = extract_field(payload, ("object_type",)) or extract_field(payload, ("resource_name",))
            
            # Extract user_id - try multiple paths
            user_paths = [
                ("user", "id"),
                ("details", "user_id"),
                ("metadata", "performer_id"),
                ("created_by", "id"),
                ("performer", "id")
            ]
            row['user_id'] = extract_field_multiple_paths(payload, user_paths)
            
            # Extract project_id - try multiple paths
            project_paths = [
                ("project", "id"),
                ("details", "project_id"),
                ("metadata", "project_id"),
                ("resource", "project_id"),
                ("resource_id",)  # Sometimes this is the project_id
            ]
            row['project_id'] = extract_field_multiple_paths(payload, project_paths)
            
            # If still no IDs found, try resource-based extraction
            if not row['user_id'] or not row['project_id']:
                resource_user_id, resource_project_id = extract_user_and_project_from_resource(payload)
                if not row['user_id'] and resource_user_id:
                    row['user_id'] = resource_user_id
                if not row['project_id'] and resource_project_id:
                    row['project_id'] = resource_project_id
            
            # Extract source IDs
            row['source_user_id'] = extract_field(payload, ("metadata", "source_user_id"))
            row['source_project_id'] = extract_field(payload, ("metadata", "source_project_id"))
            
            # Log what we found for debugging
            if row['user_id'] or row['project_id']:
                logger.debug(f"Extracted from {json_file.name}: user_id={row['user_id']}, project_id={row['project_id']}")
            else:
                logger.warning(f"No IDs found in {json_file.name}: {json.dumps(payload, indent=2)[:500]}...")
            
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
            writer.writerows(rows)
        logger.info(f"Successfully wrote {len(rows)} events to {csv_path}")
    except Exception as e:
        logger.error(f"Failed to write CSV log {csv_path}: {e}")


def process_all_days():
    """
    Iterates through all daily webhook directories and processes them.
    """
    if not WEBHOOKS_BASE.exists() or not WEBHOOKS_BASE.is_dir():
        logger.error(f"Webhooks base directory {WEBHOOKS_BASE} does not exist. Creating it.")
        WEBHOOKS_BASE.mkdir(parents=True, exist_ok=True)
        return

    # Get all subdirectories
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
    # Process all existing webhook directories
    process_all_days()