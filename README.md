# Jarvis AutoTyper

A high-performance Windows 10/11 desktop text automation application designed to read text from Notepad (including Windows 11 modern tabbed Notepad and classic Notepad), text files, or the clipboard, and type it into any target application at a configurable rate using a dual-engine Unicode keystroke injector.

## Features
- **Dual-Engine Keystroke Injector**:
  - Primary: `pynput` keyboard controller with full cross-application support (browsers, Electron apps, games, terminals, code editors).
  - Secondary / Native: Direct 64-bit and 32-bit Win32 `SendInput` with virtual keys (`VK_RETURN` for Enter, `VK_TAB` for Tab) and surrogate pairs for emojis.
- **Windows 11 & 10 Notepad Support**:
  - Full UI Automation support for modern Windows 11 Notepad (`Document` / `RichEditD2DPT`) and classic Notepad (`Edit`).
  - Read unsaved documents directly without saving to disk first.
  - **Choose Notepad window**: Dedicated selector dialog when multiple Notepad documents or windows are open.
- **Target Application Auto-Focus**:
  - Pick a target window (e.g. Chrome, Discord, Word, Notepad) from the window picker.
  - Jarvis automatically restores and focuses the destination application right before typing starts.
- **Dynamic Control**:
  - **Skip line**: Skip the remaining characters of the current line during active typing.
  - **Pause & Resume**: Continue typing exactly where you left off.
  - **Emergency Stop**: Instantly halt typing.
  - **Global Hotkeys**:
    - Ctrl+Shift+Space: Start typing (with silent 5s countdown)
    - F9 / Ctrl+Shift+P: Pause / Resume
    - F10 / Ctrl+Shift+X: Emergency stop
- **Customizable Speed & Human Simulation**:
  - Configurable CPS (Characters per second, default 60).
  - Line delay after newline characters.
  - Optional human-like speed jitter (±15% natural speed variation).
- **Execution Profiles**:
  - Save and load named configuration profiles (`Default`, `Fast`, `Human Natural`, `Code Block`, or custom).
- **Modern Jarvis Dark UI**:
  - Sleek dark theme with live character/word/line counters, color-coded status badges, and progress bar.

## Requirements
- Windows 10 or Windows 11 (64-bit or 32-bit)
- Python 3.10+ (Check "Add Python to PATH" during installation)

## How to Run

### Quick Start (Recommended)
Double-click `run_jarvis.bat`. It will create the virtual environment, install dependencies, and launch Jarvis AutoTyper.

### From Command Prompt / Terminal
```bat
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python jarvis_autotyper.py
```

## Build a Standalone Executable (.exe)
Double-click `build_exe.bat` or run:
```bat
build_exe.bat
```
The standalone single-file `.exe` will be generated in `dist\JarvisAutoTyper.exe`.

## Usage
1. Open the text you wish to type (or paste it into Jarvis, or click **Read active Notepad**).
2. Configure your speed (Characters/sec) and countdown timer.
3. (Optional) Click **Target window...** to select which application window to type into automatically.
    - Ctrl+Shift+Space: Start typing (with silent 5s countdown)
5. During the countdown, focus your target text field (if not using auto-target).
    - F9 / Ctrl+Shift+P: Pause / Resume

## Security & System Notes
- **UIPI (User Interface Privilege Isolation)**: Windows prevents standard-user applications from sending keystrokes to Elevated (Run as Administrator) applications. If you need to type into an administrator console or elevated application, launch Jarvis AutoTyper as Administrator.
