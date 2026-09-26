"""Run the multi-genre offline suite without real data, models or Embedding.

From backend: python tests/run_multi_genre_offline.py -q tests
Runtime/cache output stays in ignored tmp/multi-genre-foundation. Historical
Run replay is deliberately unavailable in this architecture-only runner.
"""
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
import tempfile


def main(*, workspace_relative="tmp/multi-genre-foundation",
         task_name="MULTI-GENRE-FOUNDATION", count_label="MULTI_GENRE_OFFLINE_COUNTS",
         require_user_manifest=False):
    backend = Path(__file__).resolve().parents[1]
    root = backend.parent
    sys.path.insert(0, str(backend))
    sys.dont_write_bytecode = True
    if "--real-run" in sys.argv:
        raise SystemExit(task_name + " does not authorize real Run access")
    for variable in ("P3A_REAL_RUN_DIR", "P3B_REAL_RUN_DIR"):
        os.environ.pop(variable, None)
    parent = root / workspace_relative
    parent.mkdir(parents=True, exist_ok=True)
    runtime = Path(tempfile.mkdtemp(prefix="runtime-", dir=parent))
    counts = dict(external_network=0, real_embedding=0, real_model=0,
                  forbidden_data_access=0, asyncio_socketpair_ipc=0)
    protected = [backend / name for name in ("data", "uploads", "output", ".env")]
    protected.extend((root / ".env", root / "writing_style_library"))
    user_manifest = parent / "protected-user-files.json"
    if require_user_manifest and not user_manifest.is_file():
        raise SystemExit(task_name + " requires the initial protected user file manifest")
    user_files = set()
    if user_manifest.exists():
        user_files = {root / path for path in json.loads(user_manifest.read_text(encoding="utf-8-sig"))}

    def audit(event, args):
        if event in {"open", "os.listdir", "os.scandir", "os.remove", "os.rename", "os.rmdir", "os.mkdir"}:
            paths = args[:2] if event == "os.rename" else args[:1]
            for value in paths:
                if not isinstance(value, (str, bytes, os.PathLike)):
                    continue
                path = Path(os.fsdecode(value)).resolve()
                if path in user_files or any(path == directory or directory in path.parents for directory in protected):
                    counts["forbidden_data_access"] += 1
                    raise AssertionError(task_name + " forbids real or pre-existing user data access")
        # Windows asyncio needs local socketpair IPC. No other socket is allowed.
        if event == "socket.connect":
            try:
                loopback = ipaddress.ip_address(args[1][0]).is_loopback
            except (ValueError, TypeError, IndexError):
                loopback = False
            if loopback:
                frame = sys._getframe(1)
                while frame:
                    if frame.f_code is socket.socketpair.__code__:
                        counts["asyncio_socketpair_ipc"] += 1
                        return
                    frame = frame.f_back
        if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr", "socket.sendto"}:
            counts["external_network"] += 1
            raise AssertionError(task_name + " forbids network: " + event)

    sys.addaudithook(audit)
    from app.utils import runtime_paths
    runtime_paths.PROJECT_ROOT = runtime
    runtime_paths.BACKEND_DIR = runtime
    from app.services.library import embedder
    import httpx

    def forbidden_model(*args, **kwargs):
        counts["real_model"] += 1
        raise AssertionError(task_name + " forbids real model calls")

    def forbidden_embedding(*args, **kwargs):
        counts["real_embedding"] += 1
        raise AssertionError(task_name + " forbids real embedding calls")

    # Preserve real Provider serializers tested over explicit in-memory doubles;
    # block actual HTTP transport. TestClient and MockTransport remain offline.
    httpx.HTTPTransport.handle_request = forbidden_model
    httpx.AsyncHTTPTransport.handle_async_request = forbidden_model
    embedder._call_embedding_api = forbidden_embedding
    import pytest
    result = pytest.main([*sys.argv[1:], "--basetemp=" + str(runtime / "pytest"),
                          "-o", "cache_dir=" + str(runtime / "cache")])
    print(count_label + "=" + json.dumps(counts, sort_keys=True))
    (runtime / "call-counts.json").write_text(json.dumps(counts, indent=2), encoding="utf-8")
    return result if not any(value for key, value in counts.items() if key != "asyncio_socketpair_ipc") else 1


if __name__ == "__main__":
    raise SystemExit(main())
