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
# FIXED: Group all alternative paths for each field together
FIELD_MAP = {
    "event_id": [
        ("id",),
        ("event_id",)
    ],
    "timestamp": [
        ("occurred_at",),
        ("created_at",),
        ("timestamp",)
    ],
    "event_type": [
        ("event",),
        ("event_type",),
        ("reason",)
    ],
    "resource_name": [
        ("object_type",),
        ("resource_name",),
        ("resource_type",)
    ]
}

# Create a list of column names for the CSV output
CSV_COLUMNS = ["event_id", "timestamp", "event_type", "resource_name", "user_id", "project_id", "source_user_id", "source_project_id"]

# Define base paths for storing raw webhooks and processed activity logs
WEBHOOKS_BASE = Path("./data/webhooks")
LOGS_BASE = Path("./data/logs")


def extract_field_multiple_paths(payload, paths):
    """
    Try multiple paths to extract a field from the payload.
    Returns the first found non-None, non-empty string value.
    """
    for path in paths:
        value = extract_field(payload, path)
        if value is not None and str(value) != '':
            return str(value)
    return ''


def extract_field(payload, path):
    """
    Safely extracts a nested field from a dictionary (webhook payload).
    Returns None if any part of the path is missing or if the value is None.
    Returns the value as is (can be int, str, etc.) otherwise.
    """
    try:
        value = payload
        for key in path:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return None
            if value is None:
                return None
        return value
    except Exception as e:
        logger.debug(f"Failed to extract field with path {path}: {e}")
        return None


def extract_user_and_project_from_resource(payload):
    """
    Special extraction logic for user and project IDs based on resource type.
    Some webhooks store IDs differently based on the resource being modified.
    This updated function ensures that for 'Company Users' webhooks, the 'resource_id'
    is correctly recognized as the user_id of the affected user.
    """
    user_id_from_resource = ''
    project_id_from_resource = ''
    
    # Get resource type from payload (use 'resource_type' or 'object_type')
    resource_type_payload = payload.get('resource_type', payload.get('object_type', '')).lower()
    
    # For user-related events, the resource_id might be the user_id (the affected user)
    if 'user' in resource_type_payload and payload.get('resource_id'):
        user_id_from_resource = str(payload['resource_id'])
    
    # For project-related events, extract from the resource object or details
    if payload.get('resource') and isinstance(payload['resource'], dict):
        project_id_from_resource = str(payload['resource'].get('project_id', ''))
        # If user_id not found yet and created_by_id exists in resource
        if not user_id_from_resource and payload['resource'].get('created_by_id'):
            user_id_from_resource = str(payload['resource']['created_by_id'])
    
    # Fallback for 'details' key if 'resource' not present (e.g., in v4.0 Company Users events)
    if 'details' in payload and isinstance(payload['details'], dict):
        if not user_id_from_resource and payload['details'].get('user_id'):
            user_id_from_resource = str(payload['details']['user_id']) # This is the performer
        if not project_id_from_resource and payload['details'].get('project_id'):
            project_id_from_resource = str(payload['details']['project_id'])
            
    return user_id_from_resource, project_id_from_resource


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

            # --- DIAGNOSTIC PRINT 1: Raw Payload ---
            print(f"\n--- Processing file: {json_file.name} ---")
            print(f"RAW PAYLOAD: {json.dumps(payload, indent=2)}")

            # Initialize row
            row = {}

            # FIXED: Use FIELD_MAP correctly for standard field extraction
            for col_name in ["event_id", "timestamp", "event_type", "resource_name"]:
                if col_name in FIELD_MAP:
                    paths = FIELD_MAP[col_name]
                    extracted_value = extract_field_multiple_paths(payload, paths)
                    row[col_name] = extracted_value
            
            # --- Dedicated User/Project ID Logic for robustness ---
            # This block handles user_id, project_id, source_user_id, source_project_id
            # This logic comes *after* the FIELD_MAP loop populates the other fields.

            # Prioritize resource_id as user_id for 'Company Users' events (affected user)
            resource_type_payload = payload.get('resource_type', payload.get('object_type', '')).lower()
            if 'user' in resource_type_payload and payload.get('resource_id'):
                row['user_id'] = str(payload['resource_id']) # Affected user
                # Performer might be in 'user_id' field of payload (the actor)
                row['source_user_id'] = str(payload.get('user_id', '')) # The user who performed the action
                if row['source_user_id'] == row['user_id']: # If actor is the same as affected, clear source_user_id
                    row['source_user_id'] = ''
            else:
                # For other event types, try to get user_id from common paths or resource
                user_paths_main = [
                    ("user", "id"),
                    ("details", "user_id"),
                    ("metadata", "performer_id"),
                    ("created_by", "id"),
                    ("performer", "id")
                ]
                row['user_id'] = extract_field_multiple_paths(payload, user_paths_main)
            
            # Project ID (prioritize specific fields, then general resource extraction)
            project_paths_main = [
                ("project", "id"),
                ("details", "project_id"),
                ("metadata", "project_id"),
                ("resource", "project_id")
            ]
            row['project_id'] = extract_field_multiple_paths(payload, project_paths_main)
            
            # If project_id is still not found, try resource_id if resource_type is project related
            if not row['project_id'] and 'project' in resource_type_payload and 'resource_id' in payload:
                row['project_id'] = str(payload['resource_id'])
            
            # Source Project ID (for transfer events etc.)
            source_project_paths = [("metadata", "source_project_id")]
            row['source_project_id'] = extract_field_multiple_paths(payload, source_project_paths)
            
            # Default empty strings for missing fields
            for col in CSV_COLUMNS:
                if col not in row:
                    row[col] = ''
            
            # --- DIAGNOSTIC PRINT 2: Final Row before Append ---
            print(f"FINAL PROCESSED ROW: {row}")

            # Log what we found for debugging (keep as is)
            if row['user_id'] or row['project_id']:
                logger.debug(f"Extracted from {json_file.name}: user_id={row['user_id']}, project_id={row['project_id']}")
            else:
                logger.warning(f"No primary IDs found in {json_file.name}: {json.dumps(payload, indent=2)[:500]}...")
            
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

    # --- DIAGNOSTIC PRINT 3: Inspect the 'rows' list before writing ---
    print(f"\n--- WRITING CSV: Inspecting {len(rows)} rows for {date_str} ---")
    for i, row_data in enumerate(rows):
        if i < 5 or i > len(rows) - 5: # Print first/last few rows to avoid excessive console output
            print(f"  Row {i} (Before Write): {row_data}")
        elif i == 5 and len(rows) > 10:
            print("  ... (many intermediate rows omitted) ...")

    try:
        with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
            csvfile.flush()
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
    print("--- ACTIVITY LOGGER SCRIPT (WITH FIXES) IS RUNNING ---")
    process_all_days()