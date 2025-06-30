"""
Configuration management for Procore Governance System.
Centralizes all configuration values and provides validation.
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional
from dotenv import load_dotenv
import logging

# Configure logging
logger = logging.getLogger(__name__)

class Config:
    """Configuration management class with validation and defaults."""
    
    def __init__(self, env_file: Optional[str] = None):
        """
        Initialize configuration with environment variables.
        
        Args:
            env_file: Optional path to .env file. Defaults to .env in project root.
        """
        if env_file:
            env_path = Path(env_file)
        else:
            env_path = Path(__file__).parent / '.env'
        
        if env_path.exists():
            load_dotenv(dotenv_path=env_path)
        else:
            logger.warning(f"Environment file not found: {env_path}")
    
    # --- API Configuration ---
    @property
    def procore_client_id(self) -> str:
        """Procore OAuth client ID."""
        value = os.getenv('PROCORE_CLIENT_ID', '')
        if not value:
            raise ValueError("PROCORE_CLIENT_ID environment variable is required")
        return value
    
    @property
    def procore_client_secret(self) -> str:
        """Procore OAuth client secret."""
        value = os.getenv('PROCORE_CLIENT_SECRET', '')
        if not value:
            raise ValueError("PROCORE_CLIENT_SECRET environment variable is required")
        return value
    
    @property
    def procore_company_id(self) -> str:
        """Procore company ID."""
        value = os.getenv('PROCORE_COMPANY_ID', '')
        if not value:
            raise ValueError("PROCORE_COMPANY_ID environment variable is required")
        return value
    
    @property
    def procore_base_url(self) -> str:
        """Procore API base URL."""
        return os.getenv('PROCORE_BASE_URL', 'https://sandbox.procore.com')
    
    @property
    def procore_token_url(self) -> str:
        """Procore OAuth token URL."""
        return os.getenv('PROCORE_TOKEN_URL', 'https://login-sandbox.procore.com/oauth/token')
    
    @property
    def webhook_delivery_url(self) -> Optional[str]:
        """Webhook delivery URL."""
        return os.getenv('WEBHOOK_DELIVERY_URL')
    
    # --- API Limits and Timeouts ---
    @property
    def api_max_page_size(self) -> int:
        """Maximum page size for API requests."""
        return int(os.getenv('API_MAX_PAGE_SIZE', '300'))
    
    @property
    def api_max_retries(self) -> int:
        """Maximum number of API retry attempts."""
        return int(os.getenv('API_MAX_RETRIES', '5'))
    
    @property
    def api_timeout_seconds(self) -> int:
        """API request timeout in seconds."""
        return int(os.getenv('API_TIMEOUT_SECONDS', '30'))
    
    @property
    def api_backoff_base_seconds(self) -> float:
        """Base backoff time for API retries in seconds."""
        return float(os.getenv('API_BACKOFF_BASE_SECONDS', '1.0'))
    
    # --- Business Logic Configuration ---
    @property
    def inactive_threshold_days(self) -> int:
        """Number of days after which a user is considered inactive."""
        return int(os.getenv('INACTIVE_THRESHOLD_DAYS', '365'))
    
    @property
    def never_logged_in_threshold_days(self) -> int:
        """Number of days after account creation before flagging never-logged-in users."""
        return int(os.getenv('NEVER_LOGGED_IN_THRESHOLD_DAYS', '180'))
    
    @property
    def company_email_domain(self) -> str:
        """Company email domain for filtering external users."""
        return os.getenv('COMPANY_EMAIL_DOMAIN', 'compassdatacenters.com').lower()
    
    @property
    def dmsa_user_ids(self) -> set:
        """Set of DMSA user IDs to exclude from deactivation."""
        ids_str = os.getenv('DMSA_USER_IDS', '')
        return set(u.strip() for u in ids_str.split(',') if u.strip())
    
    @property
    def admin_user_ids(self) -> set:
        """Set of admin user IDs to exclude from processing."""
        ids_str = os.getenv('ADMIN_USER_IDS', '')
        return set(u.strip() for u in ids_str.split(',') if u.strip())
    
    # --- Storage Configuration ---
    @property
    def webhook_storage_path(self) -> str:
        """Path for storing webhook data."""
        return os.getenv('WEBHOOK_STORAGE_PATH', './data/webhooks/')
    
    @property
    def logs_base_path(self) -> str:
        """Base path for log storage."""
        return os.getenv('LOGS_BASE_PATH', './data/logs')
    
    @property
    def output_dir(self) -> str:
        """Directory for intermediate output files."""
        return os.getenv('OUTPUT_DIR', './data/intermediate')
    
    @property
    def reports_dir(self) -> str:
        """Directory for reports."""
        return os.getenv('REPORTS_DIR', './data/reports')
    
    @property
    def audit_logs_base(self) -> str:
        """Base directory for audit logs."""
        return os.getenv('AUDIT_LOGS_BASE', './data/audit_logs')
    
    @property
    def error_logs_base(self) -> str:
        """Base directory for error logs."""
        return os.getenv('ERROR_LOGS_BASE', './data/error_logs')
    
    @property
    def deactivation_logs_base(self) -> str:
        """Base directory for deactivation logs."""
        return os.getenv('DEACTIVATION_LOGS_BASE', './data/deactivation_logs')
    
    # --- Cache Configuration ---
    @property
    def users_cache_path(self) -> str:
        """Path to users cache file."""
        return os.path.join(self.output_dir, 'users_cache.json')
    
    @property
    def projects_cache_path(self) -> str:
        """Path to projects cache file."""
        return os.path.join(self.output_dir, 'projects_cache.json')
    
    @property
    def cache_ttl_hours(self) -> int:
        """Cache time-to-live in hours."""
        return int(os.getenv('CACHE_TTL_HOURS', '24'))
    
    # --- Feature Flags ---
    @property
    def enable_webhook_listener(self) -> bool:
        """Enable webhook listener functionality."""
        return os.getenv('ENABLE_WEBHOOK_LISTENER', 'true').lower() == 'true'
    
    @property
    def enable_auto_deactivation(self) -> bool:
        """Enable automatic user deactivation."""
        return os.getenv('ENABLE_AUTO_DEACTIVATION', 'false').lower() == 'true'
    
    @property
    def dry_run_mode(self) -> bool:
        """Run in dry-run mode (no actual deactivations)."""
        return os.getenv('DRY_RUN_MODE', 'true').lower() == 'true'
    
    # --- Logging Configuration ---
    @property
    def log_level(self) -> str:
        """Logging level."""
        return os.getenv('LOG_LEVEL', 'INFO').upper()
    
    @property
    def enable_console_logging(self) -> bool:
        """Enable console logging."""
        return os.getenv('ENABLE_CONSOLE_LOGGING', 'true').lower() == 'true'
    
    @property
    def enable_file_logging(self) -> bool:
        """Enable file logging."""
        return os.getenv('ENABLE_FILE_LOGGING', 'true').lower() == 'true'
    
    # --- Validation Methods ---
    def validate(self) -> Dict[str, Any]:
        """
        Validate all configuration values.
        
        Returns:
            Dict with validation results and any errors.
        """
        errors = []
        warnings = []
        
        # Required environment variables
        try:
            _ = self.procore_client_id
        except ValueError as e:
            errors.append(str(e))
        
        try:
            _ = self.procore_client_secret
        except ValueError as e:
            errors.append(str(e))
        
        try:
            _ = self.procore_company_id
        except ValueError as e:
            errors.append(str(e))
        
        # Validate numeric values
        if self.inactive_threshold_days <= 0:
            errors.append("INACTIVE_THRESHOLD_DAYS must be positive")
        
        if self.never_logged_in_threshold_days <= 0:
            errors.append("NEVER_LOGGED_IN_THRESHOLD_DAYS must be positive")
        
        if self.api_max_page_size <= 0 or self.api_max_page_size > 1000:
            errors.append("API_MAX_PAGE_SIZE must be between 1 and 1000")
        
        if self.api_max_retries <= 0:
            errors.append("API_MAX_RETRIES must be positive")
        
        # Validate URLs
        if not self.procore_base_url.startswith(('http://', 'https://')):
            errors.append("PROCORE_BASE_URL must be a valid URL")
        
        if not self.procore_token_url.startswith(('http://', 'https://')):
            errors.append("PROCORE_TOKEN_URL must be a valid URL")
        
        # Warnings
        if self.enable_auto_deactivation and self.dry_run_mode:
            warnings.append("Auto-deactivation enabled but running in dry-run mode")
        
        if not self.webhook_delivery_url and self.enable_webhook_listener:
            warnings.append("Webhook listener enabled but no delivery URL configured")
        
        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings
        }
    
    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of current configuration (safe for logging)."""
        return {
            'procore_base_url': self.procore_base_url,
            'company_id': self.procore_company_id,
            'company_email_domain': self.company_email_domain,
            'inactive_threshold_days': self.inactive_threshold_days,
            'never_logged_in_threshold_days': self.never_logged_in_threshold_days,
            'api_max_page_size': self.api_max_page_size,
            'api_max_retries': self.api_max_retries,
            'enable_auto_deactivation': self.enable_auto_deactivation,
            'dry_run_mode': self.dry_run_mode,
            'dmsa_user_count': len(self.dmsa_user_ids),
            'admin_user_count': len(self.admin_user_ids),
        }

