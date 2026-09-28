"""Small persisted app settings used by runtime helpers."""

import json
import uuid
from pathlib import Path

from app.utils.runtime_paths import get_config_dir, get_runtime_root, is_packaged
from app.utils.storage import atomic_write_text

APP_SETTINGS_PATH = get_config_dir() / "app_settings.json"
DEFAULT_OUTPUT_DIR = get_runtime_root() / "output"
DEFAULT_GENERATION_SETTINGS = {
    "scene": "自动识别场景",
    "article_mode": "draft_article",
    "target_words": 1200,
    "temperature": 0.7,
    "export_format": "Word",
}
ALLOWED_GENERATION_SCENES = {
    "自动识别场景",
    "民主生活会对照检查",
    "年度工作总结",
    "述职报告",
    "专题汇报",
    "动员讲话",
    "调研报告",
    "党课讲稿",
    "形势分析",
    "工作计划",
    "整改方案",
    "情况通报",
    "经验交流",
    "先进事迹",
    "政治表态",
    "组织生活会",
    "意见方案",
}
ALLOWED_ARTICLE_MODES = {"draft_article", "full_article", "document_mode"}
ALLOWED_EXPORT_FORMATS = {"Word", "TXT"}


def _absolute_output_path(value: str | Path) -> Path:
    path = Path(str(value).strip()).expanduser()
    if not path.is_absolute():
        path = get_runtime_root() / path
    return path.resolve()


def _read_settings() -> dict:
    if not APP_SETTINGS_PATH.exists():
        return {}
    try:
        data = json.loads(APP_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_settings(settings: dict) -> None:
    atomic_write_text(
        APP_SETTINGS_PATH,
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _ensure_writable_dir(path: Path) -> Path:
    if is_packaged() and not path.resolve().is_relative_to((get_runtime_root() / "output").resolve()):
        raise ValueError("安装版导出目录必须位于用户运行数据的 output 目录内")
    try:
        if path.exists() and not path.is_dir():
            raise ValueError("输出目录不是有效目录")
        path.mkdir(parents=True, exist_ok=True)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"输出目录创建失败：{exc}") from exc

    probe = path / f".output_dir_write_test_{uuid.uuid4().hex}.tmp"
    try:
        probe.write_text("ok", encoding="utf-8")
    except Exception as exc:
        raise ValueError(f"输出目录不可写：{exc}") from exc
    finally:
        try:
            if probe.exists():
                probe.unlink()
        except Exception:
            pass
    return path


def validate_output_dir(output_dir: str | Path) -> Path:
    if not str(output_dir or "").strip():
        raise ValueError("输出目录不能为空")
    return _ensure_writable_dir(_absolute_output_path(output_dir))


def get_output_dir() -> Path:
    settings = _read_settings()
    saved_output = settings.get("output_dir")
    if isinstance(saved_output, str) and saved_output.strip():
        try:
            return _ensure_writable_dir(_absolute_output_path(saved_output))
        except ValueError:
            pass
    return _ensure_writable_dir(DEFAULT_OUTPUT_DIR.resolve())


def save_output_dir(output_dir: str | Path) -> Path:
    path = validate_output_dir(output_dir)
    settings = _read_settings()
    settings["output_dir"] = str(path)
    _write_settings(settings)
    return path


def _coerce_generation_defaults(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("参数配置不能为空")

    scene = str(payload.get("scene", "")).strip() or DEFAULT_GENERATION_SETTINGS["scene"]
    if scene not in ALLOWED_GENERATION_SCENES:
        raise ValueError("未知场景，无法保存")

    article_mode = str(payload.get("article_mode", "")).strip()
    if article_mode not in ALLOWED_ARTICLE_MODES:
        raise ValueError("未知生成模式，无法保存")

    target_words_value = payload.get("target_words")
    if isinstance(target_words_value, bool):
        raise ValueError("目标字数必须是整数")
    try:
        target_words = int(target_words_value)
    except (TypeError, ValueError):
        raise ValueError("目标字数必须是整数")
    if str(target_words_value).strip() == "" or target_words < 300 or target_words > 10000:
        raise ValueError("目标字数范围必须是 300-10000")

    temperature_value = payload.get("temperature")
    if isinstance(temperature_value, bool) or str(temperature_value).strip() == "":
        raise ValueError("温度必须是数字")
    try:
        temperature = float(temperature_value)
    except (TypeError, ValueError):
        raise ValueError("温度必须是数字")
    if temperature < 0 or temperature > 1:
        raise ValueError("温度范围必须是 0-1")

    export_format = str(payload.get("export_format", "")).strip()
    if export_format not in ALLOWED_EXPORT_FORMATS:
        raise ValueError("导出格式只允许 Word 或 TXT")

    return {
        "scene": scene,
        "article_mode": article_mode,
        "target_words": target_words,
        "temperature": temperature,
        "export_format": export_format,
    }


def get_generation_defaults() -> dict:
    settings = _read_settings()
    saved_defaults = settings.get("generation_defaults")
    if not isinstance(saved_defaults, dict):
        return dict(DEFAULT_GENERATION_SETTINGS)
    try:
        return _coerce_generation_defaults(saved_defaults)
    except ValueError:
        return dict(DEFAULT_GENERATION_SETTINGS)


def save_generation_defaults(payload: dict) -> dict:
    generation_defaults = _coerce_generation_defaults(payload)
    settings = _read_settings()
    settings["generation_defaults"] = generation_defaults
    _write_settings(settings)
    return generation_defaults
