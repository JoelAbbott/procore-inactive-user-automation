#!/usr/bin/env python3
"""
Cleanup and Test Script for Procore Governance System
Performs a complete system reset and validates all components
"""

import os
import sys
import json
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
import time

# Colors for terminal output
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def print_section(title):
    """Print a section header."""
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{title:^60}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.ENDC}\n")


def print_status(message, status='info'):
    """Print a status message with color."""
    if status == 'success':
        print(f"{Colors.OKGREEN}✓ {message}{Colors.ENDC}")
    elif status == 'warning':
        print(f"{Colors.WARNING}⚠ {message}{Colors.ENDC}")
    elif status == 'error':
        print(f"{Colors.FAIL}✗ {message}{Colors.ENDC}")
    else:
        print(f"{Colors.OKBLUE}→ {message}{Colors.ENDC}")


def cleanup_old_data():
    """Clean up old data files."""
    print_section("STEP 1: CLEANING UP OLD DATA")
    
    cleanup_paths = [
        'data/intermediate/users_cache.json',
        'data/intermediate/projects_cache.json',
        'data/intermediate/inactive_users_raw.csv',
        'data/reports/inactive_users_report.csv',
        'data/reports/never_logged_in_users_report.csv',
        'data/reports/non_company_email_users_report.csv',
        'data/logs/activity_log_*.csv'
    ]
    
    for path_pattern in cleanup_paths:
        path = Path(path_pattern)
        if '*' in str(path):
            # Handle glob patterns
            for file in Path('.').glob(path_pattern):
                if file.exists():
                    file.unlink()
                    print_status(f"Deleted: {file}", 'success')
        else:
            if path.exists():
                path.unlink()
                print_status(f"Deleted: {path}", 'success')
            else:
                print_status(f"Already clean: {path}", 'info')


def check_environment():
    """Check environment configuration."""
    print_section("STEP 2: CHECKING ENVIRONMENT")
    
    required_vars = [
        'PROCORE_CLIENT_ID',
        'PROCORE_CLIENT_SECRET', 
        'PROCORE_COMPANY_ID',
        'COMPANY_EMAIL_DOMAIN'
    ]
    
    from dotenv import load_dotenv
    env_path = Path('.env')
    
    if not env_path.exists():
        print_status(".env file not found!", 'error')
        return False
    
    load_dotenv(dotenv_path=env_path)
    
    all_good = True
    for var in required_vars:
        value = os.getenv(var)
        if value:
            if 'SECRET' in var:
                print_status(f"{var}: ***HIDDEN***", 'success')
            else:
                print_status(f"{var}: {value}", 'success')
        else:
            print_status(f"{var}: NOT SET", 'error')
            all_good = False
    
    return all_good


def run_activity_logger():
    """Run the activity logger to process webhooks."""
    print_section("STEP 3: PROCESSING ACTIVITY LOGS")
    
    try:
        result = subprocess.run([sys.executable, 'activity_logger.py'], 
                              capture_output=True, text=True)
        
        if result.returncode == 0:
            print_status("Activity logger completed successfully", 'success')
            # Check if CSV files were created
            csv_files = list(Path('data/logs').glob('activity_log_*.csv'))
            print_status(f"Found {len(csv_files)} activity log CSV files", 'info')
            return True
        else:
            print_status("Activity logger failed", 'error')
            print(result.stderr)
            return False
    except Exception as e:
        print_status(f"Failed to run activity logger: {e}", 'error')
        return False


def run_etl_pipeline():
    """Run the ETL pipeline."""
    print_section("STEP 4: RUNNING ETL PIPELINE")
    
    try:
        result = subprocess.run([sys.executable, 'inactivity_etl_pipeline.py'], 
                              capture_output=True, text=True)
        
        if result.returncode == 0:
            print_status("ETL pipeline completed successfully", 'success')
            return True
        else:
            print_status("ETL pipeline failed", 'error')
            print(result.stderr)
            return False
    except Exception as e:
        print_status(f"Failed to run ETL pipeline: {e}", 'error')
        return False


def run_report_generators():
    """Run additional report generators."""
    print_section("STEP 5: GENERATING ADDITIONAL REPORTS")
    success = True
    # Generate non-company email users report
    try:
        result = subprocess.run([sys.executable, 'non_company_email_report_generator.py'], 
                              capture_output=True, text=True)
        
        if result.returncode == 0:
            print_status("Non-company email users report generated", 'success')
        else:
            print_status("Non-company email report failed", 'warning')
            print(result.stderr)
            success = False
    except Exception as e:
        print_status(f"Failed to generate non-company email report: {e}", 'warning')
        success = False
    return success


