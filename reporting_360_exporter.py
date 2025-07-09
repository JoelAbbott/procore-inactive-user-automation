import os
import csv
import logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

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

# Constants
PROCORE_COMPANY_ID = os.getenv('PROCORE_COMPANY_ID')
INPUT_FILE = Path("./data/intermediate/inactive_users_raw.csv") # This aligns with ETL output
OUTPUT_FILE = Path("./data/reports/reporting_360_export.csv")

# Export columns
EXPORT_COLUMNS = ['company_id', 'user_id', 'last_activity_date', 'inactivity_days', 'status']

def validate_record(record):
    """Validate a record has required fields."""
    return (record.get('user_id') and 
            record.get('last_activity_date') and 
            record['user_id'].strip() != '' and 
            record['last_activity_date'].strip() != '')

def enrich_record(record):
    """Enrich a record with additional fields for Reporting 360."""
    return {
        'company_id': PROCORE_COMPANY_ID,
        'user_id': record['user_id'],
        'last_activity_date': record['last_activity_date'],
        'inactivity_days': record.get('inactivity_days', ''),
        'status': record.get('status', '')
    }

def export_to_reporting_360():
    """Main function to export inactive users data for Reporting 360."""
    logger.info("Starting Reporting 360 export process.")
    
    # Validate environment
    if not PROCORE_COMPANY_ID:
        logger.error("PROCORE_COMPANY_ID environment variable is required.")
        return False
    
    # Check if input file exists
    if not INPUT_FILE.exists():
        logger.error(f"Input file {INPUT_FILE} does not exist.")
        return False
    
    # Load and process input data
    total_rows = 0
    skipped_rows = 0
    processed_rows = 0
    enriched_records = []
    
    try:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                total_rows += 1
                
                if not validate_record(row):
                    logger.warning(f"Skipping row {total_rows}: missing required fields (user_id or last_activity_date)")
                    skipped_rows += 1
                    continue
                
                enriched_record = enrich_record(row)
                enriched_records.append(enriched_record)
                processed_rows += 1
        
        logger.info(f"Processed {total_rows} total rows: {processed_rows} valid, {skipped_rows} skipped")
        
        if not enriched_records:
            logger.warning("No valid records to export.")
            return False
        
        # Create output directory if it doesn't exist
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        # Write enriched data to output file
        with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS)
            writer.writeheader()
            for record in enriched_records:
                writer.writerow(record)
        
        logger.info(f"Successfully exported {len(enriched_records)} records to {OUTPUT_FILE}")
        return True
        
    except Exception as e:
        logger.error(f"Error during export process: {e}")
        return False

def get_export_summary():
    """Get a summary of the export file if it exists."""
    if not OUTPUT_FILE.exists():
        return "Export file does not exist."
    
    try:
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            row_count = sum(1 for _ in reader)
        return f"Export file contains {row_count} records."
    except Exception as e:
        return f"Error reading export file: {e}"

if __name__ == "__main__":
    success = export_to_reporting_360()
    if success:
        logger.info("Reporting 360 export completed successfully.")
        print(get_export_summary())
    else:
        logger.error("Reporting 360 export failed.")
        exit(1) 