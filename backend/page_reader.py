"""
page_reader.py — Rich page content extraction
Handles: DOM text, OCR on images, PDF inline, iframes, lazy-loaded content
All extraction settings come from the session config.
"""

import asyncio
import base64
import io
import re
from typing import Optional
from tesseract_utils import configure_pytesseract

# ── Optional OCR import ───────────────────────────────────────────────────
try:
    import pytesseract
    from PIL import Image
    configure_pytesseract(pytesseract)
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

# ── Optional PDF import ───────────────────────────────────────────────────
try:
    import fitz  # pymupdf
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False


# ═════════════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════
async def extract_page_content(page, cfg: dict) -> dict:
    """
    Full page content extraction.
    Returns dict with: text, elements, url, title, ocr_text, pdf_text, warnings
    """
    max_chars   = cfg.get("max_text_chars", 8000)
    ocr_enabled = cfg.get("ocr_enabled", False) and OCR_AVAILABLE
    pdf_enabled = cfg.get("pdf_enabled", True)  and PDF_AVAILABLE
    deep_read   = cfg.get("deep_read", False)    # scroll + collect all text

    warnings = []
    if cfg.get("ocr_enabled") and not OCR_AVAILABLE:
        warnings.append("OCR requested but pytesseract/Pillow not installed. Run: pip install pytesseract Pillow  (also install Tesseract binary)")
    if cfg.get("pdf_enabled") and not PDF_AVAILABLE:
        warnings.append("PDF extraction requested but pymupdf not installed. Run: pip install pymupdf")

    url   = page.url
    title = await page.title()

    # ── 1. Main DOM text ──────────────────────────────────────────────────
    if deep_read:
        dom_text = await extract_full_page_text(page, max_chars)
    else:
        dom_text = await extract_viewport_text(page, max_chars)

    # ── 2. Iframe text (same-origin) ──────────────────────────────────────
    iframe_text = await extract_iframe_text(page, max_chars // 4)

    # ── 3. Interactive elements ───────────────────────────────────────────
    elements = await extract_elements(page)

    # ── 4. OCR on visible images ──────────────────────────────────────────
    ocr_text = ""
    if ocr_enabled:
        try:
            ocr_text = await run_ocr_on_page(page, cfg.get("ocr_max_images", 5))
        except Exception as e:
            warnings.append(f"OCR error: {e}")

    # ── 5. PDF extraction (if page contains a PDF viewer) ─────────────────
    pdf_text = ""
    if pdf_enabled:
        try:
            pdf_text = await extract_pdf_from_page(page)
        except Exception as e:
            warnings.append(f"PDF extraction error: {e}")

    # ── Combine all text sources ──────────────────────────────────────────
    combined = _combine_text(dom_text, iframe_text, ocr_text, pdf_text, max_chars)

    return {
        "url":      url,
        "title":    title,
        "text":     combined,
        "elements": elements,
        "ocr_text": ocr_text,
        "pdf_text": pdf_text,
        "iframe_text": iframe_text,
        "char_count": len(combined),
        "warnings": warnings,
        # Breakdown for the log
        "sources": {
            "dom_chars":    len(dom_text),
            "iframe_chars": len(iframe_text),
            "ocr_chars":    len(ocr_text),
            "pdf_chars":    len(pdf_text),
        }
    }


# ═════════════════════════════════════════════════════════════════════════
#  DOM TEXT EXTRACTION
# ═════════════════════════════════════════════════════════════════════════
async def extract_viewport_text(page, max_chars: int) -> str:
    """Extract all visible text from current viewport + full DOM (cleaned)."""
    try:
        text = await page.evaluate(f"""() => {{
            const seen = new Set();
            const results = [];
            const limit = {max_chars};

            // Skip these tags entirely
            const skip = new Set(['script','style','noscript','head',
                                  'meta','link','svg','path','iframe']);

            function walk(node) {{
                if (results.join(' ').length >= limit) return;
                if (node.nodeType === Node.ELEMENT_NODE) {{
                    const tag = node.tagName.toLowerCase();
                    if (skip.has(tag)) return;
                    // Add alt text for images
                    if (tag === 'img' && node.alt) {{
                        results.push('[Image: ' + node.alt + ']');
                        return;
                    }}
                    // Add aria-label as context
                    const aria = node.getAttribute('aria-label');
                    if (aria && !seen.has(aria)) {{
                        seen.add(aria);
                    }}
                    for (const child of node.childNodes) walk(child);
                }} else if (node.nodeType === Node.TEXT_NODE) {{
                    const t = node.textContent.trim();
                    if (t.length > 1 && !seen.has(t)) {{
                        seen.add(t);
                        results.push(t);
                    }}
                }}
            }}

            walk(document.body || document.documentElement);
            return results.join(' ').slice(0, limit);
        }}""")
        return text or ""
    except Exception:
        return ""


async def extract_full_page_text(page, max_chars: int) -> str:
    """
    Deep read: scroll through entire page collecting text as content loads.
    Used when deep_read is enabled.
    """
    collected = set()
    results   = []

    async def collect():
        chunk = await extract_viewport_text(page, max_chars)
        for word_group in chunk.split(". "):
            wg = word_group.strip()
            if wg and wg not in collected:
                collected.add(wg)
                results.append(wg)

    await collect()

    # Scroll in steps to trigger lazy loading
    viewport_height = await page.evaluate("window.innerHeight")
    total_height    = await page.evaluate("document.body.scrollHeight")
    current         = 0
    steps           = 0

    while current < total_height and len(" ".join(results)) < max_chars and steps < 20:
        current += viewport_height
        await page.evaluate(f"window.scrollTo(0, {current})")
        await asyncio.sleep(0.4)  # wait for lazy content
        await collect()
        total_height = await page.evaluate("document.body.scrollHeight")
        steps += 1

    # Scroll back to top
    await page.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(0.3)

    return ". ".join(results)[:max_chars]


# ═════════════════════════════════════════════════════════════════════════
#  IFRAME TEXT
# ═════════════════════════════════════════════════════════════════════════
async def extract_iframe_text(page, max_chars: int) -> str:
    """Extract text from same-origin iframes."""
    texts = []
    try:
        frames = page.frames
        for frame in frames[1:]:  # skip main frame
            try:
                # Only same-origin frames are accessible
                t = await frame.evaluate("""() => {
                    return document.body ? document.body.innerText.slice(0, 2000) : '';
                }""")
                if t and t.strip():
                    texts.append(f"[Frame content: {t.strip()}]")
            except Exception:
                pass  # cross-origin — expected to fail
    except Exception:
        pass
    combined = " ".join(texts)
    return combined[:max_chars]


# ═════════════════════════════════════════════════════════════════════════
#  INTERACTIVE ELEMENTS
# ═════════════════════════════════════════════════════════════════════════
async def extract_elements(page) -> list:
    """
    Comprehensive interactive element extraction.
    Gets buttons, links, inputs, selects, textareas, ARIA roles, and more.
    """
    try:
        elements = await page.evaluate("""() => {
            const elems = [];
            const seen  = new Set();

            const selector = [
                'a[href]', 'button', 'input', 'select', 'textarea',
                '[role="button"]', '[role="link"]', '[role="menuitem"]',
                '[role="tab"]', '[role="checkbox"]', '[role="radio"]',
                '[role="combobox"]', '[role="listbox"]', '[role="option"]',
                '[role="switch"]', '[role="textbox"]',
                '[onclick]', '[tabindex="0"]',
                'label[for]', 'summary',
                '[contenteditable="true"]',
                '[data-placeholder]',
            ].join(',');

            document.querySelectorAll(selector).forEach((el, rawIdx) => {
                if (elems.length >= 80) return;

                const rect = el.getBoundingClientRect();
                // Include elements even if off-screen (could be in modal etc)
                // but skip zero-size elements
                if (rect.width === 0 && rect.height === 0) return;

                // Get element text — prefer visible content, fallback to hints
                const innerTxt = (el.innerText || el.textContent || '').trim();
                const text = (
                    // For contenteditable: innerText is the typed content
                    // For inputs: value is the typed content
                    el.value ||
                    (innerTxt.length > 0 ? innerTxt : null) ||
                    el.placeholder ||
                    el.getAttribute('data-placeholder') ||
                    el.getAttribute('aria-label') ||
                    el.getAttribute('title') ||
                    el.getAttribute('alt') ||
                    el.getAttribute('name') ||
                    ''
                ).trim().slice(0, 100);

                const key = el.tagName + text + Math.round(rect.x) + Math.round(rect.y);
                if (seen.has(key)) return;
                seen.add(key);

                elems.push({
                    id:       elems.length,
                    tag:      el.tagName.toLowerCase(),
                    text:     text,
                    href:     el.href   || null,
                    type:     el.type   || null,
                    role:     el.getAttribute('role') || null,
                    name:     el.getAttribute('name') || null,
                    value:    el.tagName === 'SELECT' ? el.value : null,
                    checked:  el.type === 'checkbox' || el.type === 'radio' ? el.checked : null,
                    disabled: el.disabled || false,
                    contenteditable: el.getAttribute('contenteditable') !== null,
                    visible:  rect.width > 0 && rect.height > 0 &&
                              rect.top >= -100 && rect.top <= window.innerHeight + 100,
                    x: Math.round(rect.x + rect.width  / 2),
                    y: Math.round(rect.y + rect.height / 2),
                });
            });
            return elems;
        }""")
        # Second pass: find important inputs that are off-screen (e.g. in inactive tabs)
        # These show up as hints so the AI knows to use focus_field
        off_screen_hints = await page.evaluate("""() => {
            const hints = [];
            for (const el of document.querySelectorAll('input[name],input[id],textarea[name]')) {
                const rect = el.getBoundingClientRect();
                const onscreen = rect.width > 0 && rect.height > 0 &&
                                 rect.top >= -100 && rect.top <= window.innerHeight + 100;
                if (onscreen) continue; // already in main list
                const label_el = el.id ? document.querySelector(`label[for="${el.id}"]`) : null;
                const label = (label_el ? label_el.innerText : '') ||
                              el.placeholder || el.getAttribute('aria-label') || el.name || el.id;
                if (label && label.length > 0) {
                    hints.push({
                        id: el.id||'', name: el.name||'', label: label.trim().slice(0,50),
                        tag: el.tagName.toLowerCase(), type: el.type||'textarea'
                    });
                }
            }
            return hints.slice(0, 20);
        }""")

        if off_screen_hints:
            # Attach as metadata — agent.py will add this to context
            for h in off_screen_hints:
                elements.append({
                    "id":       len(elements),
                    "tag":      h["tag"],
                    "text":     f"[HIDDEN] {h['label']}",
                    "type":     h["type"],
                    "name":     h["name"],
                    "x":        -1,
                    "y":        -1,
                    "hidden_field": True,
                    "hint":     h["name"] or h["id"] or h["label"],
                })

        return elements or []
    except Exception:
        return []


# ═════════════════════════════════════════════════════════════════════════
#  OCR
# ═════════════════════════════════════════════════════════════════════════
async def run_ocr_on_page(page, max_images: int = 5) -> str:
    """
    Find images on the page, screenshot each one, run Tesseract OCR.
    Returns combined OCR text.
    """
    if not OCR_AVAILABLE:
        return ""

    ocr_results = []

    # Get image bounding boxes from the page
    image_boxes = await page.evaluate("""(maxImages) => {
        const imgs = [];
        document.querySelectorAll('img, canvas, svg').forEach((el, i) => {
            if (i >= maxImages) return;
            const rect = el.getBoundingClientRect();
            if (rect.width < 50 || rect.height < 50) return;  // skip tiny icons
            if (rect.top < 0 || rect.top > window.innerHeight) return;  // must be visible
            imgs.push({
                x: Math.round(rect.x), y: Math.round(rect.y),
                w: Math.round(rect.width), h: Math.round(rect.height),
                tag: el.tagName.toLowerCase(),
                alt: el.alt || ''
            });
        });
        return imgs;
    }""", max_images)

    for box in image_boxes[:max_images]:
        try:
            # Clip screenshot to just this element
            clip = {"x": box["x"], "y": box["y"],
                    "width": box["w"], "height": box["h"]}
            png = await page.screenshot(clip=clip, type="png")
            img = Image.open(io.BytesIO(png))

            # Run OCR
            ocr = pytesseract.image_to_string(img, config="--psm 6").strip()
            if ocr and len(ocr) > 10:
                label = box.get("alt") or box.get("tag","img")
                ocr_results.append(f"[OCR from {label}]: {ocr}")
        except Exception:
            pass

    # Also OCR the full viewport screenshot for any text in images
    try:
        full_png = await page.screenshot(type="png", full_page=False)
        full_img = Image.open(io.BytesIO(full_png))
        full_ocr = pytesseract.image_to_string(full_img, config="--psm 3").strip()
        if full_ocr and len(full_ocr) > 20:
            ocr_results.append(f"[Full page OCR]: {full_ocr}")
    except Exception:
        pass

    return "\n".join(ocr_results)


# ═════════════════════════════════════════════════════════════════════════
#  PDF EXTRACTION
# ═════════════════════════════════════════════════════════════════════════
async def extract_pdf_from_page(page) -> str:
    """
    Detect if the page is showing a PDF (via embed/object/iframe or direct URL).
    If so, fetch and extract text using pymupdf.
    """
    if not PDF_AVAILABLE:
        return ""

    url = page.url
    texts = []

    # Check if URL is a direct PDF
    if url.lower().endswith(".pdf") or "application/pdf" in url:
        text = await _fetch_and_extract_pdf(url, page)
        if text:
            texts.append(f"[PDF content from {url}]:\n{text}")

    # Check for embedded PDF objects
    pdf_urls = await page.evaluate("""() => {
        const urls = [];
        document.querySelectorAll('embed[type="application/pdf"], object[type="application/pdf"]')
            .forEach(el => { if (el.src || el.data) urls.push(el.src || el.data); });
        // Also check iframes that might contain PDFs
        document.querySelectorAll('iframe').forEach(el => {
            if (el.src && el.src.toLowerCase().includes('.pdf')) urls.push(el.src);
        });
        return urls.slice(0, 3);
    }""")

    for pdf_url in pdf_urls:
        text = await _fetch_and_extract_pdf(pdf_url, page)
        if text:
            texts.append(f"[Embedded PDF from {pdf_url}]:\n{text}")

    return "\n\n".join(texts)


async def _fetch_and_extract_pdf(url: str, page) -> str:
    """Fetch a PDF URL (using page cookies for auth) and extract text."""
    if not PDF_AVAILABLE:
        return ""
    try:
        # Get cookies from the browser session for authenticated PDFs
        cookies = await page.context.cookies()
        cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in cookies])

        import httpx
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers={"Cookie": cookie_header})
            if r.status_code != 200:
                return ""
            pdf_bytes = r.content

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages_text = []
        for i, p in enumerate(doc):
            if i >= 10:  # max 10 pages
                break
            pages_text.append(p.get_text())
        doc.close()
        return "\n".join(pages_text)[:5000]
    except Exception:
        return ""


