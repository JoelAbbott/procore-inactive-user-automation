import os
import sys
import json
import time
import logging
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from requests.exceptions import RequestException

# --- Logging Setup ---
logger = logging.getLogger("phase10_system_test")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] %(levelname)s %(name)s: %(message)s')
handler.setFormatter(formatter)
if not logger.hasHandlers():
    logger.addHandler(handler)

def mask_secret(secret: str) -> str:
    if not secret or len(secret) < 8:
        return "***MASKED***"
    return secret[:2] + "***MASKED***" + secret[-2:]

def ensure_dir(path: str):
    Path(path).mkdir(parents=True, exist_ok=True)

def timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def load_env():
    env_path = Path(__file__).parent / '.env'
    if not env_path.exists():
        logger.error(f".env file not found at {env_path}")
        sys.exit(1)
    load_dotenv(dotenv_path=env_path)
    client_id = os.getenv('PROCORE_CLIENT_ID')
    client_secret = os.getenv('PROCORE_CLIENT_SECRET')
    base_url = os.getenv('PROCORE_BASE_URL', 'https://sandbox.procore.com')
    token_url = os.getenv('PROCORE_TOKEN_URL', 'https://sandbox.procore.com/oauth/token')
    company_id = os.getenv('PROCORE_COMPANY_ID')
    logger.info(f"Loaded credentials:")
    logger.info(f"  PROCORE_CLIENT_ID: {client_id}")
    logger.info(f"  PROCORE_CLIENT_SECRET: {mask_secret(client_secret)}")
    logger.info(f"  PROCORE_BASE_URL: {base_url}")
    logger.info(f"  PROCORE_TOKEN_URL: {token_url}")
    logger.info(f"  PROCORE_COMPANY_ID: {company_id}")
    if not all([client_id, client_secret, base_url, token_url, company_id]):
        logger.error("Missing one or more required environment variables. Aborting.")
        sys.exit(2)
    return client_id, client_secret, base_url, token_url, company_id

def get_access_token(client_id, client_secret, token_url):
    payload = {
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret
    }
    logger.info(f"Requesting OAuth token: POST {token_url}")
    logger.info(f"Token request payload: {{'grant_type': 'client_credentials', 'client_id': '{client_id}', 'client_secret': '{mask_secret(client_secret)}'}}")
    try:
        resp = requests.post(
            token_url,
            data=payload,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=10
        )
    except RequestException as e:
        logger.error(f"Token request failed: {e}")
        sys.exit(3)
    logger.info(f"Token response status: {resp.status_code}")
    try:
        resp_json = resp.json()
    except Exception as e:
        logger.error(f"Failed to parse token response as JSON: {e}\nRaw response: {resp.text}")
        sys.exit(4)
    token_log = resp_json.copy()
    if 'access_token' in token_log:
        token_log['access_token'] = mask_secret(token_log['access_token'])
    if 'refresh_token' in token_log:
        token_log['refresh_token'] = mask_secret(token_log['refresh_token'])
    logger.info(f"Token response JSON: {json.dumps(token_log, indent=2)}")
    if resp.status_code != 200:
        logger.error(f"Token request failed with status {resp.status_code}: {resp.text}")
        sys.exit(5)
    access_token = resp_json.get('access_token')
    if not access_token:
        logger.error(f"No access_token found in token response: {resp_json}")
        sys.exit(6)
    return access_token

def validate_token(base_url, company_id, access_token):
    users_url = f"{base_url}/rest/v1.1/users?company_id={company_id}"
    logger.info(f"Validating token with /users endpoint: GET {users_url}")
    try:
        resp = requests.get(
            users_url,
            headers={
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json'
            },
            timeout=10
        )
    except RequestException as e:
        logger.error(f"/users request failed: {e}")
        sys.exit(7)
    logger.info(f"/users response status: {resp.status_code}")
    try:
        users_json = resp.json()
    except Exception as e:
        logger.error(f"Failed to parse /users response as JSON: {e}\nRaw response: {resp.text}")
        sys.exit(8)
    logger.info(f"/users response JSON: {json.dumps(users_json, indent=2)}")
    if resp.status_code != 200:
        logger.error(f"/users request failed with status {resp.status_code}: {resp.text}")
        sys.exit(9)
    return users_json

