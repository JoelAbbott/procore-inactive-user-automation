import json
import os
import datetime
from pathlib import Path

# Directory setup for logs
log_date = (datetime.datetime.utcnow() - datetime.timedelta(days=400)).strftime('%Y-%m-%d')
log_dir = Path(f'./data/logs/{log_date}')
log_dir.mkdir(parents=True, exist_ok=True)

# Log file path
file_path = log_dir / 'mock_event.json'

# Mock webhook event
mock_event = {
    "user_id": "45698",
    "project_id": "67890",
    "object_type": "rfis",
    "event_type": "updated",
    "occurred_at": (datetime.datetime.utcnow() - datetime.timedelta(days=400)).isoformat() + "Z"
}

# Write mock event
if file_path.exists():
    with open(file_path, 'r', encoding='utf-8') as f:
        try:
            events = json.load(f)
        except Exception:
            events = []
else:
    events = []

events.append(mock_event)

with open(file_path, 'w', encoding='utf-8') as f:
    json.dump(events, f, indent=2)

print(f"\u2705 Mock webhook event written to {file_path}")