# ═════════════════════════════════════════════════════════════════════════
#  TEXT COMBINER
# ═════════════════════════════════════════════════════════════════════════
def _combine_text(dom: str, iframe: str, ocr: str, pdf: str, max_chars: int) -> str:
    """
    Combine all text sources intelligently.
    DOM text gets priority, then PDF (very informative), then OCR, then iframe.
    """
    parts = []

    if dom.strip():
        parts.append(dom.strip())

    if pdf.strip():
        parts.append("\n\n--- PDF CONTENT ---\n" + pdf.strip())

    if ocr.strip():
        # Only add OCR text that isn't already in the DOM
        ocr_clean = _deduplicate_against(ocr, dom)
        if ocr_clean:
            parts.append("\n\n--- OCR TEXT (from images) ---\n" + ocr_clean)

    if iframe.strip():
        parts.append("\n\n--- FRAME CONTENT ---\n" + iframe.strip())

    combined = "".join(parts)
    return combined[:max_chars]


def _deduplicate_against(new_text: str, existing: str) -> str:
    """Remove lines from new_text that already appear in existing."""
    existing_lines = set(l.strip() for l in existing.split("\n") if len(l.strip()) > 10)
    filtered = []
    for line in new_text.split("\n"):
        if line.strip() not in existing_lines:
            filtered.append(line)
    return "\n".join(filtered)


