import os
import json
import csv
from datetime import datetime
from pathlib import Path
import logging

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] %(levelname)s %(name)s: %(message)s')
handler.setFormatter(formatter)
if not logger.hasHandlers():
    logger.addHandler(handler)

# Field mapping: (CSV column, JSON path as tuple)
FIELD_MAP = [
    ("event_id", ("id",)),
    ("timestamp", ("occurred_at",)),
    ("event_type", ("event",)),
    ("resource_name", ("object_type",)),
    ("user_id", ("details", "user_id")),
    ("project_id", ("details", "project_id")),
    ("source_user_id", ("metadata", "source_user_id")),
    ("source_project_id", ("metadata", "source_project_id")),
]

CSV_COLUMNS = [col for col, _ in FIELD_MAP]

WEBHOOKS_BASE = Path("./data/webhooks")
LOGS_BASE = Path("./data/logs")


def extract_field(payload, path):
    """Safely extract a nested field from a dict, return '' if missing."""
    try:
        value = payload
        for key in path:
            value = value[key]
        return value if value is not None else ''
    except Exception:
        return ''

def process_day(date_str):
    """Process all webhook JSON files for a given date (yyyy-mm-dd)."""
    webhook_dir = WEBHOOKS_BASE / date_str
    log_dir = LOGS_BASE / date_str
    log_dir.mkdir(parents=True, exist_ok=True)
    csv_path = log_dir / "activity_log.csv"

    rows = []
    if not webhook_dir.exists() or not webhook_dir.is_dir():
        logger.warning(f"Webhook directory {webhook_dir} does not exist or is not a directory.")
        return

    for json_file in webhook_dir.glob("*.json"):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                payload = json.load(f)
            row = {col: extract_field(payload, path) for col, path in FIELD_MAP}
            rows.append(row)
        except Exception as e:
            logger.warning(f"Failed to process file {json_file}: {e}")
            continue

    if not rows:
        logger.info(f"No valid webhook events found for {date_str}.")
        return

    try:
        with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        logger.info(f"Wrote {len(rows)} events to {csv_path}")
    except Exception as e:
        logger.error(f"Failed to write CSV log {csv_path}: {e}")


def process_all_days():
    """Process all available days in the webhooks directory."""
    if not WEBHOOKS_BASE.exists() or not WEBHOOKS_BASE.is_dir():
        logger.error(f"Webhooks base directory {WEBHOOKS_BASE} does not exist.")
        return
    for day_dir in sorted(WEBHOOKS_BASE.iterdir()):
        if day_dir.is_dir() and len(day_dir.name) == 10:
            process_day(day_dir.name)

if __name__ == "__main__":
    process_all_days() 