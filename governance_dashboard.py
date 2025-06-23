import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import datetime
import pytz

# Paths
LOGS_BASE = Path("./data/logs")
REPORTS_FILE = Path("./data/reports/inactive_users_report.csv")
DEACTIVATION_LOGS_BASE = Path("./data/deactivation_logs")
AUDIT_LOGS_BASE = Path("./data/audit_logs")
ERROR_LOGS_BASE = Path("./data/error_logs")

st.set_page_config(page_title="Compass Governance Dashboard", layout="wide")

# Utility functions
def load_activity_logs():
    records = []
    if not LOGS_BASE.exists():
        return pd.DataFrame()
    for day_dir in sorted(LOGS_BASE.iterdir()):
        if day_dir.is_dir() and len(day_dir.name) == 10:
            csv_file = day_dir / "activity_log.csv"
            if csv_file.exists():
                try:
                    df = pd.read_csv(csv_file)
                    df['log_date'] = day_dir.name
                    records.append(df)
                except Exception:
                    continue
    if records:
        return pd.concat(records, ignore_index=True)
    return pd.DataFrame()

def load_inactive_users():
    if REPORTS_FILE.exists():
        try:
            return pd.read_csv(REPORTS_FILE)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()

def load_deactivation_logs():
    records = []
    if not DEACTIVATION_LOGS_BASE.exists():
        return pd.DataFrame()
    for day_dir in sorted(DEACTIVATION_LOGS_BASE.iterdir()):
        if day_dir.is_dir() and len(day_dir.name) == 10:
            csv_file = day_dir / "deactivation_log.csv"
            if csv_file.exists():
                try:
                    df = pd.read_csv(csv_file)
                    df['log_date'] = day_dir.name
                    records.append(df)
                except Exception:
                    continue
    if records:
        return pd.concat(records, ignore_index=True)
    return pd.DataFrame()

def load_audit_logs():
    records = []
    if not AUDIT_LOGS_BASE.exists():
        return pd.DataFrame()
    for day_dir in sorted(AUDIT_LOGS_BASE.iterdir(), reverse=True):
        if day_dir.is_dir() and len(day_dir.name) == 10:
            csv_file = day_dir / "audit_log.csv"
            if csv_file.exists():
                try:
                    df = pd.read_csv(csv_file)
                    df['log_date'] = day_dir.name
                    records.append(df)
                except Exception:
                    continue
    if records:
        return pd.concat(records, ignore_index=True)
    return pd.DataFrame()

def load_error_logs():
    records = []
    if not ERROR_LOGS_BASE.exists():
        return pd.DataFrame()
    for day_dir in sorted(ERROR_LOGS_BASE.iterdir(), reverse=True):
        if day_dir.is_dir() and len(day_dir.name) == 10:
            csv_file = day_dir / "error_log.csv"
            if csv_file.exists():
                try:
                    df = pd.read_csv(csv_file)
                    df['log_date'] = day_dir.name
                    records.append(df)
                except Exception:
                    continue
    if records:
        return pd.concat(records, ignore_index=True)
    return pd.DataFrame()

def get_last_successful_run(df, operation):
    if df.empty:
        return "N/A"
    filtered = df[df['operation'] == operation]
    if filtered.empty:
        return "N/A"
    # Use the latest timestamp
    try:
        return pd.to_datetime(filtered['timestamp']).max().strftime('%Y-%m-%d %H:%M:%S UTC')
    except Exception:
        return "N/A"

def get_data_refresh_time():
    return datetime.now(pytz.UTC).strftime('%Y-%m-%d %H:%M:%S UTC')

# Load all data upfront
data_refresh_time = get_data_refresh_time()
activity_logs = load_activity_logs()
inactive_users = load_inactive_users()
deactivation_logs = load_deactivation_logs()
audit_logs = load_audit_logs()
error_logs = load_error_logs()

# --- Dashboard Layout ---
st.title("Compass Governance Dashboard")
st.caption("Internal Use Only — All data is read-only and refreshed on reload.")
st.markdown(f"**Last Data Refresh:** {data_refresh_time}")

# 1️⃣ Summary Statistics
st.header("1️⃣ Summary Statistics")
col1, col2, col3, col4, col5 = st.columns(5)

with col1:
    st.metric("Total Webhook Events", f"{len(activity_logs):,}" if not activity_logs.empty else "0")
with col2:
    st.metric("Total Unique Users", f"{activity_logs['user_id'].nunique():,}" if not activity_logs.empty and 'user_id' in activity_logs else "0")
with col3:
    st.metric("Inactive Users (>12mo)", f"{len(inactive_users):,}" if not inactive_users.empty else "0")
with col4:
    last_etl = get_last_successful_run(audit_logs, "Inactivity Analysis Pipeline")
    st.metric("Last ETL Run", last_etl)
with col5:
    last_deact = get_last_successful_run(audit_logs, "User Deactivation Pipeline")
    st.metric("Last Deactivation Run", last_deact)

# 2️⃣ Activity Timeline
st.header("2️⃣ Activity Timeline")
if not activity_logs.empty and 'log_date' in activity_logs:
    timeline = activity_logs.groupby('log_date').size().reset_index(name='event_count')
    st.line_chart(timeline.rename(columns={'log_date': 'Date'}).set_index('Date')['event_count'])
else:
    st.info("No activity log data available.")

# 3️⃣ User Inactivity Aging
st.header("3️⃣ User Inactivity Aging")
if not inactive_users.empty and 'inactivity_days' in inactive_users:
    bins = [0, 90, 180, 365, 10000]
    labels = ["Active < 3 months", "Inactive 3–6 months", "Inactive 6–12 months", "Inactive > 12 months"]
    inactive_users['aging_bucket'] = pd.cut(inactive_users['inactivity_days'], bins=bins, labels=labels, right=False)
    bucket_counts = inactive_users['aging_bucket'].value_counts().reindex(labels, fill_value=0)
    st.bar_chart(bucket_counts)
    st.dataframe(bucket_counts.reset_index().rename(columns={'index': 'Aging Bucket', 'aging_bucket': 'User Count'}), use_container_width=True)
else:
    st.info("No inactivity report data available.")

# 4️⃣ Deactivation Candidate List
st.header("4️⃣ Deactivation Candidate List")
if not inactive_users.empty:
    st.dataframe(inactive_users[['user_id', 'last_activity_date', 'inactivity_days', 'status']], use_container_width=True)
else:
    st.info("No deactivation candidate data available.")

# 5️⃣ Audit & Error Logs
st.header("5️⃣ Audit & Error Logs")
col_audit, col_error = st.columns(2)

with col_audit:
    st.subheader("Recent Audit Log Entries")
    if not audit_logs.empty:
        st.dataframe(audit_logs.sort_values('timestamp', ascending=False).head(20), use_container_width=True)
    else:
        st.info("No audit log data available.")

with col_error:
    st.subheader("Recent Error Log Entries")
    if not error_logs.empty:
        st.dataframe(error_logs.sort_values('timestamp', ascending=False).head(20), use_container_width=True)
    else:
        st.info("No error log data available.") 