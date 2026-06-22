"""
Windows desktop automation helpers for non-browser applications.
"""

from __future__ import annotations

import ctypes
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import ImageGrab
from tesseract_utils import configure_pytesseract, describe_tesseract_problem

try:
    import pytesseract  # type: ignore
except Exception:  # pragma: no cover
    pytesseract = None
else:
    configure_pytesseract(pytesseract)


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# ── DPI awareness ────────────────────────────────────────────────────────────
# Tell Windows this process is per-monitor DPI aware so that SetCursorPos and
# ImageGrab both operate in the same physical-pixel space.  Call once at import
# time; failures are silently ignored so the module still works on older OS.
try:
    shcore = ctypes.windll.shcore
    # PROCESS_PER_MONITOR_DPI_AWARE = 2
    shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def _get_dpi_scale() -> float:
    """Return the primary-monitor DPI scale factor (e.g. 1.5 for 150 % scaling).

    Used to convert logical pixel coordinates (as reported by ImageGrab / OCR)
    into physical pixel coordinates needed by SetCursorPos / mouse_event.
    After SetProcessDpiAwareness(2) both spaces are identical, so this should
    return 1.0; it is kept as a safety net for environments where the call
    above was silently ignored.
    """
    try:
        hdc = ctypes.windll.gdi32.CreateDCW("DISPLAY", None, None, None)
        # LOGPIXELSX = 88
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
        ctypes.windll.gdi32.DeleteDC(hdc)
        if dpi and dpi != 96:
            return dpi / 96.0
    except Exception:
        pass
    return 1.0


_DPI_SCALE: float = _get_dpi_scale()


def _logical_to_physical(x: int, y: int) -> tuple[int, int]:
    """Convert logical (screenshot) coordinates to physical screen coordinates."""
    if _DPI_SCALE == 1.0:
        return x, y
    return int(round(x * _DPI_SCALE)), int(round(y * _DPI_SCALE))

user32.OpenClipboard.argtypes = [ctypes.c_void_p]
user32.OpenClipboard.restype = ctypes.c_bool
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = ctypes.c_bool
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = ctypes.c_bool
user32.GetClipboardData.argtypes = [ctypes.c_uint]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.restype = ctypes.c_bool
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
user32.BringWindowToTop.restype = ctypes.c_bool
user32.SetActiveWindow.argtypes = [ctypes.c_void_p]
user32.SetActiveWindow.restype = ctypes.c_void_p
user32.IsIconic.argtypes = [ctypes.c_void_p]
user32.IsIconic.restype = ctypes.c_bool

SW_RESTORE = 9
SW_SHOW = 5
WM_CLOSE = 0x0010
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

MODIFIER_KEYS = {
    "ctrl": 0x11,
    "control": 0x11,
    "shift": 0x10,
    "alt": 0x12,
    "win": 0x5B,
    "windows": 0x5B,
    "meta": 0x5B,
    "super": 0x5B,
    "logo": 0x5B,
    "command": 0x5B,
}

SPECIAL_KEYS = {
    "enter": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
}


@dataclass(slots=True)
class WindowInfo:
    hwnd: int
    title: str
    class_name: str = ""
    text_preview: str = ""


APP_SEARCH_EXTENSIONS = (".exe", ".lnk", ".bat", ".cmd")
APP_SEARCH_ROOTS = [
    os.environ.get("ProgramFiles", r"C:\Program Files"),
    os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
    os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
    os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), r"Microsoft\Windows\Start Menu\Programs"),
    os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"),
    os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop"),
]


def _sleep_brief(delay: float = 0.08) -> None:
    time.sleep(delay)


def _enum_windows() -> list[WindowInfo]:
    windows: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if title:
            class_buffer = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buffer, 255)
            class_name = class_buffer.value.strip()
            windows.append(WindowInfo(
                hwnd=int(hwnd),
                title=title,
                class_name=class_name,
                text_preview=_window_text_preview(int(hwnd)),
            ))
        return True

    user32.EnumWindows(callback, 0)
    return windows


