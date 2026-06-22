"""
Helpers for locating and configuring the Tesseract OCR binary on Windows.
"""

from __future__ import annotations

import os
from functools import lru_cache


COMMON_TESSERACT_PATHS = [
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Tesseract-OCR", "tesseract.exe"),
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "PDF24", "tesseract", "tesseract.exe"),
    os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Tesseract-OCR", "tesseract.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR", "tesseract.exe"),
]

DEFAULT_LANGUAGE = "eng"


@lru_cache(maxsize=1)
def find_tesseract_executable() -> str | None:
    env_value = os.environ.get("TESSERACT_CMD", "").strip()
    if env_value and os.path.isfile(env_value):
        return env_value

    for candidate in COMMON_TESSERACT_PATHS:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def tessdata_dir_for_executable(executable: str) -> str:
    return os.path.join(os.path.dirname(executable), "tessdata")


def traineddata_path(executable: str, language: str = DEFAULT_LANGUAGE) -> str:
    return os.path.join(tessdata_dir_for_executable(executable), f"{language}.traineddata")


def has_language_data(executable: str, language: str = DEFAULT_LANGUAGE) -> bool:
    return os.path.isfile(traineddata_path(executable, language))


@lru_cache(maxsize=1)
def find_usable_tesseract_executable(language: str = DEFAULT_LANGUAGE) -> str | None:
    env_value = os.environ.get("TESSERACT_CMD", "").strip()
    candidates: list[str] = []
    if env_value and os.path.isfile(env_value):
        candidates.append(env_value)
    auto = find_tesseract_executable()
    if auto and auto not in candidates:
        candidates.append(auto)
    for candidate in COMMON_TESSERACT_PATHS:
        if candidate and os.path.isfile(candidate) and candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        if has_language_data(candidate, language):
            return candidate
    return None


def describe_tesseract_problem(language: str = DEFAULT_LANGUAGE) -> str:
    exe = find_tesseract_executable()
    if not exe:
        return "Tesseract executable was not found."
    data_path = traineddata_path(exe, language)
    if not os.path.isfile(data_path):
        return (
            f"Tesseract was found at {exe}, but language data is missing: {data_path}. "
            f"Install {language}.traineddata or set TESSDATA_PREFIX to a valid tessdata directory."
        )
    return f"Tesseract at {exe} appears present, but could not be configured."


def configure_pytesseract(pytesseract_module) -> str | None:
    if pytesseract_module is None:
        return None
    exe = find_usable_tesseract_executable()
    if exe:
        try:
            pytesseract_module.pytesseract.tesseract_cmd = exe
            tessdata_dir = tessdata_dir_for_executable(exe)
            os.environ["TESSDATA_PREFIX"] = tessdata_dir
        except Exception:
            return None
    return exe
