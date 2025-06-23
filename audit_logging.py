import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union
import traceback

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] %(levelname)s %(name)s: %(message)s')
handler.setFormatter(formatter)
if not logger.hasHandlers():
    logger.addHandler(handler)

# Storage directories
ERROR_LOGS_BASE = Path("./data/error_logs")
AUDIT_LOGS_BASE = Path("./data/audit_logs")

# CSV field definitions
ERROR_FIELDS = ['timestamp', 'module', 'error_type', 'error_message', 'context']
AUDIT_FIELDS = ['timestamp', 'module', 'operation', 'record_count', 'api_calls', 'success_count', 'failure_count', 'notes']

def _get_today_dir(base_path: Path) -> Path:
    """Get today's directory path and create if missing."""
    try:
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        today_dir = base_path / today
        today_dir.mkdir(parents=True, exist_ok=True)
        return today_dir
    except Exception as e:
        logger.error(f"Failed to create directory {base_path}: {e}")
        # Fallback to current directory if storage fails
        return Path(".")

def _safe_write_csv(file_path: Path, fieldnames: list, data: dict) -> bool:
    """Safely write a single row to CSV file with error protection."""
    try:
        # Create directory if it doesn't exist
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Check if file exists to determine if we need to write header
        file_exists = file_path.exists()
        
        with open(file_path, 'a', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            # Write header if file is new
            if not file_exists:
                writer.writeheader()
            
            # Write data row
            writer.writerow(data)
        
        return True
    except Exception as e:
        logger.error(f"Failed to write to CSV file {file_path}: {e}")
        return False

def log_error(module: str, exception: Exception, context: str = "") -> None:
    """
    Log an error to the daily error log CSV file.
    
    Args:
        module: Name of the module where error occurred
        exception: The exception object
        context: Brief context message (file name, API call, record, etc.)
    """
    try:
        today_dir = _get_today_dir(ERROR_LOGS_BASE)
        error_log_file = today_dir / "error_log.csv"
        
        error_data = {
            'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
            'module': str(module),
            'error_type': type(exception).__name__,
            'error_message': str(exception),
            'context': str(context)
        }
        
        success = _safe_write_csv(error_log_file, ERROR_FIELDS, error_data)
        if success:
            logger.info(f"Error logged for module {module}: {type(exception).__name__}")
        else:
            logger.error(f"Failed to log error for module {module}")
            
    except Exception as e:
        # Critical: Never let logging crash the system
        logger.error(f"Critical error in log_error function: {e}")
        # Fallback to console logging
        print(f"[ERROR LOGGING FAILED] Module: {module}, Error: {exception}, Context: {context}")

def log_audit(module: str, operation: str, record_count: Optional[int] = None, 
              api_calls: Optional[int] = None, success_count: Optional[int] = None, 
              failure_count: Optional[int] = None, notes: str = "") -> None:
    """
    Log an audit entry to the daily audit log CSV file.
    
    Args:
        module: Name of the module
        operation: High-level operation (e.g., "Webhook Received", "ETL Processed", "Deactivation Run")
        record_count: Number of records processed (if applicable)
        api_calls: Number of API calls made (if applicable)
        success_count: Number of successes (if applicable)
        failure_count: Number of failures (if applicable)
        notes: Optional free-form governance notes
    """
    try:
        today_dir = _get_today_dir(AUDIT_LOGS_BASE)
        audit_log_file = today_dir / "audit_log.csv"
        
        audit_data = {
            'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),
            'module': str(module),
            'operation': str(operation),
            'record_count': str(record_count) if record_count is not None else '',
            'api_calls': str(api_calls) if api_calls is not None else '',
            'success_count': str(success_count) if success_count is not None else '',
            'failure_count': str(failure_count) if failure_count is not None else '',
            'notes': str(notes)
        }
        
        success = _safe_write_csv(audit_log_file, AUDIT_FIELDS, audit_data)
        if success:
            logger.info(f"Audit logged for module {module}: {operation}")
        else:
            logger.error(f"Failed to log audit for module {module}")
            
    except Exception as e:
        # Critical: Never let logging crash the system
        logger.error(f"Critical error in log_audit function: {e}")
        # Fallback to console logging
        print(f"[AUDIT LOGGING FAILED] Module: {module}, Operation: {operation}, Notes: {notes}")

def get_error_log_path(date_str: Optional[str] = None) -> Path:
    """Get the path to error log file for a specific date or today."""
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    return ERROR_LOGS_BASE / date_str / "error_log.csv"

def get_audit_log_path(date_str: Optional[str] = None) -> Path:
    """Get the path to audit log file for a specific date or today."""
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    return AUDIT_LOGS_BASE / date_str / "audit_log.csv"

def log_system_startup(module: str) -> None:
    """Log system startup event."""
    log_audit(module, "System Startup", notes="Module initialized and ready")

def log_system_shutdown(module: str) -> None:
    """Log system shutdown event."""
    log_audit(module, "System Shutdown", notes="Module completed execution")

def log_operation_summary(module: str, operation: str, total_processed: int, 
                         successful: int, failed: int, api_calls_made: int = 0, 
                         notes: str = "") -> None:
    """Log a summary of an operation with counts."""
    log_audit(
        module=module,
        operation=operation,
        record_count=total_processed,
        api_calls=api_calls_made,
        success_count=successful,
        failure_count=failed,
        notes=notes
    )

# Example usage functions for testing
def test_logging_functions():
    """Test function to verify logging works correctly."""
    try:
        # Test error logging
        test_exception = ValueError("Test error message")
        log_error("test_module", test_exception, "Testing error logging")
        
        # Test audit logging
        log_audit("test_module", "Test Operation", 100, 5, 95, 5, "Testing audit logging")
        
        # Test operation summary
        log_operation_summary("test_module", "Test Summary", 100, 95, 5, 10, "Testing summary logging")
        
        print("Logging test completed successfully")
        
    except Exception as e:
        print(f"Logging test failed: {e}")

if __name__ == "__main__":
    test_logging_functions() 