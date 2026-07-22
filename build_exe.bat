@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m pip install pyinstaller
  py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --name SSNE_AI_Console_v1.3.2 main.py
) else (
  python -m pip install pyinstaller
  python -m PyInstaller --noconfirm --clean --onefile --windowed --name SSNE_AI_Console_v1.3.2 main.py
)
if errorlevel 1 pause & exit /b 1
echo.
echo 已生成 dist\SSNE_AI_Console_v1.3.2.exe
pause
