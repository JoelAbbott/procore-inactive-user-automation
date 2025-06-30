import csv
import datetime
import os

date_str = datetime.datetime.utcnow().strftime('%Y%m%d')
output_path = "./data/reports/inactive_users_report.csv"

# Ensure directory exists
os.makedirs(os.path.dirname(output_path), exist_ok=True)

# Required headers for pipeline
headers = ["user_id", "last_activity_date", "status"]

# Mock data aligned with what the pipeline expects
data = [
    {
        "user_id": "45698",
        "last_activity_date": (datetime.datetime.utcnow() - datetime.timedelta(days=395)).strftime('%Y-%m-%d'),
        "status": "Inactive > 12 months"
    },
    {
        "user_id": "mock_user_002",
        "last_activity_date": (datetime.datetime.utcnow() - datetime.timedelta(days=400)).strftime('%Y-%m-%d'),
        "status": "Inactive > 12 months"
    },
    {
        "user_id": "mock_user_003",
        "last_activity_date": (datetime.datetime.utcnow() - datetime.timedelta(days=405)).strftime('%Y-%m-%d'),
        "status": "Inactive > 12 months"
    }
]

with open(output_path, "w", newline='') as f:
    writer = csv.DictWriter(f, fieldnames=headers)
    writer.writeheader()
    writer.writerows(data)

print(f"✅ Mock inactive user report written to {output_path}")