def simulate_webhook_events(user_ids, object_types, n_events=20):
    events = []
    now = datetime.now()
    for i in range(n_events):
        user_id = user_ids[i % len(user_ids)]
        object_type = object_types[i % len(object_types)]
        event_type = ["create", "update", "delete"][i % 3]
        # Simulate some events as old (inactive)
        if i < n_events // 2:
            occurred_at = (now - timedelta(days=400 + i)).isoformat()
        else:
            occurred_at = (now - timedelta(days=i)).isoformat()
        event = {
            "user_id": user_id,
            "project_id": None,
            "object_type": object_type,
            "event_type": event_type,
            "occurred_at": occurred_at
        }
        events.append(event)
    return events

def write_json(data, path):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Wrote JSON file: {path}")
    except Exception as e:
        logger.error(f"Failed to write JSON file {path}: {e}")
        sys.exit(10)

def write_csv(data, path):
    try:
        df = pd.DataFrame(data)
        df.to_csv(path, index=False)
        logger.info(f"Wrote CSV file: {path}")
    except Exception as e:
        logger.error(f"Failed to write CSV file {path}: {e}")
        sys.exit(11)

def aggregate_activity(events):
    activity = {}
    for event in events:
        user_id = event["user_id"]
        occurred_at = event["occurred_at"]
        if user_id not in activity:
            activity[user_id] = []
        activity[user_id].append(occurred_at)
    return activity

def detect_inactive_users(activity, months=12):
    cutoff = datetime.now() - timedelta(days=months*30)
    inactive_users = []
    for user_id, timestamps in activity.items():
        last_active = max([datetime.fromisoformat(ts) for ts in timestamps])
        if last_active < cutoff:
            inactive_users.append({"user_id": user_id, "last_active": last_active.isoformat()})
    return inactive_users

def mock_deactivate_users(base_url, company_id, inactive_users, access_token, audit_log_path):
    audit_log = []
    for user in inactive_users:
        user_id = user["user_id"]
        patch_url = f"{base_url}/rest/v1.1/users/{user_id}?company_id={company_id}"
        logger.info(f"[MOCK] Would PATCH (deactivate) user: {user_id} at {patch_url}")
        audit_event = {
            "user_id": user_id,
            "action": "deactivation_mock",
            "timestamp": datetime.now().isoformat(),
            "patch_url": patch_url
        }
        audit_log.append(audit_event)
    write_json(audit_log, audit_log_path)
    logger.info(f"Logged {len(audit_log)} mock deactivation events.")

def main():
    logger.info("--- Phase10 System Integration Test Start ---")
    # 1️⃣ Credential Validation
    client_id, client_secret, base_url, token_url, company_id = load_env()
    access_token = get_access_token(client_id, client_secret, token_url)
    users_json = validate_token(base_url, company_id, access_token)
    user_ids = [u["id"] for u in users_json if "id" in u]
    if not user_ids:
        logger.error("No user IDs found in /users response. Aborting.")
        sys.exit(12)
    logger.info(f"Found {len(user_ids)} users for simulation.")

    # 2️⃣ Simulated Webhook Ingestion
    object_types = ["submittals", "rfis", "meetings", "drawings", "specifications"]
    events = simulate_webhook_events(user_ids, object_types, n_events=30)
    ensure_dir("./data/activity_logs/")
    activity_log_path = f"./data/activity_logs/activity_log_{timestamp()}.json"
    write_json(events, activity_log_path)

    # 3️⃣ Activity Log Generation (aggregate per-user)
    activity = aggregate_activity(events)

    # 4️⃣ Inactivity Analysis
    inactive_users = detect_inactive_users(activity, months=12)
    logger.info(f"Identified {len(inactive_users)} inactive users (>12 months inactivity).")
    ensure_dir("./data/deactivation_candidates/")
    inactive_users_path = f"./data/deactivation_candidates/inactive_users_{timestamp()}.csv"
    write_csv(inactive_users, inactive_users_path)

    # 5️⃣ Deactivation Simulation (mock)
    ensure_dir("./data/audit_logs/")
    audit_log_path = f"./data/audit_logs/deactivation_audit_{timestamp()}.json"
    mock_deactivate_users(base_url, company_id, inactive_users, access_token, audit_log_path)

    logger.info("--- Phase10 System Integration Test Complete ---")

if __name__ == "__main__":
    main() 