# ═════════════════════════════════════════════════════════════════════════
#  CAPABILITY REPORT  (for the /capabilities endpoint)
# ═════════════════════════════════════════════════════════════════════════
def get_capabilities() -> dict:
    return {
        "ocr":           OCR_AVAILABLE,
        "pdf":           PDF_AVAILABLE,
        "ocr_package":   "pytesseract + Pillow" if OCR_AVAILABLE else "not installed",
        "pdf_package":   "pymupdf (fitz)"       if PDF_AVAILABLE else "not installed",
        "ocr_install":   "pip install pytesseract Pillow  +  install Tesseract binary from https://github.com/tesseract-ocr/tesseract",
        "pdf_install":   "pip install pymupdf",
    }


# ═════════════════════════════════════════════════════════════════════════
#  ELEMENT CANDIDATE RANKER
#  Scores every visible element by relevance to the current task description.
#  Returns a ranked list with explanations so the AI can try alternatives.
# ═════════════════════════════════════════════════════════════════════════

import re as _re

# Keyword groups → task hints → bonus scores
_INTENT_KEYWORDS = {
    # Writing / content creation
    "title":       ["title","heading","add title","post title","enter title","h1","name"],
    "content":     ["content","body","paragraph","text","write","editor","block","add text","type here"],
    "publish":     ["publish","submit","post","save","update","send","release","go live"],
    "save":        ["save","draft","update"],
    # Navigation
    "login":       ["log in","sign in","submit","login","enter"],
    "new_post":    ["add new","new post","create post","add post","write post"],
    "plugins":     ["plugins","add plugin","installed plugins"],
    "orders":      ["orders","woocommerce","wc-orders"],
    # Forms
    "username":    ["username","user","email","login"],
    "password":    ["password","pass","pwd"],
    "search":      ["search","find","query","filter"],
    "confirm":     ["ok","yes","confirm","agree","accept","continue","proceed","next"],
    "cancel":      ["cancel","close","dismiss","no","back"],
}

