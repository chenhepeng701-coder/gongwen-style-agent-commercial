"""Local pytest runner: permit loopback test clients, reject external networking.

Usage from backend: python tests/run_w3_8b_offline.py -q tests --basetemp <new-dir>
Create the parent of <new-dir> first. Do not point pytest at an existing user dir.
"""
import ipaddress
from pathlib import Path
import socket
import sys

import pytest


def main():
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_resolve = socket.getaddrinfo

    def check(host):
        if host == "localhost":
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            pass
        raise AssertionError("W3-8B: external network disabled")

    def connect(stream, address):
        check(address[0])
        return original_connect(stream, address)

    def connect_ex(stream, address):
        check(address[0])
        return original_connect_ex(stream, address)

    def resolve(host, *args, **kwargs):
        check(host)
        return original_resolve(host, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = resolve
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        return pytest.main(sys.argv[1:])
    finally:
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex
        socket.getaddrinfo = original_resolve


if __name__ == "__main__":
    raise SystemExit(main())
