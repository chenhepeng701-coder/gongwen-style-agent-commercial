"""B2 acceptance against the actual packaged EXE, using isolated synthetic data.

No provider/embedding endpoints are called. Migration is inspected at the first
published ready state, before any settings request. Failure uses an unwritable
secret-file destination, not a production test switch or a mocked DPAPI API.
"""
import argparse
import ctypes
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import httpx
import yaml

from dev_smoke import APP_NAME, ROOT, TEST_SECRET, close_normally, file_hashes, wait_for, windows_for


IDENTITIES = ("provider/deepseek/api_key", "embedding/api_key")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=Path, required=True)
    args = parser.parse_args()
    build = args.build.resolve()
    assert build.is_relative_to(ROOT / "tmp")
    package = build / "dist" / APP_NAME
    executable = package / (APP_NAME + ".exe")
    assert executable.is_file()
    sys.path.insert(0, str(ROOT / "backend"))
    from app.utils.secret_store import _decode
    counts = {"external_network_calls": 0, "real_model_calls": 0, "embedding_calls": 0}

    def network_guard(event, arguments):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
            address = arguments[0] if event == "socket.getaddrinfo" else arguments[-1][0]
            try:
                local = ipaddress.ip_address(address).is_loopback
            except ValueError:
                local = False
            if not local:
                counts["external_network_calls"] += 1
                raise AssertionError("External network is forbidden")
    sys.addaudithook(network_guard)
    work = ROOT / "tmp" / ("rc01-startup-" + uuid.uuid4().hex[:8])
    work.mkdir()
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    report = {"result": "BLOCKED", "candidate": str(build), "runtime_parent": str(work),
              "windows_process_elevated": bool(ctypes.windll.shell32.IsUserAnAdmin()),
              "cases": {}, **counts}
    assert not report["windows_process_elevated"]
    package_before = file_hashes(package)
    process = None
    config_lock = None

    def paths(case):
        local = work / case
        runtime = local / "GongwenWritingAssistant"
        directory = runtime / "config"
        directory.mkdir(parents=True, exist_ok=True)
        return local, runtime, directory / "config.yaml", directory / "desktop-instance.json"

    def spawn(local):
        env = {k: v for k, v in os.environ.items() if not any(
            word in k.upper() for word in ("API_KEY", "PROXY", "PYTHON", "VITE", "REFERENCE"))}
        env.update(LOCALAPPDATA=str(local), PATH=str(Path(os.environ["SystemRoot"]) / "System32"), NO_PROXY="*")
        env.update({key: TEST_SECRET for key in (
            "GONGWEN_LLM_API_KEY", "GONGWEN_EMBEDDING_API_KEY", "DASHSCOPE_API_KEY", "EMBEDDING_KEY",
            "WRITING_REFERENCE_PACK_PATH", "QUALITY_TEST_MODE", "ENABLE_WRITING_STYLE_LIBRARY")})
        return subprocess.Popen([str(executable)], cwd=package, env=env, startupinfo=startup)

    def ready(child, state):
        def condition():
            assert child.poll() is None, "Packaged launcher exited before ready"
            return json.loads(state.read_text(encoding="utf-8")) if state.is_file() else None
        return wait_for(condition)

    def legacy(config):
        config.write_text(yaml.safe_dump({"current_provider": "deepseek", "providers": {
            "deepseek": {"api_key": TEST_SECRET, "model": "offline"}}, "embedding": {"api_key": TEST_SECRET}}), encoding="utf-8")
        return config.read_bytes()

    def verify_migrated(config):
        assert TEST_SECRET.encode() not in config.read_bytes()
        for identity in IDENTITIES:
            path = config.parent / "secrets" / (hashlib.sha256(identity.encode()).hexdigest() + ".dpapi")
            raw = path.read_bytes()
            assert TEST_SECRET.encode() not in raw
            assert _decode(identity, raw) == TEST_SECRET

    try:
        # The real launcher performs its own health check before writing state.
        # No GET /api/settings (or any harness HTTP request) precedes inspection.
        local, runtime, config, state = paths("legacy")
        legacy(config)
        process = spawn(local)
        instance = ready(process, state)
        verify_migrated(config)
        report["cases"]["migration_before_http_ready_without_settings_get"] = "PASS"
        with httpx.Client(base_url=f'http://127.0.0.1:{instance["port"]}', trust_env=False) as client:
            assert client.get("/api/health").headers["x-gongwen-instance"] == instance["identity"]
            assert client.get("/api/settings").json()["api_key_configured"]
            assert client.get("/api/settings/embedding").json()["api_key_configured"]
        close_normally(process)
        process = None
        first = {config: config.read_bytes(), **{p: p.read_bytes() for p in (config.parent / "secrets").glob("*.dpapi")}}
        process = spawn(local)
        ready(process, state)
        verify_migrated(config)
        assert all(p.read_bytes() == raw for p, raw in first.items())
        close_normally(process)
        process = None
        report["cases"]["second_startup_idempotent"] = "PASS"

        for case in ("no-legacy", "placeholder", "spaced-placeholder"):
            local, runtime, config, state = paths(case)
            placeholders = None
            if case != "no-legacy":
                placeholders = ("${ DASHSCOPE_API_KEY }", "${ EMBEDDING_KEY }") if case == "spaced-placeholder" else (
                    "${DASHSCOPE_API_KEY}", "${GONGWEN_EMBEDDING_API_KEY}")
                config.write_text(yaml.safe_dump({"providers": {"deepseek": {"api_key": placeholders[0]}},
                    "current_provider": "deepseek", "embedding": {"api_key": placeholders[1]}}), encoding="utf-8")
            before = config.read_bytes() if config.exists() else None
            process = spawn(local)
            instance = ready(process, state)
            assert (config.read_bytes() if config.exists() else None) == before
            assert not (config.parent / "secrets").exists()
            with httpx.Client(base_url=f'http://127.0.0.1:{instance["port"]}', trust_env=False) as client:
                for suffix in ("", "/embedding"):
                    assert not client.get("/api/settings" + suffix).json()["api_key_configured"]
            assert (config.read_bytes() if config.exists() else None) == before
            close_normally(process)
            process = None
            report["cases"][case] = "PASS"
            if placeholders is None:
                continue
            # Exercise the actual Writing Reference save path and a fresh EXE,
            # then explicit Settings keys and blank-key preservation for both identities.
            process = spawn(local)
            instance = ready(process, state)
            url = f'http://127.0.0.1:{instance["port"]}'
            with httpx.Client(base_url=url, headers={"Origin": url}, trust_env=False) as client:
                fixture = (ROOT / "backend/tests/fixtures/writing_reference_pack_synthetic.json").read_bytes()
                response = client.post("/api/settings/writing-reference", files={"file": ("synthetic.json", fixture)})
                assert response.status_code == 200 and response.json() == {"configured": True}
            raw = config.read_bytes()
            saved = yaml.safe_load(raw)
            assert saved["providers"]["deepseek"]["api_key"] == placeholders[0]
            assert saved["embedding"]["api_key"] == placeholders[1]
            assert TEST_SECRET.encode() not in raw
            assert not (config.parent / "secrets").exists()
            close_normally(process)
            process = None
            process = spawn(local)
            instance = ready(process, state)
            assert config.read_bytes() == raw
            assert not (config.parent / "secrets").exists()
            report["cases"][case + "_writing_reference_save_and_restart"] = "PASS"
            url = f'http://127.0.0.1:{instance["port"]}'
            with httpx.Client(base_url=url, headers={"Origin": url}, trust_env=False) as client:
                for suffix in ("", "/embedding"):
                    assert not client.get("/api/settings" + suffix).json()["api_key_configured"]
                for index, suffix in enumerate(("", "/embedding")):
                    payload = {"api_key": TEST_SECRET, "model": "offline"}
                    if not suffix:
                        payload["provider_key"] = "deepseek"
                    assert client.post("/api/settings" + suffix, json=payload).json()["success"]
                    payload["api_key"] = ""
                    assert client.post("/api/settings" + suffix, json=payload).json()["success"]
                    assert client.get("/api/settings" + suffix).json()["api_key_configured"]
                    saved = yaml.safe_load(config.read_bytes())
                    assert "api_key" not in (saved["providers"]["deepseek"] if not suffix else saved["embedding"])
                    if index == 0:
                        assert saved["embedding"]["api_key"] == placeholders[1]
                assert client.post("/api/settings/writing-reference", files={"file": ("synthetic.json", fixture)}).json() == {"configured": True}
            verify_migrated(config)
            close_normally(process)
            process = None
            secured = {config: config.read_bytes(), **{p: p.read_bytes() for p in (config.parent / "secrets").glob("*.dpapi")}}
            process = spawn(local)
            instance = ready(process, state)
            verify_migrated(config)
            assert all(p.read_bytes() == raw for p, raw in secured.items())
            with httpx.Client(base_url=f'http://127.0.0.1:{instance["port"]}', trust_env=False) as client:
                for suffix in ("", "/embedding"):
                    assert client.get("/api/settings" + suffix).json()["api_key_configured"]
            close_normally(process)
            process = None
            report["cases"][case + "_explicit_and_blank_keys_and_restart"] = "PASS"
        report["cases"]["packaged_environment_secrets_ignored"] = "PASS"

        for failure in ("store-write", "config-replace"):
            local, runtime, config, state = paths("failure-" + failure)
            original = legacy(config)
            if failure == "store-write":
                # Encryption and verification run, but the directory cannot be
                # created over a file. No DPAPI behavior is mocked.
                (config.parent / "secrets").write_bytes(b"synthetic write obstruction")
            else:
                kernel = ctypes.windll.kernel32
                kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_uint,
                    ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
                kernel.CreateFileW.restype = ctypes.c_void_p
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                # Read sharing permits YAML load; withholding delete sharing
                # makes the final atomic replacement fail without changing it.
                config_lock = kernel.CreateFileW(str(config), 0x80000000, 1, None, 3, 0x80, None)
                assert config_lock not in (None, ctypes.c_void_p(-1).value)
            process = spawn(local)
            dialog = wait_for(lambda: next((h for h, name in windows_for(process.pid) if name == "#32770"), None))
            assert not state.exists()
            assert config.read_bytes() == original
            listeners = subprocess.check_output(["powershell.exe", "-NoProfile", "-Command",
                f"Get-NetTCPConnection -ErrorAction Stop | Where-Object {{ $_.OwningProcess -eq {process.pid} -and $_.State -eq 'Listen' }} | Select-Object -ExpandProperty LocalAddress"], text=True)
            assert not listeners.strip(), "Failed migration must not open HTTP"
            # main() logs only SecretStoreError and shows a fixed public message.
            log = (runtime / "logs/launcher.log").read_text(encoding="utf-8")
            assert "SecretStoreError" in log and TEST_SECRET not in log
            ctypes.windll.user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
            assert ctypes.windll.user32.PostMessageW(dialog, 0x0010, 0, 0), "Cannot close the owned startup-error dialog"
            assert process.wait(timeout=15) == 1
            process = None
            assert config.read_bytes() == original
            if config_lock is not None:
                kernel.CloseHandle(config_lock)
                config_lock = None
            report["cases"]["fail_closed_preserves_plaintext_" + failure] = "PASS"
        assert package_before == file_hashes(package)
        assert not any(counts.values())
        report["result"] = "PASS"
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        if config_lock is not None:
            ctypes.windll.kernel32.CloseHandle(config_lock)
        report.update(counts)
        (build / "startup-migration-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
