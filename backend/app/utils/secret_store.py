"""The sole secret persistence owner: Windows DPAPI, CurrentUser scope.

Only DPAPI ciphertext reaches disk. No application decryption key, machine-wide
flag or optional fallback is used. Identities are authenticated inside the blob.
"""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from app.utils.runtime_paths import get_config_dir


class SecretStoreError(RuntimeError):
    def __init__(self):
        super().__init__("无法安全读取或保存 API Key。原配置已保留，请在当前 Windows 用户下重试。")


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(raw: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise SecretStoreError()
    try:
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        buffer = ctypes.create_string_buffer(raw)
        source = _Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        target = _Blob()
        function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        function.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        try:
            # CRYPTPROTECT_UI_FORBIDDEN only; never CRYPTPROTECT_LOCAL_MACHINE.
            if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
                raise SecretStoreError()
            return ctypes.string_at(target.data, target.size)
        finally:
            if target.data:
                ctypes.memset(target.data, 0, target.size)
                kernel.LocalFree(target.data)
            ctypes.memset(buffer, 0, len(raw))
    except Exception:
        raise SecretStoreError() from None


def _path(identity: str) -> Path:
    if not isinstance(identity, str) or not re.fullmatch(r"(?:embedding/api_key|provider/[^/\x00]+/api_key)", identity):
        raise SecretStoreError()
    return get_config_dir() / "secrets" / (hashlib.sha256(identity.encode()).hexdigest() + ".dpapi")


def _decode(identity: str, ciphertext: bytes) -> str:
    payload = json.loads(_crypt(ciphertext, decrypt=True))
    if (not isinstance(payload, dict) or payload.get("identity") != identity or
            not isinstance(payload.get("value"), str) or not payload["value"]):
        raise SecretStoreError()
    return payload["value"]


def get_secret(identity: str) -> str:
    try:
        path = _path(identity)
        if not path.exists():
            return ""
        return _decode(identity, path.read_bytes())
    except Exception:
        raise SecretStoreError() from None


def set_secret(identity: str, value: str) -> None:
    temporary = None
    try:
        path = _path(identity)
        if not isinstance(value, str) or not value:
            raise SecretStoreError()
        raw = json.dumps({"identity": identity, "value": value}, ensure_ascii=False).encode("utf-8")
        ciphertext = _crypt(raw, decrypt=False)
        if _decode(identity, ciphertext) != value:
            raise SecretStoreError()
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(ciphertext)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    except Exception:
        raise SecretStoreError() from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def delete_secret(identity: str) -> None:
    try:
        _path(identity).unlink(missing_ok=True)
    except Exception:
        raise SecretStoreError() from None