# Global configuration instance
_config_instance = None

def get_config() -> Config:
    """Get the global configuration instance."""
    global _config_instance
    if _config_instance is None:
        _config_instance = Config()
    return _config_instance

def reload_config(env_file: Optional[str] = None) -> Config:
    """Reload configuration from environment variables."""
    global _config_instance
    _config_instance = Config(env_file)
    return _config_instance

# Convenience functions for common config values
def get_api_config() -> Dict[str, Any]:
    """Get API-related configuration."""
    config = get_config()
    return {
        'base_url': config.procore_base_url,
        'company_id': config.procore_company_id,
        'max_page_size': config.api_max_page_size,
        'max_retries': config.api_max_retries,
        'timeout': config.api_timeout_seconds,
        'backoff_base': config.api_backoff_base_seconds,
    }

def get_business_config() -> Dict[str, Any]:
    """Get business logic configuration."""
    config = get_config()
    return {
        'inactive_threshold_days': config.inactive_threshold_days,
        'never_logged_in_threshold_days': config.never_logged_in_threshold_days,
        'company_email_domain': config.company_email_domain,
        'dmsa_user_ids': config.dmsa_user_ids,
        'admin_user_ids': config.admin_user_ids,
        'enable_auto_deactivation': config.enable_auto_deactivation,
        'dry_run_mode': config.dry_run_mode,
    }

def get_storage_config() -> Dict[str, Any]:
    """Get storage-related configuration."""
    config = get_config()
    return {
        'webhook_storage_path': config.webhook_storage_path,
        'logs_base_path': config.logs_base_path,
        'output_dir': config.output_dir,
        'reports_dir': config.reports_dir,
        'users_cache_path': config.users_cache_path,
        'projects_cache_path': config.projects_cache_path,
        'audit_logs_base': config.audit_logs_base,
        'error_logs_base': config.error_logs_base,
        'deactivation_logs_base': config.deactivation_logs_base,
    }