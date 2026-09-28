"""Release storage boundaries; never touch a developer's actual data."""
import sys
import os
from pathlib import Path

import pytest

from app.utils import runtime_paths as paths


def test_source_layout_is_compatible(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path)
    paths.initialize_runtime_environment()
    assert paths.get_config_path() == tmp_path / "data/config.yaml"
    assert paths.get_logs_dir() == tmp_path / "data/logs"
    assert paths.get_uploads_dir() == tmp_path / "uploads"
    assert paths.get_output_dir() == tmp_path / "output"
    assert paths.get_library_material_assets_dir() == tmp_path / "data/library/material_assets"


@pytest.fixture
def frozen_layout(tmp_path, monkeypatch):
    install = tmp_path / "Program Files" / "App"
    install.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(install / "_internal"), raising=False)
    monkeypatch.setattr(sys, "executable", str(install / "app.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    return install, tmp_path / "Local/GongwenWritingAssistant"


def test_packaged_storage_is_separate_and_initializes(frozen_layout):
    install, runtime = frozen_layout
    paths.initialize_runtime_environment()
    assert paths.get_application_root() == install / "_internal"
    assert paths.get_runtime_root() == runtime
    assert paths.get_config_path() == runtime / "config/config.yaml"
    assert paths.get_logs_dir() == runtime / "logs"
    assert paths.get_resources_dir() == runtime / "resources"
    assert paths.get_library_material_assets_dir() == runtime / "data/library/material_assets"
    assert all((runtime / name).is_dir() for name in ("data", "uploads", "output", "logs", "resources", "config"))
    assert list(install.iterdir()) == []


def test_packaged_root_rejects_install_directory(frozen_layout, monkeypatch):
    install, _ = frozen_layout
    monkeypatch.setenv("LOCALAPPDATA", str(install))
    with pytest.raises(RuntimeError, match="安装目录"):
        paths.initialize_runtime_environment()
    assert list(install.iterdir()) == []


def test_packaged_root_requires_absolute_local_appdata(frozen_layout, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", "relative")
    with pytest.raises(RuntimeError):
        paths.get_runtime_root()


def test_packaged_exports_stay_in_runtime(frozen_layout, monkeypatch):
    from app.services import app_settings
    install, runtime = frozen_layout
    monkeypatch.setattr(app_settings, "APP_SETTINGS_PATH", paths.get_config_dir() / "app_settings.json")
    monkeypatch.setattr(app_settings, "DEFAULT_OUTPUT_DIR", runtime / "output")
    assert app_settings.save_output_dir("output/word") == runtime / "output/word"
    assert app_settings.get_output_dir() == runtime / "output/word"
    with pytest.raises(ValueError, match="output"):
        app_settings.save_output_dir(install / "exports")
    assert list(install.iterdir()) == []


def test_packaged_write_boundary_covers_explicit_paths_and_temporary_uploads(frozen_layout):
    install, runtime = frozen_layout
    paths.runtime_write_guard("open", (install / "resource.py", "r", os.O_RDONLY))
    paths.runtime_write_guard("open", (runtime / "output/report.docx", "w", os.O_WRONLY | os.O_CREAT))
    for event, args in (("open", (install / "report.docx", "w", os.O_WRONLY)),
                        ("os.mkdir", (install / "output", 0o777, -1)),
                        ("os.rename", (runtime / "output/test", install / "leak", -1, -1))):
        with pytest.raises(PermissionError, match="运行数据目录"):
            paths.runtime_write_guard(event, args)
