import os
import sys
import logging
import requests
from requests.exceptions import RequestException
from dotenv import load_dotenv
from pathlib import Path
import json

# Configure logging
logger = logging.getLogger("procore_oauth_sanity_check")
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

def main():
    # Load environment variables
    env_path = Path(__file__).parent / '.env'
    if not env_path.exists():
        logger.error(f".env file not found at {env_path}")
        sys.exit(1)
    load_dotenv(dotenv_path=env_path)

    # Read credentials
    client_id = os.getenv('PROCORE_CLIENT_ID')
    client_secret = os.getenv('PROCORE_CLIENT_SECRET')
    base_url = os.getenv('PROCORE_BASE_URL', 'https://sandbox.procore.com')
    token_url = os.getenv('PROCORE_TOKEN_URL', 'https://sandbox.procore.com/oauth/token')
    company_id = os.getenv('PROCORE_COMPANY_ID')

    # Log loaded credentials (mask secret)
    logger.info(f"Loaded credentials:")
    logger.info(f"  PROCORE_CLIENT_ID: {client_id}")
    logger.info(f"  PROCORE_CLIENT_SECRET: {mask_secret(client_secret)}")
    logger.info(f"  PROCORE_BASE_URL: {base_url}")
    logger.info(f"  PROCORE_TOKEN_URL: {token_url}")
    logger.info(f"  PROCORE_COMPANY_ID: {company_id}")

    # Check for missing env vars
    if not all([client_id, client_secret, base_url, token_url, company_id]):
        logger.error("Missing one or more required environment variables. Aborting.")
        sys.exit(2)

    # Step 1: Request OAuth token
    token_payload = {
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret
    }
    logger.info(f"Requesting OAuth token: POST {token_url}")
    logger.info(f"Token request payload: {{'grant_type': 'client_credentials', 'client_id': '{client_id}', 'client_secret': '{mask_secret(client_secret)}'}}")
    try:
        token_resp = requests.post(
            token_url,
            data=token_payload,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=10
        )
    except RequestException as e:
        logger.error(f"Token request failed: {e}")
        sys.exit(3)
    logger.info(f"Token response status: {token_resp.status_code}")
    try:
        token_resp_json = token_resp.json()
    except Exception as e:
        logger.error(f"Failed to parse token response as JSON: {e}\nRaw response: {token_resp.text}")
        sys.exit(4)
    # Mask sensitive fields in log
    token_log = token_resp_json.copy()
    if 'access_token' in token_log:
        token_log['access_token'] = mask_secret(token_log['access_token'])
    if 'refresh_token' in token_log:
        token_log['refresh_token'] = mask_secret(token_log['refresh_token'])
    logger.info(f"Token response JSON: {json.dumps(token_log, indent=2)}")
    if token_resp.status_code != 200:
        logger.error(f"Token request failed with status {token_resp.status_code}: {token_resp.text}")
        sys.exit(5)
    access_token = token_resp_json.get('access_token')
    if not access_token:
        logger.error(f"No access_token found in token response: {token_resp_json}")
        sys.exit(6)

    # Step 2: Call /users endpoint for company validation
    users_url = f"{base_url}/rest/v1.1/users?company_id={company_id}"
    logger.info(f"Calling /users endpoint: GET {users_url}")
    try:
        users_resp = requests.get(
            users_url,
            headers={
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json'
            },
            timeout=10
        )
    except RequestException as e:
        logger.error(f"/users request failed: {e}")
        sys.exit(10)
    logger.info(f"/users response status: {users_resp.status_code}")
    try:
        users_json = users_resp.json()
    except Exception as e:
        logger.error(f"Failed to parse /users response as JSON: {e}\nRaw response: {users_resp.text}")
        sys.exit(11)
    logger.info(f"/users response JSON: {json.dumps(users_json, indent=2)}")
    if users_resp.status_code != 200:
        logger.error(f"/users request failed with status {users_resp.status_code}: {users_resp.text}")
        sys.exit(12)
    logger.info("OAuth sanity check PASSED.")

if __name__ == "__main__":
    main()