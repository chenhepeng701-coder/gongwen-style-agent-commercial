"""P3-A offline regression runner; optional read-only access to the approved Run.

From backend: python tests/run_p3a_offline.py --real-run -q -s <test files>
Runtime/cache writes are isolated. Existing user library and settings stay closed.
"""
import ipaddress
import os
from pathlib import Path
import socket
import sys
import tempfile


def main():
    backend = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend))
    real_run = "--real-run" in sys.argv
    if real_run:
        sys.argv.remove("--real-run")
    run = backend / "output" / "full-draft" / "2e09c781576545a681a1ee206642618a"
    if real_run:
        os.environ["P3A_REAL_RUN_DIR"] = str(run)
    else:
        os.environ.pop("P3A_REAL_RUN_DIR", None)
    parent = backend / "tmp" / "p3a-semantic-state"
    parent.mkdir(parents=True, exist_ok=True)
    runtime = Path(tempfile.mkdtemp(prefix="runtime-", dir=parent))
    counts = dict(external_network=0, real_embedding=0, real_model=0, forbidden_data_access=0,
                  real_run_writes=0, asyncio_socketpair_ipc=0)
    protected = [backend / name for name in ("data", "uploads", "output", ".env")]
    protected.append(backend.parent / ".env")

    def audit(event, args):
        if event in {"open", "os.listdir", "os.scandir", "os.remove", "os.rename", "os.rmdir", "os.mkdir"}:
            paths = args[:2] if event == "os.rename" else args[:1]
            for value in paths:
                if not isinstance(value, (str, bytes, os.PathLike)):
                    continue
                path = Path(os.fsdecode(value)).resolve()
                if real_run and (path == run or run in path.parents):
                    reading = event in {"os.listdir", "os.scandir"} or (
                        event == "open" and not args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
                    if not reading:
                        counts["real_run_writes"] += 1
                        raise AssertionError("P3-A real Run is read-only")
                elif any(path == p or p in path.parents for p in protected):
                    counts["forbidden_data_access"] += 1
                    raise AssertionError("P3-A forbids unrelated user data access")
        if (event == "socket.connect" and sys._getframe(1).f_code is socket.socketpair.__code__
                and ipaddress.ip_address(args[1][0]).is_loopback):
            counts["asyncio_socketpair_ipc"] += 1
            return
        if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr", "socket.sendto"}:
            counts["external_network"] += 1
            raise AssertionError("P3-A forbids network: " + event)

    sys.addaudithook(audit)
    from app.utils import runtime_paths
    runtime_paths.PROJECT_ROOT = runtime
    runtime_paths.BACKEND_DIR = runtime
    from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider
    from app.services.library import embedder

    def forbidden_model(*args, **kwargs):
        counts["real_model"] += 1
        raise AssertionError("P3-A forbids real model calls")

    def forbidden_embedding(*args, **kwargs):
        counts["real_embedding"] += 1
        raise AssertionError("P3-A forbids real embedding calls")

    OpenAICompatibleProvider.generate_text = forbidden_model
    embedder._call_embedding_api = forbidden_embedding
    import pytest
    result = pytest.main(sys.argv[1:])
    print("P3A_OFFLINE_COUNTS=" + str(counts))
    return result if not any(v for k, v in counts.items() if k != "asyncio_socketpair_ipc") else 1


if __name__ == "__main__":
    raise SystemExit(main())
