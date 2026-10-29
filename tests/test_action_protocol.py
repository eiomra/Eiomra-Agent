"""Regression tests for model action parsing and sanitization."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from action_protocol import action_from_payload, action_to_history_entry, parse_action_response, normalize_action_payload

class ActionProtocolTests(unittest.TestCase):
    def test_braces_inside_json_string(self):
        parsed = parse_action_response('{"action":"type","text":"close } and open { survive"}')
        self.assertEqual(parsed["text"], "close } and open { survive")

    def test_escaped_quote_and_nested_data(self):
        raw = r'{"action":"type","text":"She said \"hi }\"","metadata":{"nested":true}}'
        parsed = parse_action_response(raw)
        self.assertEqual(parsed["text"], 'She said "hi }"')
        self.assertEqual(parsed["metadata"], {"nested": True})

    def test_trailing_comma_recovery(self):
        parsed = parse_action_response('{"action":"navigate","url":"https://example.com",}')
        self.assertEqual(parsed["url"], "https://example.com")

    def test_aliases_and_defaults(self):
        self.assertEqual(normalize_action_payload({"action":"hotkey"})["action"], "desktop_hotkey")
        self.assertEqual(parse_action_response("invalid model response")["action"], "wait")

    def test_whitespace_preserved(self):
        payload = action_from_payload({"action":"write_file","content":"  first\nsecond\n  ","old_text":" x\n","new_text":"\n y "}).to_dict()
        self.assertEqual(payload["content"], "  first\nsecond\n  ")
        self.assertEqual(payload["old_text"], " x\n")
        self.assertEqual(payload["new_text"], "\n y ")

    def test_typed_input_redacted_from_history(self):
        for name in ("type", "type_and_submit", "desktop_type"):
            with self.subTest(action=name):
                history = action_to_history_entry({"action":name,"text":"sample-secret-password"})
                self.assertNotIn("sample-secret-password", history)
                self.assertIn("redacted", history)

    def test_non_sensitive_click_text_visible(self):
        self.assertIn("Continue", action_to_history_entry({"action":"click_text","text":"Continue"}))

if __name__ == "__main__":
    unittest.main()
