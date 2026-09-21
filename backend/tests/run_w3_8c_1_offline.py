"""Run the existing backend suite with isolated runtime data and no networking.

From backend: python tests/run_w3_8c_1_offline.py -q tests --basetemp <new-dir>
No dependency installation, production config, or user-library writes.
"""
from pathlib import Path
import ipaddress
import socket
import sys
import tempfile


def main():
    backend = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend))
    parent = backend / "tmp" / "w3-8c-1"
    parent.mkdir(parents=True, exist_ok=True)
    runtime = Path(tempfile.mkdtemp(prefix="runtime-", dir=parent))
    counts = {"network": 0, "project_model": 0, "asyncio_socketpair_ipc": 0}

    def audit(event, args):
        # Windows implements Python's socketpair IPC via loopback TCP. Only
        # that exact stdlib caller is allowed, never arbitrary localhost I/O.
        if (event == "socket.connect"
                and sys._getframe(1).f_code is socket.socketpair.__code__
                and ipaddress.ip_address(args[1][0]).is_loopback):
            counts["asyncio_socketpair_ipc"] += 1
            return
        if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname",
                     "socket.gethostbyaddr", "socket.sendto"}:
            counts["network"] += 1
            raise AssertionError("W3-8C-1 forbids network: " + event)

    sys.addaudithook(audit)
    from app.utils import runtime_paths
    runtime_paths.PROJECT_ROOT = runtime
    runtime_paths.BACKEND_DIR = runtime

    from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider

    def forbidden_model(*args, **kwargs):
        counts["project_model"] += 1
        raise AssertionError("W3-8C-1 forbids real project model calls")

    # Existing tests may install their own local response fixtures. No fake
    # successful result is supplied here; the real provider fails immediately.
    OpenAICompatibleProvider.generate_text = forbidden_model
    import pytest
    result = pytest.main(sys.argv[1:])
    print("W3_8C_1_OFFLINE_COUNTS=" + str(counts))
    return result if not (counts["network"] or counts["project_model"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
