@echo off
chcp 65001 >nul
python -X utf8 "%~dp0docx_to_txt.py" %*
pause
