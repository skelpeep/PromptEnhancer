import io
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import core


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = patch.object(core, "_config_loaded", True)
        self.config.start()
        self.addCleanup(self.config.stop)

    def test_endpoint_preserves_query_and_does_not_duplicate_completion_path(self):
        cases = {
            "https://example.invalid": "https://example.invalid/v1/chat/completions",
            "https://example.invalid/v1/": "https://example.invalid/v1/chat/completions",
            "https://example.invalid/v1/chat/completions/": "https://example.invalid/v1/chat/completions",
            "https://example.invalid/custom?api-version=1": "https://example.invalid/custom/chat/completions?api-version=1",
        }
        for base, expected in cases.items():
            with self.subTest(base=base):
                self.assertEqual(core.chat_completions_url(base), expected)

    @patch("core._post_json")
    def test_invalid_configuration_preserves_draft_without_request(self, post):
        for options in ({"timeout": 0}, {"timeout": float("nan")}, {"max_tokens": -1},
                        {"base_url": "ftp://example.invalid"}, {"temperature": 3}):
            with self.subTest(options=options):
                result = core.enhance("my original draft", **options)
                self.assertFalse(result.ok)
                self.assertEqual(result.text, "my original draft")
        post.assert_not_called()

    def test_explicit_empty_key_does_not_fall_back_to_environment(self):
        os.environ["ENHANCER_API_KEY"] = "test-only-environment-key"
        self.assertEqual(core.Config.resolve(api_key="").api_key, "")
        self.assertEqual(core.Config.resolve().api_key, "test-only-environment-key")

    def test_retry_uses_remaining_timeout_and_removes_optional_parameters(self):
        error = urllib.error.HTTPError("https://example.invalid", 400, "bad", {}, io.BytesIO(b"unsupported parameter"))
        with patch("core._post_json", side_effect=[error, {"choices": [{"message": {"content": "improved"}}]}]) as post, \
                patch("core.time.monotonic", side_effect=[100, 102, 108, 109]):
            result = core.enhance("draft", timeout=10)
        self.assertTrue(result.ok)
        self.assertEqual([call.args[3] for call in post.call_args_list], [8, 2])
        self.assertIn("temperature", post.call_args_list[0].args[1])
        self.assertNotIn("temperature", post.call_args_list[1].args[1])

    def test_non_retryable_http_error_returns_original_once(self):
        error = urllib.error.HTTPError("https://example.invalid", 401, "unauthorized", {}, io.BytesIO(b"no key"))
        with patch("core._post_json", side_effect=error) as post:
            result = core.enhance("draft")
        self.assertFalse(result.ok)
        self.assertEqual(result.text, "draft")
        post.assert_called_once()

    def test_empty_and_malformed_responses_preserve_original(self):
        for response in (None, [], {"choices": []}, {"choices": [{"message": {"content": 42}}]}):
            with self.subTest(response=response), patch("core._post_json", return_value=response):
                result = core.enhance("draft")
                self.assertFalse(result.ok)
                self.assertEqual(result.text, "draft")

    def test_text_parts_are_supported(self):
        response = {"choices": [{"message": {"content": [{"type": "text", "text": "improved "}, {"type": "text", "text": "draft"}]}}]}
        with patch("core._post_json", return_value=response):
            self.assertEqual(core.enhance("draft").text, "improved draft")

    def test_invalid_utf8_template_uses_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "template.txt")
            path.write_bytes(b"\xff\xfeinvalid")
            os.environ["ENHANCER_SYSTEM_FILE"] = str(path)
            self.assertEqual(core.load_templates()[0], core.DEFAULT_SYSTEM_TEMPLATE)

    def test_only_matching_quotes_are_stripped(self):
        self.assertEqual(core.strip_wrapping_quotes("\"draft'"), "\"draft'")
        self.assertEqual(core.strip_wrapping_quotes('"draft"'), "draft")

    def test_packaged_version_is_loaded_from_frozen_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "VERSION").write_text("9.8.7\n", encoding="utf-8")
            with patch.object(sys, "_MEIPASS", directory, create=True):
                self.assertEqual(core.get_version(), "9.8.7")


if __name__ == "__main__":
    unittest.main()
