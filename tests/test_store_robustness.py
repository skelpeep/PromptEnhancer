import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


# Load storage with mocked platform crypto so these regressions also run on Linux.
_crypto = types.ModuleType("win_core")
_crypto.dpapi_protect = Mock(side_effect=AssertionError("unexpected crypto access"))
_crypto.dpapi_unprotect = Mock(side_effect=AssertionError("unexpected crypto access"))
_spec = importlib.util.spec_from_file_location(
    "store_robustness_subject", Path(__file__).resolve().parents[1] / "desktop" / "store.py")
store_module = importlib.util.module_from_spec(_spec)
with patch.dict(sys.modules, {"win_core": _crypto}):
    _spec.loader.exec_module(store_module)

Store = store_module.Store
DEFAULT_SETTINGS = store_module.DEFAULT_SETTINGS


class StoreRobustnessTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(directory.name)

    def write_raw(self, path, data):
        Path(path).write_text(json.dumps(data, allow_nan=True), encoding="utf-8")

    def test_huge_integer_settings_fall_back_and_preserve_valid_fields(self):
        values = {key: 10 ** 1000 for key in
                  ("timeout", "temperature", "max_tokens", "min_len", "undo_window_sec")}
        values.update(model="local-model", _schema=2)
        self.write_raw(self.store.settings_path, values)
        settings = self.store.load_settings()
        self.assertEqual(settings["model"], "local-model")
        for key in values.keys() - {"model", "_schema"}:
            self.assertEqual(settings[key], DEFAULT_SETTINGS[key])

    def test_nonfinite_values_are_ignored_without_discarding_valid_settings(self):
        self.write_raw(self.store.settings_path, {
            "timeout": float("inf"), "temperature": float("nan"),
            "max_tokens": float("-inf"), "model": "local-model", "_schema": 2,
        })
        settings = self.store.load_settings()
        self.assertEqual(settings["model"], "local-model")
        for key in ("timeout", "temperature", "max_tokens"):
            self.assertEqual(settings[key], DEFAULT_SETTINGS[key])

    def test_invalid_save_keeps_existing_settings_and_leaves_no_temporary_file(self):
        self.store.save_settings(dict(DEFAULT_SETTINGS, model="previous"))
        previous = Path(self.store.settings_path).read_bytes()
        for invalid in (float("nan"), float("inf"), 10 ** 1000):
            with self.subTest(invalid=type(invalid).__name__):
                with self.assertRaises(OSError):
                    self.store.save_settings(dict(DEFAULT_SETTINGS, timeout=invalid))
                self.assertEqual(Path(self.store.settings_path).read_bytes(), previous)
        self.assertEqual([file.name for file in Path(self.store.dir).iterdir()], ["settings.json"])

    def test_corrupted_history_numbers_are_filtered_before_appending(self):
        invalid = []
        for field in ("ts", "elapsed_ms"):
            for value in (float("nan"), float("inf"), float("-inf"), 10 ** 1000, -1, True, "bad"):
                invalid.append({"before": "bad draft", "after": "bad result", field: value})
        self.write_raw(self.store.history_path,
                       [*invalid, {"before": "good draft", "after": "good result", "elapsed_ms": 12}])
        self.assertEqual(len(self.store.load_history()), 1)
        self.store.add_history(before="new draft", after="new result", ok=True,
                               elapsed_ms=5, model="test-model")
        history = self.store.load_history()
        self.assertEqual([item["before"] for item in history], ["new draft", "good draft"])
        serialized = Path(self.store.history_path).read_text(encoding="utf-8")
        self.assertNotIn("NaN", serialized)
        self.assertNotIn("Infinity", serialized)

    def test_unknown_nonfinite_metadata_does_not_poison_valid_history(self):
        self.write_raw(self.store.history_path, [{"before": "old draft", "after": "result",
                                                "metadata": {"score": float("nan")}}])
        self.store.add_history(before="new draft", after="new result", ok=True,
                               elapsed_ms=1, model="test-model")
        history = self.store.load_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[1]["before"], "old draft")
        self.assertNotIn("metadata", history[1])

    def test_invalid_source_and_success_flag_do_not_reach_ui(self):
        self.write_raw(self.store.history_path, [{"before": "bad", "source": []},
                                                {"before": "bad", "ok": "false"},
                                                {"before": "good", "ok": False}])
        history = self.store.load_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["before"], "good")


if __name__ == "__main__":
    unittest.main()
