"""Jarvis AutoTyper: Advanced Unicode Desktop Text Automation for Windows."""

from __future__ import annotations

import ctypes
import json
import logging
import os
import queue
import random
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from pynput import keyboard
except ImportError:
    keyboard = None

try:
    from pywinauto import Desktop
except ImportError:
    Desktop = None


APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "JarvisAutoTyper"
SETTINGS_FILE = APP_DIR / "settings.json"
LOG_FILE = APP_DIR / "jarvis.log"


def get_logger() -> logging.Logger:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("jarvis_autotyper")
    if not logger.handlers:
        handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


LOGGER = get_logger()


# ============================================================================
# Win32 SendInput Structures (Compatible with 32-bit and 64-bit Windows)
# ============================================================================

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", INPUT_UNION),
    ]


class Win32PhysicalInput:
    """Send keyboard events using true physical virtual-key and hardware scan codes.

    This ensures that characters like uppercase 'B' (ASCII 66) send their true physical
    keyboard scan code (scan code 48 on QWERTY), and NEVER emit raw scan code 66.
    On Acer Predator, ASUS, Lenovo, and HP laptops, raw scan code 66 is hardware-hooked
    by OEM utility software (such as PredatorSense / Armoury Crate) to trigger the
    F8 Microphone Toggle!
    """

    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    VK_SHIFT = 0x10
    VK_CONTROL = 0x11
    VK_MENU = 0x12  # Alt
    VK_RETURN = 0x0D
    VK_TAB = 0x09

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Win32 SendInput is only supported on Windows.")
        self.user32 = ctypes.windll.user32
        self._send_input = self.user32.SendInput
        self._send_input.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self._send_input.restype = wintypes.UINT

        self._vk_key_scan = self.user32.VkKeyScanW
        self._vk_key_scan.argtypes = (wintypes.WCHAR,)
        self._vk_key_scan.restype = ctypes.c_short

        self._map_virtual_key = self.user32.MapVirtualKeyW
        self._map_virtual_key.argtypes = (wintypes.UINT, wintypes.UINT)
        self._map_virtual_key.restype = wintypes.UINT

    def send_char(self, char: str) -> None:
        if not char:
            return

        events = []

        if char == "\n":
            scan = self._map_virtual_key(self.VK_RETURN, 0)
            events.extend([
                INPUT(type=self.INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=self.VK_RETURN, wScan=scan, dwFlags=0, time=0, dwExtraInfo=0))),
                INPUT(type=self.INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=self.VK_RETURN, wScan=scan, dwFlags=self.KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))),
            ])
        elif char == "\t":
            scan = self._map_virtual_key(self.VK_TAB, 0)
            events.extend([
                INPUT(type=self.INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=self.VK_TAB, wScan=scan, dwFlags=0, time=0, dwExtraInfo=0))),
                INPUT(type=self.INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=self.VK_TAB, wScan=scan, dwFlags=self.KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))),
            ])
        else:
            # Check if this character maps to a keyboard key in the current Windows layout
            res = -1
            if len(char) == 1:
                try:
                    res = self._vk_key_scan(char)
                except Exception:
                    res = -1

            if res != -1:
                vk = res & 0xFF
                shift_state = (res >> 8) & 0xFF
                # Physical hardware scan code (e.g. 48 for 'B', 30 for 'A' - NEVER 66/F8!)
                scan = self._map_virtual_key(vk, 0)

                needs_shift = bool(shift_state & 1)
                needs_ctrl = bool(shift_state & 2)
                needs_alt = bool(shift_state & 4)

                shift_scan = self._map_virtual_key(self.VK_SHIFT, 0)
                ctrl_scan = self._map_virtual_key(self.VK_CONTROL, 0)
                alt_scan = self._map_virtual_key(self.VK_MENU, 0)

                # Press modifiers
                if needs_ctrl:
                    events.append(INPUT(type=self.INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=self.VK_CONTROL, wScan=ctrl_scan, dwFlags=0, time=0, dwExtraInfo=0))))
                if needs_alt:
                    events.append(INPUT(type=self.INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=self.VK_MENU, wScan=alt_scan, dwFlags=0, time=0, dwExtraInfo=0))))
                if needs_shift:
                    events.append(INPUT(type=self.INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=self.VK_SHIFT, wScan=shift_scan, dwFlags=0, time=0, dwExtraInfo=0))))

                # Key down and up with TRUE physical keyboard scan code
                events.append(INPUT(type=self.INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=0, time=0, dwExtraInfo=0))))
                events.append(INPUT(type=self.INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=self.KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))))

                # Release modifiers in reverse order
                if needs_shift:
                    events.append(INPUT(type=self.INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=self.VK_SHIFT, wScan=shift_scan, dwFlags=self.KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))))
                if needs_alt:
                    events.append(INPUT(type=self.INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=self.VK_MENU, wScan=alt_scan, dwFlags=self.KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))))
                if needs_ctrl:
                    events.append(INPUT(type=self.INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=self.VK_CONTROL, wScan=ctrl_scan, dwFlags=self.KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))))
            else:
                # Fallback for unicode characters not present on current keyboard (e.g. emojis)
                for ch in char:
                    code = ord(ch)
                    if code <= 0xFFFF:
                        units = [code]
                    else:
                        code -= 0x10000
                        units = [0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)]
                    for unit in units:
                        events.extend([
                            INPUT(type=self.INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=0, wScan=unit, dwFlags=self.KEYEVENTF_UNICODE, time=0, dwExtraInfo=0))),
                            INPUT(type=self.INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=0, wScan=unit, dwFlags=self.KEYEVENTF_UNICODE | self.KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))),
                        ])

        if events:
            arr = (INPUT * len(events))(*events)
            res = self._send_input(len(events), arr, ctypes.sizeof(INPUT))
            if res != len(events):
                err = ctypes.GetLastError()
                if err == 5:
                    LOGGER.warning("SendInput returned ERROR_ACCESS_DENIED (UIPI barrier)")
                elif err != 0:
                    LOGGER.warning("SendInput warning: code %s", err)

    def send_dedent(self) -> None:
        """Send Shift+Tab to dedent by 1 indent level in code editors."""
        shift_scan = self._map_virtual_key(self.VK_SHIFT, 0)
        tab_scan = self._map_virtual_key(self.VK_TAB, 0)
        events = [
            INPUT(type=self.INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=self.VK_SHIFT, wScan=shift_scan, dwFlags=0, time=0, dwExtraInfo=0))),
            INPUT(type=self.INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=self.VK_TAB, wScan=tab_scan, dwFlags=0, time=0, dwExtraInfo=0))),
            INPUT(type=self.INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=self.VK_TAB, wScan=tab_scan, dwFlags=self.KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))),
            INPUT(type=self.INPUT_KEYBOARD, u=INPUT_UNION(ki=KEYBDINPUT(wVk=self.VK_SHIFT, wScan=shift_scan, dwFlags=self.KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))),
        ]
        arr = (INPUT * len(events))(*events)
        self._send_input(len(events), arr, ctypes.sizeof(INPUT))


