"""
Local filesystem helpers for workspace-aware agent actions.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
import json
import csv
import io
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from attachment_reader import AttachmentReader
import fitz  # type: ignore
from PIL import Image
from tesseract_utils import configure_pytesseract, describe_tesseract_problem
import yaml

try:
    import pytesseract  # type: ignore
except Exception:  # pragma: no cover
    pytesseract = None
else:
    configure_pytesseract(pytesseract)

attachment_reader = AttachmentReader()


@dataclass(slots=True)
class CommandExecutionRequest:
    command: str
    cwd: str
    resolved_cwd: str


def get_filesystem_root(cfg: dict[str, Any], workspace_root: str) -> str:
    configured = str(cfg.get("filesystem_root") or workspace_root).strip()
    return os.path.abspath(configured or workspace_root)


def resolve_allowed_path(path_value: str, cfg: dict[str, Any], workspace_root: str) -> str:
    if not path_value:
        raise ValueError("Path is required")

    scope = str(cfg.get("filesystem_scope", "workspace"))
    base_root = get_filesystem_root(cfg, workspace_root)
    raw_path = os.path.expandvars(os.path.expanduser(path_value.strip()))
    resolved = os.path.abspath(raw_path if os.path.isabs(raw_path) else os.path.join(base_root, raw_path))

    if scope != "full_computer":
        common = os.path.commonpath([base_root, resolved])
        if common != base_root:
            raise ValueError(f"Path '{path_value}' is outside allowed root '{base_root}'")

    return resolved


def summarize_dir(path: str, limit: int = 40) -> str:
    entries = sorted(os.listdir(path))
    rows = []
    for name in entries[:limit]:
        full = os.path.join(path, name)
        kind = "dir" if os.path.isdir(full) else "file"
        extra = ""
        if os.path.isfile(full):
            try:
                summary = attachment_reader.summarize(full, max_chars=800)
                extra = f" - {summary.kind}: {summary.summary}"
            except Exception:
                extra = ""
        rows.append(f"- {name} [{kind}]{extra}")
    if len(entries) > limit:
        rows.append(f"... and {len(entries) - limit} more")
    return "\n".join(rows) if rows else "(empty directory)"


def read_file_action(path: str, cfg: dict[str, Any], workspace_root: str, max_chars: int = 6000) -> str:
    resolved = resolve_allowed_path(path, cfg, workspace_root)
    if not os.path.isfile(resolved):
        raise ValueError(f"File not found: {resolved}")
    summary = attachment_reader.summarize(resolved, max_chars=max_chars)
    return f"Read file {resolved}\n{summary.to_prompt_block()}"


def read_json_action(path: str, cfg: dict[str, Any], workspace_root: str, max_chars: int = 8000) -> str:
    resolved = resolve_allowed_path(path, cfg, workspace_root)
    if not os.path.isfile(resolved):
        raise ValueError(f"File not found: {resolved}")
    with open(resolved, "r", encoding="utf-8", errors="ignore") as handle:
        data = json.load(handle)
    rendered = json.dumps(data, indent=2, ensure_ascii=False)
    return f"Read JSON file {resolved}\n{rendered[:max_chars]}"


def read_csv_action(path: str, cfg: dict[str, Any], workspace_root: str, max_rows: int = 50) -> str:
    resolved = resolve_allowed_path(path, cfg, workspace_root)
    if not os.path.isfile(resolved):
        raise ValueError(f"File not found: {resolved}")
    with open(resolved, "r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.reader(handle)
        rows = list(reader)
    preview = rows[:max_rows]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(preview)
    return (
        f"Read CSV file {resolved}\n"
        f"Rows: {len(rows)}\n"
        f"Preview:\n{buffer.getvalue()[:8000]}"
    )


def list_directory_action(path: str, cfg: dict[str, Any], workspace_root: str) -> str:
    target = path or "."
    resolved = resolve_allowed_path(target, cfg, workspace_root)
    if not os.path.isdir(resolved):
        raise ValueError(f"Directory not found: {resolved}")
    return f"Directory listing for {resolved}\n{summarize_dir(resolved)}"


def create_directory_action(path: str, cfg: dict[str, Any], workspace_root: str) -> str:
    resolved = resolve_allowed_path(path, cfg, workspace_root)
    os.makedirs(resolved, exist_ok=True)
    return f"Created directory: {resolved}"


def create_file_action(path: str, content: str, cfg: dict[str, Any], workspace_root: str) -> str:
    resolved = resolve_allowed_path(path, cfg, workspace_root)
    parent = os.path.dirname(resolved)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.exists(resolved):
        raise ValueError(f"File already exists: {resolved}")
    with open(resolved, "w", encoding="utf-8") as handle:
        handle.write(content or "")
    return f"Created file: {resolved} ({len(content or '')} chars)"


def create_json_action(path: str, content: str, cfg: dict[str, Any], workspace_root: str) -> str:
    resolved = resolve_allowed_path(path, cfg, workspace_root)
    parent = os.path.dirname(resolved)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        data = json.loads(content) if (content or "").strip() else {}
    except Exception:
        data = {"content": content or ""}
    rendered = json.dumps(data, indent=2, ensure_ascii=False)
    with open(resolved, "w", encoding="utf-8") as handle:
        handle.write(rendered + "\n")
    return f"Created JSON file: {resolved}"


def create_csv_action(path: str, content: str, cfg: dict[str, Any], workspace_root: str) -> str:
    resolved = resolve_allowed_path(path, cfg, workspace_root)
    parent = os.path.dirname(resolved)
    if parent:
        os.makedirs(parent, exist_ok=True)

    rendered = content or ""
    text = (content or "").strip()
    if text:
        try:
            data = json.loads(text)
            buffer = io.StringIO()
            if isinstance(data, list) and data:
                if all(isinstance(item, dict) for item in data):
                    fieldnames = []
                    seen = set()
                    for row in data:
                        for key in row.keys():
                            if key not in seen:
                                seen.add(key)
                                fieldnames.append(key)
                    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(data)
                    rendered = buffer.getvalue()
                elif all(isinstance(item, (list, tuple)) for item in data):
                    writer = csv.writer(buffer)
                    writer.writerows(data)
                    rendered = buffer.getvalue()
        except Exception:
            pass

    with open(resolved, "w", encoding="utf-8", newline="") as handle:
        handle.write(rendered)
    return f"Created CSV file: {resolved}"


def create_markdown_report_action(path: str, title: str, content: str, cfg: dict[str, Any], workspace_root: str) -> str:
    resolved = resolve_allowed_path(path, cfg, workspace_root)
    parent = os.path.dirname(resolved)
    if parent:
        os.makedirs(parent, exist_ok=True)

    lines: list[str] = []
    clean_title = (title or "").strip()
    if clean_title:
        lines.append(f"# {clean_title}")
        lines.append("")
    body = (content or "").strip()
    if body:
        lines.append(body)
    else:
        lines.append("_No content provided._")
    rendered = "\n".join(lines).rstrip() + "\n"
    with open(resolved, "w", encoding="utf-8") as handle:
        handle.write(rendered)
    return f"Created Markdown report: {resolved}"


def write_yaml_action(path: str, content: str, cfg: dict[str, Any], workspace_root: str) -> str:
    resolved = resolve_allowed_path(path, cfg, workspace_root)
    parent = os.path.dirname(resolved)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        data = json.loads(content) if (content or "").strip() else {}
    except Exception:
        data = yaml.safe_load(content) if (content or "").strip() else {}
    rendered = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    with open(resolved, "w", encoding="utf-8") as handle:
        handle.write(rendered)
    return f"Created YAML file: {resolved}"


def create_pdf_action(path: str, title: str, content: str, cfg: dict[str, Any], workspace_root: str) -> str:
    resolved = resolve_allowed_path(path, cfg, workspace_root)
    parent = os.path.dirname(resolved)
    if parent:
        os.makedirs(parent, exist_ok=True)

    doc = fitz.open()
    page_width = 595
    page_height = 842
    margin = 50
    font_size = 11
    line_height = 15
    title_size = 18
    usable_width = page_width - (margin * 2)
    approx_chars = max(40, int(usable_width / 6.1))

    lines: list[tuple[str, str]] = []
    if title.strip():
        lines.append(("title", title.strip()))
        lines.append(("space", ""))
    paragraphs = (content or "").replace("\r\n", "\n").split("\n")
    for paragraph in paragraphs:
        if not paragraph.strip():
            lines.append(("space", ""))
            continue
        for wrapped in textwrap.wrap(paragraph, width=approx_chars, replace_whitespace=False, drop_whitespace=False):
            lines.append(("body", wrapped))

    if not lines:
        lines = [("body", "(empty document)")]

    page = doc.new_page(width=page_width, height=page_height)
    y = margin
    for kind, line in lines:
        size = title_size if kind == "title" else font_size
        if kind == "space":
            y += line_height
            continue
        if y > page_height - margin - line_height:
            page = doc.new_page(width=page_width, height=page_height)
            y = margin
        page.insert_text((margin, y), line, fontsize=size, fontname="helv")
        y += 24 if kind == "title" else line_height

    doc.save(resolved)
    doc.close()
    return f"Created PDF: {resolved}"


def extract_pdf_text_action(
    path: str,
    destination: str,
    cfg: dict[str, Any],
    workspace_root: str,
    max_chars: int = 8000,
) -> str:
    resolved = resolve_allowed_path(path, cfg, workspace_root)
    if not os.path.isfile(resolved):
        raise ValueError(f"File not found: {resolved}")
    doc = fitz.open(resolved)
    try:
        pages: list[str] = []
        for page in doc:
            text = page.get_text("text") or ""
            pages.append(text.strip())
    finally:
        doc.close()
    extracted = "\n\n".join(part for part in pages if part).strip()
    if destination:
        resolved_destination = resolve_allowed_path(destination, cfg, workspace_root)
        parent = os.path.dirname(resolved_destination)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(resolved_destination, "w", encoding="utf-8") as handle:
            handle.write(extracted + ("\n" if extracted else ""))
        return (
            f"Extracted PDF text from {resolved} to {resolved_destination} "
            f"({len(extracted)} chars)"
        )
    excerpt = extracted[:max_chars] if extracted else "(no extractable text found)"
    return f"Extracted PDF text from {resolved}\n{excerpt}"


def ocr_image_to_text_action(
    path: str,
    destination: str,
    cfg: dict[str, Any],
    workspace_root: str,
    max_chars: int = 8000,
) -> str:
    if pytesseract is None:
        raise ValueError("OCR support is not available because pytesseract is not installed")
    if configure_pytesseract(pytesseract) is None:
        raise ValueError(f"OCR is unavailable: {describe_tesseract_problem()}")
    resolved = resolve_allowed_path(path, cfg, workspace_root)
    if not os.path.isfile(resolved):
        raise ValueError(f"Image not found: {resolved}")
    try:
        with Image.open(resolved) as image:
            text = pytesseract.image_to_string(image).strip()
    except Exception as exc:
        raise ValueError(f"OCR failed for {resolved}: {exc}") from exc
    if destination:
        resolved_destination = resolve_allowed_path(destination, cfg, workspace_root)
        parent = os.path.dirname(resolved_destination)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(resolved_destination, "w", encoding="utf-8") as handle:
            handle.write(text + ("\n" if text else ""))
        return f"Extracted OCR text from {resolved} to {resolved_destination} ({len(text)} chars)"
    return f"OCR text from {resolved}\n{(text or '(no text detected)')[:max_chars]}"


def write_file_action(path: str, content: str, append: bool, cfg: dict[str, Any], workspace_root: str) -> str:
    resolved = resolve_allowed_path(path, cfg, workspace_root)
    parent = os.path.dirname(resolved)
    if parent:
        os.makedirs(parent, exist_ok=True)
    mode = "a" if append else "w"
    with open(resolved, mode, encoding="utf-8") as handle:
        handle.write(content or "")
    verb = "Appended to" if append else "Wrote"
    return f"{verb} file: {resolved} ({len(content or '')} chars)"


def patch_file_action(
    path: str,
    old_text: str,
    new_text: str,
    replace_all: bool,
    cfg: dict[str, Any],
    workspace_root: str,
) -> str:
    resolved = resolve_allowed_path(path, cfg, workspace_root)
    if not os.path.isfile(resolved):
        raise ValueError(f"File not found: {resolved}")
    with open(resolved, "r", encoding="utf-8", errors="ignore") as handle:
        content = handle.read()
    if old_text not in content:
        raise ValueError("old_text not found in target file")
    updated = content.replace(old_text, new_text, -1 if replace_all else 1)
    with open(resolved, "w", encoding="utf-8") as handle:
        handle.write(updated)
    count = content.count(old_text) if replace_all else 1
    return f"Patched file: {resolved} ({count} replacement{'s' if count != 1 else ''})"


def rename_path_action(source: str, destination: str, cfg: dict[str, Any], workspace_root: str) -> str:
    resolved_source = resolve_allowed_path(source, cfg, workspace_root)
    resolved_destination = resolve_allowed_path(destination, cfg, workspace_root)
    parent = os.path.dirname(resolved_destination)
    if parent:
        os.makedirs(parent, exist_ok=True)
    os.replace(resolved_source, resolved_destination)
    return f"Renamed path: {resolved_source} -> {resolved_destination}"


def copy_paths_action(sources: list[str], destination: str, cfg: dict[str, Any], workspace_root: str) -> str:
    if not sources:
        raise ValueError("sources is required")
    resolved_destination = resolve_allowed_path(destination, cfg, workspace_root)
    copied: list[str] = []
    if len(sources) > 1:
        os.makedirs(resolved_destination, exist_ok=True)
    for source in sources:
        resolved_source = resolve_allowed_path(source, cfg, workspace_root)
        target = resolved_destination
        if len(sources) > 1 or os.path.isdir(resolved_destination):
            os.makedirs(resolved_destination, exist_ok=True)
            target = os.path.join(resolved_destination, os.path.basename(resolved_source))
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if os.path.isdir(resolved_source):
            shutil.copytree(resolved_source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(resolved_source, target)
        copied.append(f"{resolved_source} -> {target}")
    return "Copied paths:\n" + "\n".join(f"- {item}" for item in copied)


def move_paths_action(sources: list[str], destination: str, cfg: dict[str, Any], workspace_root: str) -> str:
    if not sources:
        raise ValueError("sources is required")
    resolved_destination = resolve_allowed_path(destination, cfg, workspace_root)
    if len(sources) > 1:
        os.makedirs(resolved_destination, exist_ok=True)
        if not os.path.isdir(resolved_destination):
            raise ValueError("destination must be a directory when moving multiple paths")
    moved: list[str] = []
    for source in sources:
        resolved_source = resolve_allowed_path(source, cfg, workspace_root)
        target = resolved_destination
        if len(sources) > 1 or os.path.isdir(resolved_destination):
            target = os.path.join(resolved_destination, os.path.basename(resolved_source))
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.move(resolved_source, target)
        moved.append(f"{resolved_source} -> {target}")
    return "Moved paths:\n" + "\n".join(f"- {item}" for item in moved)


def delete_paths_action(paths: list[str], recursive: bool, cfg: dict[str, Any], workspace_root: str) -> str:
    if not paths:
        raise ValueError("paths is required")
    deleted: list[str] = []
    for item in paths:
        resolved = resolve_allowed_path(item, cfg, workspace_root)
        if os.path.isdir(resolved):
            if recursive:
                shutil.rmtree(resolved)
                deleted.append(f"{resolved} [dir]")
            else:
                os.rmdir(resolved)
                deleted.append(f"{resolved} [empty dir]")
        elif os.path.exists(resolved):
            os.remove(resolved)
            deleted.append(f"{resolved} [file]")
        else:
            deleted.append(f"{resolved} [missing]")
    return "Deleted paths:\n" + "\n".join(f"- {item}" for item in deleted)


def zip_paths_action(sources: list[str], destination: str, cfg: dict[str, Any], workspace_root: str) -> str:
    if not sources:
        raise ValueError("sources is required")
    resolved_destination = resolve_allowed_path(destination, cfg, workspace_root)
    parent = os.path.dirname(resolved_destination)
    if parent:
        os.makedirs(parent, exist_ok=True)

    archived: list[str] = []
    with zipfile.ZipFile(resolved_destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sources:
            resolved_source = resolve_allowed_path(source, cfg, workspace_root)
            if os.path.isdir(resolved_source):
                for dirpath, _, filenames in os.walk(resolved_source):
                    for filename in filenames:
                        full = os.path.join(dirpath, filename)
                        arcname = os.path.relpath(full, os.path.dirname(resolved_source))
                        archive.write(full, arcname)
                        archived.append(full)
            elif os.path.isfile(resolved_source):
                archive.write(resolved_source, os.path.basename(resolved_source))
                archived.append(resolved_source)
            else:
                raise ValueError(f"Source path not found: {resolved_source}")
    return f"Created zip archive: {resolved_destination} ({len(archived)} file{'s' if len(archived) != 1 else ''})"


def extract_archive_action(source: str, destination: str, cfg: dict[str, Any], workspace_root: str) -> str:
    resolved_source = resolve_allowed_path(source, cfg, workspace_root)
    if not os.path.isfile(resolved_source):
        raise ValueError(f"Archive not found: {resolved_source}")
    resolved_destination = resolve_allowed_path(destination, cfg, workspace_root)
    os.makedirs(resolved_destination, exist_ok=True)
    if zipfile.is_zipfile(resolved_source):
        with zipfile.ZipFile(resolved_source, "r") as archive:
            archive.extractall(resolved_destination)
            count = len(archive.infolist())
    elif tarfile.is_tarfile(resolved_source):
        with tarfile.open(resolved_source, "r:*") as archive:
            members = archive.getmembers()
            archive.extractall(resolved_destination)
            count = len(members)
    else:
        try:
            shutil.unpack_archive(resolved_source, resolved_destination)
        except Exception as exc:
            raise ValueError(f"Unsupported archive format: {resolved_source}") from exc
        count = len(os.listdir(resolved_destination))
    return f"Extracted archive: {resolved_source} -> {resolved_destination} ({count} entries)"


def search_in_files_action(
    path: str,
    pattern: str,
    cfg: dict[str, Any],
    workspace_root: str,
    recursive: bool = True,
    limit: int = 100,
) -> str:
    if not pattern.strip():
        raise ValueError("pattern is required")
    resolved = resolve_allowed_path(path or ".", cfg, workspace_root)
    if not os.path.exists(resolved):
        raise ValueError(f"Path not found: {resolved}")

    matches: list[str] = []
    candidates: list[str] = []
    if os.path.isfile(resolved):
        candidates = [resolved]
    else:
        for dirpath, _, filenames in os.walk(resolved):
            for filename in filenames:
                candidates.append(os.path.join(dirpath, filename))
            if not recursive:
                break

    needle = pattern.lower()
    for file_path in candidates:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if needle in line.lower():
                        rel = os.path.relpath(file_path, workspace_root)
                        snippet = line.strip()[:200]
                        matches.append(f"{rel}:{line_number}: {snippet}")
                        if len(matches) >= limit:
                            break
        except Exception:
            continue
        if len(matches) >= limit:
            break

    if not matches:
        return f"No matches found for '{pattern}' under {resolved}"
    return f"Found {len(matches)} matches for '{pattern}' under {resolved}\n" + "\n".join(matches)


def download_url_action(
    url: str,
    destination: str,
    cfg: dict[str, Any],
    workspace_root: str,
    timeout: int = 60,
) -> str:
    if not url.strip():
        raise ValueError("url is required")
    resolved_destination = resolve_allowed_path(destination, cfg, workspace_root)
    parent = os.path.dirname(resolved_destination)
    if parent:
        os.makedirs(parent, exist_ok=True)

    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            response = client.get(url.strip())
            response.raise_for_status()
            data = response.content
    except httpx.HTTPError as exc:
        raise ValueError(f"Failed to download {url.strip()}: {exc}") from exc

    with open(resolved_destination, "wb") as handle:
        handle.write(data)
    content_type = response.headers.get("content-type", "unknown")
    return (
        f"Downloaded {url.strip()} to {resolved_destination} "
        f"({len(data)} bytes, content-type={content_type})"
    )


def convert_image_format_action(
    source: str,
    destination: str,
    image_format: str,
    cfg: dict[str, Any],
    workspace_root: str,
) -> str:
    resolved_source = resolve_allowed_path(source, cfg, workspace_root)
    resolved_destination = resolve_allowed_path(destination, cfg, workspace_root)
    if not os.path.isfile(resolved_source):
        raise ValueError(f"Image not found: {resolved_source}")
    parent = os.path.dirname(resolved_destination)
    if parent:
        os.makedirs(parent, exist_ok=True)

    target_format = (image_format or Path(resolved_destination).suffix.lstrip(".") or "PNG").upper()
    normalized_format = {
        "JPG": "JPEG",
        "TIF": "TIFF",
    }.get(target_format, target_format)

    with Image.open(resolved_source) as image:
        converted = image.convert("RGB") if normalized_format in {"JPEG", "WEBP"} and image.mode in {"RGBA", "P"} else image.copy()
        converted.save(resolved_destination, format=normalized_format)
    return f"Converted image: {resolved_source} -> {resolved_destination} ({normalized_format})"


def prepare_command_action(command: str, cwd: str, cfg: dict[str, Any], workspace_root: str) -> CommandExecutionRequest:
    if not command.strip():
        raise ValueError("command is required")
    resolved_cwd = resolve_allowed_path(cwd or ".", cfg, workspace_root)
    if not os.path.isdir(resolved_cwd):
        raise ValueError(f"cwd is not a directory: {resolved_cwd}")
    return CommandExecutionRequest(command=command.strip(), cwd=cwd or ".", resolved_cwd=resolved_cwd)


def run_command_action(request: CommandExecutionRequest, timeout: int = 120) -> str:
    completed = subprocess.run(
        request.command,
        cwd=request.resolved_cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=True,
        check=False,
    )
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    pieces = [
        f"Command: {request.command}",
        f"CWD: {request.resolved_cwd}",
        f"Exit code: {completed.returncode}",
    ]
    if stdout:
        pieces.append("STDOUT:\n" + stdout[:6000])
    if stderr:
        pieces.append("STDERR:\n" + stderr[:4000])
    return "\n\n".join(pieces)
