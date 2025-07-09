import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import os
import json
import glob
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from oauth_manager import OAuthManager # Assuming this is available
from audit_logging import log_error, log_audit, log_operation_summary # For logging
import requests
import time
import logging

# Configure logging for the Streamlit dashboard
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] %(levelname)s %(name)s: %(message)s')
handler.setFormatter(formatter)
if not logger.hasHandlers():
    logger.addHandler(handler)

# --- Enhanced Streamlit Page Configuration ---
st.set_page_config(
    page_title="Procore Governance Dashboard", 
    layout="wide",
    initial_sidebar_state="expanded",
    page_icon="🏢"
)

# --- Custom CSS for Professional Styling ---
st.markdown("""
<style>
    /* Main header styling */
    .main-header {
        background: linear-gradient(90deg, #003f7f 0%, #0066cc 100%);
        padding: 1.5rem;
        border-radius: 10px;
        margin-bottom: 2rem;
        color: white;
        text-align: center;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    }
        
    /* KPI card styling (not used directly if st.metric is used, but kept for reference) */
    .kpi-card {
        background: white;
        padding: 1.5rem;
        border-radius: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        border-left: 4px solid #003f7f;
        margin-bottom: 1rem;
    }
        
    /* Metric styling (not used directly if st.metric is used, but kept for reference) */
    .metric-container {
        text-align: center;
        min-height: 50px; /* Added for consistent height */
    }
        
    .metric-value {
        font-size: 2.5rem;
        font-weight: bold;
        color: #003f7f;
        margin: 0;
    }
        
    .metric-label {
        font-size: 0.9rem;
        color: #666;
        margin-top: 0.5rem;
    }
        
    /* Status indicators */
    .status-active { color: #28a745; font-weight: bold; }
    .status-inactive { color: #dc3545; font-weight: bold; }
    .status-warning { color: #fd7e14; font-weight: bold; }
    .status-never { color: #ffc107; font-weight: bold; }
        
    /* Alert styling */
    .alert-info {
        background-color: #d1ecf1;
        border: 1px solid #bee5eb;
        color: #0c5460;
        padding: 1rem;
        border-radius: 5px;
        margin: 1rem 0;
    }
        
    /* Table styling improvements */
    .dataframe {
        border: none !important;
    }
        
    .dataframe th {
        background-color: #003f7f !important;
        color: white !important;
        font-weight: bold !important;
        text-align: center !important;
    }
        
    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 2px;
    }
        
    .stTabs [data-baseweb="tab"] {
        background-color: #f8f9fa;
        border-radius: 10px 10px 0 0;
        padding: 0.5rem 1rem;
    }
        
    .stTabs [aria-selected="true"] {
        background-color: #003f7f;
        color: white;
    }
        
    /* Sidebar styling */
    .css-1d391kg {
        background-color: #f8f9fa;
    }
        
    /* Success/warning/error styling */
    .success-box { background-color: #d4edda; border-left: 4px solid #28a745; padding: 1rem; margin: 1rem 0; }
    .warning-box { background-color: #fff3cd; border-left: 4px solid #ffc107; padding: 1rem; margin: 1rem 0; }
    .error-box { background-color: #f8d7da; border-left: 4px solid #dc3545; padding: 1rem; margin: 1rem 0; }
</style>
""", unsafe_allow_html=True)

# --- Configuration Constants (loaded from central config.py) ---
from config import get_config
_config = get_config() # Get the global configuration instance
MAX_PAGE_SIZE = _config.api_max_page_size
INACTIVE_THRESHOLD_DAYS = _config.inactive_threshold_days
NEVER_LOGGED_IN_THRESHOLD_DAYS = _config.never_logged_in_threshold_days

# --- File Paths ---
USERS_CACHE = './data/intermediate/users_cache.json' 
PROJECTS_CACHE = './data/intermediate/projects_cache.json' 
ACTIVITY_LOGS_DIR = './data/logs'
INACTIVE_USERS_REPORT_CSV = './data/intermediate/inactive_users_raw.csv'
NON_COMPANY_EMAIL_REPORT_CSV = './data/reports/non_company_email_users_report.csv'
MODULE = "governance_dashboard" 

# --- Environment Variable Loading ---
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)
COMPANY_ID = os.getenv('PROCORE_COMPANY_ID')
BASE_URL = os.getenv('PROCORE_BASE_URL', 'https://sandbox.procore.com')
COMPANY_EMAIL_DOMAIN = os.getenv('COMPANY_EMAIL_DOMAIN', 'compassdatacenters.com').lower()
DRY_RUN_MODE = os.getenv('DRY_RUN_MODE', 'false').lower() == 'true'