def validate_caches():
    """Validate the cache files."""
    print_section("STEP 6: VALIDATING CACHE FILES")
    
    cache_files = {
        'users_cache.json': Path('data/intermediate/users_cache.json'),
        'projects_cache.json': Path('data/intermediate/projects_cache.json')
    }
    
    all_valid = True
    
    for name, path in cache_files.items():
        if not path.exists():
            print_status(f"{name} not found", 'error')
            all_valid = False
            continue
        
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            
            print_status(f"{name} exists with {len(data)} entries", 'success')
            
            # Check structure of first entry
            if data:
                first_key = list(data.keys())[0]
                first_entry = data[first_key]
                
                # Check if it's flattened (not nested objects)
                if name == 'users_cache.json':
                    required_fields = ['first_name', 'last_name', 'email_address']
                    has_all = all(field in first_entry for field in required_fields)
                    if has_all:
                        print_status(f"  ✓ Cache structure is correct (flattened)", 'success')
                    else:
                        print_status(f"  ✗ Cache structure incorrect - missing fields", 'error')
                        all_valid = False
                
                # Verify all keys are strings
                non_string_keys = [k for k in data.keys() if not isinstance(k, str)]
                if non_string_keys:
                    print_status(f"  ✗ Found {len(non_string_keys)} non-string keys", 'error')
                    all_valid = False
                else:
                    print_status(f"  ✓ All keys are strings", 'success')
                    
        except Exception as e:
            print_status(f"Failed to validate {name}: {e}", 'error')
            all_valid = False
    
    return all_valid


def validate_reports():
    """Validate the generated reports."""
    print_section("STEP 7: VALIDATING REPORTS")
    
    report_files = {
        'inactive_users_raw.csv': Path('data/intermediate/inactive_users_raw.csv'),
        'non_company_email_users_report.csv': Path('data/reports/non_company_email_users_report.csv')
    }
    
    all_valid = True
    
    for name, path in report_files.items():
        if not path.exists():
            print_status(f"{name} not found", 'warning')
            continue
        
        try:
            import pandas as pd
            df = pd.read_csv(path)
            print_status(f"{name} exists with {len(df)} rows", 'success')
            
            # Check for required columns
            if 'user_id' in df.columns:
                # Check for non-string or empty user_ids
                invalid_ids = df[df['user_id'].astype(str).str.strip() == ''].shape[0]
                if invalid_ids > 0:
                    print_status(f"  ✗ Found {invalid_ids} rows with invalid user_id", 'error')
                    all_valid = False
                else:
                    print_status(f"  ✓ All user_ids are valid", 'success')
                    
        except Exception as e:
            print_status(f"Failed to validate {name}: {e}", 'error')
            all_valid = False
    
    return all_valid


def test_dashboard_launch():
    """Test if the dashboard can launch without errors."""
    print_section("STEP 8: TESTING DASHBOARD LAUNCH")
    
    try:
        # Just test import and basic initialization
        import streamlit as st
        print_status("Streamlit imported successfully", 'success')
        
        # Test importing the dashboard module
        import governance_dashboard
        print_status("Dashboard module imported successfully", 'success')
        
        print_status("Dashboard is ready to launch!", 'success')
        print_status("Run: streamlit run governance_dashboard.py", 'info')
        
        return True
        
    except Exception as e:
        print_status(f"Dashboard test failed: {e}", 'error')
        return False


def main():
    """Main test orchestration."""
    print(f"{Colors.BOLD}{Colors.OKCYAN}")
    print("╔═══════════════════════════════════════════════════════════╗")
    print("║     PROCORE GOVERNANCE SYSTEM - CLEANUP & TEST SUITE      ║")
    print("╚═══════════════════════════════════════════════════════════╝")
    print(f"{Colors.ENDC}")
    
    start_time = time.time()
    
    # Run all steps
    steps = [
        ("Cleanup old data", cleanup_old_data),
        ("Check environment", check_environment),
        ("Run activity logger", run_activity_logger),
        ("Run ETL pipeline", run_etl_pipeline),
        ("Generate reports", run_report_generators),
        ("Validate caches", validate_caches),
        ("Validate reports", validate_reports),
        ("Test dashboard", test_dashboard_launch)
    ]
    
    results = []
    for step_name, step_func in steps:
        try:
            if step_func == cleanup_old_data:
                step_func()
                results.append((step_name, True))
            else:
                result = step_func()
                results.append((step_name, result))
        except Exception as e:
            print_status(f"Step '{step_name}' failed with exception: {e}", 'error')
            results.append((step_name, False))
    
    # Print summary
    print_section("TEST SUMMARY")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    print(f"Total Steps: {total}")
    print(f"Passed: {passed}")
    print(f"Failed: {total - passed}")
    print(f"Time: {time.time() - start_time:.2f}s")
    print()
    
    for step_name, result in results:
        status = 'success' if result else 'error'
        symbol = '✓' if result else '✗'
        print_status(f"{symbol} {step_name}", status)
    
    print()
    if passed == total:
        print(f"{Colors.OKGREEN}{Colors.BOLD}✅ ALL TESTS PASSED! System is ready.{Colors.ENDC}")
        print(f"\n{Colors.OKCYAN}Next step: Launch the dashboard with:{Colors.ENDC}")
        print(f"{Colors.BOLD}   streamlit run governance_dashboard.py{Colors.ENDC}")
    else:
        print(f"{Colors.FAIL}{Colors.BOLD}❌ SOME TESTS FAILED! Please fix the issues above.{Colors.ENDC}")
    
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main()) 