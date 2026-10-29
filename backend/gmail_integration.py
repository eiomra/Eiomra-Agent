"""Local Gmail OAuth connector for Eiomra Agent.

This is deliberately single-user and loopback-only. No Google credentials,
access tokens, or refresh tokens are ever returned to the frontend.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import time
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from urllib.parse import urlencode
from cryptography.fernet import Fernet, InvalidToken

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
# gmail.modify covers read, compose, send, and label updates without permanent deletion.
SCOPES = ("https://www.googleapis.com/auth/gmail.modify",)
LOCAL_HOSTS = {"127.0.0.1", "::1"}
# These are only used for browser requests, not for OAuth's callback redirect.
UI_ORIGINS = {"http://localhost:3000", "http://127.0.0.1:3000"}
MESSAGE_ID = re.compile(r"^[A-Za-z0-9_-]{8,80}$")


def guard_local(request: Request) -> None:
    """Never make mailbox operations available over Eiomra's default 0.0.0.0 bind."""
    if not request.client or request.client.host not in LOCAL_HOSTS:
        raise HTTPException(403, "Gmail integration is accessible only from this computer.")
    origin = request.headers.get("origin")
    if origin and origin not in UI_ORIGINS:
        raise HTTPException(403, "Untrusted browser origin.")


def valid_address(value: str) -> str:
    address = parseaddr(value)[1]
    if not address or address != value.strip() or any(c in address for c in "\r\n"):
        raise ValueError("Provide a single valid recipient email address.")
    if "@" not in address or len(address) > 254 or any(c.isspace() for c in address):
        raise ValueError("Invalid recipient email address.")
    return address


def decode_body(payload: dict) -> str:
    """Return decoded plain text, avoiding HTML/script execution."""
    def walk(part: dict):
        mime = part.get("mimeType", "")
        body = part.get("body") or {}
        if mime == "text/plain" and body.get("data"):
            yield body["data"]
        for nested in part.get("parts") or []:
            yield from walk(nested)
    parts = list(walk(payload))
    if not parts and payload.get("body", {}).get("data"):
        parts = [payload["body"]["data"]]
    decoded = []
    for item in parts[:10]:
        try:
            raw = base64.urlsafe_b64decode(item + "=" * (-len(item) % 4))
            decoded.append(raw.decode("utf-8", "replace"))
        except (ValueError, TypeError):
            continue
    return "\n".join(decoded)[:12000]


def message_summary(message: dict) -> dict:
    headers = {h.get("name", "").lower(): h.get("value", "")
               for h in (message.get("payload") or {}).get("headers", [])}
    return {
        "id": message.get("id", ""),
        "thread_id": message.get("threadId", ""),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "snippet": message.get("snippet", "")[:400],
    }


class ComposeRequest(BaseModel):
    to: str = Field(min_length=3, max_length=254)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=50000)


