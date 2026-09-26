"""P3-B offline runner. Deny network/models/Embedding and protect user files.

From backend: python tests/run_p3b_offline.py --real-run -q <tests>
All runtime/cache output stays in ignored tmp/p3b-claim-boundary.
"""
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
import tempfile


def main():
    backend = Path(__file__).resolve().parents[1]
    root = backend.parent
    sys.path.insert(0, str(backend))
    real_run = "--real-run" in sys.argv
    if real_run:
        sys.argv.remove("--real-run")
    run = backend / "output/full-draft/2e09c781576545a681a1ee206642618a"
    if real_run:
        os.environ["P3B_REAL_RUN_DIR"] = str(run)
        os.environ["P3A_REAL_RUN_DIR"] = str(run)
    parent = root / "tmp/p3b-claim-boundary"
    parent.mkdir(parents=True, exist_ok=True)
    runtime = Path(tempfile.mkdtemp(prefix="runtime-", dir=parent))
    counts = dict(external_network=0, real_embedding=0, real_model=0,
                  forbidden_data_access=0, real_run_writes=0, asyncio_socketpair_ipc=0)
    protected = [backend / name for name in ("data", "uploads", "output", ".env")]
    protected.append(root / ".env")
    snapshot = parent / "baseline-hashes.json"
    user_files = set()
    if snapshot.exists():
        user_files = {root / p for p in json.loads(snapshot.read_text(encoding="utf-8-sig"))["user_untracked"]}

    def audit(event, args):
        if event in {"open", "os.listdir", "os.scandir", "os.remove", "os.rename", "os.rmdir", "os.mkdir"}:
            paths = args[:2] if event == "os.rename" else args[:1]
            for value in paths:
                if not isinstance(value, (str, bytes, os.PathLike)):
                    continue
                path = Path(os.fsdecode(value)).resolve()
                reading = event in {"os.listdir", "os.scandir"} or (
                    event == "open" and not args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
                if real_run and (path == run or run in path.parents):
                    if not reading:
                        counts["real_run_writes"] += 1
                        raise AssertionError("P3-B historical Run is read-only")
                elif path in user_files or any(path == p or p in path.parents for p in protected):
                    counts["forbidden_data_access"] += 1
                    raise AssertionError("P3-B forbids unrelated user data access")
        # Windows asyncio uses a socketpair solely for local event-loop IPC.
        if event == "socket.connect" and ipaddress.ip_address(args[1][0]).is_loopback:
            frame = sys._getframe(1)
            while frame:
                if frame.f_code is socket.socketpair.__code__:
                    counts["asyncio_socketpair_ipc"] += 1
                    return
                frame = frame.f_back
        if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr", "socket.sendto"}:
            counts["external_network"] += 1
            raise AssertionError("P3-B forbids network: " + event)

    sys.addaudithook(audit)
    from app.utils import runtime_paths
    runtime_paths.PROJECT_ROOT = runtime
    runtime_paths.BACKEND_DIR = runtime
    from app.services.library import embedder
    import httpx

    def forbidden_model(*args, **kwargs):
        counts["real_model"] += 1
        raise AssertionError("P3-B forbids real model calls")

    def forbidden_embedding(*args, **kwargs):
        counts["real_embedding"] += 1
        raise AssertionError("P3-B forbids real embedding calls")

    # Deny the real transport, not generate_text: provider regression tests run
    # the actual serializer/SSE parser over explicit in-memory HTTP doubles.
    # ASGI TestClient and MockTransport never enter HTTPTransport.
    httpx.HTTPTransport.handle_request = forbidden_model
    httpx.AsyncHTTPTransport.handle_async_request = forbidden_model
    embedder._call_embedding_api = forbidden_embedding
    import pytest
    result = pytest.main([*sys.argv[1:], "--basetemp=" + str(runtime / "pytest"),
                          "-o", "cache_dir=" + str(runtime / "cache")])
    print("P3B_OFFLINE_COUNTS=" + json.dumps(counts, sort_keys=True))
    (runtime / "call-counts.json").write_text(json.dumps(counts, indent=2), encoding="utf-8")
    return result if not any(v for k, v in counts.items() if k != "asyncio_socketpair_ipc") else 1


if __name__ == "__main__":
    raise SystemExit(main())
