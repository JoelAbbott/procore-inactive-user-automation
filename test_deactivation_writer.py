import json
import os
import datetime

# Set up output path
output_dir = "./data/audit_logs"
os.makedirs(output_dir, exist_ok=True)

date_str = datetime.datetime.utcnow().strftime('%Y%m%d')
filename = f"deactivation_audit_{date_str}.json"
filepath = os.path.join(output_dir, filename)

# Create a mock deactivation audit record
audit_record = {
    "user_id": "mock_user_001",
    "deactivated_by": "admin@example.com",
    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    "reason": "Automated governance cleanup",
    "project_id": "proj-456"
}

# Write audit record to file with proper structure
if os.path.exists(filepath):
    with open(filepath, "r+") as f:
        existing = json.load(f)
        existing.append(audit_record)
        f.seek(0)
        json.dump(existing, f, indent=2)
else:
    with open(filepath, "w") as f:
        json.dump([audit_record], f, indent=2)

print(f"✅ Mock deactivation audit record written to {filepath}")
