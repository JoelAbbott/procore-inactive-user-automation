import os
import time
import random
import logging
from typing import Optional
import requests
from requests.exceptions import RequestException
from dotenv import load_dotenv
from pathlib import Path

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

class OAuthTokenError(Exception):
    """Custom exception for OAuth token errors."""
    pass

class OAuthManager:
    """
    Handles Procore Sandbox API authentication using OAuth 2.0 Client Credentials Grant.
    Obtains and refreshes OAuth tokens securely, stores tokens in-memory during runtime,
    and provides a method to retrieve valid access tokens for authenticated API calls.
    """
    def __init__(self):
        self.client_id = os.getenv('PROCORE_CLIENT_ID')
        self.client_secret = os.getenv('PROCORE_CLIENT_SECRET')
        self.company_id = os.getenv('PROCORE_COMPANY_ID')
        self.base_url = os.getenv('PROCORE_BASE_URL', 'https://sandbox.procore.com')
        self.token_url = os.getenv('PROCORE_TOKEN_URL', 'https://login-sandbox.procore.com/oauth/token')

        if not all([self.client_id, self.client_secret, self.company_id, self.base_url, self.token_url]):
            logger.error('Missing one or more required environment variables for OAuth.')
            raise OAuthTokenError('Missing required environment variables for OAuth.')

        self._access_token: Optional[str] = None
        self._token_expiry: Optional[float] = None

    def _is_token_valid(self) -> bool:
        return self._access_token is not None and self._token_expiry is not None and time.time() < self._token_expiry

    def _request_token(self) -> None:
        max_retries = 5
        backoff_base = 1  # seconds
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(
                    self.token_url,
                    data={
                        'grant_type': 'client_credentials',
                        'client_id': self.client_id,
                        'client_secret': self.client_secret
                    },
                    headers={
                        'Content-Type': 'application/x-www-form-urlencoded'
                    },
                    timeout=10
                )
                if response.status_code == 200:
                    token_data = response.json()
                    self._access_token = token_data['access_token']
                    # Subtract 30 seconds to proactively refresh before expiry
                    self._token_expiry = time.time() + int(token_data['expires_in']) - 30
                    logger.info('Successfully obtained new OAuth access token.')
                    return
                elif response.status_code in (429, 500, 502, 503, 504):
                    # Retry on rate limit or server errors
                    logger.warning(f'Token request failed with status {response.status_code}. Retrying (attempt {attempt}/{max_retries})...')
                elif response.status_code in (400, 401):
                    logger.error(f'Permanent error during token request: {response.status_code} {response.text}')
                    raise OAuthTokenError(f'Permanent error during token request: {response.status_code} {response.text}')
                else:
                    logger.error(f'Unexpected error during token request: {response.status_code} {response.text}')
                    raise OAuthTokenError(f'Unexpected error during token request: {response.status_code} {response.text}')
            except RequestException as e:
                logger.warning(f'RequestException during token request: {e}. Retrying (attempt {attempt}/{max_retries})...')
            # Exponential backoff with jitter
            sleep_time = backoff_base * (2 ** (attempt - 1))
            sleep_time = sleep_time + random.uniform(0, 0.5 * sleep_time)
            time.sleep(sleep_time)
        logger.error('Failed to obtain OAuth token after maximum retries.')
        raise OAuthTokenError('Failed to obtain OAuth token after maximum retries.')

    def get_access_token(self) -> str:
        """
        Returns a valid OAuth access token for authenticated API calls.
        Automatically refreshes the token if expired or not present.
        """
        if not self._is_token_valid():
            logger.info('OAuth token missing or expired. Requesting new token...')
            self._request_token()
        return self._access_token 