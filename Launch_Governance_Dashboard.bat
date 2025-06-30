@echo off
echo Starting Procore Governance Dashboard...
cd /d "C:\my_repos\procore-inactive-user-automation"
call venv\Scripts\activate.bat
echo Dashboard starting at http://localhost:8501
streamlit run governance_dashboard.py
pause