class KeyboardTyper:
    """High-reliability typing engine prioritizing physical scan codes to avoid OEM laptop conflicts."""

    def __init__(self) -> None:
        self.win32_input = None
        if os.name == "nt":
            try:
                self.win32_input = Win32PhysicalInput()
            except Exception as exc:
                LOGGER.warning("Win32PhysicalInput init failed: %s", exc)

        self.pynput_controller = None
        if keyboard is not None and self.win32_input is None:
            try:
                self.pynput_controller = keyboard.Controller()
            except Exception as exc:
                LOGGER.warning("pynput Controller init failed: %s", exc)

    def type_char(self, char: str) -> None:
        # Prioritize Win32PhysicalInput on Windows: guarantees hardware scan codes (no F8 mic toggles)
        if self.win32_input is not None:
            self.win32_input.send_char(char)
            return

        # Fallback to pynput on non-Windows platforms
        if self.pynput_controller is not None:
            if char == "\n":
                self.pynput_controller.press(keyboard.Key.enter)
                self.pynput_controller.release(keyboard.Key.enter)
            elif char == "\t":
                self.pynput_controller.press(keyboard.Key.tab)
                self.pynput_controller.release(keyboard.Key.tab)
            else:
                self.pynput_controller.type(char)
            return

        raise RuntimeError("No working keyboard typing backend available.")

    def dedent(self) -> None:
        """Dedent one level in code editors (Shift+Tab)."""
        if self.win32_input is not None:
            self.win32_input.send_dedent()
        elif self.pynput_controller is not None:
            try:
                with self.pynput_controller.pressed(keyboard.Key.shift):
                    self.pynput_controller.press(keyboard.Key.tab)
                    self.pynput_controller.release(keyboard.Key.tab)
            except Exception:
                pass


# ============================================================================
# Windows Window Enumeration and Management Helpers
# ============================================================================

def get_open_windows() -> list[tuple[int, str]]:
    """Return a list of visible application windows (hwnd, title) using both UIA and Win32."""
    if os.name != "nt":
        return []
    results: list[tuple[int, str]] = []
    seen = set()

    # 1. Desktop UIA windows (Finds modern XAML, Chrome, Edge, Electron apps)
    if Desktop is not None:
        try:
            for w in Desktop(backend="uia").windows():
                try:
                    title = w.window_text().strip()
                    hwnd = getattr(w.element_info, "handle", None)
                    if title and hwnd and hwnd not in seen and title not in (
                        "Program Manager", "Settings", "Default IME", "MSCTFIME UI",
                        "Windows Input Experience", "Jarvis AutoTyper",
                    ):
                        seen.add(hwnd)
                        results.append((hwnd, title))
                except Exception:
                    pass
        except Exception:
            pass

    # 2. Win32 EnumWindows (Finds classic Win32 applications)
    try:
        user32 = ctypes.windll.user32
        def enum_proc(hwnd: int, _: int) -> bool:
            if hwnd in seen or not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()
            ignored = {
                "Program Manager", "Settings", "Default IME", "MSCTFIME UI",
                "Windows Input Experience", "Jarvis AutoTyper",
            }
            if title and title not in ignored:
                seen.add(hwnd)
                results.append((hwnd, title))
            return True

        wndproc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(enum_proc)
        user32.EnumWindows(wndproc, 0)
    except Exception:
        pass

    return results


def activate_window(hwnd: int) -> bool:
    """Restore and bring the specified window to the foreground."""
    if os.name != "nt" or not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        else:
            user32.ShowWindow(hwnd, 5)  # SW_SHOW

        cur_thread = kernel32.GetCurrentThreadId()
        fore_hwnd = user32.GetForegroundWindow()
        fore_thread = user32.GetWindowThreadProcessId(fore_hwnd, None) if fore_hwnd else 0
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)

        if fore_thread and fore_thread != cur_thread:
            user32.AttachThreadInput(cur_thread, fore_thread, True)
        if target_thread and target_thread != cur_thread:
            user32.AttachThreadInput(cur_thread, target_thread, True)

        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)

        if fore_thread and fore_thread != cur_thread:
            user32.AttachThreadInput(cur_thread, fore_thread, False)
        if target_thread and target_thread != cur_thread:
            user32.AttachThreadInput(cur_thread, target_thread, False)
        return True
    except Exception as exc:
        LOGGER.warning("Could not activate window %s: %s", hwnd, exc)
        return False


# ============================================================================
# Notepad UIA Inspection Helper (Supports Windows 10 & 11)
# ============================================================================

def get_notepad_windows() -> list[tuple[object, str]]:
    """Find all open Notepad windows using UI Automation."""
    if Desktop is None:
        return []
    try:
        desktop = Desktop(backend="uia")
        windows = desktop.windows(title_re=".*Notepad.*")
        items = []
        for w in windows:
            try:
                title = w.window_text() or "Untitled - Notepad"
                items.append((w, title))
            except Exception:
                pass
        return items
    except Exception as exc:
        LOGGER.warning("Failed searching Notepad windows: %s", exc)
        return []


def extract_notepad_text(window: object) -> str:
    """Extract text from a Notepad window (handles classic Edit and modern Document)."""
    # 1. Search descendants for Document (Win11) or Edit (Win10)
    for child in window.descendants():
        ctype = child.element_info.control_type
        cname = child.element_info.class_name
        if ctype in ("Document", "Edit") or cname in ("RichEditD2DPT", "Edit"):
            # Try TextPattern (Modern Win11 Notepad)
            try:
                pattern = child.iface_text
                text = pattern.DocumentRange.GetText(-1)
                if text:
                    return text
            except Exception:
                pass

            # Try window_text
            try:
                text = child.window_text()
                if text:
                    return text
            except Exception:
                pass

            # Try ValuePattern
            try:
                val = child.legacy_properties().get("Value")
                if val:
                    return val
            except Exception:
                pass

    # 2. Try window's own text if no child matched
    try:
        return window.window_text()
    except Exception:
        return ""


