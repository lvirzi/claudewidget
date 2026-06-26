@echo off
echo ============================================
echo  Claude Widget — Build Script
echo ============================================
echo.

echo [1/3] Installing dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install failed.
    pause & exit /b 1
)

echo.
echo [2/3] Building executable...
python -m PyInstaller --onefile --windowed --name ClaudeWidget claude_widget.py 2>nul
if errorlevel 1 (
    python "C:\Python312\Lib\site-packages\PyInstaller\__main__.py" ^
        --onefile --windowed --name ClaudeWidget claude_widget.py
)

echo.
echo [3/3] Done!
echo.
echo ► Executable: dist\ClaudeWidget.exe
echo ► To auto-start with Windows, create a shortcut to ClaudeWidget.exe
echo   in %%APPDATA%%\Microsoft\Windows\Start Menu\Programs\Startup
echo.
pause
