"""Windows window ownership only; business HTTP and its boundary stay in launcher."""
import ctypes
from ctypes import wintypes
import os
import io
import sys
import threading
import winreg

from app.utils.runtime_paths import get_runtime_root

APP_NAME = "公文写作助手"
APP_ID = "GongwenWritingAssistant.Desktop"
QUIT_MESSAGE = "退出将关闭公文写作助手。请先保存文稿，并等待正在进行的生成完成。确定退出？"
RUNTIME_KEY = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
# Release contract for pywebview 6.2.1 / WebView2 SDK 1.0.3856.49.
# build_release.py reads this literal without importing Windows UI code and
# passes it to NSIS; the installer must never maintain its own default.
WEBVIEW2_MIN_RUNTIME = (146, 0, 3856, 49)


class WindowedOutput(io.TextIOBase):
    """No-console sink without a device/file write or an unbounded memory buffer."""
    def write(self, value):
        return len(value)


def initialize_windowed_output():
    # pywebview's Bottle import otherwise opens NUL when frozen streams are None.
    # NUL is outside Runtime Root and intentionally rejected by the write guard.
    if sys.stdout is None:
        sys.stdout = WindowedOutput()
    if sys.stderr is None:
        sys.stderr = WindowedOutput()


def runtime_version():
    """Only the Evergreen Runtime registration qualifies, never Edge channels."""
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, RUNTIME_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_32KEY) as key:
                version = winreg.QueryValueEx(key, "pv")[0]
            parts = tuple(int(p) for p in version.split("."))
            if len(parts) == 4 and parts >= WEBVIEW2_MIN_RUNTIME:
                return version
        except (OSError, ValueError, TypeError):
            continue
    raise RuntimeError("缺少 WebView2 运行环境，请重新运行公文写作助手安装程序。")


def focus_existing(hwnd, pid):
    """The second process can foreground the verified first process's window."""
    if type(hwnd) is not int or type(pid) is not int or hwnd <= 0 or pid <= 0:
        return False
    user = ctypes.windll.user32
    user.IsWindow.argtypes = [wintypes.HWND]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsIconic.argtypes = [wintypes.HWND]
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    owner = wintypes.DWORD()
    if not user.IsWindow(hwnd):
        return False
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    if owner.value != pid:
        return False
    if user.IsIconic(hwnd):
        user.ShowWindow(hwnd, 9)  # SW_RESTORE; retain maximized windows otherwise.
    return bool(user.SetForegroundWindow(hwnd))


def prepare_webview():
    runtime_version()
    initialize_windowed_output()
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                        r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full") as key:
        if winreg.QueryValueEx(key, "Release")[0] < 394802:
            raise RuntimeError("此 Windows 缺少所需的 .NET Framework 运行环境。")
    # Select Windows' .NET Framework explicitly; no CoreCLR acquisition/config.
    for key in tuple(os.environ):
        if key.startswith(("PYTHONNET_", "PYWEBVIEW_", "WEBVIEW2_")):
            os.environ.pop(key, None)
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        "--disable-background-networking --disable-component-update --disable-sync --no-first-run")
    from pythonnet import load
    load("netfx")
    import webview
    from webview.guilib import initialize

    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    webview.settings["ALLOW_DOWNLOADS"] = True
    renderer = initialize("edgechromium")
    if renderer.renderer != "edgechromium":
        raise RuntimeError("无法初始化 WebView2，请重新运行安装程序。")
    return webview


def run_window(url, running, publish):
    webview = prepare_webview()
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    storage = get_runtime_root() / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    window = webview.create_window(APP_NAME, url, width=1440, height=900,
                                  min_size=(1100, 700), resizable=True,
                                  confirm_close=False)
    stopped = threading.Event()
    failed = threading.Event()

    def closing():
        return failed.is_set() or window.create_confirmation_dialog(APP_NAME, QUIT_MESSAGE)

    def shown():
        try:
            publish(int(window.native.Handle.ToInt64()))
        except Exception:
            failed.set()
            window.destroy()

    def monitor():
        while not stopped.wait(0.5):
            if not running():
                failed.set()
                window.destroy()
                return

    window.events.closing += closing
    window.events.shown += shown
    window.events.closed += stopped.set
    watchdog = threading.Thread(target=monitor, daemon=True)
    watchdog.start()
    try:
        webview.start(gui="edgechromium", debug=False, http_server=False,
                      private_mode=False, storage_path=str(storage))
        if failed.is_set():
            raise RuntimeError("本地服务已停止，请重新打开软件。")
    finally:
        stopped.set()
        watchdog.join(timeout=2)
