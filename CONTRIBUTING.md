# Contributing

Use Python 3.10 or newer. Desktop development requires Windows and Python with
Tcl/Tk; the python.org installer includes a Tcl/Tk option. Runtime code uses the
standard library. Build dependencies are listed separately in `requirements-build.txt`.

## Before a pull request

1. Describe the bug or intended behavior using sample data.
2. Keep the change focused and add a regression test for behavior that could break.
3. Run `python -m unittest discover -s tests -v`.
4. Run `python -m compileall -q core.py cli.py proxy.py mcp_server.py win_core.py hotkey.py png_util.py desktop tools tests`.
5. For Windows startup changes, run `python tools/make_release.py --smoke-source`.
   This uses a temporary profile and a separate instance name.
6. For UI or input automation changes, manually check saving, cancelling, history,
   focus changes during a request, clipboard-only output, and undo in a sample editor.

Do not commit `config.env`, API keys, settings, prompt history, logs, generated
screenshots, executables, or release archives. Inspect `git diff --cached` before
committing. Keep user-facing documentation accurate about supported platforms and
known limitations.

Old diagnostic scripts under `tools/dev/` may interact with the desktop or alter
settings. Review a script before using it and run it with a disposable profile.
CI uses the isolated smoke test instead.

Release instructions are in [docs/RELEASING.md](docs/RELEASING.md).
