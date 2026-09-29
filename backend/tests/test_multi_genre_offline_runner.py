"""Exercise the real runner network policy without sending external traffic."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from run_multi_genre_offline import _audit_network


EVENTS = ("socket.connect", "socket.sendto", "socket.getaddrinfo",
          "socket.gethostbyname", "socket.gethostbyaddr")
ALLOWED = ("127.0.0.1", "127.1.2.3", "::1", "localhost")
BLOCKED = (
    "8.8.8.8", "1.1.1.1", "192.168.1.1", "10.0.0.1", "example.com",
    "172.16.0.1", "172.31.255.255", "0.0.0.0", "::", "2001:4860:4860::8888",
    "localhost.example.com", "example.localhost", "127.0.0.1.example.com",
    "localhost.", " localhost", "127.1", "2130706433", "0x7f000001",
    "::ffff:8.8.8.8", None, 2130706433, b"\xff",
)


def counts():
    return dict(loopback_network=0, external_network=0, asyncio_socketpair_ipc=0)


def event_args(event, host):
    if event in {"socket.connect", "socket.sendto"}:
        return (None, (host, 43210))
    if event == "socket.getaddrinfo":
        return (host, 43210, 0, 1, 0)
    return (host,)


@pytest.mark.parametrize("event", EVENTS)
@pytest.mark.parametrize("host", ALLOWED)
def test_explicit_loopback_targets_are_allowed_and_counted_separately(event, host):
    observed = counts()
    _audit_network(event, event_args(event, host), observed, "RUNNER_GUARD_TEST")
    assert observed == dict(loopback_network=1, external_network=0, asyncio_socketpair_ipc=0)


@pytest.mark.parametrize("event", EVENTS)
@pytest.mark.parametrize("host", BLOCKED)
def test_non_loopback_or_ambiguous_targets_are_rejected_before_io(event, host):
    observed = counts()
    with pytest.raises(AssertionError, match="RUNNER_GUARD_TEST forbids network: " + event):
        _audit_network(event, event_args(event, host), observed, "RUNNER_GUARD_TEST")
    assert observed == dict(loopback_network=0, external_network=1, asyncio_socketpair_ipc=0)


@pytest.mark.parametrize("event", EVENTS)
@pytest.mark.parametrize("host", (b"127.0.0.1", b"::1", b"localhost"))
def test_ascii_byte_targets_obey_the_same_loopback_policy(event, host):
    observed = counts()
    _audit_network(event, event_args(event, host), observed, "RUNNER_GUARD_TEST")
    assert observed["loopback_network"] == 1 and observed["external_network"] == 0


@pytest.mark.parametrize("event", ("socket.connect", "socket.sendto"))
def test_ipv6_address_tuple_is_classified_by_host_not_flowinfo_or_scope(event):
    observed = counts()
    _audit_network(event, (None, ("::1", 43210, 0, 0)), observed, "RUNNER_GUARD_TEST")
    with pytest.raises(AssertionError):
        _audit_network(event, (None, ("fe80::1", 43210, 0, 1)), observed, "RUNNER_GUARD_TEST")
    assert observed == dict(loopback_network=1, external_network=1, asyncio_socketpair_ipc=0)


@pytest.mark.parametrize("event", EVENTS)
@pytest.mark.parametrize("args", ((), (None,), (None, "localhost"), (None, ())))
def test_malformed_network_event_cannot_fail_open(event, args):
    observed = counts()
    with pytest.raises(AssertionError):
        _audit_network(event, args, observed, "RUNNER_GUARD_TEST")
    assert observed["external_network"] == 1 and observed["loopback_network"] == 0


def test_installed_audit_hook_enforces_all_five_events_and_preserves_socketpair():
    # sys.audit emits synthetic events, not network requests. Install the real
    # policy in a separate interpreter because Python audit hooks are permanent.
    # No hook is replaced with a no-op; forbidden targets raise in that hook.
    script = r'''
import json
import socket
import sys
from run_multi_genre_offline import _audit_network

counts = dict(loopback_network=0, external_network=0, asyncio_socketpair_ipc=0)
def hook(event, args):
    _audit_network(event, args, counts, "INSTALLED_GUARD_TEST")
sys.addaudithook(hook)
events = ("socket.connect", "socket.sendto", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr")
def args_for(event, host):
    if event in {"socket.connect", "socket.sendto"}:
        return (None, (host, 43210))
    return (host, 43210, 0, 1, 0) if event == "socket.getaddrinfo" else (host,)
for event in events:
    for host in ("127.0.0.1", "127.1.2.3", "::1", "localhost"):
        sys.audit(event, *args_for(event, host))
    for host in ("8.8.8.8", "1.1.1.1", "192.168.1.1", "10.0.0.1", "example.com"):
        try:
            sys.audit(event, *args_for(event, host))
        except AssertionError:
            pass
        else:
            raise AssertionError("Non-loopback event escaped installed guard")
assert counts == dict(loopback_network=20, external_network=25, asyncio_socketpair_ipc=0)
left, right = socket.socketpair()
try:
    left.sendall(b"ipc")
    assert right.recv(3) == b"ipc"
finally:
    left.close()
    right.close()
assert counts["external_network"] == 25
if sys.platform == "win32":
    assert counts["asyncio_socketpair_ipc"] == 1
print(json.dumps(counts))
'''
    result = subprocess.run([sys.executable, "-B", "-c", script],
        cwd=Path(__file__).parent, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["loopback_network"] == 20
    assert observed["external_network"] == 25  # Synthetic audit events, no outbound attempts.
    if sys.platform == "win32":
        assert observed["asyncio_socketpair_ipc"] == 1
