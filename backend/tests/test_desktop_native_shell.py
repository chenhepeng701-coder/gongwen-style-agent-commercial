"""Desktop contracts; real loopback Uvicorn lifecycle, isolated synthetic data."""
import ast
from contextlib import nullcontext
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

from fastapi import FastAPI
import pytest

import desktop_launcher as launcher
import desktop_shell as shell


def test_no_browser_or_legacy_control_window_in_application_sources():
    for module in (launcher, shell):
        text = Path(module.__file__).read_text(encoding="utf-8")
        assert "webbrowser" not in text
        assert "control_window" not in text
        assert "GongwenDesktopControl" not in text


@pytest.fixture
def local_app(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "initialize_runtime_environment", lambda: None)
    monkeypatch.setattr(launcher, "install_packaged_write_guard", lambda: None)
    monkeypatch.setattr(launcher, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    app = FastAPI()
    app.get("/api/health")(lambda: {"status": "ok"})
    monkeypatch.setitem(sys.modules, "app.main", SimpleNamespace(app=app))
    return tmp_path


def test_health_before_window_state_and_real_server_shutdown(local_app, monkeypatch):
    state = local_app / "desktop-instance.json"
    state.write_text('{"port": 1, "identity": "stale", "hwnd": 1, "pid": 1}')
    seen = {}
    def window(url, running, publish):
        assert running()
        port = int(url.split(":")[-1].rstrip("/"))
        publish(123)
        data = json.loads(state.read_text(encoding="utf-8"))
        assert data["hwnd"] == 123 and data["port"] == port
        assert data["identity"] != "stale"
        assert launcher.healthy(port, data["identity"])
        seen.update(port=port, running=running)
    monkeypatch.setattr(launcher, "run_window", window)
    launcher.run()
    assert not seen["running"]()
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", seen["port"])) != 0
    assert not state.exists()
    lock = launcher.InstanceLock(local_app / "desktop.lock")
    assert lock.acquire()
    lock.close()


def test_failed_health_never_creates_window_and_releases_state(local_app, monkeypatch):
    def failed(*args):
        raise RuntimeError("health failure")
    monkeypatch.setattr(launcher, "wait_for_health", failed)
    monkeypatch.setattr(launcher, "run_window", lambda *a: pytest.fail("window before health"))
    with pytest.raises(RuntimeError, match="health failure"):
        launcher.run()
    assert not (local_app / "desktop-instance.json").exists()


def test_second_launch_focuses_existing_without_creating_server(local_app, monkeypatch):
    lock = launcher.InstanceLock(local_app / "desktop.lock")
    assert lock.acquire()
    (local_app / "desktop-instance.json").write_text(json.dumps({"port": 8765, "identity": "ready", "pid": 11, "hwnd": 22}))
    focused = []
    monkeypatch.setattr(launcher, "healthy", lambda *a: True)
    monkeypatch.setattr(launcher, "focus_existing", lambda *a: focused.append(a) or True)
    monkeypatch.setattr(launcher.socket, "socket", lambda *a: pytest.fail("second server"))
    monkeypatch.setattr(launcher, "run_window", lambda *a: pytest.fail("second window"))
    try:
        launcher.run()
    finally:
        lock.close()
    assert focused == [(22, 11)]


def test_unhealthy_state_cannot_focus_other_window(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"port": 8765, "identity": "unverified", "hwnd": 1, "pid": 2}))
    monkeypatch.setattr(launcher, "healthy", lambda *a: False)
    monkeypatch.setattr(launcher, "focus_existing", lambda *a: pytest.fail("unverified identity"))
    with pytest.raises(RuntimeError):
        launcher.open_existing(state, timeout=0.01)


@pytest.mark.parametrize("hwnd,pid", [(None, 1), (True, 1), (1, False), (0, 1), (1, -1), ("10", 1)])
def test_invalid_window_identity_rejected(hwnd, pid):
    assert not shell.focus_existing(hwnd, pid)


class Event:
    def __init__(self):
        self.handlers = []
    def __iadd__(self, callback):
        self.handlers.append(callback)
        return self
    def fire(self):
        return [callback() for callback in self.handlers]