class GmailConnector:
    def __init__(self, token_path: Path):
        self.path = Path(token_path)
        self.pending_auth: dict[str, dict] = {}
        self.pending_drafts: dict[str, dict] = {}
        self._refresh_lock = asyncio.Lock()
        self._owner_sessions: dict[str, float] = {}
        self.pending_path = self.path.with_name("gmail_pending.enc")
        self._load_pending()

    @property
    def client_id(self) -> str:
        return os.environ.get("EIOMRA_GMAIL_CLIENT_ID", "").strip()

    @property
    def client_secret(self) -> str:
        return os.environ.get("EIOMRA_GMAIL_CLIENT_SECRET", "").strip()

    @property
    def redirect_uri(self) -> str:
        # Fixed origin to avoid use of untrusted Host/X-Forwarded-* request headers.
        return os.environ.get("EIOMRA_GMAIL_REDIRECT_URI", "http://localhost:8765/gmail/oauth/callback")

    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and
                    os.environ.get("EIOMRA_GMAIL_ENCRYPTION_KEY") and
                    os.environ.get("EIOMRA_GMAIL_OWNER_PASSWORD"))

    def _cipher(self) -> Fernet:
        key = os.environ.get("EIOMRA_GMAIL_ENCRYPTION_KEY", "")
        if not key:
            raise ValueError("EIOMRA_GMAIL_ENCRYPTION_KEY is required.")
        try:
            return Fernet(key.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid Gmail encryption key; generate one with Fernet.generate_key().") from exc

    def _read_encrypted(self, path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return json.loads(self._cipher().decrypt(path.read_bytes()))
        except (InvalidToken, ValueError, OSError) as exc:
            raise ValueError("Cannot decrypt Gmail credentials; check encryption key.") from exc

    def _write_encrypted(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            os.chmod(path.parent, 0o700)
        temp = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
        fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(self._cipher().encrypt(json.dumps(value).encode("utf-8")))
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, path)
            if os.name != "nt":
                os.chmod(path, 0o600)
        finally:
            temp.unlink(missing_ok=True)

    def load_tokens(self) -> dict:
        return self._read_encrypted(self.path)

    def save_tokens(self, tokens: dict) -> None:
        self._write_encrypted(self.path, tokens)

    def _load_pending(self) -> None:
        try:
            self.pending_drafts = self._read_encrypted(self.pending_path)
        except ValueError:
            self.pending_drafts = {}

    def _save_pending(self) -> None:
        self._write_encrypted(self.pending_path, self.pending_drafts)

    def login(self, password: str) -> str:
        expected = os.environ.get("EIOMRA_GMAIL_OWNER_PASSWORD", "")
        if len(expected) < 16 or not secrets.compare_digest(password, expected):
            raise ValueError("Invalid Gmail owner password.")
        session_id = secrets.token_urlsafe(32)
        self._owner_sessions = {k: t for k, t in self._owner_sessions.items() if t > time.time()}
        self._owner_sessions[session_id] = time.time() + 43200
        return session_id

    def authenticated(self, request: Request) -> bool:
        key = request.cookies.get("eiomra_gmail_owner", "")
        expiry = self._owner_sessions.get(key, 0)
        return bool(key and expiry > time.time())

    def status(self) -> dict:
        try:
            data = self.load_tokens()
        except ValueError:
            data = {}
        return {
            "configured": self.configured(),
            "connected": bool(data.get("refresh_token")),
            "email": data.get("email", "") if data.get("refresh_token") else "",
            "send_requires_approval": True,
        }

    def start_auth(self) -> str:
        if not self.configured():
            raise HTTPException(400, "Set EIOMRA_GMAIL_CLIENT_ID and EIOMRA_GMAIL_CLIENT_SECRET first.")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        now = time.time()
        self.pending_auth = {key: item for key, item in self.pending_auth.items() if item["until"] > now}
        self.pending_auth[state] = {"verifier": verifier, "until": now + 600}
        return AUTH_URL + "?" + urlencode({
            "response_type": "code", "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(SCOPES),
            "access_type": "offline", "prompt": "consent",
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        })

    async def finish_auth(self, state: str, code: str) -> dict:
        auth = self.pending_auth.pop(state, None)
        if not auth or auth["until"] < time.time():
            raise HTTPException(400, "OAuth state expired or invalid. Try connecting again.")
        if not code:
            raise HTTPException(400, "Missing Google authorization code.")
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(TOKEN_URL, data={
                "client_id": self.client_id, "client_secret": self.client_secret,
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": self.redirect_uri, "code_verifier": auth["verifier"],
            })
            if response.status_code != 200:
                raise HTTPException(400, "Google token exchange failed.")
            payload = response.json()
            granted = set(payload.get("scope", "").split())
            if not set(SCOPES).issubset(granted):
                raise HTTPException(403, "Both Gmail read and send permissions are required.")
            refresh = payload.get("refresh_token")
            if not refresh:
                raise HTTPException(400, "No offline refresh token returned; revoke consent and retry.")
            email_resp = await client.get(GMAIL_URL + "/profile", headers={
                "Authorization": "Bearer " + payload["access_token"],
            })
            if email_resp.status_code != 200:
                raise HTTPException(400, "Could not verify the connected Gmail account.")
            email = email_resp.json().get("emailAddress", "")
            if not email:
                raise HTTPException(400, "Google did not return the Gmail address.")
            self.save_tokens({
                "email": email, "access_token": payload["access_token"],
                "refresh_token": refresh, "expires_at": time.time() + int(payload.get("expires_in", 3600)) - 60,
            })
            return {"email": email}

    async def access_token(self) -> str:
        if not self.configured():
            raise ValueError("Google OAuth credentials are not configured.")
        async with self._refresh_lock:
            data = self.load_tokens()
            if not data.get("refresh_token"):
                raise ValueError("Gmail is not connected. Connect it in Settings.")
            if data.get("access_token") and data.get("expires_at", 0) > time.time():
                return data["access_token"]
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(TOKEN_URL, data={
                    "client_id": self.client_id, "client_secret": self.client_secret,
                    "refresh_token": data["refresh_token"], "grant_type": "refresh_token",
                })
            if response.status_code != 200:
                raise ValueError("Gmail token refresh failed. Reconnect your account.")
            payload = response.json()
            data["access_token"] = payload["access_token"]
            data["expires_at"] = time.time() + int(payload.get("expires_in", 3600)) - 60
            self.save_tokens(data)
            return data["access_token"]

    async def request_api(self, method: str, path: str, *, params=None, json_data=None) -> dict:
        token = await self.access_token()
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.request(method, GMAIL_URL + path, params=params,
                                            json=json_data,
                                            headers={"Authorization": "Bearer " + token})
        if response.status_code >= 400:
            # Avoid returning Google responses that could echo sensitive values.
            raise ValueError("Gmail API request failed (HTTP %s)." % response.status_code)
        return response.json()

    async def search(self, query: str = "", limit: int = 5) -> dict:
        limit = max(1, min(int(limit), 10))
        raw = await self.request_api("GET", "/messages", params={"q": query[:300], "maxResults": limit})
        messages = []
        for msg in raw.get("messages", [])[:limit]:
            item = await self.request_api("GET", "/messages/" + msg["id"],
                                          params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date", "To"]})
            messages.append(message_summary(item))
        return {"messages": messages, "next_page_token": raw.get("nextPageToken", "")}

    async def read(self, message_id: str) -> dict:
        if not MESSAGE_ID.fullmatch(message_id):
            raise ValueError("Invalid Gmail message ID.")
        raw = await self.request_api("GET", "/messages/" + message_id, params={"format": "full"})
        return {**message_summary(raw), "body": decode_body(raw.get("payload") or {})}

    def draft(self, to: str, subject: str, body: str) -> dict:
        if not self.status()["connected"]:
            raise ValueError("Gmail is not connected.")
        addr = valid_address(to)
        if not subject.strip() or not body.strip():
            raise ValueError("Subject and message body are required.")
        if len(subject) > 500 or len(body) > 50000 or "\r" in subject or "\n" in subject:
            raise ValueError("Invalid subject or body length.")
        now = time.time()
        self.pending_drafts = {k: v for k, v in self.pending_drafts.items() if v["expires_at"] > now}
        if len(self.pending_drafts) >= 25:
            raise ValueError("Too many pending messages; approve or discard existing messages first.")
        draft_id = secrets.token_urlsafe(20)
        self.pending_drafts[draft_id] = {
            "id": draft_id, "to": addr, "subject": subject,
            "body": body, "expires_at": now + 86400,
        }
        self._save_pending()
        return self.pending_drafts[draft_id].copy()

    def drafts(self) -> list[dict]:
        now = time.time()
        current = {k: v for k, v in self.pending_drafts.items() if v["expires_at"] > now}
        if current != self.pending_drafts:
            self.pending_drafts = current
            self._save_pending()
        return list(self.pending_drafts.values())

    async def approve_send(self, draft_id: str) -> dict:
        # Pop before awaiting network so concurrent approvals cannot duplicate a send.
        draft = self.pending_drafts.pop(draft_id, None)
        if not draft or draft["expires_at"] <= time.time():
            raise ValueError("Draft is missing or expired.")
        # Persist the removal before sending; retries after ambiguous errors
        # risk duplicate delivery and are intentionally not automatic.
        self._save_pending()
        msg = EmailMessage()
        msg["To"] = draft["to"]
        msg["Subject"] = draft["subject"]
        if draft.get("in_reply_to"):
            msg["In-Reply-To"] = draft["in_reply_to"]
            msg["References"] = draft["in_reply_to"]
        msg.set_content(draft["body"])
        encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
        payload = {"raw": encoded}
        if draft.get("thread_id"):
            payload["threadId"] = draft["thread_id"]
        try:
            result = await self.request_api("POST", "/messages/send", json_data=payload)
        except Exception:
            # Ambiguous delivery on transport errors: do not automatically retry.
            raise ValueError("Send failed or outcome unknown. Check Gmail Sent before retrying.")
        return {"id": result.get("id"), "thread_id": result.get("threadId"), "sent": True}

    async def disconnect(self):
        tokens = self.load_tokens()
        self.path.unlink(missing_ok=True)
        self.pending_path.unlink(missing_ok=True)
        self.pending_drafts.clear()
        self.pending_auth.clear()
        self._owner_sessions.clear()
        token = tokens.get("refresh_token") or tokens.get("access_token")
        if token:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(REVOKE_URL, data={"token": token})
            except httpx.HTTPError:
                pass


def make_router(service: GmailConnector) -> APIRouter:
    router = APIRouter(prefix="/gmail", tags=["gmail"])

    @router.get("/status")
    async def status(request: Request):
        guard_local(request)
        return service.status()

    @router.get("/oauth/start")
    async def oauth_start(request: Request):
        guard_local(request)
        return RedirectResponse(service.start_auth(), status_code=302)

    @router.get("/oauth/callback")
    async def oauth_callback(request: Request, state: str = "", code: str = "", error: str = ""):
        guard_local(request)
        if error:
            # Never persist or display an OAuth code.
            return HTMLResponse("<h3>Gmail connection was cancelled.</h3>", status_code=400)
        try:
            await service.finish_auth(state, code)
        except (HTTPException, ValueError):
            return HTMLResponse("<h3>Gmail connection failed. Return to Eiomra Settings and retry.</h3>", status_code=400)
        return RedirectResponse("http://localhost:3000/?gmail=connected", status_code=303)

    @router.post("/disconnect")
    async def disconnect(request: Request):
        guard_local(request)
        await service.disconnect()
        return {"connected": False}

    @router.get("/messages")
    async def search(request: Request, q: str = "", limit: int = 5):
        guard_local(request)
        try:
            return await service.search(q, limit)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/messages/{message_id}")
    async def read(request: Request, message_id: str):
        guard_local(request)
        try:
            return await service.read(message_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/drafts")
    async def drafts(request: Request):
        guard_local(request)
        return {"drafts": service.drafts()}

    @router.post("/drafts")
    async def create_draft(request: Request, body: ComposeRequest):
        guard_local(request)
        try:
            return service.draft(body.to, body.subject, body.body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/drafts/{draft_id}/approve")
    async def approve(request: Request, draft_id: str):
        guard_local(request)
        try:
            return await service.approve_send(draft_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.delete("/drafts/{draft_id}")
    async def discard(request: Request, draft_id: str):
        guard_local(request)
        return {"discarded": service.pending_drafts.pop(draft_id, None) is not None}

    return router
