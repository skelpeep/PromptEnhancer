import json
import os
import queue
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch


HAS_DESKTOP = sys.platform == "win32"
if HAS_DESKTOP:
    try:
        import tkinter
    except ImportError:
        HAS_DESKTOP = False
if HAS_DESKTOP:
    from desktop import main
    from desktop.store import DEFAULT_SETTINGS, Store
    import win_core


@unittest.skipUnless(HAS_DESKTOP, "Windows with tkinter required")
class DesktopOperationTests(unittest.TestCase):
    def setUp(self):
        self.app = object.__new__(main.EnhancerApp)
        self.app.settings = dict(DEFAULT_SETTINGS)
        self.app._job_settings = dict(DEFAULT_SETTINGS)
        self.app._input_target = (100, 101)
        self.app._captured_seq = 42
        self.app._quitting = False
        self.app.queue = queue.Queue()
        self.app.last = None
        self.app.busy = False
        self.app.notify = Mock()
        self.app.log = Mock()
        self.app.store = Mock()
        self.app.win = Mock()
        self.app.shell = Mock()

    def deliver(self, target=(100, 101), seq=42, manual=False, source="selection"):
        with patch.object(win_core, "get_input_target", return_value=target), \
                patch.object(win_core, "clipboard_sequence", return_value=seq), \
                patch.object(win_core, "set_clipboard_text", return_value=True) as write, \
                patch.object(win_core, "wait_modifiers_released"), \
                patch.object(win_core, "modifier_down", return_value=[]), \
                patch.object(win_core, "send_ctrl") as send, \
                patch.object(main.time, "sleep"):
            self.app._deliver_worker("enhanced", "original", source, "old", manual)
        event = self.app.queue.get_nowait()
        return event, send, write

    def test_switching_window_does_not_paste(self):
        event, send, write = self.deliver(target=(200, 201))
        self.assertEqual(event[1], "focus_changed")
        self.assertFalse(event[2]["pasted"])
        send.assert_not_called()
        write.assert_called_once_with("enhanced")

    def test_switching_control_does_not_paste(self):
        event, send, _ = self.deliver(target=(100, 102))
        self.assertEqual(event[1], "focus_changed")
        send.assert_not_called()

    def test_typing_in_same_control_does_not_paste(self):
        self.app._captured_input = 123
        with patch.object(win_core, "last_input_tick", return_value=456):
            event, send, write = self.deliver()
        self.assertEqual(event[1], "input_changed")
        send.assert_not_called()
        write.assert_called_once_with("enhanced")

    def test_new_clipboard_is_not_overwritten(self):
        event, send, write = self.deliver(seq=43)
        self.assertEqual(event[1], "clipboard_changed")
        self.assertIsNone(event[2])
        write.assert_not_called()
        send.assert_not_called()

    def test_paste_records_target_and_restores_old_text(self):
        event, send, write = self.deliver()
        self.assertEqual(event[1], "pasted")
        self.assertTrue(event[2]["pasted"])
        self.assertEqual(event[2]["target"], (100, 101))
        send.assert_called_once_with(win_core.VK_V)
        self.assertEqual([c.args[0] for c in write.call_args_list], ["enhanced", "old"])

    def test_history_and_clipboard_fallback_only_copy(self):
        for manual, source in ((True, "manual"), (False, "clipboard")):
            with self.subTest(source=source):
                event, send, _ = self.deliver(manual=manual, source=source)
                self.assertEqual(event[1], "copied")
                self.assertFalse(event[2]["pasted"])
                send.assert_not_called()

    def test_undo_only_injects_for_pasted_result_in_same_target(self):
        for pasted, target, age, expected in (
            (True, (100, 101), 1, "ok"),
            (False, (100, 101), 1, "copied"),
            (True, (200, 201), 1, "copied"),
            (True, (100, 101), 700, "copied"),
        ):
            with self.subTest(pasted=pasted, target=target, age=age), \
                    patch.object(win_core, "get_input_target", return_value=target), \
                    patch.object(win_core, "wait_modifiers_released"), \
                    patch.object(win_core, "modifier_down", return_value=[]), \
                    patch.object(win_core, "set_clipboard_text", return_value=True) as write, \
                    patch.object(win_core, "send_ctrl") as send:
                self.app._undo_worker({"before": "original", "pasted": pasted,
                                       "target": (100, 101), "ts": time.time() - age})
                self.assertEqual(self.app.queue.get_nowait()[1], expected)
                self.assertEqual(send.call_count, int(expected == "ok"))
                write.assert_called_once_with("original")

    def test_failed_clipboard_write_prevents_native_undo(self):
        with patch.object(win_core, "wait_modifiers_released"), \
                patch.object(win_core, "set_clipboard_text", return_value=False), \
                patch.object(win_core, "send_ctrl") as send:
            self.app._undo_worker({"before": "original", "ts": time.time(), "pasted": True})
        self.assertEqual(self.app.queue.get_nowait()[1], "error")
        send.assert_not_called()

    def test_busy_prevents_overlapping_undo(self):
        self.app.busy = True
        self.app.last = {"before": "original"}
        with patch.object(main.threading, "Thread") as worker:
            self.app.start_undo()
        worker.assert_not_called()

    def test_save_failure_keeps_active_configuration(self):
        original = dict(self.app.settings)
        self.app.store.save_settings.side_effect = OSError("disk full")
        ok, message = self.app.save_settings({"model": "new-model"})
        self.assertFalse(ok)
        self.assertIn("disk full", message)
        self.assertEqual(self.app.settings, original)
        self.app.shell.apply_hotkeys.assert_not_called()

    def test_zero_temperature_is_preserved(self):
        self.app._job_settings["temperature"] = 0.0
        with patch.object(win_core, "get_clipboard_text", return_value="old"), \
                patch.object(win_core, "clipboard_sequence", return_value=42), \
                patch.object(main, "enhance") as enhance:
            self.app._enhance_worker("long enough input", True)
        self.assertEqual(enhance.call_args.kwargs["temperature"], 0.0)


