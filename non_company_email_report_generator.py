import os
import json
import csv
import logging
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load environment variables
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

# Configuration
COMPANY_EMAIL_DOMAIN = os.getenv('COMPANY_EMAIL_DOMAIN', 'compassdatacenters.com').lower()
# NEW: Define common public email domains to flag as 'non-company'
PUBLIC_EMAIL_DOMAINS = [d.strip().lower() for d in os.getenv('PUBLIC_EMAIL_DOMAINS', '').split(',') if d.strip()]

USERS_CACHE_PATH = Path('./data/intermediate/users_cache.json')
ACTIVITY_LOGS_DIR = Path('./data/logs')
OUTPUT_PATH = Path('./data/reports/non_company_email_users_report.csv')
MODULE = "non_company_email_report_generator"

def load_users_cache():
    """Load the users cache file."""
    try:
        if not USERS_CACHE_PATH.exists():
            logger.error(f"Users cache not found at {USERS_CACHE_PATH}")
            return {}
        
        with open(USERS_CACHE_PATH, 'r', encoding='utf-8') as f:
            cache = json.load(f)
            logger.info(f"Loaded {len(cache)} users from cache")
            return cache
    except Exception as e:
        logger.error(f"Failed to load users cache: {e}")
        return {}


def load_user_activity():
    """Load all user activity from activity logs to find last activity dates."""
    user_activity = {}
    
    if not ACTIVITY_LOGS_DIR.exists():
        logger.warning(f"Activity logs directory {ACTIVITY_LOGS_DIR} does not exist")
        return user_activity
    
    # Look for activity log CSV files
    for csv_file in ACTIVITY_LOGS_DIR.glob('activity_log_*.csv'):
        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    user_id = row.get('user_id', '').strip()
                    timestamp = row.get('timestamp', '').strip()
                    
                    if user_id and timestamp:
                        # Keep the most recent activity for each user
                        if user_id not in user_activity or timestamp > user_activity[user_id]:
                            user_activity[user_id] = timestamp
        except Exception as e:
            logger.warning(f"Failed to process activity log {csv_file}: {e}")
    
    logger.info(f"Found activity for {len(user_activity)} users")
    return user_activity


def identify_non_company_email_users(users_cache, user_activity):
    """
    Identify users with non-company email addresses based on new definition:
    Email is NOT COMPANY_EMAIL_DOMAIN AND its domain IS in PUBLIC_EMAIL_DOMAINS.
    """
    non_company_users = []
    
    for user_id, user_data in users_cache.items():
        email = user_data.get('email_address', '').lower()
        
        # Skip if no email or if email is internal company domain
        if not email or email.endswith(f'@{COMPANY_EMAIL_DOMAIN}'):
            continue
        
        # Extract email domain
        email_domain = email.split('@')[-1]
        
        # ONLY flag if the email domain is in our list of known public domains
        if email_domain in PUBLIC_EMAIL_DOMAINS:
            last_active = user_activity.get(user_id, '')
            
            non_company_users.append({
                'user_id': user_id,
                'first_name': user_data.get('first_name', ''),
                'last_name': user_data.get('last_name', ''),
                'email_address': user_data.get('email_address', ''),
                'vendor_name': user_data.get('vendor_name', ''),
                'created_at': user_data.get('created_at', ''),
                'last_active': last_active
            })
    
    logger.info(f"Identified {len(non_company_users)} non-company email users")
    return non_company_users


def write_report(non_company_users):
    """Write the non-company email users report to CSV."""
    if not non_company_users:
        logger.warning("No non-company email users found")
        return
    
    # Ensure output directory exists
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    # Define CSV columns
    fieldnames = [
        'user_id', 'first_name', 'last_name', 'email_address', 
        'vendor_name', 'created_at', 'last_active'
    ]
    
    try:
        with open(OUTPUT_PATH, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(non_company_users)
        
        logger.info(f"Successfully wrote report to {OUTPUT_PATH}")
        
    except Exception as e:
        logger.error(f"Failed to write report: {e}")
        raise


def main():
    """Main function to generate non-company email users report."""
    logger.info("Starting non-company email users report generation")
    
    try:
        # Load users cache
        users_cache = load_users_cache()
        if not users_cache:
            logger.error("No users data available")
            return
        
        # Load user activity
        user_activity = load_user_activity()
        
        # Identify non-company email users
        non_company_users = identify_non_company_email_users(users_cache, user_activity)
        
        # Write report
        write_report(non_company_users)
        
        # Log summary
        logger.info(f"Report generation completed successfully")
        logger.info(f"Total users: {len(users_cache)}")
        logger.info(f"Non-company email users: {len(non_company_users)}")
        
    except Exception as e:
        logger.error(f"Report generation failed: {e}")
        raise


if __name__ == "__main__":
    main()