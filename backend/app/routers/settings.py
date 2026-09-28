from datetime import datetime

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from app.utils.secret_store import SecretStoreError

from app.config import (
    EmbeddingConfig,
    ProviderConfig,
    get_embedding_config,
    get_settings,
    update_embedding_config,
    update_provider_config,
)
from app.models.schemas import (
    EmbeddingRequest,
    EmbeddingResponse,
    MessageResponse,
    ModelPreset,
    ModelPresetListResponse,
    SettingsRequest,
    SettingsResponse,
    TestConnectionResponse,
)
from app.services.library.embedder import test_embedding_connection as run_embedding_connection_test
from app.services.llm.presets import list_model_presets
from app.services.llm.connection_test import run_llm_connection_test, _redact_secret

class SettingsRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_settings_request(request):
            try:
                return await handler(request)
            except SecretStoreError:
                raise HTTPException(status_code=503, detail={
                    "error_code": "SECRET_STORE_UNAVAILABLE", "message": str(SecretStoreError()),
                }) from None
            except RequestValidationError:
                # FastAPI's default validation response includes submitted input.
                raise HTTPException(status_code=422, detail={
                    "error_code": "SETTINGS_INVALID", "message": "模型配置参数无效，请检查后重试。",
                }) from None
        return safe_settings_request


router = APIRouter(route_class=SettingsRoute)


def _normalize_base_url(value: str) -> str:
    return (value or "").strip().rstrip("/")


@router.get("", response_model=SettingsResponse)
def get_current_settings(provider_key: str = ""):
    settings = get_settings()
    key = provider_key or settings.current_provider or "deepseek"
    cfg = settings.providers.get(key)
    if cfg is None:
        return SettingsResponse(provider_key=key)
    return SettingsResponse(
        provider_key=key,
        provider=cfg.provider,
        base_url=cfg.base_url,
        model=cfg.model,
        embedding_model=cfg.embedding_model,
        endpoint_path=cfg.endpoint_path,
        temperature=cfg.temperature,
        timeout=cfg.timeout,
        api_key_configured=bool(cfg.api_key),
    )


@router.post("", response_model=MessageResponse)
def save_current_settings(payload: SettingsRequest):
    provider_key = payload.provider_key or "deepseek"
    config = ProviderConfig(
        provider=payload.provider,
        base_url=payload.base_url,
        endpoint_path=payload.endpoint_path,
        api_key=payload.api_key,
        model=payload.model,
        embedding_model=payload.embedding_model,
        temperature=payload.temperature,
        timeout=payload.timeout,
    )
    update_provider_config(provider_key, config)
    return MessageResponse(success=True, message=f"配置已保存[{provider_key}]")


@router.post("/test", response_model=TestConnectionResponse)
def test_llm_connection(payload: SettingsRequest):
    timestamp = datetime.now().isoformat()
    provider_key = payload.provider_key or "deepseek"

    settings = get_settings()
    saved = settings.providers.get(provider_key)
    request_api_key = (payload.api_key or "").strip()
    request_base_url = (payload.base_url or "").strip()
    saved_base_url = (saved.base_url if saved else "").strip()

    api_key = request_api_key
    base_url = request_base_url or saved_base_url
    if not api_key:
        if saved and saved.api_key:
            if request_base_url and _normalize_base_url(request_base_url) != _normalize_base_url(saved_base_url):
                return TestConnectionResponse(
                    success=False,
                    message="Custom base_url requires an api_key for this test; saved api_key cannot be sent to a custom base_url.",
                    response="",
                    timestamp=timestamp,
                )
            api_key = saved.api_key
            base_url = saved_base_url
        else:
            return TestConnectionResponse(
                success=False,
                message=f"未配置 API Key（{provider_key}）。请先填写后再测试。",
                response="",
                timestamp=timestamp,
            )

    config = ProviderConfig(
        provider=payload.provider,
        base_url=base_url,
        endpoint_path=payload.endpoint_path,
        api_key=api_key,
        model=payload.model,
        temperature=payload.temperature,
        timeout=payload.timeout,
    )
    try:
        result = run_llm_connection_test(config)
    except Exception:
        result = {"success": False, "message": "模型连接测试失败，请检查配置后重试。", "response": ""}
    result = {key: _redact_secret(value, api_key) if isinstance(value, str) else value
              for key, value in result.items()}
    result["timestamp"] = timestamp
    return TestConnectionResponse(**result)


@router.get("/writing-reference")
def get_writing_reference():
    from app.services.writing_reference_settings import reference_status
    return reference_status()


@router.post("/writing-reference")
async def upload_writing_reference(file: UploadFile):
    from app.models.full_article import FullArticleContractError
    from app.services.writing_reference_settings import MAX_REFERENCE_BYTES, configure_reference
    try:
        raw = await file.read(MAX_REFERENCE_BYTES + 1)
        return configure_reference(raw)
    except FullArticleContractError as exc:
        raise HTTPException(status_code=422, detail={
            "error_code": exc.code, "message": "Writing Reference JSON 校验失败，请选择有效文件。",
        }) from None
    finally:
        await file.close()


@router.get("/model-presets", response_model=ModelPresetListResponse)
def get_model_presets():
    presets = list_model_presets()
    return ModelPresetListResponse(items=[ModelPreset(**preset) for preset in presets])


@router.get("/embedding", response_model=EmbeddingResponse)
def get_embedding_settings():
    cfg = get_embedding_config()
    return EmbeddingResponse(
        provider=cfg.provider,
        base_url=cfg.base_url,
        model=cfg.model,
        endpoint_path=cfg.endpoint_path,
        timeout=cfg.timeout,
        api_key_configured=bool(cfg.api_key),
    )


@router.post("/embedding", response_model=MessageResponse)
def save_embedding_settings(payload: EmbeddingRequest):
    config = EmbeddingConfig(
        provider=payload.provider,
        base_url=payload.base_url,
        endpoint_path=payload.endpoint_path,
        api_key=payload.api_key,
        model=payload.model,
        timeout=payload.timeout,
    )
    update_embedding_config(config)
    return MessageResponse(success=True, message="向量模型配置已保存")


@router.post("/embedding/test", response_model=TestConnectionResponse)
def test_embedding_connection(payload: EmbeddingRequest):
    timestamp = datetime.now().isoformat()

    saved = get_embedding_config()
    api_key = payload.api_key or saved.api_key

    config = EmbeddingConfig(
        provider=payload.provider or saved.provider,
        base_url=payload.base_url or saved.base_url,
        endpoint_path=payload.endpoint_path or saved.endpoint_path,
        api_key=api_key,
        model=payload.model or saved.model,
        timeout=payload.timeout or saved.timeout,
    )
    try:
        result = run_embedding_connection_test(config)
    except Exception:
        result = {"success": False, "message": "向量模型连接测试失败，请检查配置后重试。", "response": ""}
    result = {key: _redact_secret(value, api_key) if isinstance(value, str) else value
              for key, value in result.items()}
    result["timestamp"] = timestamp
    return TestConnectionResponse(**result)