# ============================================================================
# Main Application UI & Logic
# ============================================================================

class AutoTyperApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Jarvis AutoTyper")

        # Responsive window sizing & automatic screen centering
        self.root.update_idletasks()
        w, h = 980, 740
        ws = self.root.winfo_screenwidth()
        hs = self.root.winfo_screenheight()
        x = max(0, int((ws / 2) - (w / 2)))
        y = max(0, int((hs / 2) - (h / 2)))
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(760, 520)

        # Threading & Control events
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.skip_line_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()

        self.hotkeys = None
        self.typer = KeyboardTyper()
        self.profile_data = self._load_settings()

        # Target window settings
        self.target_hwnd: int | None = None
        self.last_external_hwnd: int | None = None
        self.last_external_title: str = ""
        self.target_name = tk.StringVar(value="Currently Focused Window (Auto-switch)")
        self.auto_minimize_var = tk.BooleanVar(value=True)
        self.smart_indent_var = tk.BooleanVar(value=True)

        # Stats
        self.stats_var = tk.StringVar(value="0 characters • 0 words • 0 lines")

        self._configure_styles()
        self._build_ui()
        self._start_hotkeys()

        self.root.after(100, self._poll_events)
        self.root.after(300, self._track_external_window)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _load_settings(self) -> dict:
        default_profiles = {
            "Default": {"cps": 60, "countdown": 5, "line_delay": 0.05, "jitter": False, "confirm": False, "smart_indent": True},
            "Fast": {"cps": 120, "countdown": 3, "line_delay": 0.0, "jitter": False, "confirm": False, "smart_indent": True},
            "Human Natural": {"cps": 45, "countdown": 5, "line_delay": 0.2, "jitter": True, "confirm": False, "smart_indent": True},
            "Code Block": {"cps": 70, "countdown": 5, "line_delay": 0.1, "jitter": False, "confirm": False, "smart_indent": True},
        }
        merged = dict(default_profiles)
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if "profiles" in data and isinstance(data["profiles"], dict):
                merged.update(data["profiles"])
        except (OSError, json.JSONDecodeError):
            pass
        return {"profiles": merged}

    def _configure_styles(self) -> None:
        """Apply modern, high-contrast dark theme."""
        self.root.configure(bg="#0b0f19")
        style = ttk.Style(self.root)

        # Base style config
        style.theme_use("clam")

        # Palette
        bg_dark = "#0b0f19"
        bg_card = "#111827"
        bg_card_border = "#1f2937"
        text_light = "#f8fafc"
        text_muted = "#94a3b8"
        cyan_accent = "#0284c7"

        style.configure(".", background=bg_dark, foreground=text_light, font=("Segoe UI", 10))
        style.configure("TFrame", background=bg_dark)
        style.configure("Card.TFrame", background=bg_card)
        style.configure("TLabel", background=bg_dark, foreground=text_light, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=bg_dark, foreground=text_muted, font=("Segoe UI", 9))
        style.configure("Card.TLabel", background=bg_card, foreground=text_light, font=("Segoe UI", 10))
        style.configure("CardMuted.TLabel", background=bg_card, foreground=text_muted, font=("Segoe UI", 9))
        style.configure("Header.TLabel", background=bg_dark, foreground="#38bdf8", font=("Segoe UI", 15, "bold"))

        style.configure("TLabelframe", background=bg_card, bordercolor=bg_card_border)
        style.configure("TLabelframe.Label", background=bg_card, foreground="#38bdf8", font=("Segoe UI", 9, "bold"))

        # Default Buttons
        style.configure(
            "TButton",
            background="#1e293b",
            foreground="#f8fafc",
            borderwidth=1,
            bordercolor="#334155",
            focusthickness=0,
            padding=(9, 5),
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "TButton",
            background=[("active", "#334155"), ("disabled", "#0f172a")],
            foreground=[("disabled", "#475569")],
            bordercolor=[("active", "#475569"), ("disabled", "#1e293b")],
        )

        # Accent Primary Button (Blue/Cyan)
        style.configure(
            "Primary.TButton",
            background="#0284c7",
            foreground="#ffffff",
            borderwidth=0,
            padding=(14, 8),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#0369a1"), ("disabled", "#1e293b")],
            foreground=[("disabled", "#64748b")],
        )

        # Pause / Warning Button (Amber)
        style.configure(
            "Warning.TButton",
            background="#d97706",
            foreground="#ffffff",
            borderwidth=0,
            padding=(10, 7),
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Warning.TButton",
            background=[("active", "#b45309"), ("disabled", "#1e293b")],
            foreground=[("disabled", "#64748b")],
        )

        # Danger Button (Rose Red)
        style.configure(
            "Danger.TButton",
            background="#e11d48",
            foreground="#ffffff",
            borderwidth=0,
            padding=(10, 7),
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#be123c"), ("disabled", "#1e293b")],
            foreground=[("disabled", "#64748b")],
        )

        # Progressbar
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor="#111827",
            background="#06b6d4",
            lightcolor="#06b6d4",
            darkcolor="#06b6d4",
            bordercolor="#1f2937",
            thickness=8,
        )

        # Checkbutton
        style.configure("TCheckbutton", background=bg_card, foreground=text_light, font=("Segoe UI", 9))
        style.map(
            "TCheckbutton",
            background=[("active", bg_card)],
            indicatorcolor=[("selected", "#06b6d4"), ("pressed", "#0891b2"), ("!selected", "#0b0f19")],
        )

        # Entry & Combobox
        style.configure("TEntry", fieldbackground="#0b0f19", foreground="#f8fafc", bordercolor="#334155", padding=4)
        style.configure("TCombobox", fieldbackground="#0b0f19", background="#1e293b", foreground="#f8fafc", bordercolor="#334155", padding=4)
        style.map("TCombobox", fieldbackground=[("readonly", "#0b0f19")], foreground=[("readonly", "#f8fafc")])

    def _build_ui(self) -> None:
        # Main shell container with neat margins
        main_container = ttk.Frame(self.root, padding=(16, 12, 16, 12))
        main_container.pack(fill="both", expand=True)

        # --------------------------------------------------------------------
        # TOP PANEL: Header & Target Banner & Quick Actions
        # --------------------------------------------------------------------
        top_panel = ttk.Frame(main_container)
        top_panel.pack(side="top", fill="x", pady=(0, 8))

        # Row 1: Header Brand + Live Status Badge
        header = ttk.Frame(top_panel)
        header.pack(fill="x", pady=(0, 8))

        title_frame = ttk.Frame(header)
        title_frame.pack(side="left")
        ttk.Label(title_frame, text="⚡ JARVIS AUTOTYPER", style="Header.TLabel").pack(anchor="w")
        ttk.Label(title_frame, text="Advanced Unicode & Desktop Keyboard Automation Engine", style="Muted.TLabel").pack(anchor="w")

        # Live Status Badge (pill format)
        self.badge_var = tk.StringVar(value="● READY")
        self.status_badge = tk.Label(
            header,
            textvariable=self.badge_var,
            bg="#064e3b",
            fg="#34d399",
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=5,
            relief="flat",
        )
        self.status_badge.pack(side="right")

        # Row 2: Target Application Banner & Action Toolbar
        banner_row = ttk.Frame(top_panel)
        banner_row.pack(fill="x")

        # Target App Card (Left side of banner)
        target_card = ttk.Frame(banner_row, style="Card.TFrame", padding=(10, 6))
        target_card.pack(side="left", fill="x", expand=True, padx=(0, 8))

        ttk.Label(target_card, text="🎯 Target:", style="CardMuted.TLabel").pack(side="left", padx=(0, 6))
        self.target_label = ttk.Label(target_card, textvariable=self.target_name, foreground="#38bdf8", font=("Segoe UI", 9, "bold"))
        self.target_label.pack(side="left", fill="x", expand=True)

        ttk.Button(target_card, text="Select Target...", command=self.select_target_window).pack(side="right", padx=(6, 0))

        # Quick Actions Toolbar (Right side of banner)
        toolbar = ttk.Frame(banner_row)
        toolbar.pack(side="right")

        ttk.Button(toolbar, text="📝 Notepad", command=self.read_notepad).pack(side="left", padx=2)
        ttk.Button(toolbar, text="📋 Paste", command=self.paste_clipboard).pack(side="left", padx=2)
        ttk.Button(toolbar, text="📂 Open File", command=self.open_file).pack(side="left", padx=2)
        ttk.Button(toolbar, text="🧹 Clear", command=self.clear_text).pack(side="left", padx=2)

        # --------------------------------------------------------------------
        # BOTTOM PANEL: Settings Card, Control Buttons, Progress, Status
        # IMPORTANT: Pack bottom_panel with side="bottom" BEFORE center_panel
        # so it is GUARANTEED to remain pinned and visible at any window height!
        # --------------------------------------------------------------------
        bottom_panel = ttk.Frame(main_container)
        bottom_panel.pack(side="bottom", fill="x", pady=(8, 0))

        # Status Message Bar (Absolute bottom)
        status_bar = ttk.Frame(bottom_panel)
        status_bar.pack(side="bottom", fill="x", pady=(4, 0))
        self.status = tk.StringVar(value="Ready. Switch to your document and press Ctrl+Shift+Space, or click 'Start Typing' below.")
        ttk.Label(status_bar, textvariable=self.status, font=("Segoe UI", 9), foreground="#94a3b8").pack(side="left")
        ttk.Label(status_bar, text="Pause: F9 | Stop: F10", font=("Segoe UI", 8, "bold"), foreground="#64748b").pack(side="right")

        # Sleek Progress Bar
        self.progress = ttk.Progressbar(bottom_panel, mode="determinate", style="Horizontal.TProgressbar")
        self.progress.pack(side="bottom", fill="x", pady=(0, 6))

        # Action Buttons Dock
        controls = ttk.Frame(bottom_panel)
        controls.pack(side="bottom", fill="x", pady=(0, 8))

        self.start_button = ttk.Button(controls, text="▶ Start Typing (Ctrl+Shift+Space)", style="Primary.TButton", command=self.start)
        self.start_button.pack(side="left", padx=(0, 8))

        self.pause_button = ttk.Button(controls, text="⏸ Pause (F9)", style="Warning.TButton", command=self.toggle_pause, state="disabled")
        self.pause_button.pack(side="left", padx=(0, 8))

        self.skip_button = ttk.Button(controls, text="⏭ Skip Line", command=self.skip_line, state="disabled")
        self.skip_button.pack(side="left", padx=(0, 8))

        self.stop_button = ttk.Button(controls, text="⏹ Emergency Stop (F10)", style="Danger.TButton", command=self.stop, state="disabled")
        self.stop_button.pack(side="right")

        # Execution Settings Card (Directly above the action buttons dock)
        settings_frame = ttk.LabelFrame(bottom_panel, text=" Execution Settings & Profiles ", padding=(12, 8))
        settings_frame.pack(side="bottom", fill="x", pady=(0, 8))

        self.cps = tk.StringVar(value="60")
        self.countdown = tk.StringVar(value="5")
        self.line_delay = tk.StringVar(value="0.05")
        self.jitter_var = tk.BooleanVar(value=False)
        self.confirm_var = tk.BooleanVar(value=False)
        self.profile = tk.StringVar(value="Default")

        # 3 Sections in Settings:
        # Left: Speed & Timing inputs
        timing_frame = ttk.Frame(settings_frame, style="Card.TFrame")
        timing_frame.pack(side="left", padx=(0, 16))

        ttk.Label(timing_frame, text="Speed (chars/s):", style="CardMuted.TLabel").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        ttk.Entry(timing_frame, textvariable=self.cps, width=7).grid(row=0, column=1, sticky="w", padx=4, pady=2)

        ttk.Label(timing_frame, text="Countdown (s):", style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", padx=4, pady=2)
        ttk.Entry(timing_frame, textvariable=self.countdown, width=7).grid(row=1, column=1, sticky="w", padx=4, pady=2)

        ttk.Label(timing_frame, text="Line Delay (s):", style="CardMuted.TLabel").grid(row=0, column=2, sticky="w", padx=(10, 4), pady=2)
        ttk.Entry(timing_frame, textvariable=self.line_delay, width=7).grid(row=0, column=3, sticky="w", padx=4, pady=2)

        # Middle: Behavioral checkbuttons
        check_frame = ttk.Frame(settings_frame, style="Card.TFrame")
        check_frame.pack(side="left", padx=(0, 16))

        ttk.Checkbutton(check_frame, text="Smart Code Indent (LeetCode/IDEs)", variable=self.smart_indent_var).pack(anchor="w", pady=1)
        ttk.Checkbutton(check_frame, text="Auto-minimize on Start", variable=self.auto_minimize_var).pack(anchor="w", pady=1)
        ttk.Checkbutton(check_frame, text="Human speed jitter (±15%)", variable=self.jitter_var).pack(anchor="w", pady=1)
        ttk.Checkbutton(check_frame, text="Confirm before starting", variable=self.confirm_var).pack(anchor="w", pady=1)

        # Right: Profile manager
        prof_frame = ttk.Frame(settings_frame, style="Card.TFrame")
        prof_frame.pack(side="right")

        ttk.Label(prof_frame, text="Profile Preset:", style="CardMuted.TLabel").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        self.profile_box = ttk.Combobox(
            prof_frame,
            textvariable=self.profile,
            values=list(self.profile_data["profiles"].keys()),
            width=14,
            state="readonly",
        )
        self.profile_box.grid(row=0, column=1, columnspan=2, sticky="w", padx=4, pady=2)
        self.profile_box.bind("<<ComboboxSelected>>", lambda _: self.load_profile())

        ttk.Button(prof_frame, text="Save", command=self.save_profile, width=6).grid(row=1, column=1, sticky="w", padx=4, pady=(4, 0))
        ttk.Button(prof_frame, text="Delete", command=self.delete_profile, width=7).grid(row=1, column=2, sticky="w", padx=4, pady=(4, 0))

        # --------------------------------------------------------------------
        # CENTER PANEL: Text Editor Area (Fills all remaining vertical space!)
        # --------------------------------------------------------------------
        center_panel = ttk.Frame(main_container)
        center_panel.pack(side="top", fill="both", expand=True)

        editor_header = ttk.Frame(center_panel)
        editor_header.pack(fill="x", pady=(0, 4))
        ttk.Label(editor_header, text="TEXT TO TYPE", font=("Segoe UI", 9, "bold"), foreground="#64748b").pack(side="left")
        ttk.Label(editor_header, textvariable=self.stats_var, style="Muted.TLabel").pack(side="right")

        editor_box = ttk.Frame(center_panel, style="Card.TFrame")
        editor_box.pack(fill="both", expand=True)

        scroll = ttk.Scrollbar(editor_box)
        scroll.pack(side="right", fill="y")

        self.text = tk.Text(
            editor_box,
            wrap="word",
            undo=True,
            font=("Consolas", 11),
            bg="#080c14",
            fg="#f1f5f9",
            insertbackground="#38bdf8",
            selectbackground="#1d4ed8",
            selectforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            padx=12,
            pady=10,
            yscrollcommand=scroll.set,
        )
        self.text.pack(fill="both", expand=True)
        scroll.config(command=self.text.yview)
        self.text.bind("<<Modified>>", self._on_text_modified)

    def _set_badge(self, state: str) -> None:
        if state == "ready":
            self.badge_var.set("● READY")
            self.status_badge.configure(bg="#064e3b", fg="#34d399")
        elif state == "countdown":
            self.badge_var.set("⏳ COUNTDOWN")
            self.status_badge.configure(bg="#78350f", fg="#fcd34d")
        elif state == "typing":
            self.badge_var.set("⚡ TYPING")
            self.status_badge.configure(bg="#0c4a6e", fg="#38bdf8")
        elif state == "paused":
            self.badge_var.set("⏸ PAUSED")
            self.status_badge.configure(bg="#78350f", fg="#fcd34d")
        elif state == "stopped":
            self.badge_var.set("⏹ STOPPED")
            self.status_badge.configure(bg="#7f1d1d", fg="#fca5a5")

    def _on_text_modified(self, _=None) -> None:
        if self.text.edit_modified():
            content = self.text.get("1.0", "end-1c")
            num_chars = len(content)
            num_words = len(content.split())
            num_lines = content.count("\n") + (1 if content else 0)
            self.stats_var.set(f"{num_chars:,} characters • {num_words:,} words • {num_lines:,} lines")
            self.text.edit_modified(False)

    def _start_hotkeys(self) -> None:
        if keyboard is None:
            LOGGER.warning("pynput is unavailable; global shortcuts are disabled")
            return
        try:
            self.hotkeys = keyboard.GlobalHotKeys({
                # Safe, conflict-free hotkey to start typing
                "<ctrl>+<shift>+<space>": lambda: self.root.after(0, lambda: self.start(from_hotkey=True)),

                # Pause & Stop hotkeys
                "<f9>": lambda: self.root.after(0, self.toggle_pause),
                "<ctrl>+<shift>+p": lambda: self.root.after(0, self.toggle_pause),
                "<f10>": lambda: self.root.after(0, self.stop),
                "<ctrl>+<shift>+x": lambda: self.root.after(0, self.stop),
            })
            self.hotkeys.start()
        except Exception as exc:
            LOGGER.warning("Global shortcuts could not be registered: %s", exc)

    def _track_external_window(self) -> None:
        """Periodically track the last active application window before Jarvis was focused."""
        try:
            if os.name == "nt":
                hwnd = ctypes.windll.user32.GetForegroundWindow()
                my_hwnd = self.root.winfo_id()
                if hwnd and hwnd != my_hwnd:
                    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                        title = buf.value.strip()
                        if title and "Jarvis" not in title and title not in ("Program Manager", "Settings", "Default IME"):
                            self.last_external_hwnd = hwnd
                            self.last_external_title = title
                            if not self.target_hwnd:
                                self.target_name.set(f"Auto-target: {title[:40]}")
        except Exception:
            pass
        self.root.after(300, self._track_external_window)

    def _numeric(self, variable: tk.StringVar, name: str, minimum: float) -> float:
        try:
            value = float(variable.get().strip())
        except ValueError as exc:
            raise ValueError(f"{name} must be a valid number.") from exc
        if value < minimum:
            raise ValueError(f"{name} must be at least {minimum}.")
        return value

    # ========================================================================
    # Typing Worker Execution
    # ========================================================================

    def start(self, from_hotkey: bool = False) -> None:
        if self.worker and self.worker.is_alive():
            return
        try:
            cps = self._numeric(self.cps, "Characters/sec", 0.1)
            countdown = self._numeric(self.countdown, "Countdown", 0)
            line_delay = self._numeric(self.line_delay, "Line delay", 0)
        except ValueError as exc:
            messagebox.showerror("Invalid Settings", str(exc))
            return

        content = self.text.get("1.0", "end-1c")
        if not content:
            messagebox.showwarning("No Text", "Please enter, paste, or import text to type.")
            return

        # Target window to activate
        target_hwnd = self.target_hwnd or self.last_external_hwnd

        # Optional confirmation (ONLY if clicked from UI button and enabled; never when from hotkey!)
        if not from_hotkey and self.confirm_var.get():
            target_desc = self.target_name.get()
            if not messagebox.askyesno(
                "Confirm Typing",
                f"Text will be typed into:\n👉 {target_desc}\n\nDo you want to proceed?",
            ):
                return

        # 1. Lock text editor so it CANNOT type into itself!
        self.text.configure(state="disabled")

        # 2. Auto-minimize Jarvis window IMMEDIATELY to reveal target application
        if self.auto_minimize_var.get():
            self.root.iconify()
            if target_hwnd:
                self.root.after(120, lambda: activate_window(target_hwnd))

        self.stop_event.clear()
        self.pause_event.clear()
        self.skip_line_event.clear()

        self.start_button.configure(state="disabled")
        self.pause_button.configure(state="normal", text="⏸ Pause")
        self.skip_button.configure(state="normal")
        self.stop_button.configure(state="normal")

        jitter = self.jitter_var.get()
        smart_indent = self.smart_indent_var.get()

        self.worker = threading.Thread(
            target=self._type_worker,
            args=(content, cps, countdown, line_delay, jitter, smart_indent, target_hwnd),
            daemon=True,
        )
        self.worker.start()

    def _type_worker(
        self,
        content: str,
        cps: float,
        countdown: float,
        line_delay: float,
        jitter: bool,
        smart_indent: bool,
        target_hwnd: int | None,
    ) -> None:
        try:
            # 1. Normalize text (prevent duplicate enters from CRLF)
            clean_text = content.replace("\r\n", "\n").replace("\r", "\n")
            total_chars = len(clean_text)
            lines = clean_text.split("\n")
            total_lines = len(lines)

            # Activate target window at the start of countdown
            if target_hwnd:
                activate_window(target_hwnd)

            # 2. Countdown phase
            if countdown > 0:
                self.events.put(("badge", "countdown"))
                steps = int(countdown * 10)
                for step in range(steps, 0, -1):
                    if self.stop_event.is_set():
                        return
                    remaining = step / 10.0
                    self.events.put(("status", f"Starting in {remaining:.1f}s... switch to document and click your cursor!"))
                    self.events.put(("progress", (steps - step, steps)))
                    if self.stop_event.wait(0.1):
                        return

            # Reactivate target right before first keystroke
            if target_hwnd:
                activate_window(target_hwnd)
                time.sleep(0.2)

            # 3. Typing phase
            self.events.put(("badge", "typing"))
            self.events.put(("status", f"Typing 0/{total_chars} chars..."))

            # Detect indent step (usually 4 for Python/C++, or 2 for JS/HTML)
            indent_step = 4
            for l in lines:
                ld = len(l) - len(l.lstrip(" "))
                if ld in (2, 4):
                    indent_step = ld
                    break

            editor_indent = 0
            chars_processed = 0

            for line_idx, line in enumerate(lines):
                if self.stop_event.is_set():
                    return

                # Handle pause
                if self.pause_event.is_set():
                    self.events.put(("badge", "paused"))
                    self.events.put(("status", f"Paused at line {line_idx + 1}/{total_lines}. Press Resume."))
                    while self.pause_event.is_set() and not self.stop_event.is_set():
                        time.sleep(0.05)
                    if self.stop_event.is_set():
                        return
                    self.events.put(("badge", "typing"))

                # Handle skip line
                if self.skip_line_event.is_set():
                    self.skip_line_event.clear()
                    chars_processed += len(line) + (1 if line_idx < total_lines - 1 else 0)
                    self.events.put(("status", f"Skipped line {line_idx + 1}/{total_lines}."))
                    self.events.put(("progress", (min(chars_processed, total_chars), total_chars)))
                    continue

                stripped = line.strip()

                if smart_indent:
                    if line_idx == 0:
                        # First line: type as in source text
                        to_type = line
                        if not stripped.startswith("#") and not stripped.startswith("//") and (stripped.endswith(":") or stripped.endswith("{")):
                            editor_indent = len(line) - len(line.lstrip(" ")) + indent_step
                        else:
                            editor_indent = len(line) - len(line.lstrip(" "))
                    else:
                        if not stripped:
                            # Empty / blank line: do not alter editor_indent, send blank newline
                            to_type = ""
                        else:
                            desired_indent = len(line) - len(line.lstrip(" "))
                            if desired_indent < editor_indent:
                                dedent_count = max(1, (editor_indent - desired_indent) // indent_step)
                                for _ in range(dedent_count):
                                    if self.stop_event.is_set():
                                        return
                                    self.typer.dedent()
                                    time.sleep(0.03)
                                editor_indent = desired_indent
                                to_type = line.lstrip(" ")
                            elif desired_indent > editor_indent:
                                extra_spaces = desired_indent - editor_indent
                                to_type = (" " * extra_spaces) + line.lstrip(" ")
                                editor_indent = desired_indent
                            else:
                                # Target editor auto-indentation matches desired indentation
                                to_type = line.lstrip(" ")

                            is_comment = stripped.startswith("#") or stripped.startswith("//")
                            if not is_comment and (stripped.endswith(":") or stripped.endswith("{")):
                                editor_indent += indent_step
                else:
                    to_type = line

                # Type line characters
                for char in to_type:
                    if self.stop_event.is_set():
                        return
                    while self.pause_event.is_set() and not self.stop_event.is_set():
                        time.sleep(0.05)
                    if self.stop_event.is_set():
                        return

                    self.typer.type_char(char)
                    chars_processed += 1
                    pct = min(100, int((chars_processed / total_chars) * 100))
                    self.events.put(("progress", (min(chars_processed, total_chars), total_chars)))
                    self.events.put(("status", f"Typing: {pct}% • Line {line_idx + 1}/{total_lines}"))

                    if cps > 0:
                        base_delay = 1.0 / cps
                        actual_delay = base_delay * random.uniform(0.85, 1.15) if jitter else base_delay
                        if self.stop_event.wait(actual_delay):
                            return

                # End of line: send Enter if not the last line
                if line_idx < total_lines - 1:
                    if self.stop_event.is_set():
                        return
                    self.typer.type_char("\n")
                    chars_processed += (len(line) - len(to_type)) + 1
                    pct = min(100, int((chars_processed / total_chars) * 100))
                    self.events.put(("progress", (min(chars_processed, total_chars), total_chars)))
                    self.events.put(("status", f"Typing: {pct}% • Line {line_idx + 1}/{total_lines}"))

                    if line_delay > 0:
                        if self.stop_event.wait(line_delay):
                            return
                else:
                    chars_processed += (len(line) - len(to_type))
                    self.events.put(("progress", (min(chars_processed, total_chars), total_chars)))

            self.events.put(("badge", "ready"))
            self.events.put(("status", f"Finished! Successfully typed {total_chars:,} characters across {total_lines:,} lines."))
        except Exception as exc:
            LOGGER.exception("Typing worker failed")
            self.events.put(("badge", "stopped"))
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("done", None))

    def toggle_pause(self) -> None:
        if not self.worker or not self.worker.is_alive():
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.pause_button.configure(text="⏸ Pause")
            self._set_badge("typing")
        else:
            self.pause_event.set()
            self.pause_button.configure(text="▶ Resume")
            self._set_badge("paused")

    def stop(self) -> None:
        self.stop_event.set()
        self.pause_event.clear()
        self.skip_line_event.clear()
        self.text.configure(state="normal")
        if self.auto_minimize_var.get():
            self.root.deiconify()
            self.root.lift()
        self._set_badge("stopped")
        self.status.set("Stopped by user.")

    def skip_line(self) -> None:
        if self.worker and self.worker.is_alive():
            self.skip_line_event.set()

    # ========================================================================
    # Notepad & File Import Handlers
    # ========================================================================

    def read_notepad(self) -> None:
        """Read text from the active / topmost open Notepad window."""
        windows = get_notepad_windows()
        if not windows:
            messagebox.showinfo("Notepad", "No open Notepad window was found.\n\nPlease open Notepad with your document and try again.")
            return

        if len(windows) == 1:
            win_obj, title = windows[0]
            text = extract_notepad_text(win_obj)
            if not text:
                messagebox.showwarning("Notepad", f"Could not extract text from '{title}'.\n\nPlease use 'Paste from clipboard' as a fallback.")
                return
            self._set_editor_text(text)
            self.status.set(f"Successfully loaded text from {title}.")
        else:
            # If multiple windows, open the selector
            self.choose_notepad_window()

    def choose_notepad_window(self) -> None:
        """Open a dialog allowing the user to pick among multiple open Notepad windows."""
        windows = get_notepad_windows()
        if not windows:
            messagebox.showinfo("Notepad", "No open Notepad window was found.\n\nPlease open Notepad and try again.")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Choose Notepad Window")
        dialog.geometry("560x360")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg="#0f172a")

        ttk.Label(dialog, text="Select Notepad Window to Import:", style="Header.TLabel", font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(16, 8))

        list_frame = ttk.Frame(dialog)
        list_frame.pack(fill="both", expand=True, padx=16, pady=4)

        listbox = tk.Listbox(
            list_frame,
            bg="#1e293b",
            fg="#f8fafc",
            selectbackground="#06b6d4",
            selectforeground="#0f172a",
            font=("Segoe UI", 10),
            borderwidth=1,
            relief="flat",
        )
        listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(list_frame, command=listbox.yview)
        sb.pack(side="right", fill="y")
        listbox.config(yscrollcommand=sb.set)

        for _, title in windows:
            listbox.insert("end", f"📄 {title}")
        listbox.selection_set(0)

        def on_import() -> None:
            sel = listbox.curselection()
            if not sel:
                return
            idx = sel[0]
            win_obj, title = windows[idx]
            text = extract_notepad_text(win_obj)
            if not text:
                messagebox.showwarning("Notepad", f"Could not read text from '{title}'. Try clipboard fallback.")
                dialog.destroy()
                return
            self._set_editor_text(text)
            self.status.set(f"Loaded text from {title}.")
            dialog.destroy()

        btn_bar = ttk.Frame(dialog)
        btn_bar.pack(fill="x", padx=16, pady=16)
        ttk.Button(btn_bar, text="Import Text", style="Primary.TButton", command=on_import).pack(side="right", padx=(6, 0))
        ttk.Button(btn_bar, text="Cancel", command=dialog.destroy).pack(side="right")

    def paste_clipboard(self) -> None:
        try:
            content = self.root.clipboard_get()
            if not content:
                messagebox.showinfo("Clipboard", "Clipboard is empty.")
                return
            self._set_editor_text(content)
            self.status.set("Imported text from clipboard.")
        except tk.TclError:
            messagebox.showerror("Clipboard", "No text data found on clipboard.")

    def open_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("Markdown files", "*.md"), ("All files", "*.*")])
        if path:
            try:
                # Try UTF-8-sig first, fallback to latin-1
                try:
                    content = Path(path).read_text(encoding="utf-8-sig")
                except UnicodeDecodeError:
                    content = Path(path).read_text(encoding="latin-1")
                self._set_editor_text(content)
                self.status.set(f"Loaded file: {Path(path).name}")
            except Exception as exc:
                messagebox.showerror("Open File", f"Error opening file: {exc}")

    def clear_text(self) -> None:
        if self.text.get("1.0", "end-1c"):
            if messagebox.askyesno("Clear Text", "Clear all text from editor?"):
                self.text.delete("1.0", "end")
                self._on_text_modified()
                self.status.set("Text cleared.")

    def _set_editor_text(self, text: str) -> None:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", normalized)
        self._on_text_modified()

    # ========================================================================
    # Target Window Selection
    # ========================================================================

    def select_target_window(self) -> None:
        windows = get_open_windows()

        dialog = tk.Toplevel(self.root)
        dialog.title("Select Target Application")
        dialog.geometry("600x420")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg="#0f172a")

        ttk.Label(dialog, text="Select Target Application Window", style="Header.TLabel", font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(16, 4))
        ttk.Label(dialog, text="Jarvis will automatically focus this window before typing starts.", style="Muted.TLabel").pack(anchor="w", padx=16, pady=(0, 8))

        list_frame = ttk.Frame(dialog)
        list_frame.pack(fill="both", expand=True, padx=16, pady=4)

        listbox = tk.Listbox(
            list_frame,
            bg="#1e293b",
            fg="#f8fafc",
            selectbackground="#06b6d4",
            selectforeground="#0f172a",
            font=("Segoe UI", 10),
            borderwidth=1,
            relief="flat",
        )
        listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(list_frame, command=listbox.yview)
        sb.pack(side="right", fill="y")
        listbox.config(yscrollcommand=sb.set)

        def populate_list() -> None:
            nonlocal windows
            windows = get_open_windows()
            listbox.delete(0, "end")
            listbox.insert("end", "🎯 Currently Focused Window (No auto-switch)")
            for _, title in windows:
                listbox.insert("end", f"🪟 {title}")
            listbox.selection_set(0)

        populate_list()

        def on_select() -> None:
            sel = listbox.curselection()
            if not sel:
                return
            idx = sel[0]
            if idx == 0:
                self.target_hwnd = None
                self.target_name.set("Currently Focused Window")
            else:
                hwnd, title = windows[idx - 1]
                self.target_hwnd = hwnd
                self.target_name.set(title)
            self.status.set(f"Target set to: {self.target_name.get()}")
            dialog.destroy()

        def on_detect_click() -> None:
            detect_btn.configure(text="Click target window now! (3s)", state="disabled")
            dialog.update()
            def capture():
                for sec in range(3, 0, -1):
                    time.sleep(1)
                hwnd = ctypes.windll.user32.GetForegroundWindow()
                if hwnd and hwnd != self.root.winfo_id():
                    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                    buf = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                    captured_title = buf.value.strip() or "Selected Window"
                    self.target_hwnd = hwnd
                    self.target_name.set(captured_title)
                    self.status.set(f"Target captured: {captured_title}")
                dialog.destroy()
            threading.Thread(target=capture, daemon=True).start()

        btn_bar = ttk.Frame(dialog)
        btn_bar.pack(fill="x", padx=16, pady=16)

        ttk.Button(btn_bar, text="🔄 Refresh List", command=populate_list).pack(side="left")
        detect_btn = ttk.Button(btn_bar, text="⏱ Click to Detect (3s)", command=on_detect_click)
        detect_btn.pack(side="left", padx=6)

        ttk.Button(btn_bar, text="Set Target", style="Primary.TButton", command=on_select).pack(side="right", padx=(6, 0))
        ttk.Button(btn_bar, text="Cancel", command=dialog.destroy).pack(side="right")

    # ========================================================================
    # Profile Management
    # ========================================================================

    def save_profile(self) -> None:
        name = self.profile.get().strip() or "Custom"
        try:
            cps_val = float(self.cps.get().strip())
            countdown_val = float(self.countdown.get().strip())
            line_delay_val = float(self.line_delay.get().strip())
        except ValueError:
            messagebox.showerror("Invalid Input", "Please enter valid numeric settings before saving the profile.")
            return

        self.profile_data["profiles"][name] = {
            "cps": cps_val,
            "countdown": countdown_val,
            "line_delay": line_delay_val,
            "jitter": self.jitter_var.get(),
            "confirm": self.confirm_var.get(),
            "smart_indent": self.smart_indent_var.get(),
        }
        self.profile_box.configure(values=list(self.profile_data["profiles"].keys()))
        self.profile.set(name)
        self._persist()
        self.status.set(f"Profile '{name}' saved.")

    def load_profile(self) -> None:
        name = self.profile.get()
        values = self.profile_data["profiles"].get(name)
        if values:
            self.cps.set(str(values.get("cps", 60)))
            self.countdown.set(str(values.get("countdown", 5)))
            self.line_delay.set(str(values.get("line_delay", 0.05)))
            self.jitter_var.set(bool(values.get("jitter", False)))
            self.confirm_var.set(bool(values.get("confirm", True)))
            self.smart_indent_var.set(bool(values.get("smart_indent", True)))
            self.status.set(f"Loaded profile: {name}")

    def delete_profile(self) -> None:
        name = self.profile.get()
        if name in ("Default", "Fast", "Human Natural", "Code Block"):
            messagebox.showinfo("Profile", "Built-in profiles cannot be deleted.")
            return
        if name in self.profile_data["profiles"]:
            if messagebox.askyesno("Delete Profile", f"Delete profile '{name}'?"):
                del self.profile_data["profiles"][name]
                names = list(self.profile_data["profiles"].keys())
                self.profile_box.configure(values=names)
                self.profile.set(names[0] if names else "Default")
                self.load_profile()
                self._persist()

    def _persist(self) -> None:
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps(self.profile_data, indent=2), encoding="utf-8")
        except Exception as exc:
            LOGGER.warning("Could not persist settings: %s", exc)

    # ========================================================================
    # Event Queue & Lifecycle
    # ========================================================================

    def _poll_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status.set(str(value))
                elif kind == "badge":
                    self._set_badge(str(value))
                elif kind == "progress":
                    cur, total = value
                    self.progress.configure(maximum=total, value=cur)
                elif kind == "error":
                    self.text.configure(state="normal")
                    if self.auto_minimize_var.get():
                        self.root.deiconify()
                        self.root.lift()
                    messagebox.showerror("Typing Encountered an Error", str(value))
                    self.status.set("Typing stopped due to an error.")
                elif kind == "done":
                    self.start_button.configure(state="normal")
                    self.pause_button.configure(state="disabled", text="⏸ Pause")
                    self.skip_button.configure(state="disabled")
                    self.stop_button.configure(state="disabled")
                    self.text.configure(state="normal")
                    self._set_badge("ready")
                    if self.auto_minimize_var.get():
                        self.root.deiconify()
                        self.root.lift()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def close(self) -> None:
        self.stop()
        self._persist()
        if self.hotkeys is not None:
            try:
                self.hotkeys.stop()
            except Exception:
                pass
        self.root.destroy()


def main() -> None:
    if os.name == "nt":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    root = tk.Tk()
    try:
        AutoTyperApp(root)
        root.mainloop()
    except Exception:
        LOGGER.exception("Application startup failed")
        raise


if __name__ == "__main__":
    main()