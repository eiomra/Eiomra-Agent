"""Offline tests for Gmail OAuth state, approval, and local security."""
import asyncio
import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi import HTTPException, FastAPI
from fastapi.testclient import TestClient
import httpx
from cryptography.fernet import Fernet
from gmail_integration import (
    GmailConnector, decode_body, guard_local, message_summary, valid_address, make_router, SCOPES,
)


class GmailConnectorTests(unittest.TestCase):
    def setUp(self):
        self.env_patcher = patch.dict(os.environ, {
            "EIOMRA_GMAIL_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
            "EIOMRA_GMAIL_OWNER_PASSWORD": "test-strong-passphrase-only",
            "EIOMRA_GMAIL_CLIENT_ID": "test-id",
            "EIOMRA_GMAIL_CLIENT_SECRET": "test-secret",
        })
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.gmail = GmailConnector(Path(self.dir.name) / "gmail_tokens.json")

    def connect_fake_account(self):
        self.gmail.save_tokens({
            "email": "example@gmail.com",
            "refresh_token": "test-refresh-placeholder",
            "access_token": "test-access-placeholder",
            "expires_at": 9999999999,
        })

    def test_oauth_state_and_pkce(self):
        with patch.dict(os.environ, {
            "EIOMRA_GMAIL_CLIENT_ID": "test-id",
            "EIOMRA_GMAIL_CLIENT_SECRET": "test-secret",
        }):
            url = self.gmail.start_auth()
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn("access_type=offline", url)
        self.assertIn("state=", url)
        self.assertEqual(len(self.gmail.pending_auth), 1)

    def test_invalid_oauth_state_rejected(self):
        with self.assertRaises(HTTPException):
            asyncio.run(self.gmail.finish_auth("wrong-state", "example-code"))

    def test_never_return_credentials_in_status(self):
        self.connect_fake_account()
        status = self.gmail.status()
        self.assertEqual(status["email"], "example@gmail.com")
        self.assertTrue(status["connected"])
        self.assertNotIn("refresh_token", str(status))
        self.assertTrue(status["send_requires_approval"])
        self.assertNotIn(b"test-refresh-placeholder", self.gmail.path.read_bytes())

    def test_owner_login_and_session(self):
        from types import SimpleNamespace
        session = self.gmail.login("test-strong-passphrase-only")
        request = SimpleNamespace(cookies={"eiomra_gmail_owner": session})
        self.assertTrue(self.gmail.authenticated(request))
        self.assertFalse(self.gmail.authenticated(SimpleNamespace(cookies={})))
        with self.assertRaises(ValueError):
            self.gmail.login("invalid-password")

    def test_pending_queue_is_encrypted_and_survives_restart(self):
        self.connect_fake_account()
        draft = self.gmail.draft("to@example.com", "Hello", "Important private body")
        self.assertNotIn(b"Important private body", self.gmail.pending_path.read_bytes())
        restored = GmailConnector(self.gmail.path)
        self.assertEqual(restored.drafts()[0]["id"], draft["id"])

    def test_label_operations_use_gmail_api(self):
        self.connect_fake_account()
        async def verify():
            with patch.object(self.gmail, "request_api", AsyncMock(return_value={"labels": [{"id":"INBOX","name":"Inbox"}]})) as mock:
                labels = await self.gmail.list_labels()
                self.assertEqual(labels["labels"][0]["id"], "INBOX")
                mock.assert_awaited_once()
            with patch.object(self.gmail, "request_api", AsyncMock(return_value={"id":"0123456789","labelIds":["STARRED"]})) as mock:
                result = await self.gmail.modify_labels("0123456789", ["STARRED"], ["UNREAD"])
                self.assertEqual(result["label_ids"], ["STARRED"])
                self.assertEqual(mock.await_args.kwargs["json_data"]["removeLabelIds"], ["UNREAD"])
        asyncio.run(verify())

    def test_create_real_gmail_draft(self):
        self.connect_fake_account()
        with patch.object(self.gmail, "request_api", AsyncMock(return_value={"id":"draft123"})) as mock:
            result = asyncio.run(self.gmail.create_gmail_draft("to@example.com", "Subject", "Body"))
            self.assertEqual(result["draft_id"], "draft123")
            self.assertEqual(mock.await_args.args[:2], ("POST", "/drafts"))

    def test_send_is_queued_until_explicit_approval(self):
        self.connect_fake_account()
        draft = self.gmail.draft("to@example.com", "Hello", "Test body")
        self.assertEqual(len(self.gmail.drafts()), 1)
        self.assertEqual(draft["to"], "to@example.com")
        mock_send = AsyncMock(return_value={"id": "sent-123", "threadId": "thread-456"})
        with patch.object(self.gmail, "request_api", mock_send):
            result = asyncio.run(self.gmail.approve_send(draft["id"]))
        self.assertTrue(result["sent"])
        self.assertEqual(len(self.gmail.drafts()), 0)
        self.assertEqual(mock_send.await_count, 1)
        args, kwargs = mock_send.await_args
        self.assertEqual((args[0], args[1]), ("POST", "/messages/send"))
        payload = kwargs["json_data"]
        self.assertIn("raw", payload)
        decoded = base64.urlsafe_b64decode(payload["raw"] + "==").decode()
        self.assertIn("To: to@example.com", decoded)
        self.assertIn("Subject: Hello", decoded)

    def test_no_duplicate_send_from_same_approval_id(self):
        self.connect_fake_account()
        draft = self.gmail.draft("person@example.com", "Hi", "Hello")
        with patch.object(self.gmail, "request_api", AsyncMock(return_value={"id": "ok"})):
            asyncio.run(self.gmail.approve_send(draft["id"]))
        with self.assertRaises(ValueError):
            asyncio.run(self.gmail.approve_send(draft["id"]))

    def test_reject_newline_header_injection(self):
        self.connect_fake_account()
        with self.assertRaises(ValueError):
            self.gmail.draft("to@example.com", "Hello\nBcc: victim@example.com", "Body")

    def test_reject_invalid_to(self):
        for candidate in ("nobody", "a@example.com\nBcc: victim@example.com"):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                valid_address(candidate)

    def test_decode_plain_text_payload(self):
        data = base64.urlsafe_b64encode(b"Thanks for your message").decode()
        body = decode_body({"mimeType":"multipart/alternative","parts":[
            {"mimeType":"text/plain","body":{"data":data}},
        ]})
        self.assertEqual(body, "Thanks for your message")

    def test_message_summary_does_not_include_raw_body(self):
        message = {"id":"abc123", "snippet":"Hello", "payload":{"headers":[
            {"name":"Subject","value":"Subject line"},{"name":"From","value":"sender@example.com"}
        ],"body":{"data":"private"}}}
        summary = message_summary(message)
        self.assertEqual(summary["subject"],"Subject line")
        self.assertNotIn("payload",summary)

    def test_local_only_gate(self):
        class FakeRequest:
            def __init__(self, host, origin=None):
                self.client = type("Client", (), {"host":host})()
                self.headers = {"origin":origin} if origin else {}
        guard_local(FakeRequest("127.0.0.1", "http://localhost:3000"))
        with self.assertRaises(HTTPException):
            guard_local(FakeRequest("192.168.1.3", "http://localhost:3000"))
        with self.assertRaises(HTTPException):
            guard_local(FakeRequest("127.0.0.1", "https://evil.example"))


    def test_routes_require_owner_and_unlock_cookie(self):
        app = FastAPI()
        app.include_router(make_router(self.gmail))
        headers = {"Origin": "http://localhost:3000"}
        with TestClient(app, base_url="http://localhost:8765",
                        client=("127.0.0.1", 50000)) as client:
            locked = client.get("/gmail/status", headers=headers)
            self.assertEqual(locked.status_code, 200)
            self.assertTrue(locked.json()["locked"])
            self.assertEqual(client.get("/gmail/drafts", headers=headers).status_code, 401)
            bad = client.post("/gmail/unlock", headers=headers, json={"password": "wrong"})
            self.assertEqual(bad.status_code, 401)
            ok = client.post("/gmail/unlock", headers=headers,
                             json={"password": "test-strong-passphrase-only"})
            self.assertEqual(ok.status_code, 200)
            self.assertTrue(ok.json()["unlocked"])
            self.assertFalse(client.get("/gmail/status", headers=headers).json()["locked"])
            self.assertEqual(client.get("/gmail/drafts", headers=headers).json(), {"drafts": []})
            self.assertEqual(client.post("/gmail/unlock",
                                         headers={"Origin": "http://evil.example"},
                                         json={"password": "test-strong-passphrase-only"}).status_code, 403)

    def test_oauth_exchange_mocked_with_no_external_requests(self):
        self.assertEqual(len(SCOPES), 1)
        url = self.gmail.start_auth()
        self.assertIn("state=", url)
        state = next(iter(self.gmail.pending_auth))
        real_client = httpx.AsyncClient

        def handler(request):
            if str(request.url) == "https://oauth2.googleapis.com/token":
                return httpx.Response(200, json={
                    "access_token": "fake-test-access-token",
                    "refresh_token": "fake-test-refresh-token",
                    "scope": " ".join(SCOPES),
                    "expires_in": 3600,
                })
            if str(request.url).endswith("/gmail/v1/users/me/profile"):
                return httpx.Response(200, json={"emailAddress": "verified@gmail.com"})
            return httpx.Response(404)

        def fake_client(*args, **kwargs):
            return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

        with patch("gmail_integration.httpx.AsyncClient", side_effect=fake_client):
            account = asyncio.run(self.gmail.finish_auth(state, "fake-google-code"))
        self.assertEqual(account["email"], "verified@gmail.com")
        self.assertEqual(self.gmail.status()["email"], "verified@gmail.com")
        self.assertNotIn(b"fake-test-refresh-token", self.gmail.path.read_bytes())
        with self.assertRaises(HTTPException):
            asyncio.run(self.gmail.finish_auth(state, "fake-google-code"))


if __name__ == "__main__":
    unittest.main()
