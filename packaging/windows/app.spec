# Run only against build_release.py's fresh, manifest-controlled stage.
import json
import os
from pathlib import Path

stage = Path(os.environ['GONGWEN_BUILD_STAGE']).resolve()
inventory = json.loads((stage / 'bundle-inputs.json').read_text(encoding='utf-8'))
datas = [(str(stage / item['source']), item['destination']) for item in inventory]
a = Analysis([str(stage / 'backend/desktop_launcher.py')],
    pathex=[str(stage / 'backend'), os.environ['GONGWEN_BUILD_SITE_PACKAGES']],
    binaries=[], datas=datas,
    hiddenimports=['app.main', 'uvicorn.logging', 'uvicorn.loops.asyncio',
                   'uvicorn.protocols.http.h11_impl', 'uvicorn.lifespan.on', 'anyio._backends._asyncio'],
    excludes=['pytest', '_pytest', 'anyio.pytest_plugin', 'fastapi.testclient', 'starlette.testclient',
              'dotenv', 'packaging', 'pip', 'setuptools', 'tzdata', 'bz2', 'lzma', '_bz2', '_lzma', 'tkinter', 'IPython', 'numpy', 'pandas', 'matplotlib',
              'uvloop', 'watchfiles', 'websockets', 'httptools'],
    noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='公文写作助手',
    debug=False, strip=False, upx=False, console=False,
    version=str(stage / 'packaging/windows/version_info.txt'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='公文写作助手')
