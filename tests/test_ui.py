"""Settings regressions that do not create a window or send network requests."""

import queue
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "desktop"))

try:
    from ui import SettingsError, SettingsWindow, parse_number, validate_connection
    from widgets import HotkeyEntry, keysym_to_key
except ModuleNotFoundError as error:
    if error.name not in ("tkinter", "_tkinter"):
        raise
    raise unittest.SkipTest("Tkinter is not installed in this Python runtime") from error


def settings():
    return {
        "base_url": "http://127.0.0.1:8000/v1", "api_key": "", "model": "test-model",
        "timeout": 25, "temperature": 0, "max_tokens": 1200, "min_len": 4,
        "undo_window_sec": 600, "hotkey_enhance": "ctrl+e",
        "hotkey_undo": "ctrl+z", "hotkey_settings": "ctrl+o",
        "system_template": "", "user_template": "", "auto_paste": True,
    }


def form():
    values = settings()
    app = SimpleNamespace(settings=values, default_templates=lambda: ("system", "use {input}"))
    return SimpleNamespace(
        app=app,
        vars={key: SimpleNamespace(get=lambda value=value: str(value))
              for key, value in values.items() if key not in ("system_template", "user_template", "auto_paste")},
        checks={"auto_paste": SimpleNamespace(get=lambda: True)},
        txt_system=SimpleNamespace(get=lambda *args: "system\n"),
        txt_user=SimpleNamespace(get=lambda *args: "use {input}\n"),
        geometry=lambda: "800x600+120+200",
    )


class ValidationTests(unittest.TestCase):
    def test_zero_temperature_is_preserved(self):
        self.assertEqual(parse_number("temperature", "0"), 0)
        self.assertEqual(SettingsWindow.collect(form())["temperature"], 0)

    def test_nonfinite_and_out_of_range_numbers_are_rejected(self):
        for value in ("nan", "inf", "-inf", "", "abc", "0", "301"):
            with self.subTest(value=value), self.assertRaises(SettingsError):
                parse_number("timeout", value)

    def test_integer_settings_are_not_silently_truncated(self):
        with self.assertRaises(SettingsError):
            parse_number("max_tokens", "1200.5")
        self.assertEqual(parse_number("max_tokens", "1200.0"), 1200)

    def test_invalid_form_does_not_fall_back_to_saved_value(self):
        window = form()
        window.vars["timeout"] = SimpleNamespace(get=lambda: "invalid")
        with self.assertRaises(SettingsError) as caught:
            SettingsWindow.collect(window)
        self.assertEqual(caught.exception.field, "timeout")
        self.assertEqual(window.app.settings["timeout"], 25)

    def test_collect_preserves_geometry_and_default_templates(self):
        collected = SettingsWindow.collect(form())
        self.assertEqual(collected["_geometry"], "800x600+120+200")
        self.assertEqual(collected["system_template"], "")
        self.assertEqual(collected["user_template"], "")

    def test_custom_user_template_requires_input(self):
        window = form()
        window.txt_user = SimpleNamespace(get=lambda *args: "missing placeholder")
        with self.assertRaises(SettingsError) as caught:
            SettingsWindow.collect(window)
        self.assertEqual(caught.exception.field, "user_template")

    def test_connection_url_and_model_validation(self):
        for url in ("api.example.com", "file:///tmp/api", "https://user:pass@example.com", "https://example.com/#fragment", "http://example.com:bad", "http://exa mple.com"):
            with self.subTest(url=url), self.assertRaises(SettingsError):
                validate_connection({"base_url": url, "model": "test"})
        validate_connection({"base_url": "http://localhost:8000/v1", "model": "test"})
        validate_connection({"base_url": "https://example.com/v1?api-version=2024", "model": "test"})
        with self.assertRaises(SettingsError):
            validate_connection({"base_url": "https://example.com", "model": " "})

    def test_equivalent_hotkeys_are_duplicates(self):
        window = form()
        window.vars["hotkey_undo"] = SimpleNamespace(get=lambda: "CONTROL+E")
        parser = Mock(return_value=(2, 69))
        with patch.dict(sys.modules, {"win_core": SimpleNamespace(parse_hotkey=parser)}):
            self.assertTrue(SettingsWindow._invalid_hotkey(window))
        self.assertEqual(parser.call_count, 2)

    def test_empty_hotkeys_do_not_conflict(self):
        window = form()
        for key in ("hotkey_enhance", "hotkey_undo", "hotkey_settings"):
            window.vars[key] = SimpleNamespace(get=lambda: "")
        parser = Mock()
        with patch.dict(sys.modules, {"win_core": SimpleNamespace(parse_hotkey=parser)}):
            self.assertEqual(SettingsWindow._invalid_hotkey(window), "")
        parser.assert_not_called()


class InteractionTests(unittest.TestCase):
    def test_connection_exception_reenables_the_test_button(self):
        window = SimpleNamespace(
            _test_running=False, _test_results=queue.Queue(), collect=settings,
            btn_test=Mock(), lbl_test=Mock(), after=Mock(), _poll_test=Mock(),
        )
        class ImmediateThread:
            def __init__(self, *, target, **kwargs):
                self.target = target

            def start(self):
                self.target()

        with patch("ui.threading.Thread", ImmediateThread), patch.dict(
                sys.modules, {"core": SimpleNamespace(enhance=Mock(side_effect=RuntimeError("failed")))}):
            SettingsWindow.test_connection(window)
        self.assertTrue(window._test_running)
        SettingsWindow._poll_test(window)
        self.assertFalse(window._test_running)
        window.btn_test.configure.assert_called_with(state="normal")
        self.assertIn("RuntimeError", window.lbl_test.configure.call_args.kwargs["text"])

    def test_cancel_does_not_hide_or_discard(self):
        window = SimpleNamespace(_update_dirty=Mock(), _dirty=True, save=Mock(),
                                 _load_values=Mock(), _mark_saved=Mock(), set_status=Mock())
        with patch("ui.messagebox.askyesnocancel", return_value=None):
            self.assertFalse(SettingsWindow.confirm_discard_changes(window))
        window._load_values.assert_not_called()
        window.save.assert_not_called()

    def test_clipboard_failure_is_reported_as_failure(self):
        window = SimpleNamespace(_current=lambda: {"before": "draft"},
                                 app=SimpleNamespace(copy_to_clipboard=Mock(return_value=False)),
                                 set_status=Mock())
        SettingsWindow._copy(window, "before")
        from ui import ERR_C
        self.assertEqual(window.set_status.call_args.args[1], ERR_C)

    def test_key_release_allows_replacing_a_chord(self):
        entry = SimpleNamespace(_mods=["ctrl", "alt"], _fresh=False)
        HotkeyEntry._on_release(entry, SimpleNamespace(keysym="Alt_L"))
        self.assertEqual(entry._mods, ["ctrl"])
        HotkeyEntry._on_release(entry, SimpleNamespace(keysym="Control_L"))
        self.assertTrue(entry._fresh)

    def test_shifted_punctuation_uses_the_physical_key(self):
        self.assertEqual(keysym_to_key("exclam"), "1")
        self.assertEqual(keysym_to_key("plus"), "=")
        self.assertEqual(keysym_to_key("E"), "e")


if __name__ == "__main__":
    unittest.main()
