"""Offline suite runner: isolated runtime, deny real library access and networking.

From backend: python tests/run_w3_8c_2a_offline.py -q tests --basetemp <new-dir>
Uses existing installed dependencies only. Git fetch is outside this test runner.
"""
from pathlib import Path
import ipaddress
import os
import socket
import sys
import tempfile


def main():
    backend = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend))
    parent = backend / "tmp" / "w3-8c-2a"
    parent.mkdir(parents=True, exist_ok=True)
    runtime = Path(tempfile.mkdtemp(prefix="runtime-", dir=parent))
    counts = {"external_network": 0, "real_embedding": 0, "real_model": 0,
              "real_user_data_access": 0, "asyncio_socketpair_ipc": 0}
    protected = [backend / name for name in ("data", "uploads", "output", ".env")]
    protected.append(backend.parent / ".env")

    def audit(event, args):
        if event in {"open", "os.listdir", "os.scandir"} and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            if any(path == root or root in path.parents for root in protected):
                counts["real_user_data_access"] += 1
                raise AssertionError("W3-8C-2A forbids real user data access")
        if (event == "socket.connect"
                and sys._getframe(1).f_code is socket.socketpair.__code__
                and ipaddress.ip_address(args[1][0]).is_loopback):
            counts["asyncio_socketpair_ipc"] += 1
            return
        if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname",
                     "socket.gethostbyaddr", "socket.sendto"}:
            counts["external_network"] += 1
            raise AssertionError("W3-8C-2A forbids network: " + event)

    sys.addaudithook(audit)
    from app.utils import runtime_paths
    runtime_paths.PROJECT_ROOT = runtime
    runtime_paths.BACKEND_DIR = runtime
    from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider
    from app.services.library import embedder

    def forbidden_model(*args, **kwargs):
        counts["real_model"] += 1
        raise AssertionError("W3-8C-2A forbids real model calls")

    def forbidden_embedding(*args, **kwargs):
        counts["real_embedding"] += 1
        raise AssertionError("W3-8C-2A requires fake embedding")

    OpenAICompatibleProvider.generate_text = forbidden_model
    embedder._call_embedding_api = forbidden_embedding
    import pytest
    result = pytest.main(sys.argv[1:])
    print("W3_8C_2A_OFFLINE_COUNTS=" + str(counts))
    return result if not any(value for key, value in counts.items()
                             if key != "asyncio_socketpair_ipc") else 1


if __name__ == "__main__":
    raise SystemExit(main())