@unittest.skipUnless(HAS_DESKTOP, "Windows with tkinter required")
class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)

    def test_corrupted_setting_types_fall_back(self):
        Store._atomic_write(self.store.settings_path, {
            "_schema": "broken", "temperature": "bad", "timeout": -1,
            "auto_paste": "false", "model": ["bad"], "max_tokens": 1.5,
        })
        saved = self.store.load_settings()
        for key in ("temperature", "timeout", "auto_paste", "model", "max_tokens", "_schema"):
            self.assertEqual(saved[key], DEFAULT_SETTINGS[key])

    def test_invalid_encoding_uses_defaults(self):
        with open(self.store.settings_path, "wb") as handle:
            handle.write(b"\xff\xfeinvalid")
        self.assertEqual(self.store.load_settings(), DEFAULT_SETTINGS)

    def test_encryption_failure_never_writes_plaintext(self):
        settings = dict(DEFAULT_SETTINGS, api_key="test-secret")
        with patch("desktop.store.dpapi_protect", side_effect=OSError("unavailable")):
            with self.assertRaises(OSError):
                self.store.save_settings(settings)
        self.assertFalse(os.path.exists(self.store.settings_path))

    def test_atomic_write_preserves_previous_file_on_failure(self):
        Store._atomic_write(self.store.settings_path, {"model": "previous"})
        with patch("desktop.store.os.replace", side_effect=OSError("denied")):
            with self.assertRaises(OSError):
                Store._atomic_write(self.store.settings_path, {"model": "new"})
        with open(self.store.settings_path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"model": "previous"})
        self.assertEqual(os.listdir(self.tmp.name), ["settings.json"])

    def test_invalid_history_entries_are_filtered(self):
        Store._atomic_write(self.store.history_path, [None, "bad", {"before": []},
                                                     {"before": "good", "after": "result"}])
        self.assertEqual(len(self.store.load_history()), 1)
        self.assertEqual(self.store.load_history()[0]["before"], "good")

    def test_multiple_hotkey_main_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            win_core.parse_hotkey("ctrl+e+x")


if __name__ == "__main__":
    unittest.main()
