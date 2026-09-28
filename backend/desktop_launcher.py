"""Windows desktop entry point. One process owns the local server and window."""
import ctypes
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import uuid
import urllib.request
import webbrowser

from app.utils.runtime_paths import get_config_dir, get_logs_dir, initialize_runtime_environment, install_packaged_write_guard
from app.utils.storage import atomic_write_text

APP_NAME = "公文写作助手"
VERSION = "0.1.0"


class InstanceLock:
    def __init__(self, path):
        self.path, self.handle = path, None

    def acquire(self):
        import msvcrt
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        try:
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.handle.close()
            self.handle = None
            return False
        if os.fstat(self.handle.fileno()).st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        return True

    def close(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None


class DesktopBoundary:
    """Allow the local browser only; a foreign site cannot submit local settings."""
    def __init__(self, app, authority, identity):
        self.app, self.authority, self.identity = app, authority, identity

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = scope.get("headers", [])
        hosts = [value for name, value in headers if name.lower() == b"host"]
        origins = [value for name, value in headers if name.lower() == b"origin"]
        expected_origin = ("http://" + self.authority).encode("ascii")
        stateful = scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}
        if (hosts != [self.authority.encode("ascii")] or
                (origins != [expected_origin] if stateful or origins else False)):
            from starlette.responses import JSONResponse
            return await JSONResponse({"detail": "本机访问校验失败"}, status_code=403)(scope, receive, send)

        async def identified(message):
            if message["type"] == "http.response.start" and scope["path"] == "/api/health":
                message["headers"] = [*message.get("headers", []), (b"x-gongwen-instance", self.identity.encode())]
            await send(message)
        await self.app(scope, receive, identified)


def healthy(port, identity):
    if type(port) is not int or not 1024 <= port <= 65535 or not isinstance(identity, str):
        return False
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=0.5) as response:
            return (response.headers.get("X-Gongwen-Instance") == identity and
                    json.loads(response.read(4096)).get("status") == "ok")
    except (OSError, ValueError):
        return False


def wait_for_health(port, identity, running, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and running():
        if healthy(port, identity):
            return
        time.sleep(0.15)
    raise RuntimeError("本地服务启动失败。请关闭软件后重试；仍失败时请联系支持人员并提供运行日志。")


def open_existing(state, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            data = json.loads(state.read_text(encoding="utf-8"))
            if healthy(data.get("port"), data.get("identity")):
                webbrowser.open(f'http://127.0.0.1:{data["port"]}/')
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.15)
    raise RuntimeError("软件正在启动或退出，请稍后再次打开。")


def show_error(message):
    ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)


