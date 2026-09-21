import httpx

from app.config import ProviderConfig
from app.services.llm.factory import get_llm_provider_from_config


def _redact_secret(message: str, secret: str) -> str:
    if not secret:
        return str(message)
    return str(message).replace(secret, "[REDACTED_API_KEY]")


def run_llm_connection_test(config: ProviderConfig) -> dict[str, str | bool]:
    try:
        provider = get_llm_provider_from_config(config)
        response_text = provider.generate_text("Please reply: connection successful", temperature=config.temperature)
        return {"success": True, "message": "Connection successful", "response": response_text}
    except ValueError as exc:
        return {"success": False, "message": _redact_secret(str(exc), config.api_key), "response": ""}
    except httpx.TimeoutException:
        return {
            "success": False,
            "message": "Network timeout. Please check base_url, network environment, or timeout settings.",
            "response": "",
        }
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code == 401:
            message = "401 authentication failed. Please check whether api_key is correct."
        elif status_code == 404:
            message = "404 endpoint error. Please check base_url or endpoint_path."
        elif status_code == 400:
            message = "Request failed. The model may not exist or the request format may be incompatible."
        else:
            message = f"{status_code} API call failed: {_redact_secret(exc.response.text, config.api_key)}"
        return {"success": False, "message": message, "response": ""}
    except httpx.RequestError as exc:
        message = _redact_secret(str(exc), config.api_key)
        return {"success": False, "message": f"Network request failed: {message}", "response": ""}