# --- Enhanced Caching Functions ---
@st.cache_data
def load_json_file(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log_error('governance_dashboard', e, f'Loading {path}')
        return []

@st.cache_data
def load_csv_file(path):
    try:
        df = pd.read_csv(path)
        if df.empty or len(df.columns) == 0:
            return pd.DataFrame()
        return df
    except pd.errors.EmptyDataError:
        logger.warning(f"CSV file '{path}' is empty or malformed. Returning empty DataFrame.")
        return pd.DataFrame()
    except Exception as e:
        log_error('governance_dashboard', e, f'Loading {path}')
        return pd.DataFrame()

@st.cache_data
def load_cache_df(cache_path, index_col):
    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            cache = json.load(f)
        df = pd.DataFrame.from_dict(cache, orient='index')
        df.index.name = index_col
        df.index = df.index.astype(str)
        return df
    except FileNotFoundError:
        logger.error(f"Cache file not found: {cache_path}. Please run ETL pipeline to generate it.")
        return pd.DataFrame()
    except Exception as e:
        log_error('governance_dashboard', e, f'Loading {cache_path}')
        return pd.DataFrame()

# Load caches globally for enrichment
users_cache_df = load_cache_df(USERS_CACHE, 'user_id')
projects_cache_df = load_cache_df(PROJECTS_CACHE, 'project_id')

# --- Helper Functions ---
def enrich_with_user_project_vendor(df):
    try:
        if df.empty:
            return df
        # Ensure user_id column is string for merge
        if 'user_id' in df.columns:
            df['user_id'] = df['user_id'].astype(str)
            if not users_cache_df.empty:
                df = df.merge(users_cache_df[['first_name', 'last_name', 'email_address', 'vendor_name', 'created_at']], 
                              left_on='user_id', right_index=True, how='left', suffixes=('', '_cache'))
                if 'created_at' in df.columns and 'created_at_cache' in df.columns:
                    df['created_at'] = df['created_at'].fillna(df['created_at_cache'])
                    df = df.drop(columns=['created_at_cache'])
                elif 'created_at_cache' in df.columns and 'created_at' not in df.columns: 
                    df = df.rename(columns={'created_at_cache': 'created_at'})
        # Project enrichment
        if 'project_id' in df.columns:
            df['project_id_str_for_merge'] = df['project_id'].apply(lambda x: str(int(float(x))) if pd.notna(x) and str(x).replace('.','',1).isdigit() else '')
            if not projects_cache_df.empty:
                df = df.merge(projects_cache_df[['name']].rename(columns={'name': 'project_name'}), 
                              left_on='project_id_str_for_merge', right_index=True, how='left')
            df = df.drop(columns=['project_id_str_for_merge'])
        # Fill NaNs and ensure columns exist
        for col in ['first_name', 'last_name', 'email_address', 'vendor_name', 'project_name', 'last_activity_date', 'status', 'inactivity_days', 'deactivation_status', 'error_message', 'created_at']:
            if col not in df.columns:
                df[col] = ''
            df[col] = df[col].fillna('')
                
        if 'project_name' in df.columns:
            df['project_name'] = df['project_name'].replace({'': 'N/A', None: 'N/A', pd.NA: 'N/A'})

        return df
    except Exception as e:
        st.error(f"Error enriching data: {e}")
        log_error(MODULE, e, 'Enrichment')
        for col in ['first_name', 'last_name', 'email_address', 'vendor_name', 'project_name']:
            if col not in df.columns:
                df[col] = ''
            df[col] = df[col].fillna('')
        return df

def compare_ids(df, cache_df, id_col, cache_name):
    if df.empty or cache_df.empty or id_col not in df.columns:
        return []
    data_ids = set(df[id_col].astype(str).unique())
    cache_ids = set(cache_df.index.astype(str).unique())
    missing = sorted(list(data_ids - cache_ids))
    return missing

def get_latest_file(pattern):
    files = glob.glob(pattern)
    if not files:
        return None, []
    files = sorted(files, key=os.path.getmtime, reverse=True)
    return files[0], files

def fix_project_id_format(df):
    """Fix project_id format to ensure proper matching with cache (handles float to string conversion)"""
    if 'project_id' in df.columns:
        df['project_id'] = pd.to_numeric(df['project_id'], errors='coerce').fillna(0).astype(int).astype(str).replace('0', '')
    return df

def create_kpi_card(title, value, delta=None, delta_color="normal"):
    """Create a professional KPI card (Note: replaced by st.metric for robust display)"""
    # This function is retained for context but its direct use for display is commented out in this version.
    # The new approach uses st.metric directly in the dashboard body.
    delta_html = ""
    if delta is not None:
        color = "#28a745" if delta_color == "normal" else "#dc3545" if delta_color == "inverse" else "#666"
        delta_html = f'<p style="color: {color}; font-size: 0.8rem; margin: 0;">△ {delta}</p>'
    return f"""
    <div class="kpi-card">
        <div class="metric-container">
            <h1 class="metric-value">{str(value)}</h1>
            <p class="metric-label">{title}</p>
            {delta_html}
        </div>
    </div>
    """

def style_dataframe_by_status(df):
    """Apply conditional styling to dataframes based on status"""
    def highlight_status(row):
        if 'status' in row:
            if row['status'] == 'Inactive':
                return ['background-color: #ffe6e6; color: #b30000'] * len(row)
            elif row['status'] == 'Never Logged In':
                return ['background-color: #fffacd; color: #cc9900'] * len(row)
        return [''] * len(row)
        
    if not df.empty and 'status' in df.columns:
        return df.style.apply(highlight_status, axis=1)
    return df

def create_activity_chart(df):
    """Create an enhanced activity timeline chart"""
    if df.empty or 'timestamp' not in df.columns:
        return None
        
    # Convert timestamp and create daily aggregation
    df_copy = df.copy()
    df_copy['timestamp'] = pd.to_datetime(df_copy['timestamp'])
    df_copy['date'] = df_copy['timestamp'].dt.date
    daily_activity = df_copy.groupby('date').size().reset_index(name='count')
    
    # Ensure we have data points to plot
    if daily_activity.empty:
        return None
        
    # Create chart with markers to make data points visible
    fig = px.line(
        daily_activity,
        x='date',
        y='count',
        title='Daily Activity Trend',
        labels={'count': 'Events', 'date': 'Date'},
        markers=True,
        template='plotly_dark' # Apply dark theme
    )
    
    fig.update_layout(
        title_font_size=16,
        title_x=0.5,
        font=dict(color='white'), # White font for dark theme
        plot_bgcolor='rgba(0,0,0,0)', # Transparent plot background
        paper_bgcolor='rgba(0,0,0,0)', # Transparent paper background
        xaxis=dict(showgrid=True, gridcolor='gray'), # Tighten grid
        yaxis=dict(showgrid=True, gridcolor='gray'), # Tighten grid
        showlegend=False,
        margin=dict(t=40, b=40, l=40, r=40) # Tighten margins
    )
        
    fig.update_traces(
        line_color='#28a745', # Accent color (green)
        line_width=2,
        marker=dict(size=6, color='#28a745') # Accent color (green)
    )
    return fig

def create_user_status_chart(df):
    """Create a user status distribution chart with inside-only, shortened labels."""
    if df.empty or 'status' not in df.columns or 'count' not in df.columns:
        return None

    # Map to shorter labels
    pie_df = df.copy()
    pie_df['label'] = pie_df['status'].map({
        'Active': 'Active',
        'Inactive': 'Inactive',
        'Never Logged In': 'N/Logged In'
    })

    colors = {
        'Active': '#28a745',
        'Inactive': '#dc3545',
        'N/Logged In': '#ffc107'
    }

    fig = px.pie(
        pie_df,
        names='label',
        values='count',
        title='User Status Distribution',
        color_discrete_map=colors,
        hole=0.4
    )

    # Put everything inside, use a smaller font
    fig.update_traces(
        textinfo='percent+label',
        textposition='inside',
        textfont=dict(color='white', size=12, family="Arial Black"),
        pull=[0.05] * len(pie_df)
    )

    fig.update_layout(
        title_x=0.5,
        title_font_color='white',
        font=dict(color='white'),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        margin=dict(t=40, b=20, l=20, r=20),
        legend=dict(orientation="h", y=-0.1, x=0.5, xanchor='center')
    )

    return fig

def create_vendor_chart(df):
    """Create a vendor analysis chart"""
    if df.empty or 'vendor_name' not in df.columns:
        return None
        
    # Filter out empty/null vendor names
    vendor_data = df[df['vendor_name'].notna() & (df['vendor_name'] != '') & (df['vendor_name'] != 'N/A')]
        
    if vendor_data.empty:
        return None # Return None if no valid vendor data to plot
            
    vendor_counts = vendor_data['vendor_name'].value_counts().head(10)
        
    # Only create chart if there are actual vendor counts
    if vendor_counts.empty:
        return None
            
    fig = px.bar(
        x=vendor_counts.values,
        y=vendor_counts.index,
        orientation='h',
        title='Top 10 Vendors by User Count',
        labels={'x': 'Number of Users', 'y': ''}, # Remove y-axis label
        template='plotly_dark', # Apply dark theme
        color_discrete_sequence=['#0066cc'] # Use Compass blue
    )
    
    fig.update_layout(
        title_font_size=16,
        title_x=0.5,
        font=dict(color='white'), # White font for dark theme
        plot_bgcolor='rgba(0,0,0,0)', # Transparent plot background
        paper_bgcolor='rgba(0,0,0,0)', # Transparent paper background
        xaxis=dict(showgrid=False), # Hide grid
        yaxis=dict(tickfont=dict(size=12)), # Adjust tick font size
        margin=dict(t=40, b=40, l=100, r=40) # Adjust margins
    )
    return fig

# --- Data File Discovery ---
activity_log_pattern = f'{ACTIVITY_LOGS_DIR}/activity_log_*.csv' 
inactive_users_report_pattern = f'{INACTIVE_USERS_REPORT_CSV}' 
deactivated_users_path = './data/deactivation_logs/*/deactivation_log.csv'
non_company_email_report_path = NON_COMPANY_EMAIL_REPORT_CSV

latest_activity_log, all_activity_logs = get_latest_file(activity_log_pattern)
latest_deactivated_users, all_deactivated_users_logs = get_latest_file(deactivated_users_path)

# --- Main Dashboard Header ---
st.markdown("""
<div class="main-header">
    <h1>🏢 Procore User Governance Dashboard</h1>
    <p>Comprehensive user lifecycle management and compliance monitoring</p>
</div>
""", unsafe_allow_html=True)

# --- Sidebar with System Status ---
with st.sidebar:
    st.header("📊 System Status")
        
    # Show data freshness
    if latest_activity_log:
        last_update = datetime.fromtimestamp(os.path.getmtime(latest_activity_log))
        st.success(f"🔄 Last Updated: {last_update.strftime('%Y-%m-%d %H:%M')}")
    else:
        st.warning("⚠️ No activity data found")
        
    # Show environment info
    if DRY_RUN_MODE:
        st.warning("🧪 DRY RUN MODE")
    else:
        st.info("🔴 LIVE MODE")
        
    # Quick stats
    st.subheader("📈 Quick Stats")
    inactive_df = load_csv_file(INACTIVE_USERS_REPORT_CSV)
    if not inactive_df.empty:
        total_users = len(users_cache_df) if not users_cache_df.empty else 0
        inactive_count = len(inactive_df[inactive_df['status'] == 'Inactive'])
        never_logged_in = len(inactive_df[inactive_df['status'] == 'Never Logged In'])
                
        st.metric("Total Users", total_users)
        st.metric("Inactive Users", inactive_count)
        st.metric("Never Logged In", never_logged_in)

# --- Enhanced Tabs Navigation ---
tabs = st.tabs([
    "🏠 Executive Overview",
    "📊 Activity Intelligence", 
    "⚠️ Inactive Users",
    "🔍 Never Logged In",
    "🏢 External Users",
    "📋 Deactivation Audit"
])

# --- Enhanced Executive Overview Tab ---
with tabs[0]:
    st.header("📈 Executive Overview")
        
    # Load data for overview
    inactive_df = load_csv_file(INACTIVE_USERS_REPORT_CSV)
    activity_df = load_csv_file(latest_activity_log) if latest_activity_log else pd.DataFrame()
        
    # --- Fix project_id format in activity_df to prevent cache lookup issues ---
    if not activity_df.empty:
        activity_df = fix_project_id_format(activity_df)
        
    # KPI Row (using st.metric for robust display)
    col1, col2, col3, col4 = st.columns(4)
        
    with col1:
        total_users = len(users_cache_df) if not users_cache_df.empty else 0
        st.metric("Total Users", total_users)
        
    with col2:
        active_users = total_users - (len(inactive_df) if not inactive_df.empty else 0)
        st.metric("Active Users", active_users)
        
    with col3:
        inactive_count = len(inactive_df[inactive_df['status'] == 'Inactive']) if not inactive_df.empty else 0
        st.metric("Inactive Users", inactive_count)
        
    with col4:
        compliance_score = round((active_users / total_users * 100), 1) if total_users > 0 else 0
        st.metric("Compliance Score", f"{compliance_score}%")
        
    # Charts Row
    col1, col2 = st.columns(2)
        
    with col1:
        if not activity_df.empty:
            activity_chart = create_activity_chart(activity_df)
            if activity_chart:
                st.plotly_chart(activity_chart, use_container_width=True, key="executive_activity_chart")
        else:
            st.info("No activity data available for chart")
        
    with col2:
        if not users_cache_df.empty:
            # Create status distribution
            total_users = len(users_cache_df)
            
            # Count inactive and never logged in from inactive_df
            inactive_count = len(inactive_df[inactive_df['status'] == 'Inactive']) if not inactive_df.empty else 0
            never_logged_in_count = len(inactive_df[inactive_df['status'] == 'Never Logged In']) if not inactive_df.empty else 0
            
            # Calculate active users (users who are not in the inactive report)
            active_count = total_users - inactive_count - never_logged_in_count
            
            # Create proper status data - only include categories that have users
            user_status_data = []
            if active_count > 0:
                user_status_data.append({'status': 'Active', 'count': active_count})
            if inactive_count > 0:
                user_status_data.append({'status': 'Inactive', 'count': inactive_count})
            if never_logged_in_count > 0:
                user_status_data.append({'status': 'Never Logged In', 'count': never_logged_in_count})
                        
            if user_status_data:
                status_df = pd.DataFrame(user_status_data)
                status_chart = create_user_status_chart(status_df)
                if status_chart:
                    st.plotly_chart(status_chart, use_container_width=True, key="executive_status_chart")
            else:
                st.info("No user status data available for chart")
        
    # Vendor Analysis
    if not users_cache_df.empty:
        st.subheader("🏢 Top Vendors by User Count")
        vendor_chart = create_vendor_chart(users_cache_df)
        if vendor_chart:
            st.plotly_chart(vendor_chart, use_container_width=True, key="executive_vendor_chart")
        else:
            st.info("📊 No vendor data available. Vendors will appear here once user metadata includes vendor associations.")

# --- Enhanced Activity Intelligence Tab ---
with tabs[1]:
    st.header("📊 User Activity Intelligence")
        
    if not all_activity_logs:
        st.error("❌ No activity log files found. Please run `activity_logger.py`.")
    else:
        # File selector
        selected_log_file = st.selectbox(
            "📁 Select Activity Log File", 
            all_activity_logs, 
            index=0, 
            format_func=lambda x: f"{os.path.basename(x)} ({datetime.fromtimestamp(os.path.getmtime(x)).strftime('%Y-%m-%d %H:%M')})"
        )
            
        df = load_csv_file(selected_log_file)
            
        # --- Fix project_id format in df to prevent cache lookup issues ---
        if not df.empty:
            df = fix_project_id_format(df)
            
        if df.empty:
            st.warning(f"⚠️ No activity records found in selected log file: {os.path.basename(selected_log_file)}.")
        else:
            df = enrich_with_user_project_vendor(df)
                        
            # Activity metrics
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Total Events", len(df))
            with col2:
                unique_users = df['user_id'].nunique()
                st.metric("Active Users", unique_users)
            with col3:
                if 'timestamp' in df.columns:
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    today_events = len(df[df['timestamp'].dt.date == datetime.now(timezone.utc).date()])
                    st.metric("Today's Events", today_events)
            with col4:
                most_common_event = df['event_type'].mode().iloc[0] if not df.empty else "N/A"
                st.metric("Most Common Event", most_common_event)
                        
            # Activity timeline
            if 'timestamp' in df.columns:
                activity_chart = create_activity_chart(df)
                if activity_chart:
                    st.plotly_chart(activity_chart, use_container_width=True, key="activity_timeline_chart")
                        
            # Filters
            st.subheader("🔍 Filters")
            col1, col2, col3 = st.columns(3)
                        
            with col1:
                if 'vendor_name' in df.columns and not df['vendor_name'].isna().all():
                    vendors = ['All'] + sorted(df['vendor_name'].dropna().unique().tolist())
                    selected_vendor = st.selectbox("Vendor", vendors)
                    if selected_vendor != 'All':
                        df = df[df['vendor_name'] == selected_vendor]
                        
            with col2:
                if 'event_type' in df.columns:
                    event_types = ['All'] + sorted(df['event_type'].dropna().unique().tolist())
                    selected_event = st.selectbox("Event Type", event_types)
                    if selected_event != 'All':
                        df = df[df['event_type'] == selected_event]
                        
            with col3:
                if 'resource_name' in df.columns:
                    resources = ['All'] + sorted(df['resource_name'].dropna().unique().tolist())
                    selected_resource = st.selectbox("Resource Type", resources)
                    if selected_resource != 'All':
                        df = df[df['resource_name'] == selected_resource]
                        
            # Data quality warnings
            missing_users = compare_ids(df, users_cache_df, 'user_id', 'users_cache')
            if missing_users:
                st.warning(f"⚠️ {len(missing_users)} user_id(s) not found in users_cache")
                        
            if 'project_id' in df.columns:
                missing_projects = compare_ids(df, projects_cache_df, 'project_id', 'projects_cache')
                if missing_projects:
                    st.warning(f"⚠️ {len(missing_projects)} project_id(s) not found in projects_cache")
                        
            # Enhanced data table
            display_cols = [
                'first_name', 'last_name', 'email_address', 'vendor_name', 'project_name',
                'event_type', 'resource_name', 'timestamp', 'user_id', 'project_id'
            ]
            display_cols_filtered = [col for col in display_cols if col in df.columns]
                        
            st.subheader("📋 Activity Details")
            st.dataframe(
                style_dataframe_by_status(df[display_cols_filtered]), 
                use_container_width=True,
                height=400
            )
                        
            # Export functionality
            csv = df[display_cols_filtered].to_csv(index=False)
            st.download_button(
                "📥 Export Activity Log to CSV", 
                csv, 
                file_name=f"activity_log_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv", 
                mime="text/csv"
            )

# --- Enhanced Inactive Users Tab ---
with tabs[2]:
    st.header("⚠️ Inactive User Management")
        
    df_inactive_raw = load_csv_file(INACTIVE_USERS_REPORT_CSV)
        
    # --- Fix project_id format in df_inactive_raw to prevent cache lookup issues ---
    if not df_inactive_raw.empty:
        df_inactive_raw = fix_project_id_format(df_inactive_raw)
        
    if df_inactive_raw.empty:
        st.info("ℹ️ No inactive users found. Please run `inactivity_etl_pipeline.py`.")
    else:
        df = df_inactive_raw[df_inactive_raw['status'] == 'Inactive'].copy()
        df = enrich_with_user_project_vendor(df)
                
        if df.empty:
            st.success("✅ No users currently flagged as 'Inactive > 365 days'.")
        else:
            # Risk metrics
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Total Inactive", len(df))
            with col2:
                high_risk = len(df[df['inactivity_days'] > 730]) if 'inactivity_days' in df.columns else 0
                st.metric("High Risk (>2 years)", high_risk)
            with col3:
                avg_days = df['inactivity_days'].mean() if 'inactivity_days' in df.columns else 0
                st.metric("Average Days Inactive", f"{avg_days:.0f}")
            with col4:
                if 'vendor_name' in df.columns:
                    affected_vendors = df['vendor_name'].nunique()
                    st.metric("Affected Vendors", affected_vendors)
                        
            # Risk distribution chart
            if 'inactivity_days' in df.columns:
                st.subheader("📊 Inactivity Distribution")
                                
                # Create risk buckets
                df['risk_category'] = pd.cut(df['inactivity_days'],
                                             bins=[0, 365, 730, 1095, float('inf')],
                                             labels=['365-730 days', '1-2 years', '2-3 years', '3+ years'])
                                
                risk_chart = px.histogram(df, x='risk_category',
                                         title='Users by Inactivity Period',
                                         color_discrete_sequence=['#dc3545'])
                risk_chart.update_layout(
                    xaxis_title="Inactivity Period", 
                    yaxis_title="Number of Users",
                    title_font_color='white', 
                    xaxis=dict(title_font_color='white', tickfont_color='white'), 
                    yaxis=dict(title_font_color='white', tickfont_color='white')
                )
                st.plotly_chart(risk_chart, use_container_width=True, key="inactive_risk_chart")
                        
            # Data quality warnings
            missing_users = compare_ids(df, users_cache_df, 'user_id', 'users_cache')
            if missing_users:
                st.warning(f"⚠️ {len(missing_users)} user_id(s) not found in users_cache")
                        
            # Enhanced data table
            display_cols = [
                'first_name', 'last_name', 'email_address', 'vendor_name', 'project_name', 
                'last_activity_date', 'inactivity_days', 'status', 'user_id', 'created_at'
            ]
            display_cols_filtered = [col for col in display_cols if col in df.columns]
                        
            st.subheader("📋 Inactive Users Details")
            styled_df = style_dataframe_by_status(df[display_cols_filtered])
            st.dataframe(styled_df, use_container_width=True, height=400)
                        
            # Export functionality
            csv = df[display_cols_filtered].to_csv(index=False)
            st.download_button(
                "📥 Export Inactive Users to CSV", 
                csv, 
                file_name=f"inactive_users_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv", 
                mime="text/csv"
            )

# --- Enhanced Never Logged In Tab ---
with tabs[3]:
    st.header("🔍 Never Logged In Analysis")
        
    df_inactive_raw = load_csv_file(INACTIVE_USERS_REPORT_CSV)
        
    if df_inactive_raw.empty:
        st.info("ℹ️ No users found. Please run `inactivity_etl_pipeline.py`.")
    else:
        df = df_inactive_raw[df_inactive_raw['status'] == 'Never Logged In'].copy()
        df = enrich_with_user_project_vendor(df)
                
        if df.empty:
            st.success("✅ No users currently flagged as 'Never Logged In'.")
        else:
            # Convert created_at to datetime for analysis
            if 'created_at' in df.columns:
                df['created_at'] = pd.to_datetime(df['created_at'], errors='coerce')
                df['days_since_creation'] = (datetime.now(timezone.utc) - df['created_at']).dt.days
                        
            # Metrics
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Never Logged In", len(df))
            with col2:
                old_accounts = len(df[df['days_since_creation'] > 180]) if 'days_since_creation' in df.columns else 0
                st.metric("Old Accounts (>6 months)", old_accounts)
            with col3:
                recent_accounts = len(df[df['days_since_creation'] <= 30]) if 'days_since_creation' in df.columns else 0
                st.metric("Recent Accounts (<30 days)", recent_accounts)
            with col4:
                if 'vendor_name' in df.columns:
                    affected_vendors = df['vendor_name'].nunique()
                    st.metric("Affected Vendors", affected_vendors)
                        
            # Account age analysis
            if 'days_since_creation' in df.columns:
                st.subheader("📊 Account Age Analysis")
                                
                # Create age buckets
                df['age_category'] = pd.cut(df['days_since_creation'],
                                             bins=[0, 30, 90, 180, float('inf')],
                                             labels=['0-30 days', '31-90 days', '91-180 days', '180+ days'])
                                
                age_chart = px.histogram(df, x='age_category',
                                         title='Never Logged In Users by Account Age',
                                         color_discrete_sequence=['#ffc107'])
                age_chart.update_layout(
                    xaxis_title="Account Age", 
                    yaxis_title="Number of Users",
                    title_font_color='white', 
                    xaxis=dict(title_font_color='white', tickfont_color='white'), 
                    yaxis=dict(title_font_color='white', tickfont_color='white')
                )
                st.plotly_chart(age_chart, use_container_width=True, key="never_logged_age_chart")
                        
            # Vendor analysis for never logged in
            if 'vendor_name' in df.columns and not df['vendor_name'].isna().all():
                st.subheader("🏢 Never Logged In by Vendor")
                vendor_never = df['vendor_name'].value_counts().head(10)
                vendor_chart = px.bar(x=vendor_never.values, y=vendor_never.index,
                                    orientation='h',
                                    title='Top Vendors with Never Logged In Users',
                                    color_discrete_sequence=['#ffc107'])
                vendor_chart.update_layout(
                    title_font_color='black', 
                    xaxis=dict(
                        title='Number of Users', 
                        title_font_color='black', 
                        tickfont_color='black',
                        title_standoff=20
                    ), 
                    yaxis=dict(
                        title='', # Remove y-axis title to avoid sideways text
                        categoryorder='total ascending', 
                        title_font_color='black', 
                        tickfont_color='black',
                        tickfont_size=12
                    ),
                    plot_bgcolor='white',
                    paper_bgcolor='white'
                )
                st.plotly_chart(vendor_chart, use_container_width=True, key="never_logged_vendor_chart")
                        
            # Data table
            display_cols = [
                'first_name', 'last_name', 'email_address', 'vendor_name', 'created_at', 
                'days_since_creation', 'status', 'user_id'
            ]
            display_cols_filtered = [col for col in display_cols if col in df.columns]
                        
            st.subheader("📋 Never Logged In User Details")
            styled_df = style_dataframe_by_status(df[display_cols_filtered])
            st.dataframe(styled_df, use_container_width=True, height=400)
                        
            # Export functionality
            csv = df[display_cols_filtered].to_csv(index=False)
            st.download_button(
                "📥 Export Never Logged In Users to CSV", 
                csv, 
                file_name=f"never_logged_in_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv", 
                mime="text/csv"
            )

# --- Enhanced External Users Tab ---
with tabs[4]:
    st.header("🏢 External User Oversight")
        
    non_company_df = load_csv_file(NON_COMPANY_EMAIL_REPORT_CSV)
        
    if non_company_df.empty:
        st.info("ℹ️ No non-company email users found. Please run `non_company_email_report_generator.py`.")
    else:
        non_company_df = enrich_with_user_project_vendor(non_company_df)
                
        # External user metrics
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("External Users", len(non_company_df))
        with col2:
            gmail_users = len(non_company_df[non_company_df['email_address'].str.contains('gmail.com', na=False)])
            st.metric("Gmail Users", gmail_users)
        with col3:
            if 'vendor_name' in non_company_df.columns:
                no_vendor = len(non_company_df[non_company_df['vendor_name'].isna() | (non_company_df['vendor_name'] == '')])
                st.metric("No Vendor Assigned", no_vendor)
        with col4:
            unique_domains = non_company_df['email_address'].str.split('@').str[1].nunique()
            st.metric("Unique Email Domains", unique_domains)
                
        # Email domain analysis
        st.subheader("📧 Email Domain Analysis")
        email_domains = non_company_df['email_address'].str.split('@').str[1].value_counts().head(10)
                
        domain_chart = px.bar(x=email_domains.values, y=email_domains.index, 
                                orientation='h', 
                                title='Top External Email Domains', 
                                color_discrete_sequence=['#17a2b8'])
        domain_chart.update_layout(
            title_font_color='white', 
            xaxis=dict(title='Number of Users', title_font_color='white', tickfont_color='white'), 
            yaxis=dict(title='Email Domain', categoryorder='total ascending', title_font_color='white', tickfont_color='white')
        )
        st.plotly_chart(domain_chart, use_container_width=True, key="external_domain_chart")
                
        # Risk assessment
        st.subheader("⚠️ Risk Assessment")
                
        # Create risk categories
        risk_indicators = []
        for idx, row in non_company_df.iterrows():
            risk_level = "Low"
            risk_factors = []
                        
            if pd.isna(row.get('vendor_name')) or row.get('vendor_name') == '':
                risk_level = "High"
                risk_factors.append("No vendor assigned")
                        
            email_domain = row['email_address'].split('@')[1] if '@' in row['email_address'] else ''
            if email_domain in ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com']:
                if risk_level != "High":
                    risk_level = "Medium"
                risk_factors.append("Public email provider")
                        
            risk_indicators.append({
                'user_id': row['user_id'],
                'risk_level': risk_level,
                'risk_factors': ', '.join(risk_factors) if risk_factors else 'None'
            })
                
        risk_df = pd.DataFrame(risk_indicators)
        risk_summary = risk_df['risk_level'].value_counts()
                
        # Risk level chart
        colors = {'High': '#dc3545', 'Medium': '#fd7e14', 'Low': '#28a745'}
        risk_chart = px.pie(values=risk_summary.values, names=risk_summary.index,
                            title='External User Risk Distribution',
                            color_discrete_map=colors,
                            hole=0.4) 
        risk_chart.update_traces(textinfo='percent+label', textfont_color='white') 
        risk_chart.update_layout(
            title_font_color='white', 
            font=dict(color='white') 
        )
        st.plotly_chart(risk_chart, use_container_width=True, key="external_risk_chart")
                
        # Merge risk data with main dataframe
        non_company_df = non_company_df.merge(risk_df, on='user_id', how='left')
                
        # Data quality warnings
        missing_users = compare_ids(non_company_df, users_cache_df, 'user_id', 'users_cache')
        if missing_users:
            st.warning(f"⚠️ {len(missing_users)} user_id(s) not found in users_cache")
                
        # Enhanced data table with risk indicators
        display_cols = [
            'first_name', 'last_name', 'email_address', 'vendor_name', 'created_at', 
            'last_active', 'risk_level', 'risk_factors', 'user_id'
        ]
        display_cols_filtered = [col for col in display_cols if col in non_company_df.columns]
                
        st.subheader("📋 External User Details")
                
        # Color code by risk level
        def highlight_risk(row):
            if 'risk_level' in row:
                if row['risk_level'] == 'High':
                    return ['background-color: #f8d7da; color: #721c24'] * len(row)
                elif row['risk_level'] == 'Medium':
                    return ['background-color: #fff3cd; color: #856404'] * len(row)
                elif row['risk_level'] == 'Low':
                    return ['background-color: #d4edda; color: #155724'] * len(row)
            return [''] * len(row)
                
        styled_df = non_company_df[display_cols_filtered].style.apply(highlight_risk, axis=1)
        st.dataframe(styled_df, use_container_width=True, height=400)
                
        # Export functionality
        csv = non_company_df[display_cols_filtered].to_csv(index=False)
        st.download_button(
            "📥 Export External Users to CSV", 
            csv, 
            file_name=f"external_users_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv", 
            mime="text/csv"
        )

# --- Enhanced Deactivation Audit Tab ---
with tabs[5]:
    st.header("📋 Deactivation Audit Trail")
        
    if DRY_RUN_MODE:
        st.markdown("""
        <div class="warning-box">
            🧪 <strong>DRY RUN MODE is ENABLED.</strong> Actual user deactivations are not being performed.
        </div>
        """, unsafe_allow_html=True)
        
    if not all_deactivated_users_logs:
        st.info("ℹ️ No deactivated user log files found. Please run `user_deactivation_pipeline.py`.")
    else:
        # File selector
        selected_deact_log_file = st.selectbox(
            "📁 Select Deactivation Log File", 
            all_deactivated_users_logs, 
            index=0, 
            format_func=lambda x: f"{os.path.basename(x)} ({datetime.fromtimestamp(os.path.getmtime(x)).strftime('%Y-%m-%d %H:%M')})"
        )
            
        df = load_csv_file(selected_deact_log_file)
            
        # --- Fix project_id format in df to prevent cache lookup issues ---
        if not df.empty:
            df = fix_project_id_format(df)
            
        if df.empty:
            st.warning(f"No deactivation records found in selected log file: {os.path.basename(selected_deact_log_file)}.")
        else:
            df = enrich_with_user_project_vendor(df)
                        
            # Deactivation metrics
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Total Attempted", len(df))
            with col2:
                successful = len(df[df['deactivation_status'] == 'success']) if 'deactivation_status' in df.columns else 0
                st.metric("Successful", successful)
            with col3:
                failed = len(df[df['deactivation_status'] == 'error']) if 'deactivation_status' in df.columns else 0
                st.metric("Failed", failed)
            with col4:
                success_rate = (successful / len(df) * 100) if len(df) > 0 else 0
                st.metric("Success Rate", f"{success_rate:.1f}%")
                        
            # Status distribution
            if 'deactivation_status' in df.columns:
                st.subheader("📊 Deactivation Results")
                                
                status_counts = df['deactivation_status'].value_counts()
                colors = {
                    'success': '#28a745',
                    'error': '#dc3545', 
                    'dry_run': '#ffc107'
                }
                                
                status_chart = px.pie(values=status_counts.values, names=status_counts.index,
                                        title='Deactivation Status Distribution',
                                        color_discrete_map=colors,
                                        hole=0.4) 
                status_chart.update_traces(textinfo='percent+label', textfont_color='white') 
                status_chart.update_layout(
                    title_font_color='white', 
                    font=dict(color='white') 
                )
                st.plotly_chart(status_chart, use_container_width=True, key="deactivation_status_chart")
                        
            # Error analysis
            if 'error_message' in df.columns:
                errors_df = df[df['error_message'].notna() & (df['error_message'] != '')]
                if not errors_df.empty:
                    st.subheader("❌ Error Analysis")
                    error_counts = errors_df['error_message'].value_counts().head(5)
                                        
                    if not error_counts.empty:
                        error_chart = px.bar(x=error_counts.values, y=error_counts.index,
                                            orientation='h',
                                            title='Top Error Messages',
                                            color_discrete_sequence=['#dc3545'])
                        error_chart.update_layout(
                            title_font_color='white', 
                            xaxis=dict(title='Number of Occurrences', title_font_color='white', tickfont_color='white'), 
                            yaxis=dict(title='Error Message', categoryorder='total ascending', title_font_color='white', tickfont_color='white')
                        )
                        st.plotly_chart(error_chart, use_container_width=True, key="deactivation_error_chart")
                        
            # Data quality warnings
            missing_users = compare_ids(df, users_cache_df, 'user_id', 'users_cache')
            if missing_users:
                st.warning(f"⚠️ {len(missing_users)} user_id(s) not found in users_cache")
                        
            # Enhanced data table
            display_cols = [
                'first_name', 'last_name', 'email_address', 'vendor_name', 'project_name', 
                'last_activity_date', 'deactivation_status', 'error_message', 'user_id'
            ]
            display_cols_filtered = [col for col in display_cols if col in df.columns]
                        
            st.subheader("📋 Deactivation Log Details")
                        
            # Color code by status 
            def highlight_deactivation_status(row): 
                if 'deactivation_status' in row:
                    if row['deactivation_status'] == 'success':
                        return ['background-color: #d4edda; color: #155724'] * len(row)
                    elif row['deactivation_status'] == 'error':
                        return ['background-color: #f8d7da; color: #721c24'] * len(row)
                    elif row['deactivation_status'] == 'dry_run':
                        return ['background-color: #fff3cd; color: #856404'] * len(row)
                return [''] * len(row)
                        
            styled_df = df[display_cols_filtered].style.apply(highlight_deactivation_status, axis=1)
            st.dataframe(styled_df, use_container_width=True, height=400)
                        
            # Export functionality
            csv = df[display_cols_filtered].to_csv(index=False)
            st.download_button(
                "📥 Export Deactivation Log to CSV", 
                csv, 
                file_name=f"deactivation_log_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv", 
                mime="text/csv"
            )

# --- Footer ---
st.markdown("---")
st.markdown("""<div style="text-align: center; color: #666; padding: 2rem 0;">
    <p><strong>Compass Governance Dashboard</strong> • Phase 10 Inactive User Automation</p>
    <p>For audit and compliance use only • Last updated: {}</p></div>""".format(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')), unsafe_allow_html=True)