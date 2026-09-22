"""Runtime path management for gongwen-style-agent.

All paths are relative to the project root (backend/ directory).
"""

from pathlib import Path

# ── Project Root ──────────────────────────────────────────────────────────
# PROJECT_ROOT is the backend/ directory (parent of app/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT


# ── Data Directory ────────────────────────────────────────────────────────
def get_data_dir() -> Path:
    """Return PROJECT_ROOT/data, creating it if needed."""
    data_dir = PROJECT_ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


# ── Logs Directory ────────────────────────────────────────────────────────
def get_logs_dir() -> Path:
    """Return data/logs, creating it if needed."""
    logs_dir = get_data_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


# ── Config Path ───────────────────────────────────────────────────────────
def get_config_path() -> Path:
    """Return path to data/config.yaml."""
    return get_data_dir() / "config.yaml"


# ── Output Directory ──────────────────────────────────────────────────────
def get_output_dir() -> Path:
    """Return PROJECT_ROOT/output, creating it if needed."""
    output_dir = PROJECT_ROOT / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


# ── Uploads Directory ─────────────────────────────────────────────────────
def get_uploads_dir() -> Path:
    """Return PROJECT_ROOT/uploads, creating it if needed."""
    uploads_dir = PROJECT_ROOT / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    return uploads_dir


# ── Runtime Initialisation ────────────────────────────────────────────────
def initialize_runtime_environment() -> None:
    """Ensure all runtime directories exist (safe to call multiple times)."""
    get_data_dir()
    get_logs_dir()
    get_output_dir()
    get_uploads_dir()
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