def test_webview_storage_renderer_window_controls_and_close_confirmation(tmp_path, monkeypatch):
    calls = {}
    events = SimpleNamespace(**{n: Event() for n in ("closing", "shown", "closed")})
    window = SimpleNamespace(events=events, native=SimpleNamespace(Handle=SimpleNamespace(ToInt64=lambda: 777)))
    choices = iter([False, True])
    def confirm(title, message):
        assert title == "公文写作助手" and message == shell.QUIT_MESSAGE
        return next(choices)
    window.create_confirmation_dialog = confirm
    def create(title, url, **kwargs):
        calls.update(title=title, url=url, window=kwargs)
        return window
    def start(**kwargs):
        calls["start"] = kwargs
        events.shown.fire()
        assert events.closing.fire() == [False]
        assert events.closing.fire() == [True]
        events.closed.fire()
    webview = SimpleNamespace(create_window=create, start=start)
    monkeypatch.setattr(shell, "prepare_webview", lambda: webview)
    monkeypatch.setattr(shell, "get_runtime_root", lambda: tmp_path)
    published = []
    shell.run_window("http://127.0.0.1:8765/", lambda: True, published.append)
    assert published == [777]
    assert calls["title"] == "公文写作助手"
    assert calls["window"]["width"] == 1440 and calls["window"]["height"] == 900
    assert calls["window"]["min_size"] == (1100, 700) and calls["window"]["resizable"]
    assert calls["start"] == dict(gui="edgechromium", debug=False, http_server=False,
                                 private_mode=False, storage_path=str(tmp_path / "webview"))
    assert (tmp_path / "webview").is_dir()


def test_runtime_absence_has_no_browser_fallback(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(shell.winreg, "OpenKey", missing)
    with pytest.raises(RuntimeError, match="WebView2"):
        shell.prepare_webview()


@pytest.mark.parametrize("version,accepted", [
    ("145.0.9999.99", False),
    ("146.0.3856.48", False),
    ("146.0.3856.49", True),
    ("154.0.4258.37", True),
    ("155.0.4283.18", True),
])
def test_webview2_runtime_release_threshold(monkeypatch, version, accepted):
    monkeypatch.setattr(shell.winreg, "OpenKey", lambda *a, **k: nullcontext(object()))
    monkeypatch.setattr(shell.winreg, "QueryValueEx", lambda *a: (version, shell.winreg.REG_SZ))
    if accepted:
        assert shell.runtime_version() == version
    else:
        # Must fail before importing a renderer, with no browser fallback.
        with pytest.raises(RuntimeError, match="WebView2"):
            shell.prepare_webview()


def test_old_user_runtime_does_not_hide_supported_machine_runtime(monkeypatch):
    def registry(hive, key, reserved, access):
        assert key == shell.RUNTIME_KEY
        assert access & shell.winreg.KEY_WOW64_32KEY
        return nullcontext(hive)
    monkeypatch.setattr(shell.winreg, "OpenKey", registry)
    monkeypatch.setattr(shell.winreg, "QueryValueEx", lambda hive, name: (
        "145.0.9999.99" if hive == shell.winreg.HKEY_CURRENT_USER else "146.0.3856.49",
        shell.winreg.REG_SZ))
    assert shell.runtime_version() == "146.0.3856.49"


def test_consoleless_streams_do_not_open_nul_or_write_outside_runtime(monkeypatch):
    import builtins
    monkeypatch.setattr(sys, 'stdout', None)
    monkeypatch.setattr(sys, 'stderr', None)
    monkeypatch.setattr(builtins, 'open', lambda *a, **k: pytest.fail('Consoleless init must not write a device/file'))
    shell.initialize_windowed_output()
    assert sys.stdout.write('discard') == 7
    assert sys.stderr.write('discard') == 7
    assert not sys.stdout.isatty()
    sys.stderr.flush()


def test_origin_boundary_exact_ast_unchanged_from_required_base():
    import subprocess
    root = Path(__file__).resolve().parents[2]
    original = subprocess.check_output(["git", "show", "7d2df31b3660e656eadf483478785588e5ec53aa:backend/desktop_launcher.py"], cwd=root)
    def boundary(raw):
        return next(n for n in ast.parse(raw).body if isinstance(n, ast.ClassDef) and n.name == "DesktopBoundary")
    assert ast.dump(boundary(original)) == ast.dump(boundary(Path(launcher.__file__).read_bytes()))
