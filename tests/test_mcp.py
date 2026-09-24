import io
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

import mcp_server
from core import EnhanceResult


class McpTests(unittest.TestCase):
    def request(self, method, params=None, **fields):
        output = io.StringIO()
        msg = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}, **fields}
        with patch.object(sys, "stdout", output):
            mcp_server.handle(msg)
        return json.loads(output.getvalue()) if output.getvalue() else None

    def test_malformed_input_does_not_stop_following_requests(self):
        input_text = 'not json\n[]\nnull\n42\n{"jsonrpc":"2.0","id":7,"method":"ping"}\n'
        output = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(input_text)), patch.object(sys, "stdout", output):
            self.assertEqual(mcp_server.main(), 0)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[-1], {"jsonrpc": "2.0", "id": 7, "result": {}})

    def test_notifications_have_no_response(self):
        output = io.StringIO()
        with patch.object(sys, "stdout", output), patch("mcp_server.enhance") as enhance:
            mcp_server.handle({"jsonrpc": "2.0", "method": "tools/call", "params": {}})
            mcp_server.handle({"jsonrpc": "2.0", "method": "ping"})
        self.assertEqual(output.getvalue(), "")
        enhance.assert_not_called()

    def test_invalid_tool_arguments_do_not_reach_service(self):
        for args in ([], {"text": 3}, {"text": ""}, {"text": "draft", "model": []}):
            with self.subTest(args=args), patch("mcp_server.enhance") as enhance:
                response = self.request("tools/call", {"name": "enhance_prompt", "arguments": args})
                self.assertTrue("error" in response or response["result"]["isError"])
                enhance.assert_not_called()

    def test_tool_failure_preserves_original_draft(self):
        with patch("mcp_server.enhance", return_value=EnhanceResult(False, "original draft", "offline")):
            response = self.request("tools/call", {"name": "enhance_prompt", "arguments": {"text": "original draft"}})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("original draft", response["result"]["content"][0]["text"])

    def test_prompt_requires_a_string_draft(self):
        for args in ({}, {"draft": 3}, {"draft": ""}):
            response = self.request("prompts/get", {"name": "enhance", "arguments": args})
            self.assertEqual(response["error"]["code"], -32602)

    def test_initialize_negotiates_a_supported_version(self):
        response = self.request("initialize", {"protocolVersion": "unsupported-future-version"})
        self.assertEqual(response["result"]["protocolVersion"], mcp_server.DEFAULT_PROTOCOL)

    def test_stdio_uses_utf8_independently_of_windows_code_page(self):
        draft = "\u4e2d\u6587\u8349\u7a3f"
        request = {"jsonrpc": "2.0", "id": 2, "method": "prompts/get",
                   "params": {"name": "enhance", "arguments": {"draft": draft}}}
        process = subprocess.run([sys.executable, mcp_server.__file__],
                                 input=(json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=dict(os.environ, PYTHONIOENCODING="ascii"), timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        response = json.loads(process.stdout.decode("utf-8"))
        self.assertIn(draft, response["result"]["messages"][0]["content"]["text"])


if __name__ == "__main__":
    unittest.main()
