import os
import json
import datetime

# Output path
today = datetime.datetime.utcnow().strftime('%Y%m%d')
file_path = f"./data/audit_logs/deactivation_audit_{today}.json"

# Create mock deactivation event
mock_event = {
    "user_id": "mock_user_001",
    "deactivated_by": "admin@example.com",
    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    "reason": "Automated governance cleanup",
    "project_id": "proj-456"
}

# Ensure file exists or initialize with empty array
if not os.path.exists(file_path):
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump([], f)

# Load existing events
with open(file_path, 'r+', encoding='utf-8') as f:
    data = json.load(f)
    data.append(mock_event)
    f.seek(0)
    json.dump(data, f, indent=2)
    f.truncate()

print(f"✅ Mock deactivation event written to {file_path}")