def control_window(url, running):
    """Small native Win32 control window; no browser engine or UI runtime."""
    from ctypes import wintypes as w
    user, kernel = ctypes.windll.user32, ctypes.windll.kernel32
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

    class WindowClass(ctypes.Structure):
        _fields_ = [("style", w.UINT), ("proc", callback_type), ("class_extra", ctypes.c_int),
                    ("window_extra", ctypes.c_int), ("instance", w.HINSTANCE), ("icon", w.HICON),
                    ("cursor", w.HANDLE), ("background", w.HBRUSH), ("menu", w.LPCWSTR), ("name", w.LPCWSTR)]

    kernel.GetModuleHandleW.restype = w.HMODULE
    user.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.DefWindowProcW.restype = ctypes.c_ssize_t
    user.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
    user.CreateWindowExW.restype = w.HWND
    user.DestroyWindow.argtypes = [w.HWND]
    user.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
    user.SetTimer.argtypes = [w.HWND, ctypes.c_size_t, w.UINT, w.LPVOID]
    user.MessageBoxW.argtypes = [w.HWND, w.LPCWSTR, w.LPCWSTR, w.UINT]

    @callback_type
    def procedure(hwnd, message, wp, lp):
        if message == 0x0111:  # WM_COMMAND
            if wp & 0xffff == 1:
                webbrowser.open(url)
                return 0
            if wp & 0xffff == 2:
                message = 0x0010
        if message == 0x0010:  # WM_CLOSE
            if user.MessageBoxW(hwnd, "退出将关闭本地服务。请先保存文稿，并等待正在进行的生成完成。确定退出？", APP_NAME, 0x24) == 6:
                user.DestroyWindow(hwnd)
            return 0
        if message == 0x0113 and not running():  # WM_TIMER
            show_error("本地服务已停止，请重新打开软件。")
            user.DestroyWindow(hwnd)
            return 0
        if message == 0x0002:
            user.PostQuitMessage(0)
            return 0
        return user.DefWindowProcW(hwnd, message, wp, lp)

    instance = kernel.GetModuleHandleW(None)
    cls = WindowClass(0, procedure, 0, 0, instance, None, None, 6, None, "GongwenDesktopControl")
    if not user.RegisterClassW(ctypes.byref(cls)):
        raise RuntimeError("无法创建软件窗口，请重试。")
    hwnd = user.CreateWindowExW(0, cls.name, APP_NAME + " " + VERSION, 0x00CA0000,
                                200, 200, 480, 185, None, None, instance, None)
    if not hwnd:
        raise RuntimeError("无法打开软件窗口，请重试。")
    user.CreateWindowExW(0, "STATIC", "本地服务运行中。请保留此窗口，在浏览器中使用软件。", 0x50000000,
                         20, 20, 430, 35, hwnd, None, instance, None)
    for label, ident, x in (("打开界面", 1, 70), ("退出软件", 2, 260)):
        user.CreateWindowExW(0, "BUTTON", label, 0x50010000, x, 75, 130, 32, hwnd, ident, instance, None)
    user.SetTimer(hwnd, 1, 1000, None)
    user.ShowWindow(hwnd, 1)
    message = w.MSG()
    while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        user.TranslateMessage(ctypes.byref(message))
        user.DispatchMessageW(ctypes.byref(message))


def run():
    initialize_runtime_environment()
    install_packaged_write_guard()
    lock = InstanceLock(get_config_dir() / "desktop.lock")
    state = get_config_dir() / "desktop-instance.json"
    if not lock.acquire():
        open_existing(state)
        return
    server = thread = listener = None
    try:
        # Packaged operation uses saved user configuration, never dev environment.
        from app.utils.runtime_paths import is_packaged
        if is_packaged():
            for key in ("GONGWEN_LLM_API_KEY", "GONGWEN_EMBEDDING_API_KEY", "DASHSCOPE_API_KEY",
                        "WRITING_REFERENCE_PACK_PATH", "QUALITY_TEST_MODE", "ENABLE_WRITING_STYLE_LIBRARY"):
                os.environ.pop(key, None)
            from app.config import migrate_legacy_api_keys
            migrate_legacy_api_keys()
        import uvicorn
        from app.main import app
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        identity = uuid.uuid4().hex
        url = f"http://127.0.0.1:{port}/"
        server = uvicorn.Server(uvicorn.Config(DesktopBoundary(app, f"127.0.0.1:{port}", identity),
            host="127.0.0.1", port=port, loop="asyncio", http="h11", ws="none",
            log_config=None, access_log=False, timeout_graceful_shutdown=5))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        wait_for_health(port, identity, thread.is_alive)
        atomic_write_text(state, json.dumps({"port": port, "identity": identity, "pid": os.getpid()}))
        webbrowser.open(url)
        control_window(url, thread.is_alive)
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=8)
        if listener is not None:
            listener.close()
        state.unlink(missing_ok=True)
        lock.close()


def main():
    try:
        run()
        return 0
    except Exception as exc:
        # Log only the exception type: no credential, response or private path.
        try:
            with (get_logs_dir() / "launcher.log").open("a", encoding="utf-8") as log:
                log.write(time.strftime("%Y-%m-%d %H:%M:%S ") + type(exc).__name__ + "\n")
        except Exception:
            pass
        show_error("软件启动失败。请关闭后重试，或重新安装软件。您的用户数据会保留。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