def _window_text_preview(hwnd: int, max_chars: int = 240) -> str:
    rows: list[str] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(child_hwnd, _lparam):
        length = user32.GetWindowTextLengthW(child_hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(child_hwnd, buffer, length + 1)
        text = buffer.value.strip()
        if text:
            rows.append(text)
        return len(" ".join(rows)) < max_chars

    try:
        user32.EnumChildWindows(hwnd, callback, 0)
    except Exception:
        return ""
    joined = " | ".join(rows)
    return joined[:max_chars]


def list_windows_action(limit: int = 50) -> str:
    windows = _enum_windows()
    rows = [f"- {item.title} [hwnd={item.hwnd}]" for item in windows[:limit]]
    if len(windows) > limit:
        rows.append(f"... and {len(windows) - limit} more")
    return "Open windows:\n" + ("\n".join(rows) if rows else "(none)")


def _command_app_tokens(command: str) -> list[str]:
    raw = (command or "").strip().strip('"').lower()
    if not raw:
        return []
    normalized = re.sub(r"\.exe\b", "", raw)
    normalized = re.sub(r"[\"']", " ", normalized)
    pieces = re.split(r"[\s\\/:\(\)\[\],;]+", normalized)
    names: list[str] = []
    for piece in pieces:
        token = piece.strip().strip('"').strip("'")
        if not token:
            continue
        if token in {"program", "programs", "files", "windows", "microsoft", "app", "application"}:
            continue
        if token and len(token) > 1 and token not in names:
            names.append(token)
        if token.startswith("ms") and len(token) > 4:
            trimmed = token[2:]
            if trimmed not in names:
                names.append(trimmed)
        if token == "edge" and "microsoft edge" not in names:
            names.append("microsoft edge")
        if token == "chrome" and "google chrome" not in names:
            names.append("google chrome")
    return names


def _iter_search_roots() -> list[str]:
    roots = []
    for root in APP_SEARCH_ROOTS:
        if root and os.path.exists(root):
            roots.append(root)
    return roots


def _normalize_command(command: str) -> str:
    return (command or "").strip().strip('"').strip()


def _split_command(command: str) -> list[str]:
    text = (command or "").strip()
    if not text:
        return []
    try:
        parts = shlex.split(text, posix=False)
    except Exception:
        parts = [text]
    cleaned = [part.strip().strip('"') for part in parts if part and part.strip().strip('"')]
    return cleaned or [text]


def _wait_for_window(tokens: list[str], timeout: float = 8.0) -> WindowInfo | None:
    deadline = time.time() + timeout
    while tokens and time.time() < deadline:
        for token in tokens:
            matches = _find_window_matches(token)
            if matches:
                return matches[0]
        _sleep_brief(0.25)
    return None


def _looks_like_path(command: str) -> bool:
    value = _normalize_command(command).lower()
    return (
        "\\" in value
        or "/" in value
        or value.endswith(APP_SEARCH_EXTENSIONS)
        or value.startswith(".")
    )


def _launch_process(command: str, cwd: str = "", shell: bool = False) -> subprocess.Popen[Any]:
    working_dir = os.path.abspath(cwd) if cwd else None
    if shell:
        return subprocess.Popen(command, cwd=working_dir, shell=True)
    return subprocess.Popen(_split_command(command), cwd=working_dir, shell=False)


def _launch_path(path: str, cwd: str = "") -> str:
    target = os.path.abspath(path)
    working_dir = os.path.abspath(cwd) if cwd else os.path.dirname(target) or None
    os.startfile(target)  # type: ignore[attr-defined]
    return f"Opened application path: {target}" + (f" (cwd={working_dir})" if working_dir else "")


def _candidate_names(tokens: list[str], command: str) -> list[str]:
    names: list[str] = []
    normalized = _normalize_command(command)
    if normalized:
        names.append(normalized)
    for token in tokens:
        if token not in names:
            names.append(token)
        exe_name = f"{token}.exe"
        if exe_name not in names:
            names.append(exe_name)
    return names


def _find_installed_app_targets(command: str, limit: int = 12) -> list[str]:
    tokens = _command_app_tokens(command)
    if not tokens:
        return []
    token_set = set(tokens)
    matches: list[tuple[int, str]] = []
    seen: set[str] = set()
    for root in _iter_search_roots():
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                depth = Path(dirpath).relative_to(root).parts
                if len(depth) > 4:
                    dirnames[:] = []
                    continue
                for filename in filenames:
                    lower_name = filename.lower()
                    if not lower_name.endswith(APP_SEARCH_EXTENSIONS):
                        continue
                    stem = os.path.splitext(lower_name)[0]
                    score = sum(1 for token in token_set if token in stem or stem in token)
                    if score <= 0:
                        continue
                    full_path = os.path.join(dirpath, filename)
                    if full_path in seen:
                        continue
                    seen.add(full_path)
                    matches.append((score, full_path))
        except Exception:
            continue
    matches.sort(key=lambda item: (-item[0], len(item[1])))
    return [path for _, path in matches[:limit]]


def _powershell_start_command(target: str) -> str:
    escaped = target.replace("'", "''")
    return f"powershell -NoProfile -Command \"Start-Process -FilePath '{escaped}'\""


def _powershell_startapps_lookup_command(command: str) -> str:
    escaped = command.replace("'", "''")
    return (
        "powershell -NoProfile -Command "
        f"\"$q='{escaped}'; "
        "$apps = Get-StartApps | Where-Object { $_.Name -like ('*' + $q + '*') -or $_.AppID -like ('*' + $q + '*') }; "
        "if ($apps) { $apps | Select-Object -First 5 | ForEach-Object { Write-Output ($_.Name + '|' + $_.AppID) } }\""
    )


def _lookup_start_apps(command: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    try:
        completed = subprocess.run(
            _powershell_startapps_lookup_command(command),
            capture_output=True,
            text=True,
            timeout=12,
            shell=True,
            check=False,
        )
        output = (completed.stdout or "").splitlines()
        for line in output:
            if "|" not in line:
                continue
            name, app_id = line.split("|", 1)
            name = name.strip()
            app_id = app_id.strip()
            if name and app_id:
                pairs.append((name, app_id))
    except Exception:
        return []
    return pairs


def _launch_start_app(app_id: str, cwd: str = "") -> subprocess.Popen[Any]:
    return _launch_process(f'explorer.exe "shell:AppsFolder\\{app_id}"', cwd=cwd, shell=True)


def _window_match_score(title: str, aliases: list[str]) -> float:
    lowered = title.lower()
    score = 0.0
    for alias in aliases:
        if not alias:
            continue
        if lowered == alias:
            score += 10.0
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            score += 6.0
        elif alias in lowered:
            score += 2.0
    if "properties" in lowered:
        score -= 8.0
    if any(ext in lowered for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff")):
        score -= 8.0
    if "visual studio code" in lowered:
        score -= 2.0
    if "file explorer" in lowered:
        score -= 1.5
    return score


def _is_command_error_dialog(item: WindowInfo, aliases: list[str]) -> bool:
    title = item.title.lower()
    preview = (item.text_preview or "").lower()
    if item.class_name != "#32770":
        return False
    if "windows cannot find" in preview:
        return True
    return any(alias and alias == title for alias in aliases) and "make sure you typed the name correctly" in preview


def _find_window_matches(query: str) -> list[WindowInfo]:
    lowered = (query or "").strip().lower()
    if not lowered:
        return []
    aliases = _command_app_tokens(lowered)
    if lowered not in aliases:
        aliases.insert(0, lowered)
    matches: list[tuple[float, WindowInfo]] = []
    for item in _enum_windows():
        title = item.title.lower()
        if any(alias and alias in title for alias in aliases):
            if _is_command_error_dialog(item, aliases):
                continue
            score = _window_match_score(item.title, aliases)
            if score > 0:
                matches.append((score, item))
    matches.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in matches]


def _foreground_hwnd() -> int:
    hwnd = user32.GetForegroundWindow()
    return int(hwnd) if hwnd else 0


def _force_foreground_window(hwnd: int, settle_seconds: float = 0.35) -> bool:
    if not hwnd:
        return False
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        else:
            user32.ShowWindow(hwnd, SW_SHOW)
        _sleep_brief(0.05)

        # Alt-tap often lets Windows accept the following foreground request.
        _key_down(MODIFIER_KEYS["alt"])
        _sleep_brief(0.02)
        _key_up(MODIFIER_KEYS["alt"])
        _sleep_brief(0.02)

        for _ in range(3):
            user32.BringWindowToTop(hwnd)
            user32.SetActiveWindow(hwnd)
            user32.SetForegroundWindow(hwnd)
            _sleep_brief(settle_seconds / 3)
            if _foreground_hwnd() == int(hwnd):
                return True
    except Exception:
        return False
    return _foreground_hwnd() == int(hwnd)


def _focus_window_info(target: WindowInfo) -> str:
    focused = _force_foreground_window(target.hwnd)
    status = "Focused window" if focused else "Focus requested for window"
    foreground = "foreground=yes" if _foreground_hwnd() == target.hwnd else "foreground=no"
    return f"{status}: {target.title} [hwnd={target.hwnd}] ({foreground})"


def focus_window_action(title: str) -> str:
    query = (title or "").strip().lower()
    if not query:
        raise ValueError("title text is required")
    matches = _find_window_matches(query)
    if not matches:
        raise ValueError(f"No visible window matched '{title}'")
    return _focus_window_info(matches[0])


def list_installed_apps_action(query: str = "") -> str:
    """Return a list of installed applications on this Windows machine.

    Queries three sources and merges results:
      1. Get-StartApps (UWP / Store apps + pinned shortcuts, includes AUMID)
      2. HKLM/HKCU Uninstall registry keys (Win32 installers)
      3. File-system scan of common install roots for .exe files

    If *query* is provided, only entries whose name/path contain the query
    (case-insensitive) are returned.  Otherwise all entries are returned
    (capped at 200).

    Output format — one app per line:
      [startapp]  <Name>  |  <AUMID>
      [registry]  <DisplayName>  |  <InstallLocation or UninstallString>
      [file]      <ExeName>  |  <FullPath>
    """
    q = (query or "").strip().lower()
    results: list[str] = []

    # ── 1. Get-StartApps (UWP + shortcuts) ──────────────────────────────
    try:
        ps = (
            "powershell -NoProfile -Command \""
            "Get-StartApps | Select-Object Name,AppID | "
            "ForEach-Object { Write-Output ($_.Name + '|||' + $_.AppID) }\""
        )
        out = subprocess.run(ps, capture_output=True, text=True, timeout=20, shell=True, check=False).stdout
        for line in out.splitlines():
            if "|||" not in line:
                continue
            name, app_id = line.split("|||", 1)
            name, app_id = name.strip(), app_id.strip()
            if not name:
                continue
            if q and q not in name.lower() and q not in app_id.lower():
                continue
            results.append(f"[startapp]  {name}  |  {app_id}")
    except Exception:
        pass

    # ── 2. Registry uninstall keys ───────────────────────────────────────
    try:
        import winreg  # type: ignore
        reg_roots = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        seen_reg: set[str] = set()
        for hive, path in reg_roots:
            try:
                with winreg.OpenKey(hive, path) as base:
                    i = 0
                    while True:
                        try:
                            sub_name = winreg.EnumKey(base, i)
                            i += 1
                        except OSError:
                            break
                        try:
                            with winreg.OpenKey(base, sub_name) as sub:
                                def _rv(name: str) -> str:
                                    try:
                                        return str(winreg.QueryValueEx(sub, name)[0])
                                    except OSError:
                                        return ""
                                display = _rv("DisplayName")
                                if not display:
                                    continue
                                location = _rv("InstallLocation") or _rv("UninstallString") or ""
                                key = display.lower()
                                if key in seen_reg:
                                    continue
                                seen_reg.add(key)
                                if q and q not in display.lower() and q not in location.lower():
                                    continue
                                results.append(f"[registry]  {display}  |  {location[:120]}")
                        except OSError:
                            continue
            except OSError:
                continue
    except ImportError:
        pass

    # ── 3. File-system scan ──────────────────────────────────────────────
    if q:  # only scan filesystem when a specific query is given (too slow otherwise)
        for path in _find_installed_app_targets(query, limit=20):
            results.append(f"[file]      {os.path.basename(path)}  |  {path}")

    if not results:
        return f"No installed apps found{' matching ' + repr(query) if q else ''}."

    # Cap total output
    cap = 300 if not q else len(results)
    out_lines = results[:cap]
    suffix = f"\n... ({len(results) - cap} more entries omitted, narrow with a query)" if len(results) > cap else ""
    return f"Installed apps{' matching ' + repr(query) if q else ''} ({len(out_lines)} shown):\n" + "\n".join(out_lines) + suffix


def open_application_action(command: str, cwd: str = "") -> str:
    command = _normalize_command(command)
    if not command:
        raise ValueError("command is required")
    tokens = _command_app_tokens(command)

    # Only accept an existing window match if its title actually relates to the
    # requested app — prevents Edge/Chrome windows being returned as "WhatsApp"
    matches = _find_window_matches(command)
    if matches:
        win_info = _focus_window_info(matches[0])
        # Verify: at least one token from the command appears in the window title
        win_title = (matches[0].get("title") or "").lower()
        if any(t and t in win_title for t in tokens):
            return f"Focused existing window before launch: {win_info}"
        # Window title doesn't match — fall through to launch

    attempted: list[str] = []
    partial_launches: list[str] = []

    candidate_commands = _candidate_names(tokens, command)
    for candidate in candidate_commands:
        try:
            proc = _launch_process(candidate, cwd=cwd, shell=False)
            focused = _wait_for_window(tokens or _command_app_tokens(candidate), timeout=8.0)
            if focused:
                return (
                    f"Opened application via direct executable: {candidate} (pid={proc.pid}) | "
                    f"{_focus_window_info(focused)}"
                )
            partial_launches.append(f"direct:{candidate} pid={proc.pid} no visible window")
        except Exception as exc:
            attempted.append(f"direct:{candidate} -> {exc}")

    start_apps = _lookup_start_apps(command)
    for app_name, app_id in start_apps:
        try:
            proc = _launch_start_app(app_id, cwd=cwd)
            focused = _wait_for_window(tokens or _command_app_tokens(app_name), timeout=12.0)
            if focused:
                return (
                    f"Opened application via StartApps/AUMID: {app_name} [{app_id}] (pid={proc.pid}) | "
                    f"{_focus_window_info(focused)}"
                )
            partial_launches.append(f"startapps:{app_name}|{app_id} pid={proc.pid} no visible window")
        except Exception as exc:
            attempted.append(f"startapps:{app_name}|{app_id} -> {exc}")

    shell_commands: list[str] = []
    resolved_path = shutil.which(command) if not _looks_like_path(command) else None
    if _looks_like_path(command) or resolved_path:
        shell_commands.extend([
            command if _looks_like_path(command) else resolved_path or command,
            f'cmd /c start "" "{command if _looks_like_path(command) else (resolved_path or command)}"',
            _powershell_start_command(command if _looks_like_path(command) else (resolved_path or command)),
        ])
    for shell_command in shell_commands:
        try:
            proc = _launch_process(shell_command, cwd=cwd, shell=True)
            focused = _wait_for_window(tokens or _command_app_tokens(shell_command), timeout=8.0)
            if focused:
                return (
                    f"Opened application via shell command: {shell_command} (pid={proc.pid}) | "
                    f"{_focus_window_info(focused)}"
                )
            partial_launches.append(f"shell:{shell_command} pid={proc.pid} no visible window")
        except Exception as exc:
            attempted.append(f"shell:{shell_command} -> {exc}")

    installed_targets = []
    if _looks_like_path(command) and os.path.exists(command):
        installed_targets.append(os.path.abspath(command))
    installed_targets.extend(_find_installed_app_targets(command))
    seen_targets: set[str] = set()
    for target in installed_targets:
        if target in seen_targets:
            continue
        seen_targets.add(target)
        try:
            result = _launch_path(target, cwd=cwd)
            focused = _wait_for_window(tokens or _command_app_tokens(target), timeout=8.0)
            if focused:
                return f"{result} | {_focus_window_info(focused)}"
            partial_launches.append(f"path:{target} no visible window")
        except Exception as exc:
            attempted.append(f"path:{target} -> {exc}")
        try:
            proc = _launch_process(_powershell_start_command(target), cwd=cwd, shell=True)
            focused = _wait_for_window(tokens or _command_app_tokens(target), timeout=8.0)
            if focused:
                return (
                    f"Opened installed target via PowerShell: {target} (pid={proc.pid}) | "
                    f"{_focus_window_info(focused)}"
                )
            partial_launches.append(f"powershell:{target} pid={proc.pid} no visible window")
        except Exception as exc:
            attempted.append(f"powershell:{target} -> {exc}")

    attempted_text = "; ".join(attempted[:8]) if attempted else "no launch methods were attempted"
    partial_text = "; ".join(partial_launches[:8]) if partial_launches else "no launch process ever started"
    return (
        f"Action failed: Automatic application launch chain exhausted for '{command}'. "
        f"No verified visible window appeared. Tried focus-existing-window, direct executable, shell launch, "
        f"StartApps/AUMID lookup, and installed-app search. GUI search fallback is now appropriate. "
        f"Partial launches: {partial_text[:500]}. Details: {attempted_text[:500]}"
    )


def open_browser_url_action(browser_command: str, url: str, cwd: str = "") -> str:
    browser_command = _normalize_command(browser_command)
    url = (url or "").strip()
    if not browser_command:
        raise ValueError("browser_command is required")
    if not url:
        raise ValueError("url is required")
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        url = "https://" + url
    launch_command = f'{browser_command} "{url}"'
    tokens = _command_app_tokens(browser_command)
    attempted: list[str] = []
    try:
        proc = _launch_process(launch_command, cwd=cwd, shell=False)
        focused = _wait_for_window(tokens, timeout=10.0)
        if focused:
            return (
                f"Opened URL in external browser: {url} via {browser_command} (pid={proc.pid}) | "
                f"{_focus_window_info(focused)}"
            )
        attempted.append(f"direct:{launch_command} pid={proc.pid} no visible window")
    except Exception as exc:
        attempted.append(f"direct:{launch_command} -> {exc}")
    try:
        proc = _launch_process(f'start "" {launch_command}', cwd=cwd, shell=True)
        focused = _wait_for_window(tokens, timeout=10.0)
        if focused:
            return (
                f"Opened URL in external browser via shell: {url} with {browser_command} (pid={proc.pid}) | "
                f"{_focus_window_info(focused)}"
            )
        attempted.append(f"shell:start {launch_command} pid={proc.pid} no visible window")
    except Exception as exc:
        attempted.append(f"shell:start {launch_command} -> {exc}")
    return (
        f"Action failed: Could not open URL in external browser with '{browser_command}'. "
        f"URL: {url}. Details: {'; '.join(attempted)[:500]}"
    )


def _set_cursor_pos(x: int, y: int) -> None:
    px, py = _logical_to_physical(int(x), int(y))
    if not user32.SetCursorPos(px, py):
        raise ValueError(f"Failed to move cursor to ({x}, {y}) [physical=({px},{py})]")


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUTUNION_MOUSE(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class MOUSE_INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", _INPUTUNION_MOUSE)]


def _send_mouse_event(flag: int, wheel_delta: int = 0) -> None:
    extra = ctypes.c_ulong(0)
    inp = MOUSE_INPUT(
        type=0,  # INPUT_MOUSE
        union=_INPUTUNION_MOUSE(
            mi=MOUSEINPUT(0, 0, wheel_delta & 0xFFFFFFFF, flag, 0, ctypes.pointer(extra))
        ),
    )
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(MOUSE_INPUT))


def _mouse_click(button: str = "left") -> None:
    btn = button.strip().lower() or "left"
    down_flag, up_flag = {
        "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    }.get(btn, (None, None))
    if down_flag is None or up_flag is None:
        raise ValueError(f"Unsupported mouse button: {button}")
    _send_mouse_event(down_flag)
    _sleep_brief(0.04)
    _send_mouse_event(up_flag)


def desktop_click_action(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
    _set_cursor_pos(x, y)
    _sleep_brief(0.08)  # let the OS/browser register the hover before clicking
    for _ in range(max(1, clicks)):
        _mouse_click(button)
        _sleep_brief(0.08)
    return f"Desktop clicked at ({x}, {y}) with {button} x{max(1, clicks)}"


def move_mouse_action(x: int, y: int) -> str:
    _set_cursor_pos(x, y)
    return f"Moved mouse to ({x}, {y})"


def drag_mouse_action(start_x: int, start_y: int, end_x: int, end_y: int, button: str = "left") -> str:
    btn = button.strip().lower() or "left"
    down_flag, up_flag = {
        "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    }.get(btn, (None, None))
    if down_flag is None or up_flag is None:
        raise ValueError(f"Unsupported mouse button: {button}")
    _set_cursor_pos(start_x, start_y)
    _sleep_brief(0.05)
    _send_mouse_event(down_flag)
    _sleep_brief(0.05)
    steps = max(abs(end_x - start_x), abs(end_y - start_y), 1)
    for index in range(1, steps + 1):
        x = start_x + (end_x - start_x) * index / steps
        y = start_y + (end_y - start_y) * index / steps
        _set_cursor_pos(int(round(x)), int(round(y)))
        _sleep_brief(0.002)
    _sleep_brief(0.05)
    _send_mouse_event(up_flag)
    return f"Dragged mouse from ({start_x}, {start_y}) to ({end_x}, {end_y}) with {btn}"


def desktop_scroll_action(amount: int) -> str:
    _send_mouse_event(MOUSEEVENTF_WHEEL, int(amount))
    return f"Desktop scrolled by {amount}"


def _key_down(vk: int) -> None:
    user32.keybd_event(vk, 0, 0, 0)


def _key_up(vk: int) -> None:
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def _resolve_vk(key: str) -> int:
    token = key.strip().lower()
    if not token:
        raise ValueError("key is required")
    if token in MODIFIER_KEYS:
        return MODIFIER_KEYS[token]
    if token in SPECIAL_KEYS:
        return SPECIAL_KEYS[token]
    if len(token) == 1:
        code = user32.VkKeyScanW(ord(token))
        if code == -1:
            raise ValueError(f"Unsupported key: {key}")
        return code & 0xFF
    raise ValueError(f"Unsupported key: {key}")


def desktop_hotkey_action(keys: list[str]) -> str:
    if not keys:
        raise ValueError("keys are required")
    normalized = [str(key).strip().lower() for key in keys if str(key).strip()]
    if not normalized:
        raise ValueError("keys are required")
    held: list[int] = []
    try:
        for token in normalized[:-1]:
            vk = MODIFIER_KEYS.get(token) or SPECIAL_KEYS.get(token)
            if vk is None:
                vk = _resolve_vk(token)
            _key_down(vk)
            held.append(vk)
            _sleep_brief(0.03)
        final_vk = _resolve_vk(normalized[-1])
        _key_down(final_vk)
        _sleep_brief(0.05)
        _key_up(final_vk)
    finally:
        for vk in reversed(held):
            _key_up(vk)
            _sleep_brief(0.02)
    return f"Desktop hotkey pressed: {'+'.join(normalized)}"


def desktop_press_key_action(key: str) -> str:
    vk = _resolve_vk(key)
    _key_down(vk)
    _sleep_brief(0.05)
    _key_up(vk)
    return f"Desktop key pressed: {key}"


def _type_token_char(token: str) -> None:
    if not token:
        return
    if len(token) == 1:
        _send_unicode_char(token)
    else:
        vk = _resolve_vk(token)
        _key_down(vk)
        _sleep_brief(0.03)
        _key_up(vk)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", _INPUTUNION)]


def _send_unicode_char(ch: str) -> None:
    extra = ctypes.c_ulong(0)
    scan = ord(ch)
    down = INPUT(
        type=1,
        union=_INPUTUNION(
            ki=KEYBDINPUT(0, scan, KEYEVENTF_UNICODE, 0, ctypes.pointer(extra))
        ),
    )
    up = INPUT(
        type=1,
        union=_INPUTUNION(
            ki=KEYBDINPUT(0, scan, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, ctypes.pointer(extra))
        ),
    )
    user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
    user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))


def _send_vk_char(ch: str) -> bool:
    if not ch or len(ch) != 1:
        return False
    code = user32.VkKeyScanW(ord(ch))
    if code == -1:
        return False
    vk = code & 0xFF
    modifier_state = (code >> 8) & 0xFF
    held: list[int] = []
    try:
        if modifier_state & 0x01:
            _key_down(MODIFIER_KEYS["shift"])
            held.append(MODIFIER_KEYS["shift"])
            _sleep_brief(0.01)
        if modifier_state & 0x02:
            _key_down(MODIFIER_KEYS["ctrl"])
            held.append(MODIFIER_KEYS["ctrl"])
            _sleep_brief(0.01)
        if modifier_state & 0x04:
            _key_down(MODIFIER_KEYS["alt"])
            held.append(MODIFIER_KEYS["alt"])
            _sleep_brief(0.01)
        _key_down(vk)
        _sleep_brief(0.02)
        _key_up(vk)
        return True
    finally:
        for modifier in reversed(held):
            _sleep_brief(0.008)
            _key_up(modifier)


def _get_clipboard_text() -> str | None:
    if not user32.OpenClipboard(None):
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        locked = kernel32.GlobalLock(handle)
        if not locked:
            return ""
        try:
            return ctypes.wstring_at(locked)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _set_clipboard_text(text: str) -> None:
    data = str(text or "")
    encoded = data.encode("utf-16-le") + b"\x00\x00"
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
    if not handle:
        raise ValueError("Failed to allocate clipboard memory")
    locked = kernel32.GlobalLock(handle)
    if not locked:
        raise ValueError("Failed to lock clipboard memory")
    try:
        ctypes.memmove(locked, encoded, len(encoded))
    finally:
        kernel32.GlobalUnlock(handle)
    if not user32.OpenClipboard(None):
        raise ValueError("Failed to open clipboard")
    try:
        if not user32.EmptyClipboard():
            raise ValueError("Failed to empty clipboard")
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise ValueError("Failed to set clipboard data")
        handle = None
    finally:
        user32.CloseClipboard()


def _paste_text(text: str) -> bool:
    previous = _get_clipboard_text()
    try:
        _set_clipboard_text(text)
        _sleep_brief(0.05)
        desktop_hotkey_action(["ctrl", "v"])
        _sleep_brief(0.12)
        return True
    except Exception:
        return False
    finally:
        if previous is not None:
            try:
                _sleep_brief(0.02)
                _set_clipboard_text(previous)
            except Exception:
                pass


def _should_prefer_paste_for_text(text: str) -> bool:
    typed = str(text or "")
    if len(typed) >= 4:
        return True
    return any(token in typed for token in ("@", "://", "\\", "/", ":", ".", " "))


def _should_prefer_raw_for_text(text: str) -> bool:
    typed = str(text or "")
    if not typed:
        return False
    if len(typed) <= 160 and ("@" in typed or "://" in typed):
        return True
    if len(typed) <= 80 and typed.count(" ") <= 2 and any(ch in typed for ch in "@._-:/\\+"):
        return True
    return False


def _type_text_raw(text: str) -> None:
    for ch in str(text):
        if ch == "\n":
            desktop_press_key_action("enter")
        elif ch == "\t":
            desktop_press_key_action("tab")
        elif not _send_vk_char(ch):
            _send_unicode_char(ch)
        _sleep_brief(0.012)


def _type_text_char_by_char(text: str, delay: float = 0.035) -> None:
    """Type text character-by-character using VK scan codes only (no unicode injection).

    This bypasses Gmail's / Chrome's paste-blocking and SendInput-unicode filter.
    It uses VkKeyScanW to resolve each character to a virtual key + modifier state,
    then synthesises the exact physical keystrokes the keyboard would produce.
    Falls back to unicode injection for characters not in the current keyboard layout.
    """
    for ch in str(text):
        if ch == "\n":
            desktop_press_key_action("enter")
        elif ch == "\t":
            desktop_press_key_action("tab")
        elif not _send_vk_char(ch):
            # Character not in current keyboard layout — use unicode injection as
            # last resort (may be blocked by some fields, but there's no alternative).
            _send_unicode_char(ch)
        time.sleep(delay)


def _verify_text_appeared(text: str, timeout: float = 1.5) -> bool:
    """Check via OCR whether `text` (or its first meaningful chunk) appears on screen."""
    if pytesseract is None or configure_pytesseract(pytesseract) is None:
        return True  # can't verify, assume ok
    # Use first 20 non-whitespace chars as the search token
    token = text.strip()[:20].strip()
    if not token:
        return True
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = find_text_on_screen_action(token)
            if "No screen text matched" not in result:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def desktop_type_verified_action(text: str, typing_mode: str = "auto") -> str:
    """Type text and verify it appeared on screen via OCR.

    If OCR shows nothing after the first attempt, retries with a slower inter-key
    delay (75 ms). If that also fails, tries paste as a last resort.
    Returns a result string describing what succeeded.

    This is the most reliable method for external browsers that may silently drop
    keystrokes or block synthetic input.
    """
    if text is None:
        raise ValueError("text is required")
    typed = str(text)
    if not typed:
        return "Nothing to type (empty text)"

    # Attempt 1: use the normal desktop_type_action with requested mode
    result1 = desktop_type_action(typed, typing_mode=typing_mode)
    if _verify_text_appeared(typed):
        return result1 + " [verified]"

    # Attempt 2: slower char-by-char (75 ms inter-key) — harder to filter
    _type_text_char_by_char(typed, delay=0.075)
    if _verify_text_appeared(typed):
        return f"Desktop typed {len(typed)} chars via slow char-by-char (75ms) [verified after retry]"

    # Attempt 3: paste
    if _paste_text(typed):
        if _verify_text_appeared(typed):
            return f"Desktop pasted {len(typed)} chars [verified after paste retry]"

    # Attempt 4: select-all then paste (clears any partial typing that landed)
    desktop_hotkey_action(["ctrl", "a"])
    _sleep_brief(0.05)
    if _paste_text(typed):
        if _verify_text_appeared(typed):
            return f"Desktop pasted {len(typed)} chars after select-all [verified]"

    return (
        f"Desktop typed {len(typed)} chars but OCR verification could not confirm text appeared. "
        "The field may still be unfocused, or the text may have appeared in a different location. "
        "Take a screenshot to inspect current state."
    )


def desktop_type_action(text: str, typing_mode: str = "auto") -> str:
    if text is None:
        raise ValueError("text is required")
    typed = str(text)
    mode = str(typing_mode or "auto").strip().lower()
    if mode not in {"auto", "raw", "paste", "char"}:
        mode = "auto"

    # "char" mode: slow but works on fields that block paste and unicode injection
    # (e.g. Gmail To/Subject in Chrome/Edge).  Use when paste is known to fail.
    if mode == "char":
        _type_text_char_by_char(typed)
        return f"Desktop typed {len(typed)} chars via char-by-char VK keys"

    if mode == "paste":
        if typed and _paste_text(typed):
            return f"Desktop pasted {len(typed)} chars"
        _type_text_raw(typed)
        return f"Desktop typed {len(typed)} chars via raw keys (paste fallback failed)"

    if mode == "raw":
        _type_text_raw(typed)
        return f"Desktop typed {len(typed)} chars via raw keys"

    # auto: for short tokens (email addresses, subjects) prefer char-by-char because
    # those fields are most likely to be in Chrome/Edge where paste is blocked.
    # For long body text, prefer paste (faster) and fall back to raw if it fails.
    is_short_token = len(typed) <= 120 and "\n" not in typed
    if is_short_token:
        # char-by-char is robust for short text in protected fields
        _type_text_char_by_char(typed)
        return f"Desktop typed {len(typed)} chars via char-by-char VK keys (auto)"
    if typed and _paste_text(typed):
        return f"Desktop pasted {len(typed)} chars"
    _type_text_raw(typed)
    return f"Desktop typed {len(typed)} chars via raw keys (auto fallback)"


def gmail_compose_action(
    to: str,
    subject: str,
    body: str,
    *,
    window_focused: bool = False,
) -> str:
    """Fill a Gmail compose window using keyboard navigation only — no coordinate guessing.

    Gmail's compose popup is a floating overlay whose on-screen position changes with
    window size and zoom.  Tesseract OCR cannot reliably locate the small placeholder
    text ("To", "Subject") inside it, so coordinate-based clicking always fails.

    This function uses the guaranteed keyboard path instead:
      1. Press Escape to dismiss any stray popup / restore focus to inbox.
      2. Press 'c' (Gmail compose shortcut) — works when inbox has keyboard focus.
      3. Wait for compose to open (detected by Tab-navigating to the To field).
      4. Type the recipient, press Tab to move to Subject, type it, press Tab to body.
      5. Type the body.

    Returns a summary string on success, raises ValueError on failure.
    """
    if not window_focused:
        # Make sure the Gmail tab is the foreground window before we send keystrokes.
        # Caller should already have done focus_window; this is a safety net.
        user32.SetForegroundWindow(user32.GetForegroundWindow())
        _sleep_brief(0.15)

    # Step 1: press Escape to close any stray dialog and return focus to the page body
    desktop_press_key_action("escape")
    _sleep_brief(0.3)

    # Step 2: open compose with the 'c' keyboard shortcut
    _send_vk_char("c")
    _sleep_brief(1.2)  # Gmail takes ~800ms to animate the compose panel open

    # Step 3: Tab into the To field.  Gmail puts focus inside the compose window
    # automatically when it opens; a single Tab press moves from the compose title
    # bar area into the To input.  We send one Tab and then wait.
    desktop_press_key_action("tab")
    _sleep_brief(0.25)

    # Step 4: type the recipient address character-by-character (immune to paste block)
    _type_text_char_by_char(str(to or ""), delay=0.04)
    _sleep_brief(0.15)

    # Confirm recipient — Gmail needs Enter or Tab to resolve the address chip
    desktop_press_key_action("tab")
    _sleep_brief(0.35)

    # Step 5: Tab to Subject
    desktop_press_key_action("tab")
    _sleep_brief(0.2)

    _type_text_char_by_char(str(subject or ""), delay=0.04)
    _sleep_brief(0.15)

    # Step 6: Tab to message body
    desktop_press_key_action("tab")
    _sleep_brief(0.25)

    # Body may contain newlines — split and use Enter between paragraphs
    body_text = str(body or "")
    for i, line in enumerate(body_text.split("\n")):
        if i > 0:
            desktop_press_key_action("enter")
            _sleep_brief(0.1)
        _type_text_char_by_char(line, delay=0.03)

    _sleep_brief(0.2)
    return (
        f"Gmail compose filled via keyboard: to='{to}', subject='{subject}', "
        f"body={len(body_text)} chars. Ready to send — call desktop_hotkey ctrl+Enter or click Send."
    )


def select_menu_item_action(menu_path: str) -> str:
    parts = [part.strip() for part in str(menu_path or "").split(">") if part.strip()]
    if not parts:
        raise ValueError("menu path is required, for example 'File>Open'")
    _key_down(MODIFIER_KEYS["alt"])
    _sleep_brief(0.05)
    _key_up(MODIFIER_KEYS["alt"])
    _sleep_brief(0.12)
    for index, part in enumerate(parts):
        token = part[0]
        _type_token_char(token)
        _sleep_brief(0.18 if index < len(parts) - 1 else 0.08)
    return f"Selected menu path: {' > '.join(parts)}"


def desktop_screenshot_action(path: str) -> str:
    image = ImageGrab.grab()
    image.save(path)
    return f"Saved desktop screenshot: {path} ({image.width}x{image.height})"


def desktop_screenshot_b64(max_width: int = 1280) -> tuple[str, int, int]:
    """Capture the screen and return (base64_jpeg_string, width, height).

    Downscales to *max_width* if wider, to keep token usage reasonable.
    Returns a JPEG base64 string suitable for passing directly to vision APIs.
    """
    import base64, io
    image = ImageGrab.grab()
    w, h = image.size
    if w > max_width:
        scale = max_width / w
        image = image.resize((max_width, int(h * scale)), resample=1)  # LANCZOS=1
        w, h = image.size
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return b64, w, h


def close_window_action(title: str) -> str:
    query = (title or "").strip().lower()
    if not query:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            raise ValueError("No foreground window to close")
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        return f"Requested close for foreground window [hwnd={int(hwnd)}]"
    matches = [item for item in _enum_windows() if query in item.title.lower()]
    if not matches:
        raise ValueError(f"No visible window matched '{title}'")
    target = matches[0]
    user32.PostMessageW(target.hwnd, WM_CLOSE, 0, 0)
    return f"Requested close for window: {target.title} [hwnd={target.hwnd}]"


def find_text_on_screen_action(text: str, limit: int = 20) -> str:
    if pytesseract is None:
        raise ValueError("OCR support is not available because pytesseract is not installed")
    if configure_pytesseract(pytesseract) is None:
        raise ValueError(f"Screen OCR is unavailable: {describe_tesseract_problem()}")
    query = (text or "").strip().lower()
    if not query:
        raise ValueError("text is required")
    try:
        image = ImageGrab.grab()
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    except Exception as exc:
        raise ValueError(f"Screen OCR failed: {exc}") from exc

    matches: list[str] = []
    for idx, raw in enumerate(data.get("text", [])):
        token = (raw or "").strip()
        if token and query in token.lower():
            left = data["left"][idx]
            top = data["top"][idx]
            width = data["width"][idx]
            height = data["height"][idx]
            center_x = left + width // 2
            center_y = top + height // 2
            matches.append(
                f"- '{token}' at ({center_x},{center_y}) box=({left},{top},{width},{height})"
            )
            if len(matches) >= limit:
                break

    if not matches:
        return f"No screen text matched '{text}'"
    return f"Screen text matches for '{text}':\n" + "\n".join(matches)


def click_text_on_screen_action(text: str, occurrence: int = 1) -> str:
    if pytesseract is None:
        raise ValueError("OCR support is not available because pytesseract is not installed")
    if configure_pytesseract(pytesseract) is None:
        raise ValueError(f"Screen OCR is unavailable: {describe_tesseract_problem()}")
    query = (text or "").strip().lower()
    if not query:
        raise ValueError("text is required")
    try:
        image = ImageGrab.grab()
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    except Exception as exc:
        raise ValueError(f"Screen OCR failed: {exc}") from exc

    matches: list[tuple[int, int, int, int, int, str]] = []
    for idx, raw in enumerate(data.get("text", [])):
        token = (raw or "").strip()
        if token and query in token.lower():
            left = int(data["left"][idx])
            top = int(data["top"][idx])
            width = int(data["width"][idx])
            height = int(data["height"][idx])
            center_x = left + width // 2
            center_y = top + height // 2
            matches.append((top, left, center_x, center_y, idx, token))

    if not matches:
        raise ValueError(f"No screen text matched '{text}'")

    matches.sort(key=lambda item: (item[0], item[1]))
    index = max(0, min(int(occurrence or 1) - 1, len(matches) - 1))
    _top, _left, center_x, center_y, _idx, token = matches[index]
    desktop_click_action(center_x, center_y, button="left", clicks=1)
    return f"Clicked screen text '{token}' for query '{text}' at ({center_x},{center_y})"
