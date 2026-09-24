import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cli
from core import EnhanceResult


class CliTests(unittest.TestCase):
    def run_cli(self, arguments, result=None, stdin=""):
        output, error = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["cli.py", *arguments]), \
                patch.object(sys, "stdin", io.StringIO(stdin)), \
                patch.object(sys, "stdout", output), patch.object(sys, "stderr", error), \
                patch("cli.enhance", return_value=result or EnhanceResult(True, "improved")) as enhance:
            try:
                code = cli.main()
            except SystemExit as exc:
                code = exc.code
        return code, output.getvalue(), error.getvalue(), enhance

    def test_missing_file_is_readable_error_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            code, output, error, enhance = self.run_cli(["--file", str(Path(directory, "missing.txt"))])
        self.assertEqual(code, 2)
        self.assertIn("cannot read input", error)
        self.assertEqual(output, "")
        enhance.assert_not_called()

    def test_utf8_bom_file_is_read_without_bom(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "draft.txt")
            path.write_text("draft", encoding="utf-8-sig")
            code, _, _, enhance = self.run_cli(["--file", str(path)])
        self.assertEqual(code, 0)
        self.assertEqual(enhance.call_args.args[0], "draft")

    def test_invalid_timeout_and_conflicting_inputs_do_not_call_service(self):
        for args in (["--timeout", "-1", "draft"], ["--timeout", "nan", "draft"],
                     ["--file", "draft.txt", "text"]):
            with self.subTest(args=args):
                code, _, _, enhance = self.run_cli(args)
                self.assertEqual(code, 2)
                enhance.assert_not_called()

    def test_strict_json_preserves_original_and_reports_failure(self):
        code, output, error, _ = self.run_cli(["--json", "--strict", "draft"], EnhanceResult(False, "draft", "unavailable"))
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output)["ok"])
        self.assertEqual(json.loads(output)["text"], "draft")
        self.assertEqual(error, "")

    def test_piped_input_is_passed_verbatim(self):
        code, _, _, enhance = self.run_cli([], stdin="a\nmultiline draft\n")
        self.assertEqual(code, 0)
        self.assertEqual(enhance.call_args.args[0], "a\nmultiline draft\n")


if __name__ == "__main__":
    unittest.main()
