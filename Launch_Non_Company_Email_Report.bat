@echo off
REM Launch the Non-Company Email Users Report Generator

REM Activate the virtual environment if needed
call .\venv\Scripts\activate

REM Run the report generator script
python non_company_email_report_generator.py

pause 