# Tags that are more likely to be interactive in a useful way
_TAG_SCORES = {
    "button": 8,
    "input":  6,
    "a":      4,
    "select": 5,
    "textarea": 7,
    "div":    1,
    "span":   1,
    "label":  2,
    "li":     2,
    "summary": 3,
}

# Input types that are likely content targets
_INPUT_TYPE_SCORES = {
    "text":     5,
    "email":    4,
    "password": 4,
    "submit":   7,
    "button":   6,
    "search":   5,
    "checkbox": 3,
    "radio":    3,
}


def rank_candidates(elements: list, task_description: str,
                    already_tried: list = None,
                    max_results: int = 6) -> list:
    """
    Score every visible element by how relevant it is to the task description.
    Returns top N elements with scores and match reasons.

    Parameters
    ----------
    elements        : list of element dicts from extract_elements()
    task_description: the current sub-task description
    already_tried   : list of (x, y) tuples that already failed this step
    max_results     : how many candidates to return

    Returns
    -------
    list of dicts: [{element, score, reasons, rank}]
    """
    already_tried = already_tried or []
    task_lower = task_description.lower()
    task_words = set(_re.findall(r'\w+', task_lower))

    scored = []
    for e in elements:
        # Skip disabled
        if e.get("disabled"):
            continue
        # Skip off-screen
        y = e.get("y", 0)
        x = e.get("x", 0)
        if not (-50 <= y <= 900 and -50 <= x <= 1400):
            continue

        score   = 0
        reasons = []

        tag      = e.get("tag", "")
        el_text  = (e.get("text") or "").strip().lower()
        el_type  = (e.get("type") or "").lower()
        el_role  = (e.get("role") or "").lower()
        el_href  = (e.get("href") or "").lower()
        el_name  = (e.get("name") or "").lower()

        # Base tag score
        tag_score = _TAG_SCORES.get(tag, 1)
        score += tag_score
        if tag_score >= 6:
            reasons.append(f"interactive <{tag}>")

        # Input type score
        if tag == "input" and el_type:
            ts = _INPUT_TYPE_SCORES.get(el_type, 2)
            score += ts
            if ts >= 5:
                reasons.append(f"input[type={el_type}]")

        # Keyword matching between task and element text
        el_words = set(_re.findall(r'\w+', el_text + " " + el_href + " " + el_name))
        common   = task_words & el_words
        if common:
            kw_score = min(len(common) * 4, 20)
            score   += kw_score
            reasons.append(f"matches: {', '.join(list(common)[:4])}")

        # Intent keyword groups
        for intent, kws in _INTENT_KEYWORDS.items():
            if any(k in task_lower for k in kws):
                # Task wants this intent — boost matching elements
                if any(k in el_text or k in el_href or k in el_name for k in kws):
                    score += 10
                    reasons.append(f"intent:{intent}")
                    break

        # Penalty for already-tried coordinates
        already_key = (e.get("x"), e.get("y"))
        if already_key in [(t[0], t[1]) for t in already_tried]:
            score -= 50
            reasons.append("⚠ already tried")

        # Slight position bonus for elements in the main content area (centre of page)
        if 200 <= x <= 1080 and 50 <= y <= 700:
            score += 2

        scored.append({
            "element": e,
            "score":   score,
            "reasons": reasons,
        })

    # Sort descending
    scored.sort(key=lambda s: s["score"], reverse=True)

    # Add rank
    results = []
    for i, s in enumerate(scored[:max_results]):
        s["rank"] = i + 1
        results.append(s)

    return results


def format_candidates(candidates: list, task_description: str) -> str:
    """
    Format ranked candidates into a prompt-ready string.
    """
    if not candidates:
        return "(no matching elements found — consider using navigate with a direct URL)"

    lines = [f"RANKED CANDIDATES for task: \"{task_description[:80]}\"",
             "(Try #1 first. If it fails or nothing changes, try #2, #3, etc.)",
             ""]
    for c in candidates:
        e = c["element"]
        tag  = e.get("tag","?")
        text = e.get("text","")[:60]
        x, y = e.get("x",0), e.get("y",0)
        href = e.get("href","")
        why  = ", ".join(c["reasons"][:3]) if c["reasons"] else "generic element"
        tried_mark = " ⚠ALREADY TRIED — skip this" if c["score"] < 0 else ""
        lines.append(
            f"  #{c['rank']} <{tag}> \"{text}\" @ ({x},{y})"
            + (f" → {href[:50]}" if href else "")
            + f"\n      Why: {why}{tried_mark}"
        )
    return "\n".join(lines)
