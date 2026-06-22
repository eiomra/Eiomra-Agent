"""
Shared attachment/file summarization for local files and uploaded files.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image
from tesseract_utils import configure_pytesseract

try:
    import pytesseract  # type: ignore
except Exception:  # pragma: no cover
    pytesseract = None
else:
    configure_pytesseract(pytesseract)

try:
    import fitz  # type: ignore
except Exception:  # pragma: no cover
    fitz = None


TEXT_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".html", ".css", ".scss", ".sql", ".sh", ".bat", ".ps1",
    ".csv", ".tsv", ".log", ".env",
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff"}
PDF_EXTENSIONS = {".pdf"}


@dataclass(slots=True)
class AttachmentSummary:
    path: str
    name: str
    kind: str
    mime_type: str
    size: int
    summary: str
    content_excerpt: str = ""
    metadata: dict[str, Any] | None = None

    def to_prompt_block(self) -> str:
        lines = [
            f"ATTACHMENT: {self.name}",
            f"Kind: {self.kind} | MIME: {self.mime_type} | Size: {self.size} bytes",
            f"Summary: {self.summary}",
        ]
        if self.metadata:
            meta = ", ".join(f"{k}={v}" for k, v in self.metadata.items())
            if meta:
                lines.append(f"Metadata: {meta}")
        if self.content_excerpt:
            lines.append("Excerpt:")
            lines.append(self.content_excerpt[:2000])
        return "\n".join(lines)


class AttachmentReader:
    def __init__(self):
        self._cache: dict[str, AttachmentSummary] = {}

    def summarize(self, path: str, max_chars: int = 4000) -> AttachmentSummary:
        resolved = os.path.abspath(path)
        stat = os.stat(resolved)
        cache_key = self._cache_key(resolved, stat.st_mtime_ns, stat.st_size)
        cached = self._cache.get(cache_key)
        if cached:
            return cached

        suffix = Path(resolved).suffix.lower()
        if suffix in TEXT_EXTENSIONS or Path(resolved).name in {"README", "Dockerfile", "Makefile"}:
            summary = self._summarize_text(resolved, stat.st_size, max_chars)
        elif suffix in IMAGE_EXTENSIONS:
            summary = self._summarize_image(resolved, stat.st_size)
        elif suffix in PDF_EXTENSIONS:
            summary = self._summarize_pdf(resolved, stat.st_size, max_chars)
        else:
            summary = self._summarize_binary(resolved, stat.st_size)

        self._cache = {k: v for k, v in self._cache.items() if len(self._cache) < 512}
        self._cache[cache_key] = summary
        return summary

    def _summarize_text(self, path: str, size: int, max_chars: int) -> AttachmentSummary:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            content = handle.read(max_chars)
        line_count = content.count("\n") + 1 if content else 0
        suffix = Path(path).suffix.lower()
        mime = {
            ".json": "application/json",
            ".md": "text/markdown",
            ".html": "text/html",
            ".css": "text/css",
            ".csv": "text/csv",
        }.get(suffix, "text/plain")
        return AttachmentSummary(
            path=path,
            name=os.path.basename(path),
            kind="text",
            mime_type=mime,
            size=size,
            summary=f"Text file with {line_count} lines",
            content_excerpt=content,
            metadata={"extension": suffix or "(none)"},
        )

    def _summarize_image(self, path: str, size: int) -> AttachmentSummary:
        with Image.open(path) as image:
            width, height = image.size
            mode = image.mode
            fmt = image.format or "unknown"
            ocr_text = ""
            if pytesseract is not None:
                try:
                    ocr_text = pytesseract.image_to_string(image).strip()
                except Exception:
                    ocr_text = ""
        summary = f"Image {width}x{height} ({fmt}, mode={mode})"
        if ocr_text:
            snippet = " ".join(ocr_text.split())[:240]
            summary += f" with readable text detected"
        else:
            snippet = ""
            summary += " with no OCR text detected"
        return AttachmentSummary(
            path=path,
            name=os.path.basename(path),
            kind="image",
            mime_type=f"image/{fmt.lower()}" if fmt and fmt != "unknown" else "image/*",
            size=size,
            summary=summary,
            content_excerpt=snippet,
            metadata={"width": width, "height": height, "format": fmt, "mode": mode},
        )

    def _summarize_pdf(self, path: str, size: int, max_chars: int) -> AttachmentSummary:
        text = ""
        pages = 0
        if fitz is not None:
            try:
                doc = fitz.open(path)
                pages = len(doc)
                chunks = []
                for page in doc[: min(4, pages)]:
                    chunks.append(page.get_text("text"))
                    if sum(len(chunk) for chunk in chunks) >= max_chars:
                        break
                text = "\n".join(chunks)[:max_chars]
            except Exception:
                text = ""
        summary = f"PDF document with {pages or 'unknown'} pages"
        if text.strip():
            summary += " and extracted text"
        return AttachmentSummary(
            path=path,
            name=os.path.basename(path),
            kind="pdf",
            mime_type="application/pdf",
            size=size,
            summary=summary,
            content_excerpt=text,
            metadata={"pages": pages or "unknown"},
        )

    def _summarize_binary(self, path: str, size: int) -> AttachmentSummary:
        suffix = Path(path).suffix.lower()
        return AttachmentSummary(
            path=path,
            name=os.path.basename(path),
            kind="binary",
            mime_type="application/octet-stream",
            size=size,
            summary=f"Binary file ({suffix or 'no extension'})",
            metadata={"extension": suffix or "(none)"},
        )

    def _cache_key(self, path: str, mtime_ns: int, size: int) -> str:
        return hashlib.sha1(f"{path}|{mtime_ns}|{size}".encode("utf-8")).hexdigest()
