import os
import json
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify
import logging
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

# Get storage path from environment or use default
WEBHOOK_STORAGE_PATH = os.getenv('WEBHOOK_STORAGE_PATH', './data/webhooks/')

app = Flask(__name__)

@app.route('/procore/webhook', methods=['POST'])
def procore_webhook():
    if request.method != 'POST':
        logger.warning('Received non-POST request at /procore/webhook')
        return jsonify({'error': 'Invalid request method'}), 400

    try:
        payload = request.get_json(force=True)
    except Exception as e:
        logger.error(f'Failed to parse JSON payload: {e}')
        return jsonify({'error': 'Malformed JSON payload'}), 400

    if not payload:
        logger.error('Empty or invalid JSON payload received')
        return jsonify({'error': 'Empty or invalid JSON payload'}), 400

    # Log the full payload for audit
    logger.info(f'Received webhook event: {json.dumps(payload)}')

    # Determine event_id for filename
    event_id = payload.get('id') or payload.get('event_id') or None
    if not event_id:
        # Fallback: use timestamp and random suffix
        event_id = f"noid_{datetime.utcnow().strftime('%H%M%S%f')}"

    # Create daily subfolder
    date_str = datetime.utcnow().strftime('%Y-%m-%d')
    daily_folder = Path(WEBHOOK_STORAGE_PATH) / date_str
    try:
        daily_folder.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f'Failed to create storage directory: {e}')
        return jsonify({'error': 'Internal server error'}), 500

    # Write payload to file
    file_path = daily_folder / f"{event_id}.json"
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.info(f'Webhook payload stored at {file_path}')
    except Exception as e:
        logger.error(f'Failed to write webhook payload to file: {e}')
        return jsonify({'error': 'Internal server error'}), 500

    return jsonify({'status': 'received'}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000) 