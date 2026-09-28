"""Runtime path management for gongwen-style-agent.

Source mode retains backend-relative data; frozen apps use per-user storage.
Application resources are read-only and never double as the runtime root.
"""

import os
import sys
from pathlib import Path

# ── Project Root ──────────────────────────────────────────────────────────
# PROJECT_ROOT is the backend/ directory (parent of app/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT


def is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False))


def get_application_root() -> Path:
    """Read-only resource root, including PyInstaller's onedir contents."""
    return Path(sys._MEIPASS) if is_packaged() else PROJECT_ROOT.parent


def get_runtime_root() -> Path:
    if not is_packaged():
        return PROJECT_ROOT
    local = os.environ.get("LOCALAPPDATA", "")
    if not local or not Path(local).is_absolute():
        raise RuntimeError("无法找到当前用户的本地数据目录。请重新登录 Windows 后再试。")
    root = (Path(local) / "GongwenWritingAssistant").resolve()
    install_roots = (get_application_root().resolve(), Path(sys.executable).resolve().parent)
    if any(root == install or install in root.parents for install in install_roots):
        raise RuntimeError("用户数据目录不能位于程序安装目录内。")
    return root


def _directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_config_dir() -> Path:
    return _directory(get_runtime_root() / "config") if is_packaged() else get_data_dir()


def get_resources_dir() -> Path:
    return _directory((get_runtime_root() if is_packaged() else get_data_dir()) / "resources")


def runtime_write_guard(event, arguments) -> None:
    """Frozen-process write boundary, including legacy explicit export paths."""
    paths = ()
    if event == "open":
        value, mode, flags = arguments
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            paths = (value,)
    elif event in {"os.mkdir", "os.remove", "os.rmdir"}:
        paths = arguments[:1]
    elif event in {"os.rename", "os.link", "os.symlink"}:
        paths = arguments[:2]
    for value in paths:
        if isinstance(value, (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(value)).resolve()
            if not path.is_relative_to(get_runtime_root().resolve()):
                raise PermissionError("安装版只允许写入当前用户的运行数据目录")


def install_packaged_write_guard() -> None:
    if not is_packaged():
        return
    import tempfile
    tempfile.tempdir = str(_directory(get_uploads_dir() / ".tmp"))
    sys.dont_write_bytecode = True
    sys.addaudithook(runtime_write_guard)


# ── Data Directory ────────────────────────────────────────────────────────
def get_data_dir() -> Path:
    """Return PROJECT_ROOT/data, creating it if needed."""
    data_dir = get_runtime_root() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


# ── Logs Directory ────────────────────────────────────────────────────────
def get_logs_dir() -> Path:
    """Return data/logs, creating it if needed."""
    logs_dir = (get_runtime_root() if is_packaged() else get_data_dir()) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


# ── Config Path ───────────────────────────────────────────────────────────
def get_config_path() -> Path:
    """Return path to data/config.yaml."""
    return get_config_dir() / "config.yaml"


# ── Output Directory ──────────────────────────────────────────────────────
def get_output_dir() -> Path:
    """Return PROJECT_ROOT/output, creating it if needed."""
    output_dir = get_runtime_root() / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


# ── Uploads Directory ─────────────────────────────────────────────────────
def get_uploads_dir() -> Path:
    """Return PROJECT_ROOT/uploads, creating it if needed."""
    uploads_dir = get_runtime_root() / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    return uploads_dir


# ── Runtime Initialisation ────────────────────────────────────────────────
def initialize_runtime_environment() -> None:
    """Ensure all runtime directories exist (safe to call multiple times)."""
    get_data_dir()
    get_logs_dir()
    get_output_dir()
    get_uploads_dir()
    get_config_dir()
    get_resources_dir()
    get_library_index_dir()
    get_library_embeddings_dir()
    get_library_cards_dir()
    get_library_styles_dir()


# ── Library Directories ─────────────────────────────────────────────────────
def get_library_dir() -> Path:
    """Return data/library/, creating it if needed."""
    lib = get_data_dir() / "library"
    lib.mkdir(parents=True, exist_ok=True)
    return lib


def get_library_index_dir() -> Path:
    """Return data/library/index/, creating it if needed."""
    idx = get_library_dir() / "index"
    idx.mkdir(parents=True, exist_ok=True)
    return idx


def get_library_embeddings_dir() -> Path:
    """Return data/library/embeddings/, creating it if needed."""
    emb = get_library_dir() / "embeddings"
    emb.mkdir(parents=True, exist_ok=True)
    return emb


def get_library_cards_dir() -> Path:
    """Return data/library/cards/, creating it if needed."""
    cards = get_library_dir() / "cards"
    cards.mkdir(parents=True, exist_ok=True)
    return cards


def get_library_styles_dir() -> Path:
    """Return data/library/styles/, creating it if needed."""
    styles = get_library_dir() / "styles"
    styles.mkdir(parents=True, exist_ok=True)
    return styles


def get_library_material_assets_dir() -> Path:
    """Document-scoped stable assets; created only when ingestion needs them."""
    assets = get_library_dir() / "material_assets"
    assets.mkdir(parents=True, exist_ok=True)
    return assets
