import os
import time
import random
import logging
from typing import Optional
import requests
from requests.exceptions import RequestException
from dotenv import load_dotenv
from pathlib import Path
from oauth_manager import OAuthManager, OAuthTokenError

# Load environment variables from .env file
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] %(levelname)s %(name)s: %(message)s')
handler.setFormatter(formatter)
if not logger.hasHandlers():
    logger.addHandler(handler)

# Read environment variables
PROCORE_CLIENT_ID = os.getenv('PROCORE_CLIENT_ID')
PROCORE_CLIENT_SECRET = os.getenv('PROCORE_CLIENT_SECRET')
PROCORE_COMPANY_ID = os.getenv('PROCORE_COMPANY_ID')
PROCORE_BASE_URL = os.getenv('PROCORE_BASE_URL', 'https://sandbox.procore.com')
PROCORE_TOKEN_URL = os.getenv('PROCORE_TOKEN_URL', 'https://login-sandbox.procore.com/oauth/token')
WEBHOOK_DELIVERY_URL = os.getenv('WEBHOOK_DELIVERY_URL')

if not all([PROCORE_CLIENT_ID, PROCORE_CLIENT_SECRET, PROCORE_COMPANY_ID, PROCORE_BASE_URL, PROCORE_TOKEN_URL, WEBHOOK_DELIVERY_URL]):
    logger.error('Missing one or more required environment variables for webhook subscription management.')
    raise RuntimeError('Missing required environment variables for webhook subscription management.')

# Constants
HOOKS_ENDPOINT = f"{PROCORE_BASE_URL}/rest/v1.0/webhooks/hooks"
TRIGGERS_ENDPOINT = f"{PROCORE_BASE_URL}/rest/v1.0/webhooks/triggers"
USER_RESOURCE = "Company Users"
USER_EVENTS = ["create", "update", "delete"]
API_VERSION = "v2"

MAX_RETRIES = 5
BACKOFF_BASE = 1  # seconds

class WebhookSubscriptionManager:
    def __init__(self):
        self.oauth_manager = OAuthManager()
        self.company_id = PROCORE_COMPANY_ID
        self.delivery_url = WEBHOOK_DELIVERY_URL

    def _request_with_retry(self, method, url, headers=None, json_body=None, expected_status=201, log_context="API call"):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    timeout=10
                )
                if response.status_code == expected_status:
                    logger.info(f"{log_context} succeeded: {response.status_code}")
                    return response.json()
                elif response.status_code in (429, 500, 502, 503, 504):
                    logger.warning(f"{log_context} failed with status {response.status_code}. Retrying (attempt {attempt}/{MAX_RETRIES})...")
                elif response.status_code in (400, 401, 403, 404, 409):
                    logger.error(f"Permanent error during {log_context}: {response.status_code} {response.text}")
                    raise RuntimeError(f"Permanent error during {log_context}: {response.status_code} {response.text}")
                else:
                    logger.error(f"Unexpected error during {log_context}: {response.status_code} {response.text}")
                    raise RuntimeError(f"Unexpected error during {log_context}: {response.status_code} {response.text}")
            except RequestException as e:
                logger.warning(f"RequestException during {log_context}: {e}. Retrying (attempt {attempt}/{MAX_RETRIES})...")
            sleep_time = BACKOFF_BASE * (2 ** (attempt - 1))
            sleep_time = sleep_time + random.uniform(0, 0.5 * sleep_time)
            time.sleep(sleep_time)
        logger.error(f"Failed {log_context} after maximum retries.")
        raise RuntimeError(f"Failed {log_context} after maximum retries.")

    def create_hook(self) -> str:
        access_token = self.oauth_manager.get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Procore-Company-Id": str(self.company_id)
        }
        body = {
            "company_id": int(self.company_id),
            "destination_url": self.delivery_url,
            "status": "active",
            "api_version": "v1.0"
        }
        logger.info(f"Creating webhook Hook for company_id={self.company_id} at url={self.delivery_url}")
        response = self._request_with_retry(
            method="POST",
            url=HOOKS_ENDPOINT,
            headers=headers,
            json_body=body,
            expected_status=201,
            log_context="Create Hook"
        )
        hook_id = response.get("id")
        if not hook_id:
            logger.error(f"No hook_id returned in response: {response}")
            raise RuntimeError(f"No hook_id returned in response: {response}")
        logger.info(f"Successfully created Hook with id={hook_id}")
        return hook_id

    def create_trigger(self, hook_id: str, event_type: str) -> str:
        access_token = self.oauth_manager.get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Procore-Company-Id": str(self.company_id)
        }
        body = {
                "company_id": int(self.company_id),
                "api_version": API_VERSION,
                "trigger": {
                "resource_name": USER_RESOURCE,
                "event_type": event_type
            }
        }
        logger.info(f"Creating Trigger for hook_id={hook_id}, event={event_type}")
        response = self._request_with_retry(
            method="POST",
            url=f"{PROCORE_BASE_URL}/rest/v1.0/webhooks/hooks/{hook_id}/triggers",
            headers=headers,
            json_body=body,
            expected_status=201,
            log_context=f"Create Trigger ({event_type})"
        )
        trigger_id = response.get("id")
        if not trigger_id:
            logger.error(f"No trigger_id returned in response: {response}")
            raise RuntimeError(f"No trigger_id returned in response: {response}")
        logger.info(f"Successfully created Trigger with id={trigger_id} for event={event_type}")
        return trigger_id

    def setup_user_webhook_subscriptions(self):
        logger.info("Starting webhook subscription setup for user-related events.")
        hook_id = self.create_hook()
        for event in USER_EVENTS:
            self.create_trigger(hook_id, event)
        logger.info("Webhook subscription setup complete.")

if __name__ == "__main__":
    manager = WebhookSubscriptionManager()
    try:
        manager.setup_user_webhook_subscriptions()
    except (OAuthTokenError, RuntimeError) as e:
        logger.error(f"Webhook subscription setup failed: {e}")
        exit(1) 