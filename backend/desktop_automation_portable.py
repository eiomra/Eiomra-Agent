"""
Cross-platform desktop automation adapter.

On Windows, this delegates to the existing desktop_automation module.
On Linux, it uses common desktop commands such as xdotool, wmctrl,
gtk-launch, and xdg-open when available.
"""

from __future__ import annotations

import base64
import io
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageGrab

from tesseract_utils import configure_pytesseract, describe_tesseract_problem

try:
    import pytesseract  # type: ignore
except Exception:  # pragma: no cover
    pytesseract = None
else:
    configure_pytesseract(pytesseract)


IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")


if IS_WINDOWS:
    from desktop_automation import (  # type: ignore
        click_text_on_screen_action,
        close_window_action,
        desktop_click_action,
        desktop_hotkey_action,
        desktop_press_key_action,
        desktop_scroll_action,
        desktop_screenshot_action,
        desktop_screenshot_b64,
        desktop_type_action,
        desktop_type_verified_action,
        drag_mouse_action,
        find_text_on_screen_action,
        focus_window_action,
        gmail_compose_action,
        list_installed_apps_action,
        list_windows_action,
        move_mouse_action,
        open_application_action,
        open_browser_url_action,
        select_menu_item_action,
    )
else:
    @dataclass(slots=True)
    class WindowInfo:
        window_id: str
        desktop: str
        class_name: str
        title: str

    _DESKTOP_ENTRY_DIRS = [
        os.path.expanduser("~/.local/share/applications"),
        "/usr/local/share/applications",
        "/usr/share/applications",
        "/var/lib/flatpak/exports/share/applications",
    ]

    _SPECIAL_KEYS = {
        "enter": "Return",
        "tab": "Tab",
        "esc": "Escape",
        "escape": "Escape",
        "space": "space",
        "backspace": "BackSpace",
        "delete": "Delete",
        "up": "Up",
        "down": "Down",
        "left": "Left",
        "right": "Right",
        "home": "Home",
        "end": "End",
        "pageup": "Page_Up",
        "pagedown": "Page_Down",
        "f1": "F1",
        "f2": "F2",
        "f3": "F3",
        "f4": "F4",
        "f5": "F5",
        "f6": "F6",
        "f7": "F7",
        "f8": "F8",
        "f9": "F9",
        "f10": "F10",
        "f11": "F11",
        "f12": "F12",
    }

    _MODIFIER_KEYS = {
        "ctrl": "ctrl",
        "control": "ctrl",
        "shift": "shift",
        "alt": "alt",
        "meta": "super",
        "super": "super",
        "win": "super",
        "windows": "super",
        "command": "super",
    }

    def _sleep_brief(delay: float = 0.08) -> None:
        time.sleep(delay)

    def _has_tool(name: str) -> bool:
        return shutil.which(name) is not None

    def _require_tool(name: str, purpose: str) -> None:
        if not _has_tool(name):
            raise ValueError(
                f"{purpose} requires '{name}' on Linux. Install it and retry."
            )

    def _run(
        args: list[str],
        *,
        check: bool = True,
        capture_output: bool = True,
        text: bool = True,
        cwd: str | None = None,
        timeout: int = 20,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            args,
            cwd=cwd or None,
            capture_output=capture_output,
            text=text,
            timeout=timeout,
            check=False,
        )
        if check and completed.returncode != 0:
            stderr = (completed.stderr or completed.stdout or "").strip()
            raise ValueError(f"Command failed ({' '.join(args)}): {stderr or completed.returncode}")
        return completed

    def _normalize_command(command: str) -> str:
        return (command or "").strip().strip('"').strip()

    def _split_command(command: str) -> list[str]:
        text = _normalize_command(command)
        if not text:
            return []
        try:
            parts = shlex.split(text)
        except Exception:
            parts = [text]
        return [part for part in parts if part]

    def _command_app_tokens(command: str) -> list[str]:
        raw = _normalize_command(command).lower()
        if not raw:
            return []
        raw = raw.replace(".desktop", "")
        pieces = re.split(r"[\s\\/:\(\)\[\],;._-]+", raw)
        tokens: list[str] = []
        for piece in pieces:
            token = piece.strip()
            if not token:
                continue
            if token in {"app", "application", "linux", "usr", "bin"}:
                continue
            if token not in tokens:
                tokens.append(token)
        return tokens

    def _resolve_key_name(key: str) -> str:
        token = str(key or "").strip().lower()
        if not token:
            raise ValueError("key is required")
        if token in _MODIFIER_KEYS:
            return _MODIFIER_KEYS[token]
        if token in _SPECIAL_KEYS:
            return _SPECIAL_KEYS[token]
        if len(token) == 1:
            return token
        return key

    def _desktop_entry_candidates() -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for directory in _DESKTOP_ENTRY_DIRS:
            if not os.path.isdir(directory):
                continue
            for path in Path(directory).glob("*.desktop"):
                path_str = str(path)
                if path_str in seen:
                    continue
                seen.add(path_str)
                parser = ConfigParser(interpolation=None)
                try:
                    parser.read(path, encoding="utf-8")
                    if not parser.has_section("Desktop Entry"):
                        continue
                    name = parser.get("Desktop Entry", "Name", fallback=path.stem)
                    exec_line = parser.get("Desktop Entry", "Exec", fallback="")
                    no_display = parser.get("Desktop Entry", "NoDisplay", fallback="false").lower() == "true"
                    if no_display:
                        continue
                    rows.append((path.stem, name, exec_line))
                except Exception:
                    continue
        return rows

    def _search_desktop_entries(query: str, limit: int = 20) -> list[tuple[str, str, str]]:
        lowered = _normalize_command(query).lower()
        tokens = _command_app_tokens(query)
        matches: list[tuple[int, tuple[str, str, str]]] = []
        for desktop_id, name, exec_line in _desktop_entry_candidates():
            haystack = f"{desktop_id} {name} {exec_line}".lower()
            score = 0
            if lowered and lowered in haystack:
                score += 8
            score += sum(3 for token in tokens if token in haystack)
            if score > 0:
                matches.append((score, (desktop_id, name, exec_line)))
        matches.sort(key=lambda item: (-item[0], item[1][1]))
        return [row for _, row in matches[:limit]]

    def _list_windows() -> list[WindowInfo]:
        if not _has_tool("wmctrl"):
            return []
        out = _run(["wmctrl", "-lx"], check=False)
        rows: list[WindowInfo] = []
        for line in (out.stdout or "").splitlines():
            parts = line.split(None, 4)
            if len(parts) < 5:
                continue
            rows.append(
                WindowInfo(
                    window_id=parts[0],
                    desktop=parts[1],
                    class_name=parts[3],
                    title=parts[4].strip(),
                )
            )
        return rows

    def _find_window_matches(query: str) -> list[WindowInfo]:
        lowered = _normalize_command(query).lower()
        if not lowered:
            return []
        tokens = _command_app_tokens(query)
        matches: list[tuple[int, WindowInfo]] = []
        for item in _list_windows():
            haystack = f"{item.title} {item.class_name}".lower()
            score = 0
            if lowered in haystack:
                score += 10
            score += sum(3 for token in tokens if token in haystack)
            if score > 0:
                matches.append((score, item))
        matches.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in matches]

    def _wait_for_window(query: str, timeout: float = 8.0) -> WindowInfo | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            matches = _find_window_matches(query)
            if matches:
                return matches[0]
            _sleep_brief(0.25)
        return None

    def _activate_window(window_id: str) -> None:
        if _has_tool("wmctrl"):
            _run(["wmctrl", "-ia", window_id], check=False)
        if _has_tool("xdotool"):
            _run(["xdotool", "windowactivate", "--sync", window_id], check=False)

    def _type_text_linux(text: str, delay_ms: int = 25) -> None:
        _require_tool("xdotool", "Desktop typing")
        value = str(text or "")
        if not value:
            return
        for index, line in enumerate(value.split("\n")):
            if line:
                _run(["xdotool", "type", "--delay", str(delay_ms), "--", line], check=True)
            if index < len(value.split("\n")) - 1:
                _run(["xdotool", "key", "Return"], check=True)

    def _capture_screen_image() -> Image.Image:
        try:
            return ImageGrab.grab()
        except Exception:
            pass

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            if _has_tool("gnome-screenshot"):
                _run(["gnome-screenshot", "-f", tmp_path], check=True)
            elif _has_tool("scrot"):
                _run(["scrot", tmp_path], check=True)
            elif _has_tool("import"):
                _run(["import", "-window", "root", tmp_path], check=True)
            else:
                raise ValueError(
                    "Desktop screenshot capture on Linux requires Pillow ImageGrab support, "
                    "or one of: gnome-screenshot, scrot, imagemagick 'import'."
                )
            with Image.open(tmp_path) as image:
                return image.copy()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _verify_text_appeared(text: str, timeout: float = 1.5) -> bool:
        if pytesseract is None or configure_pytesseract(pytesseract) is None:
            return True
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

    def list_windows_action(limit: int = 50) -> str:
        _require_tool("wmctrl", "Listing open windows")
        windows = _list_windows()
        rows = [f"- {item.title} [{item.class_name}] id={item.window_id}" for item in windows[:limit]]
        if len(windows) > limit:
            rows.append(f"... and {len(windows) - limit} more")
        return "Open windows:\n" + ("\n".join(rows) if rows else "(none)")

    def focus_window_action(title: str) -> str:
        _require_tool("wmctrl", "Focusing a window")
        query = _normalize_command(title)
        if not query:
            raise ValueError("title text is required")
        matches = _find_window_matches(query)
        if not matches:
            raise ValueError(f"No visible window matched '{title}'")
        target = matches[0]
        _activate_window(target.window_id)
        return f"Focused window: {target.title} [{target.class_name}] id={target.window_id}"

    def list_installed_apps_action(query: str = "") -> str:
        q = _normalize_command(query).lower()
        results: list[str] = []

        for desktop_id, name, exec_line in _search_desktop_entries(query or "app", limit=100 if q else 30):
            if q and q not in f"{desktop_id} {name} {exec_line}".lower():
                continue
            results.append(f"[desktop]  {name}  |  {desktop_id}  |  {exec_line[:120]}")

        if q:
            for path_dir in os.environ.get("PATH", "").split(os.pathsep):
                if not os.path.isdir(path_dir):
                    continue
                try:
                    for name in os.listdir(path_dir):
                        lowered = name.lower()
                        if q not in lowered:
                            continue
                        full_path = os.path.join(path_dir, name)
                        if os.path.isfile(full_path) and os.access(full_path, os.X_OK):
                            results.append(f"[exec]     {name}  |  {full_path}")
                except OSError:
                    continue

        if not results:
            return f"No installed apps found{' matching ' + repr(query) if q else ''}."
        capped = results[:300 if not q else len(results)]
        suffix = f"\n... ({len(results) - len(capped)} more entries omitted, narrow with a query)" if len(results) > len(capped) else ""
        return f"Installed apps{' matching ' + repr(query) if q else ''} ({len(capped)} shown):\n" + "\n".join(capped) + suffix

    def open_application_action(command: str, cwd: str = "") -> str:
        normalized = _normalize_command(command)
        if not normalized:
            raise ValueError("command is required")

        matches = _find_window_matches(normalized)
        if matches:
            target = matches[0]
            _activate_window(target.window_id)
            return f"Focused existing window before launch: {target.title} [{target.class_name}] id={target.window_id}"

        attempted: list[str] = []
        working_dir = os.path.abspath(cwd) if cwd else None
        parts = _split_command(normalized)
        primary = parts[0] if parts else normalized

        if os.path.exists(primary):
            try:
                proc = subprocess.Popen(parts or [primary], cwd=working_dir)
                focused = _wait_for_window(normalized, timeout=8.0)
                if focused:
                    return f"Opened application path: {os.path.abspath(primary)} (pid={proc.pid}) | Focused window: {focused.title} [{focused.class_name}] id={focused.window_id}"
                return f"Opened application path: {os.path.abspath(primary)} (pid={proc.pid})"
            except Exception as exc:
                attempted.append(f"path:{primary} -> {exc}")

        resolved = shutil.which(primary)
        if resolved:
            try:
                proc = subprocess.Popen(parts, cwd=working_dir)
                focused = _wait_for_window(normalized, timeout=8.0)
                if focused:
                    return f"Opened application via executable: {resolved} (pid={proc.pid}) | Focused window: {focused.title} [{focused.class_name}] id={focused.window_id}"
                return f"Opened application via executable: {resolved} (pid={proc.pid})"
            except Exception as exc:
                attempted.append(f"exec:{resolved} -> {exc}")

        for desktop_id, name, _exec_line in _search_desktop_entries(normalized, limit=8):
            if _has_tool("gtk-launch"):
                try:
                    proc = subprocess.Popen(["gtk-launch", desktop_id], cwd=working_dir)
                    focused = _wait_for_window(name or normalized, timeout=10.0)
                    if focused:
                        return f"Opened application via desktop entry: {name} [{desktop_id}] (pid={proc.pid}) | Focused window: {focused.title} [{focused.class_name}] id={focused.window_id}"
                    return f"Opened application via desktop entry: {name} [{desktop_id}] (pid={proc.pid})"
                except Exception as exc:
                    attempted.append(f"gtk-launch:{desktop_id} -> {exc}")

        if os.path.exists(normalized):
            for opener in ("xdg-open", "gio"):
                if not _has_tool(opener):
                    continue
                try:
                    cmd = [opener, normalized] if opener == "xdg-open" else [opener, "open", normalized]
                    proc = subprocess.Popen(cmd, cwd=working_dir)
                    return f"Opened path via {opener}: {normalized} (pid={proc.pid})"
                except Exception as exc:
                    attempted.append(f"{opener}:{normalized} -> {exc}")

        try:
            proc = subprocess.Popen(normalized, cwd=working_dir, shell=True)
            focused = _wait_for_window(normalized, timeout=8.0)
            if focused:
                return f"Opened application via shell command: {normalized} (pid={proc.pid}) | Focused window: {focused.title} [{focused.class_name}] id={focused.window_id}"
            return f"Opened application via shell command: {normalized} (pid={proc.pid})"
        except Exception as exc:
            attempted.append(f"shell:{normalized} -> {exc}")

        attempted_text = "; ".join(attempted[:8]) if attempted else "no launch methods were attempted"
        raise ValueError(
            f"Automatic application launch failed for '{normalized}'. Details: {attempted_text[:500]}"
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

        parts = _split_command(browser_command)
        primary = parts[0] if parts else browser_command
        working_dir = os.path.abspath(cwd) if cwd else None

        if shutil.which(primary):
            proc = subprocess.Popen(parts + [url], cwd=working_dir)
            focused = _wait_for_window(primary, timeout=8.0)
            if focused:
                return f"Opened URL in external browser: {url} via {browser_command} (pid={proc.pid}) | Focused window: {focused.title} [{focused.class_name}] id={focused.window_id}"
            return f"Opened URL in external browser: {url} via {browser_command} (pid={proc.pid})"

        if _has_tool("xdg-open"):
            proc = subprocess.Popen(["xdg-open", url], cwd=working_dir)
            return f"Opened URL via xdg-open: {url} (pid={proc.pid})"

        raise ValueError(f"Could not open URL in external browser with '{browser_command}'")

    def desktop_click_action(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
        _require_tool("xdotool", "Desktop clicking")
        button_name = str(button or "left").lower()
        button_map = {"left": "1", "middle": "2", "right": "3"}
        if button_name not in button_map:
            raise ValueError(f"Unsupported mouse button: {button}")
        _run(["xdotool", "mousemove", str(int(x)), str(int(y))], check=True)
        for _ in range(max(1, int(clicks))):
            _run(["xdotool", "click", button_map[button_name]], check=True)
        return f"Desktop clicked {button_name} at ({int(x)}, {int(y)}) x{max(1, int(clicks))}"

    def move_mouse_action(x: int, y: int) -> str:
        _require_tool("xdotool", "Mouse movement")
        _run(["xdotool", "mousemove", str(int(x)), str(int(y))], check=True)
        return f"Moved mouse to ({int(x)}, {int(y)})"

    def drag_mouse_action(start_x: int, start_y: int, end_x: int, end_y: int, button: str = "left") -> str:
        _require_tool("xdotool", "Mouse dragging")
        button_name = str(button or "left").lower()
        button_map = {"left": "1", "middle": "2", "right": "3"}
        if button_name not in button_map:
            raise ValueError(f"Unsupported mouse button: {button}")
        _run(["xdotool", "mousemove", str(int(start_x)), str(int(start_y))], check=True)
        _run(["xdotool", "mousedown", button_map[button_name]], check=True)
        _run(["xdotool", "mousemove", "--sync", str(int(end_x)), str(int(end_y))], check=True)
        _run(["xdotool", "mouseup", button_map[button_name]], check=True)
        return f"Dragged mouse from ({int(start_x)}, {int(start_y)}) to ({int(end_x)}, {int(end_y)}) with {button_name}"

    def desktop_scroll_action(amount: int) -> str:
        _require_tool("xdotool", "Desktop scrolling")
        signed = int(amount or 0)
        if signed == 0:
            return "No scrolling needed (amount=0)"
        button = "4" if signed > 0 else "5"
        steps = max(1, min(20, abs(signed) // 120 or 1))
        for _ in range(steps):
            _run(["xdotool", "click", button], check=True)
        return f"Desktop scrolled {'up' if signed > 0 else 'down'} {steps} step(s)"

    def desktop_hotkey_action(keys: list[str]) -> str:
        _require_tool("xdotool", "Desktop hotkeys")
        normalized = [_resolve_key_name(str(key)) for key in keys if str(key).strip()]
        if not normalized:
            raise ValueError("keys are required")
        _run(["xdotool", "key", "+".join(normalized)], check=True)
        return f"Desktop hotkey pressed: {'+'.join(normalized)}"

    def desktop_press_key_action(key: str) -> str:
        _require_tool("xdotool", "Desktop key presses")
        resolved = _resolve_key_name(key)
        _run(["xdotool", "key", resolved], check=True)
        return f"Desktop key pressed: {resolved}"

    def desktop_type_action(text: str, typing_mode: str = "auto") -> str:
        if text is None:
            raise ValueError("text is required")
        typed = str(text)
        if not typed:
            return "Nothing to type (empty text)"
        delay = 25 if str(typing_mode or "auto").lower() != "char" else 60
        _type_text_linux(typed, delay_ms=delay)
        return f"Desktop typed {len(typed)} chars on Linux via xdotool"

    def desktop_type_verified_action(text: str, typing_mode: str = "auto") -> str:
        result = desktop_type_action(text, typing_mode=typing_mode)
        if _verify_text_appeared(str(text or "")):
            return result + " [verified]"
        return result + " [verification unavailable or inconclusive]"

    def gmail_compose_action(
        to: str,
        subject: str,
        body: str,
        *,
        window_focused: bool = False,
    ) -> str:
        del window_focused
        desktop_press_key_action("escape")
        _sleep_brief(0.3)
        desktop_press_key_action("c")
        _sleep_brief(1.2)
        desktop_press_key_action("tab")
        _sleep_brief(0.25)
        _type_text_linux(str(to or ""), delay_ms=35)
        _sleep_brief(0.15)
        desktop_press_key_action("tab")
        _sleep_brief(0.35)
        desktop_press_key_action("tab")
        _sleep_brief(0.2)
        _type_text_linux(str(subject or ""), delay_ms=35)
        _sleep_brief(0.15)
        desktop_press_key_action("tab")
        _sleep_brief(0.25)
        _type_text_linux(str(body or ""), delay_ms=25)
        return (
            f"Gmail compose filled via keyboard: to='{to}', subject='{subject}', "
            f"body={len(str(body or ''))} chars. Ready to send."
        )

    def select_menu_item_action(menu_path: str) -> str:
        parts = [part.strip() for part in str(menu_path or "").split(">") if part.strip()]
        if not parts:
            raise ValueError("menu path is required, for example 'File>Open'")
        desktop_hotkey_action(["alt"])
        _sleep_brief(0.12)
        for index, part in enumerate(parts):
            desktop_press_key_action(part[0])
            _sleep_brief(0.18 if index < len(parts) - 1 else 0.08)
        return f"Selected menu path: {' > '.join(parts)}"

    def desktop_screenshot_action(path: str) -> str:
        image = _capture_screen_image()
        image.save(path)
        return f"Saved desktop screenshot: {path} ({image.width}x{image.height})"

    def desktop_screenshot_b64(max_width: int = 1280) -> tuple[str, int, int]:
        image = _capture_screen_image()
        w, h = image.size
        if w > max_width:
            scale = max_width / w
            image = image.resize((max_width, int(h * scale)), resample=1)
            w, h = image.size
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return b64, w, h

    def close_window_action(title: str) -> str:
        if title and _has_tool("wmctrl"):
            matches = _find_window_matches(title)
            if not matches:
                raise ValueError(f"No visible window matched '{title}'")
            target = matches[0]
            _run(["wmctrl", "-ic", target.window_id], check=True)
            return f"Requested close for window: {target.title} [{target.class_name}] id={target.window_id}"
        _require_tool("xdotool", "Closing the active window")
        _run(["xdotool", "getactivewindow", "windowkill"], check=True)
        return "Requested close for active window"

    def find_text_on_screen_action(text: str, limit: int = 20) -> str:
        if pytesseract is None:
            raise ValueError("OCR support is not available because pytesseract is not installed")
        if configure_pytesseract(pytesseract) is None:
            raise ValueError(f"Screen OCR is unavailable: {describe_tesseract_problem()}")
        query = (text or "").strip().lower()
        if not query:
            raise ValueError("text is required")
        image = _capture_screen_image()
        try:
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        except Exception as exc:
            raise ValueError(f"Screen OCR failed: {exc}") from exc

        matches: list[str] = []
        for idx, raw in enumerate(data.get("text", [])):
            token = (raw or "").strip()
            if token and query in token.lower():
                left = int(data["left"][idx])
                top = int(data["top"][idx])
                width = int(data["width"][idx])
                height = int(data["height"][idx])
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
        image = _capture_screen_image()
        try:
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        except Exception as exc:
            raise ValueError(f"Screen OCR failed: {exc}") from exc

        matches: list[tuple[int, int, int, int, str]] = []
        for idx, raw in enumerate(data.get("text", [])):
            token = (raw or "").strip()
            if token and query in token.lower():
                left = int(data["left"][idx])
                top = int(data["top"][idx])
                width = int(data["width"][idx])
                height = int(data["height"][idx])
                center_x = left + width // 2
                center_y = top + height // 2
                matches.append((top, left, center_x, center_y, token))

        if not matches:
            raise ValueError(f"No screen text matched '{text}'")

        matches.sort(key=lambda item: (item[0], item[1]))
        index = max(0, min(int(occurrence or 1) - 1, len(matches) - 1))
        _top, _left, center_x, center_y, token = matches[index]
        desktop_click_action(center_x, center_y, button="left", clicks=1)
        return f"Clicked screen text '{token}' for query '{text}' at ({center_x},{center_y})"

