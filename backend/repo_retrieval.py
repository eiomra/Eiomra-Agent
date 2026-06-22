"""
Lightweight workspace retrieval for the backend agent.

This keeps the implementation dependency-free and optimized for local repo
context:
- recursively index relevant source/config/docs files
- skip bulky/generated folders
- rank files with token overlap and path hints
- format compact prompt-ready context blocks
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from attachment_reader import AttachmentReader

TOKEN_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "your", "have",
    "code", "file", "files", "please", "need", "make", "update", "change", "agent",
    "backend", "frontend", "task", "step", "page", "goal",
}

SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".next", "dist", "build",
    "coverage", "logs", "results", "profiles", "sessions", "memory",
    ".venv", "venv", ".idea", ".vscode", "out",
}

TEXT_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".html", ".css", ".scss", ".sql", ".sh", ".bat",
    ".ps1", ".env", ".gitignore", ".dockerignore",
}
ATTACHMENT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".pdf"}

MAX_FILE_BYTES = 300_000
attachment_reader = AttachmentReader()


@dataclass(slots=True)
class IndexedRepoFile:
    relative_path: str
    language: str
    size: int
    terms: dict[str, int]
    symbols: list[str]
    imports: list[str]
    summary: str
    preview: str


@dataclass(slots=True)
class RetrievalResult:
    file: IndexedRepoFile
    score: float
    reasons: list[str]


def tokenize_for_retrieval(text: str) -> list[str]:
    return [
        token for token in re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
        .lower()
        .split()
        for token in re.split(r"[^a-z0-9_.$/-]+", token)
        if len(token) >= 2 and token not in TOKEN_STOPWORDS
    ][:2500]


def _guess_language(path: str) -> str:
    ext = Path(path).suffix.lower()
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".js": "javascript",
        ".jsx": "jsx",
        ".json": "json",
        ".md": "markdown",
        ".html": "html",
        ".css": "css",
        ".scss": "scss",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".sql": "sql",
        ".sh": "bash",
        ".bat": "batch",
        ".ps1": "powershell",
    }.get(ext, ext.lstrip(".") or "text")


def _extract_symbols(content: str) -> list[str]:
    patterns = [
        r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"\bdef\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"\basync\s+def\s+([A-Za-z_][A-Za-z0-9_]*)",
        r"\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)",
        r"\bconst\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=",
        r"\blet\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=",
        r"\binterface\s+([A-Za-z_$][A-Za-z0-9_$]*)",
        r"\btype\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=",
    ]
    found: list[str] = []
    for pattern in patterns:
        found.extend(re.findall(pattern, content))
    seen = set()
    deduped = []
    for symbol in found:
        if symbol not in seen:
            seen.add(symbol)
            deduped.append(symbol)
    return deduped[:80]


def _extract_imports(content: str) -> list[str]:
    found = re.findall(r"""(?:from|import)\s+['"]([^'"]+)['"]""", content)
    found.extend(re.findall(r"""require\(\s*['"]([^'"]+)['"]\s*\)""", content))
    seen = set()
    deduped = []
    for value in found:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped[:80]


def _summarize(relative_path: str, language: str, symbols: list[str], imports: list[str]) -> str:
    bits = [f"{relative_path} ({language})"]
    if symbols:
        bits.append(f"symbols: {', '.join(symbols[:6])}")
    if imports:
        bits.append(f"imports: {', '.join(imports[:4])}")
    return " | ".join(bits)


class RepoRetrievalIndex:
    def __init__(self, workspace_root: str, max_files: int = 1200):
        self.workspace_root = workspace_root
        self.max_files = max_files
        self._cache: list[IndexedRepoFile] | None = None
        self._built_at: str | None = None

    def invalidate(self) -> None:
        self._cache = None
        self._built_at = None

    def build(self) -> list[IndexedRepoFile]:
        if self._cache is not None:
            return self._cache

        indexed: list[IndexedRepoFile] = []
        root = Path(self.workspace_root)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
            for filename in filenames:
                if len(indexed) >= self.max_files:
                    break
                full_path = Path(dirpath) / filename
                if not self._is_indexable(full_path):
                    continue
                try:
                    stat = full_path.stat()
                except OSError:
                    continue
                if stat.st_size > MAX_FILE_BYTES:
                    continue
                try:
                    if full_path.suffix.lower() in ATTACHMENT_EXTENSIONS:
                        attachment = attachment_reader.summarize(str(full_path), max_chars=2500)
                        content = "\n".join(filter(None, [attachment.summary, attachment.content_excerpt]))
                    else:
                        content = full_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                relative = full_path.relative_to(root).as_posix()
                symbols = _extract_symbols(content)
                imports = _extract_imports(content)
                weighted = "\n".join([
                    relative,
                    full_path.name,
                    " ".join(symbols),
                    " ".join(imports),
                    content[:30_000],
                ])
                indexed.append(IndexedRepoFile(
                    relative_path=relative,
                    language=_guess_language(relative),
                    size=stat.st_size,
                    terms=dict(Counter(tokenize_for_retrieval(weighted))),
                    symbols=symbols,
                    imports=imports,
                    summary=_summarize(relative, _guess_language(relative), symbols, imports),
                    preview=content[:8_000],
                ))
            if len(indexed) >= self.max_files:
                break

        self._cache = indexed
        self._built_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        return indexed

    def retrieve(self, query: str, limit: int = 4) -> list[RetrievalResult]:
        files = self.build()
        if not files:
            return []
        query_tokens = tokenize_for_retrieval(query)
        if not query_tokens:
            return []
        query_terms = Counter(query_tokens)
        doc_freq = self._document_frequency(files)
        lowered_query = query.lower().replace("\\", "/")
        results: list[RetrievalResult] = []

        for file in files:
            reasons: list[str] = []
            score = self._tfidf_score(query_terms, file.terms, doc_freq, len(files)) * 40.0
            lower_path = file.relative_path.lower()
            base = os.path.basename(lower_path)

            if lower_path in lowered_query:
                score += 30
                reasons.append("exact path mention")
            if base and base in lowered_query:
                score += 15
                reasons.append("filename mention")
            if any(symbol.lower() in lowered_query for symbol in file.symbols[:20]):
                score += 10
                reasons.append("symbol match")
            if any(item.lower() in lowered_query for item in file.imports[:20]):
                score += 6
                reasons.append("import match")

            if score > 0.25:
                if not reasons:
                    reasons.append("semantic token match")
                results.append(RetrievalResult(file=file, score=score, reasons=reasons))

        results.sort(key=lambda item: (-item.score, item.file.relative_path))
        return results[:limit]

    def format_context(self, results: Iterable[RetrievalResult]) -> str:
        rows = list(results)
        if not rows:
            return ""
        sections = ["REPO CONTEXT:"]
        for result in rows:
            sections.append(
                "\n".join([
                    f"FILE: {result.file.relative_path} ({result.file.language})",
                    f"Score: {result.score:.2f} | Reasons: {', '.join(result.reasons)}",
                    f"Summary: {result.file.summary}",
                    f"```{result.file.language}",
                    result.file.preview[:1500].rstrip(),
                    "```",
                ])
            )
        sections.append("Use these retrieved workspace files as concrete local context when relevant.")
        return "\n\n".join(sections)

    def format_index_summary(self) -> str:
        files = self.build()
        return f"REPO INDEX: {len(files)} files indexed at {self._built_at or 'unknown'}"

    def _document_frequency(self, files: list[IndexedRepoFile]) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for file in files:
            counts.update(set(file.terms))
        return dict(counts)

    def _tfidf_score(
        self,
        query_terms: Counter[str],
        file_terms: dict[str, int],
        doc_freq: dict[str, int],
        total_docs: int,
    ) -> float:
        score = 0.0
        for term, qfreq in query_terms.items():
            tf = file_terms.get(term, 0)
            if not tf:
                continue
            df = doc_freq.get(term, 1)
            idf = math.log((1 + total_docs) / (1 + df)) + 1
            score += qfreq * tf * idf
        return score / max(1.0, math.sqrt(sum(v * v for v in file_terms.values())))

    def _is_indexable(self, path: Path) -> bool:
        name = path.name
        suffix = path.suffix.lower()
        return suffix in TEXT_EXTENSIONS or suffix in ATTACHMENT_EXTENSIONS or name in {"Dockerfile", "README", "README.md", "Makefile"}
