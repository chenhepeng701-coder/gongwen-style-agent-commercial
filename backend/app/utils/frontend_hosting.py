"""Same-origin production UI; API misses must remain API misses."""
from pathlib import Path

from starlette.exceptions import HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.utils.runtime_paths import get_application_root, is_packaged


class ReactStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        path = path.replace("\\", "/")
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            response = None
        if response is not None and response.status_code != 404:
            return response
        if path.startswith(("assets/", "static/")) or Path(path).suffix:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return await super().get_response("index.html", scope)


def mount_frontend(app, directory: Path | None = None) -> bool:
    directory = directory or get_application_root() / "frontend/dist-commercial"
    if not (directory / "index.html").is_file():
        if is_packaged():
            raise RuntimeError("前端资源缺失，请重新安装公文写作助手。")
        return False
    app.mount("/", ReactStaticFiles(directory=directory, html=True), name="frontend")
    return True
