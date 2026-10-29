"""Regression tests for native Gmail tools across model-provider response formats."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from gmail_tool_calls import (
    FIELDS, openai_tools, gemini_tools, anthropic_tools,
    from_openai_message, from_ollama_message, from_gemini_response,
    from_anthropic_response,
)


class GmailNativeToolTests(unittest.TestCase):
    def test_all_providers_expose_same_functions(self):
        names = set(FIELDS)
        self.assertEqual(names, {tool["function"]["name"] for tool in openai_tools()})
        self.assertEqual(names, {tool["name"] for tool in anthropic_tools()})
        self.assertEqual(names, {tool["name"] for tool in gemini_tools()[0]["functionDeclarations"]})

    def test_openai_send_is_never_direct(self):
        message = {"tool_calls": [{"type": "function", "function": {
            "name": "gmail_send", "arguments": json.dumps({
                "to": "person@example.com", "subject": "Hello", "body": "Message",
            }),
        }}]}
        action = json.loads(from_openai_message(message))
        self.assertEqual(action["action"], "gmail_queue_send")
        self.assertEqual(action["to"], "person@example.com")

    def test_ollama_tool_argument_dictionary(self):
        message = {"tool_calls": [{"function": {
            "name": "gmail_read", "arguments": {"message_id": "1234567890"},
        }}]}
        self.assertEqual(json.loads(from_ollama_message(message))["message_id"], "1234567890")

    def test_gemini_function_call(self):
        data = {"candidates": [{"content": {"parts": [{
            "functionCall": {"name": "gmail_search", "args": {"query": "from:example@gmail.com", "limit": 3}},
        }]}}]}
        result = json.loads(from_gemini_response(data))
        self.assertEqual(result["action"], "gmail_search")
        self.assertEqual(result["limit"], 3)

    def test_anthropic_function_call(self):
        data = {"content": [{"type": "tool_use", "name": "gmail_list_labels", "input": {}}]}
        self.assertEqual(json.loads(from_anthropic_response(data))["action"], "gmail_list_labels")

    def test_reject_unknown_functions_and_extra_arguments(self):
        self.assertIsNone(from_openai_message({"tool_calls": [
            {"function": {"name": "run_command", "arguments": '{"command":"rm -rf /"}'}}
        ]}))
        self.assertIsNone(from_openai_message({"tool_calls": [
            {"function": {"name": "gmail_read", "arguments": '{"message_id":"12345678","extra":"bad"}'}}
        ]}))

    def test_reject_invalid_arguments(self):
        invalid = {"tool_calls": [{"function": {"name": "gmail_search",
                              "arguments": '{"query":"hi","limit":"many"}'}}]}
        self.assertIsNone(from_openai_message(invalid))

    def test_limit_is_bounded(self):
        data = {"tool_calls": [{"function": {"name": "gmail_search",
               "arguments": '{"query":"in:inbox","limit":999999}'}}]}
        self.assertEqual(json.loads(from_openai_message(data))["limit"], 10)


if __name__ == "__main__":
    unittest.main()
