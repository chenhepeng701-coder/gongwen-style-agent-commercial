# Run only against build_release.py's fresh, manifest-controlled stage.
import json
import os
from pathlib import Path

stage = Path(os.environ['GONGWEN_BUILD_STAGE']).resolve()
inventory = json.loads((stage / 'bundle-inputs.json').read_text(encoding='utf-8'))
datas = [(str(stage / item['source']), item['destination']) for item in inventory]
# 6.2.1 probes all three loader directories even on x64. License notices keep
# the unused directories present without shipping x86/ARM64 executable code.
for arch in ('win-x86', 'win-arm64'):
    datas.append((str(stage / 'licenses/Microsoft-WebView2-SDK-LICENSE.txt'),
                  'webview/lib/runtimes/' + arch + '/native'))
a = Analysis([str(stage / 'backend/desktop_launcher.py')],
    pathex=[str(stage / 'backend'), os.environ['GONGWEN_BUILD_SITE_PACKAGES']],
    binaries=[], datas=datas,
    hiddenimports=['app.main', 'uvicorn.logging', 'uvicorn.loops.asyncio',
                   'uvicorn.protocols.http.h11_impl', 'uvicorn.lifespan.on', 'anyio._backends._asyncio',
                   'webview.platforms.edgechromium', 'webview.platforms.winforms', '_cffi_backend', 'clr_loader.netfx'],
    excludes=['pytest', '_pytest', 'anyio.pytest_plugin', 'fastapi.testclient', 'starlette.testclient',
              'dotenv', 'packaging', 'pip', 'setuptools', 'tzdata', 'bz2', 'lzma', '_bz2', '_lzma', 'tkinter', 'IPython', 'numpy', 'pandas', 'matplotlib',
              'uvloop', 'watchfiles', 'websockets', 'httptools',
              'webview.platforms.qt', 'webview.platforms.cef', 'webview.platforms.mshtml',
              'webview.platforms.gtk', 'webview.platforms.cocoa', 'webview.platforms.android',
              'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'qtpy', 'cefpython3', 'cryptography'],
    noarchive=False)
def needed(item):
    name = item[0].replace('\\', '/').lower()
    return not (name.endswith(('.dll', '.jar')) and
                any(part in name for part in ('webbrowserinterop', 'win-x86', 'win-arm64',
                                               '/dlls/x86/', 'pywebview-android')))
a.binaries = [item for item in a.binaries if needed(item)]
a.datas = [item for item in a.datas if needed(item)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='公文写作助手',
    debug=False, strip=False, upx=False, console=False,
    version=str(stage / 'packaging/windows/version_info.txt'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='公文写作助手')
