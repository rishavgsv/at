@echo off
cd /d "%~dp0"
if not exist .venv (
  py -3 -m venv .venv
)
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pyinstaller --noconfirm --clean --onefile --windowed --collect-all pynput --collect-all pywinauto --name JarvisAutoTyper jarvis_autotyper.py
echo.
echo Build complete. Find dist\JarvisAutoTyper.exe
pause
