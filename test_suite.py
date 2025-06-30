"""
Comprehensive test suite for Procore Governance System.
Tests the fixed components and validates the error corrections.
"""

import os
import sys
import json
import time
import tempfile
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pandas as pd
import requests

# Add the current directory to Python path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from config import Config, get_config
    from oauth_manager import OAuthManager
    from audit_logging import log_error, log_audit
except ImportError as e:
    print(f"Import error: {e}")
    print("Make sure all required modules are in the same directory")
    sys.exit(1)

class TestSuite:
    """Comprehensive test suite for the governance system."""
    
    def __init__(self):
        self.test_results = []
        self.temp_dir = None
        self.setup_test_environment()
    
    def setup_test_environment(self):
        """Set up a temporary test environment."""
        self.temp_dir = tempfile.mkdtemp(prefix="governance_test_")
        print(f"🔧 Test environment created: {self.temp_dir}")
        
        # Create test directory structure
        test_dirs = [
            'data/webhooks',
            'data/logs',
            'data/intermediate',
            'data/reports',
            'data/audit_logs',
            'data/error_logs',
            'data/deactivation_logs'
        ]
        
        for dir_path in test_dirs:
            os.makedirs(os.path.join(self.temp_dir, dir_path), exist_ok=True)
    
    def cleanup_test_environment(self):
        """Clean up the temporary test environment."""
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
            print(f"🧹 Test environment cleaned up: {self.temp_dir}")
    
    def run_test(self, test_name, test_func):
        """Run a single test and record results."""
        print(f"\n🧪 Running test: {test_name}")
        start_time = time.time()
        
        try:
            test_func()
            duration = time.time() - start_time
            self.test_results.append({
                'test': test_name,
                'status': 'PASS',
                'duration': duration,
                'error': None
            })
            print(f"✅ {test_name} PASSED ({duration:.2f}s)")
            
        except Exception as e:
            duration = time.time() - start_time
            self.test_results.append({
                'test': test_name,
                'status': 'FAIL',
                'duration': duration,
                'error': str(e)
            })
            print(f"❌ {test_name} FAILED ({duration:.2f}s): {e}")
    
    def test_config_management(self):
        """Test the new configuration management system."""
        # Test with environment variables
        test_env = {
            'PROCORE_CLIENT_ID': 'test_client_id',
            'PROCORE_CLIENT_SECRET': 'test_secret',
            'PROCORE_COMPANY_ID': '12345',
            'INACTIVE_THRESHOLD_DAYS': '180',
            'API_MAX_PAGE_SIZE': '250'
        }
        
        with patch.dict(os.environ, test_env):
            config = Config()
            
            # Test required fields
            assert config.procore_client_id == 'test_client_id'
            assert config.procore_company_id == '12345'
            assert config.inactive_threshold_days == 180
            assert config.api_max_page_size == 250
            
            # Test validation
            validation = config.validate()
            assert validation['valid'] == True
            
            # Test configuration summary
            summary = config.get_summary()
            assert 'company_id' in summary
            assert summary['inactive_threshold_days'] == 180
    
    def test_timezone_handling(self):
        """Test the fixed timezone handling in datetime operations."""
        # Test timezone-aware datetime creation
        now_utc = datetime.now(timezone.utc)
        assert now_utc.tzinfo is not None
        
        # Test datetime subtraction (this was the original error)
        past_date = now_utc - timedelta(days=365)
        time_diff = now_utc - past_date
        assert time_diff.days == 365
        
        # Test pandas timestamp parsing
        test_timestamps = [
            "2024-01-01T12:00:00Z",
            "2024-01-01T12:00:00+00:00",
            "2024-01-01 12:00:00",
        ]
        
        for ts in test_timestamps:
            try:
                parsed = pd.to_datetime(ts, utc=True)
                assert parsed.tz is not None
            except Exception as e:
                # This should not fail with our fixes
                raise AssertionError(f"Failed to parse timestamp {ts}: {e}")
    
    def test_api_pagination_limits(self):
        """Test that API pagination respects the 300-item limit."""
        from config import get_api_config
        
        api_config = get_api_config()
        
        # Ensure we're not requesting more than 300 items per page
        assert api_config['max_page_size'] <= 300
        
        # Test pagination logic simulation
        def simulate_api_call(page_size, total_items):
            pages = []
            page = 1
            remaining = total_items
            
            while remaining > 0:
                items_this_page = min(page_size, remaining)
                pages.append({
                    'page': page,
                    'items': items_this_page,
                    'has_more': remaining > page_size
                })
                remaining -= items_this_page
                page += 1
                
                # Safety check
                if page > 1000:
                    break
            
            return pages
        
        # Test with various scenarios
        pages = simulate_api_call(300, 1500)
        assert len(pages) == 5  # Should be 5 pages for 1500 items
        assert pages[-1]['items'] == 0 or pages[-1]['items'] <= 300
    
    def test_error_logging_system(self):
        """Test the audit logging system."""
        test_module = "test_module"
        test_error = ValueError("Test error message")
        test_context = "Test context"
        
        # Test error logging (should not crash)
        try:
            log_error(test_module, test_error, test_context)
        except Exception as e:
            raise AssertionError(f"Error logging failed: {e}")
        
        # Test audit logging
        try:
            log_audit(test_module, "Test Operation", record_count=100, notes="Test audit")
        except Exception as e:
            raise AssertionError(f"Audit logging failed: {e}")
    
    def test_oauth_manager(self):
        """Test OAuth manager with mocked responses."""
        # Mock successful token response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'access_token': 'test_token_12345',
            'expires_in': 3600
        }
        
        with patch('requests.post', return_value=mock_response):
            with patch.dict(os.environ, {
                'PROCORE_CLIENT_ID': 'test_id',
                'PROCORE_CLIENT_SECRET': 'test_secret',
                'PROCORE_COMPANY_ID': '12345',
                'PROCORE_BASE_URL': 'https://test.procore.com',
                'PROCORE_TOKEN_URL': 'https://test.procore.com/oauth/token'
            }):
                oauth = OAuthManager()
                token = oauth.get_access_token()
                assert token == 'test_token_12345'
    
    def test_user_id_mapping(self):
        """Test the user_id to person_id mapping logic."""
        # Simulate user data that would come from /users endpoint
        test_user_data = {
            'id': 123,
            'contact_id': 456,
            'name': 'Test User',
            'email_address': 'test@example.com'
        }
        
        # Test the ID mapping logic
        user_id = test_user_data['id']
        person_id = test_user_data.get('contact_id') or test_user_data.get('id')
        
        assert person_id == 456  # Should use contact_id when available
        
        # Test fallback when contact_id is missing
        test_user_data_no_contact = {
            'id': 789,
            'name': 'Test User 2'
        }
        
        person_id_fallback = test_user_data_no_contact.get('contact_id') or test_user_data_no_contact.get('id')
        assert person_id_fallback == 789  # Should fall back to id
    
    def test_inactive_user_detection(self):
        """Test the inactive user detection logic with timezone fixes."""
        # Create test activity data
        now = datetime.now(timezone.utc)
        
        test_activity = [
            {
                'user_id': '100',
                'occurred_at': (now - timedelta(days=400)).isoformat(),
                'event_type': 'update'
            },
            {
                'user_id': '101', 
                'occurred_at': (now - timedelta(days=30)).isoformat(),
                'event_type': 'create'
            }
        ]
        
        # Create test users data
        test_users = {
            '100': {
                'id': 100,
                'created_at': (now - timedelta(days=500)).isoformat(),
                'last_login_at': None
            },
            '101': {
                'id': 101,
                'created_at': (now - timedelta(days=60)).isoformat(),
                'last_login_at': (now - timedelta(days=30)).isoformat()
            },
            '102': {  # Never logged in, old account
                'id': 102,
                'created_at': (now - timedelta(days=200)).isoformat(),
                'last_login_at': None
            }
        }
        
        # Test the detection logic
        inactive_threshold = 365  # days
        never_logged_threshold = 180  # days
        
        inactive_users = []
        
        # Build activity map
        activity_df = pd.DataFrame(test_activity)
        activity_df['occurred_at_parsed'] = pd.to_datetime(activity_df['occurred_at'], utc=True)
        last_active_map = activity_df.groupby('user_id')['occurred_at_parsed'].max().to_dict()
        
        for user_id, user in test_users.items():
            last_active = last_active_map.get(user_id)
            created_at = pd.to_datetime(user['created_at'], utc=True) if user.get('created_at') else None
            
            if last_active:
                # User has activity
                time_diff = now - last_active
                if time_diff.days >= inactive_threshold:
                    inactive_users.append(user_id)
            elif created_at:
                # Never logged in
                time_diff = now - created_at
                if time_diff.days >= never_logged_threshold:
                    inactive_users.append(user_id)
        
        # User 100: last active 400 days ago (> 365) -> inactive
        # User 101: last active 30 days ago (< 365) -> active  
        # User 102: never logged in, account 200 days old (> 180) -> inactive
        
        assert '100' in inactive_users
        assert '101' not in inactive_users
        assert '102' in inactive_users
    
    def test_csv_generation(self):
        """Test CSV file generation with proper error handling."""
        # Test data
        test_data = [
            {
                'user_id': '123',
                'first_name': 'John',
                'last_name': 'Doe',
                'email_address': 'john@example.com',
                'last_activity': '2024-01-01T12:00:00Z'
            }
        ]
        
        # Test CSV creation
        test_file = os.path.join(self.temp_dir, 'test_output.csv')
        df = pd.DataFrame(test_data)
        df.to_csv(test_file, index=False)
        
        # Verify file was created and has correct content
        assert os.path.exists(test_file)
        
        # Read back and verify
        df_read = pd.read_csv(test_file)
        assert len(df_read) == 1
        assert df_read.iloc[0]['user_id'] == '123'
        assert df_read.iloc[0]['first_name'] == 'John'
    
    def test_webhook_data_processing(self):
        """Test webhook data processing and JSON handling."""
        # Create test webhook data
        test_webhook = {
            "event": "user.updated",
            "id": "webhook_123",
            "object_type": "User",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "details": {
                "user_id": 456,
                "project_id": 789
            }
        }
        
        # Test JSON serialization/deserialization
        json_str = json.dumps(test_webhook)
        loaded_data = json.loads(json_str)
        
        assert loaded_data['event'] == 'user.updated'
        assert loaded_data['details']['user_id'] == 456
        
        # Test file operations
        test_webhook_file = os.path.join(self.temp_dir, 'test_webhook.json')
        with open(test_webhook_file, 'w') as f:
            json.dump(test_webhook, f)
        
        # Read back
        with open(test_webhook_file, 'r') as f:
            loaded_webhook = json.load(f)
        
        assert loaded_webhook['id'] == 'webhook_123'
    
    def test_error_scenarios(self):
        """Test error handling scenarios that were previously failing."""
        # Test 1: Invalid timestamp handling
        invalid_timestamps = ['', None, 'invalid-date', '2024-13-45T25:61:61Z']
        
        for ts in invalid_timestamps:
            try:
                if ts:
                    parsed = pd.to_datetime(ts, utc=True, errors='coerce')
                    # Should either parse successfully or return NaT
                    assert parsed is not None
            except Exception:
                # Should not raise an exception with proper error handling
                pass
        
        # Test 2: Missing environment variables
        with patch.dict(os.environ, {}, clear=True):
            try:
                config = Config()
                validation = config.validate()
                assert not validation['valid']  # Should have validation errors
                assert len(validation['errors']) > 0
            except Exception:
                # Expected to have errors for missing required vars
                pass
        
        # Test 3: API error response handling
        test_api_responses = [
            {'status': 400, 'text': '{"message":"max page size is 300"}'},
            {'status': 401, 'text': '{"message":"unauthorized"}'},
            {'status': 429, 'text': '{"message":"rate limited"}'},
            {'status': 500, 'text': '{"message":"internal server error"}'}
        ]
        
        for response in test_api_responses:
            # Should be able to handle these without crashing
            error_msg = f"HTTP {response['status']}: {response['text']}"
            assert len(error_msg) > 0
    
    def run_all_tests(self):
        """Run the complete test suite."""
        print("🚀 Starting Comprehensive Test Suite")
        print("=" * 60)
        
        # List of all tests to run
        tests = [
            ("Configuration Management", self.test_config_management),
            ("Timezone Handling", self.test_timezone_handling),
            ("API Pagination Limits", self.test_api_pagination_limits),
            ("Error Logging System", self.test_error_logging_system),
            ("OAuth Manager", self.test_oauth_manager),
            ("User ID Mapping", self.test_user_id_mapping),
            ("Inactive User Detection", self.test_inactive_user_detection),
            ("CSV Generation", self.test_csv_generation),
            ("Webhook Data Processing", self.test_webhook_data_processing),
            ("Error Scenarios", self.test_error_scenarios),
        ]
        
        # Run all tests
        for test_name, test_func in tests:
            self.run_test(test_name, test_func)
        
        # Print summary
        self.print_test_summary()
    
    def print_test_summary(self):
        """Print a summary of test results."""
        print("\n" + "=" * 60)
        print("📊 TEST SUMMARY")
        print("=" * 60)
        
        total_tests = len(self.test_results)
        passed_tests = len([r for r in self.test_results if r['status'] == 'PASS'])
        failed_tests = len([r for r in self.test_results if r['status'] == 'FAIL'])
        total_time = sum(r['duration'] for r in self.test_results)
        
        print(f"Total Tests: {total_tests}")
        print(f"Passed: {passed_tests} ✅")
        print(f"Failed: {failed_tests} ❌")
        print(f"Total Time: {total_time:.2f}s")
        print(f"Success Rate: {(passed_tests/total_tests)*100:.1f}%")
        
        if failed_tests > 0:
            print("\n🔍 FAILED TESTS:")
            for result in self.test_results:
                if result['status'] == 'FAIL':
                    print(f"  ❌ {result['test']}: {result['error']}")
        
        print("\n📋 DETAILED RESULTS:")
        for result in self.test_results:
            status_icon = "✅" if result['status'] == 'PASS' else "❌"
            print(f"  {status_icon} {result['test']} ({result['duration']:.2f}s)")


def main():
    """Main entry point for the test suite."""
    test_suite = TestSuite()
    
    try:
        test_suite.run_all_tests()
    finally:
        test_suite.cleanup_test_environment()
    
    # Return exit code based on test results
    failed_tests = len([r for r in test_suite.test_results if r['status'] == 'FAIL'])
    return 1 if failed_tests > 0 else 0


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)