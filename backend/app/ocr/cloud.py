"""Opt-in Gemini and Groq OCR adapters; Tesseract remains authoritative."""
from __future__ import annotations
import base64, json, time
from dataclasses import dataclass
from typing import Any
import httpx
from backend.app.core.config import Settings

@dataclass
class CloudResult:
    provider: str
    text: str = ""
    lines: list[dict[str, Any]] | None = None
    confidence: float | None = None
    status: str = "completed"
    error: str | None = None
    phase: str = "request"
    status_code: int | None = None
    reason: str | None = None
    duration_ms: int = 0
    observations: list[dict[str, Any]] | None = None


def _gemini_generation_config() -> dict[str, Any]:
    return {"responseMimeType": "application/json"}

def _json(response: httpx.Response) -> Any:
    try: return response.json()
    except (ValueError, json.JSONDecodeError): return {}

def _category(payload: Any, status: int | None) -> str:
    values = []
    if isinstance(payload, dict):
        error = payload.get("error", payload)
        if isinstance(error, dict):
            for key in ("status", "message", "reason", "code"):
                if isinstance(error.get(key), str): values.append(error[key].lower())
            for item in error.get("details", []) if isinstance(error.get("details"), list) else []:
                if isinstance(item, dict):
                    values.extend(str(item[k]).lower() for k in ("reason", "type", "domain") if isinstance(item.get(k), str))
    text = " ".join(values)
    if status == 429:
        return "quota"
    if status == 404:
        return "model_unavailable"
    rules = (
        ("invalid_credentials", ("unauthenticated", "invalid api key", "authentication", "invalid key")),
        ("quota", ("quota", "rate limit", "resource exhausted", "too many requests")),
        ("api_disabled", ("api not enabled", "api_disabled", "service disabled")),
        ("billing_disabled", ("billing disabled", "billing_not_enabled")),
        ("permission_denied", ("permission denied", "permission_denied", "forbidden")),
        ("policy_restricted", ("organization policy", "policy restricted", "vpc service controls")),
        ("unsupported_operation", ("response_format", "json mode not supported", "structured output not supported")),
        ("unsupported_format", ("unsupported image", "unsupported format")),
        ("image_size", ("image size", "too large", "payload size")),
        ("bad_request", ("invalid request", "bad request", "malformed")),
    )
    for category, needles in rules:
        if any(needle in text for needle in needles): return category
    return "invalid_credentials" if status == 401 else "bad_request" if status in (400, 422) else "provider_error"

def _failure(provider: str, phase: str, error: str, started: float, status: int | None = None) -> CloudResult:
    return CloudResult(provider, status="failed", error=error, phase=phase, status_code=status, reason=error,
                       duration_ms=round((time.perf_counter() - started) * 1000))

def _transport(provider: str, exc: Exception, started: float, phase: str) -> CloudResult:
    if isinstance(exc, httpx.TimeoutException): error = "timeout"
    elif isinstance(exc, httpx.ConnectError): error = "network"
    elif isinstance(exc, httpx.TransportError): error = "tls" if "ssl" in str(exc).lower() else "network"
    else: error = "provider_error"
    return _failure(provider, phase, error, started)

def _prompt(reference: str = "") -> str:
    return ("Read this screenshot. Return JSON only with keys raw_text and rows. Each row must contain phone, "
            "message, timestamp, confidence, uncertain, and box. Use null for anything not clearly visible; "
            "never guess digits or text. Preserve visible text in raw_text. Output exactly one valid JSON object, "
            "with raw_text as a string and rows as an array; do not use markdown fences or explanatory text. "
            "For comparison, preserve each supplied row position in the output as an integer position field; "
            "every returned row must include exactly one supplied position, never omit it, duplicate it, or invent it. "
            "Return only visible corrections; "
            "timestamps are ignored by the correction workflow." + (f" Existing Tesseract rows: {reference}" if reference else ""))

def _parse(provider: str, content: str, started: float, status: int | None,
           required_positions: set[int] | None = None) -> CloudResult:
    try:
        payload = json.loads(content.strip())
        text, rows = payload.get("raw_text", ""), payload.get("rows", [])
        if not isinstance(text, str) or not isinstance(rows, list): raise ValueError
        observations = [row for row in rows if isinstance(row, dict)]
        if required_positions is not None:
            missing = sum(1 for row in observations if not isinstance(row.get("position"), int) or row.get("position") < 1)
            positions = [row.get("position") for row in observations if isinstance(row.get("position"), int) and row.get("position") >= 1]
            duplicates = len(positions) - len(set(positions))
            unknown = sum(1 for position in set(positions) if position not in required_positions)
            if missing or duplicates or unknown:
                reason = f"invalid_positions:missing={missing};unknown={unknown};duplicate={duplicates}"
                return _failure(provider, "response", reason, started, status)
        lines = [{"text": " ".join(str(row.get(k) or "") for k in ("phone", "message", "timestamp")).strip(),
                  "confidence": row.get("confidence"), "box": row.get("box")} for row in observations]
        return CloudResult(provider, text=text, lines=lines, status_code=status,
                           duration_ms=round((time.perf_counter() - started) * 1000), observations=observations)
    except (ValueError, TypeError, AttributeError):
        return _failure(provider, "response", "malformed_output", started, status)

def gemini(data: bytes, credential: str, timeout: float, mime_type: str, model: str) -> CloudResult:
    started = time.perf_counter()
    try:
        response = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            params={"key": credential}, json={"contents": [{"parts": [{"text": _prompt()},
            {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(data).decode()}}]}],
            "generationConfig": _gemini_generation_config()}, timeout=timeout)
        payload = _json(response)
        if response.status_code >= 400: return _failure("gemini", "request", _category(payload, response.status_code), started, response.status_code)
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return _failure("gemini", "response", "empty_candidates", started, response.status_code)
        candidate = candidates[0]
        if not isinstance(candidate, dict):
            return _failure("gemini", "response", "invalid_candidate", started, response.status_code)
        finish = candidate.get("finishReason")
        if finish in {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}:
            return _failure("gemini", "response", f"blocked_{str(finish).lower()}", started, response.status_code)
        parts = candidate.get("content", {}).get("parts", [])
        content = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        if not content.strip():
            return _failure("gemini", "response", "empty_content", started, response.status_code)
        return _parse("gemini", content, started, response.status_code)
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc: return _transport("gemini", exc, started, "response")

def gemini_model_status(credential: str, timeout: float, model: str) -> CloudResult:
    """Credential-only diagnostic; never sends image data or returns provider payloads."""
    started = time.perf_counter()
    try:
        response = httpx.get("https://generativelanguage.googleapis.com/v1beta/models",
                             params={"key": credential, "pageSize": 1000}, timeout=timeout)
        payload = _json(response)
        if response.status_code >= 400:
            return _failure("gemini", "model_listing", _category(payload, response.status_code), started, response.status_code)
        available = payload.get("models", []) if isinstance(payload, dict) else []
        match = next((item for item in available if isinstance(item, dict) and item.get("name") == f"models/{model}"), None)
        methods = match.get("supportedGenerationMethods", []) if isinstance(match, dict) else []
        if not match:
            return _failure("gemini", "model_listing", "model_unavailable", started, response.status_code)
        if "generateContent" not in methods:
            return _failure("gemini", "model_listing", "unsupported_operation", started, response.status_code)
        return CloudResult("gemini", status="completed", phase="model_listing",
                           status_code=response.status_code, duration_ms=round((time.perf_counter() - started) * 1000))
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        return _transport("gemini", exc, started, "model_listing")

def groq(data: bytes, credential: str, timeout: float, mime_type: str, model: str, reference: str = "",
         required_positions: set[int] | None = None) -> CloudResult:
    started = time.perf_counter()
    try:
        response = httpx.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {credential}"}, json={"model": model, "temperature": 0,
            "response_format": {"type": "json_object"}, "messages": [{"role": "user", "content": [{"type": "text", "text": _prompt(reference)},
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64.b64encode(data).decode()}"}}]}]}, timeout=timeout)
        payload = _json(response)
        if response.status_code >= 400: return _failure("groq", "request", _category(payload, response.status_code), started, response.status_code)
        content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        return _parse("groq", content, started, response.status_code, required_positions)
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc: return _transport("groq", exc, started, "response")


def groq_text_cleanup(credential: str, timeout: float, model: str,
                      rows: list[dict[str, Any]]) -> CloudResult:
    """Clean already-extracted text; this path never accepts or sends image bytes."""
    started = time.perf_counter()
    row_ids = [row.get("row_id") for row in rows]
    if any(not isinstance(row_id, str) or not row_id for row_id in row_ids) or len(row_ids) != len(set(row_ids)):
        return _failure("groq", "configuration", "invalid_row_context", started)
    prompt = ("Clean OCR text only. Return exactly one JSON object with key rows, containing exactly one entry "
              "for each supplied row_id. Each entry must contain row_id, phone, message, and uncertain. "
              "Remove obvious OCR garbage prefixes, correct clear character confusion, and normalize whitespace "
              "or punctuation only when meaning is preserved. Preserve names, amounts, dates, languages, phone "
              "digits, and country codes exactly. Never invent missing digits or text. Return the original value "
              "when uncertain. Do not include any fields other than row_id, phone, message, and uncertain.\n"
              f"Rows: {json.dumps(rows, ensure_ascii=False)}")
    try:
        response = httpx.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {credential}"},
            json={"model": model, "temperature": 0, "response_format": {"type": "json_object"},
                  "messages": [{"role": "user", "content": prompt}]}, timeout=timeout)
        payload = _json(response)
        if response.status_code >= 400:
            return _failure("groq", "request", _category(payload, response.status_code), started, response.status_code)
        content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = json.loads(content.strip())
        observations = parsed.get("rows") if isinstance(parsed, dict) else None
        if not isinstance(observations, list) or any(not isinstance(row, dict) for row in observations):
            raise ValueError
        returned_ids = [row.get("row_id") for row in observations]
        expected = set(row_ids)
        if len(returned_ids) != len(set(returned_ids)) or set(returned_ids) != expected or any(not isinstance(row_id, str) for row_id in returned_ids):
            return _failure("groq", "response", "invalid_row_ids", started, response.status_code)
        return CloudResult("groq", status="completed", phase="response", status_code=response.status_code,
                           duration_ms=round((time.perf_counter() - started) * 1000), observations=observations)
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        return _transport("groq", exc, started, "response") if isinstance(exc, httpx.HTTPError) else _failure("groq", "response", "malformed_output", started, locals().get("response", None).status_code if "response" in locals() else None)

def run_cloud(settings: Settings, data: bytes, enabled: list[str], mime_type: str = "image/png") -> list[CloudResult]:
    started = time.perf_counter()
    if not settings.ocr_external_transmission_enabled:
        return [_failure(provider, "policy", "consent_required", started) for provider in enabled]
    credentials = {"gemini": settings.gemini_api_key, "groq": settings.groq_api_key}
    results = []
    for provider in enabled[:max(0, settings.ocr_cloud_request_budget)]:
        if not credentials.get(provider): results.append(_failure(provider, "configuration", "not_configured", started))
        elif provider == "gemini": results.append(gemini(data, credentials[provider], settings.ocr_cloud_timeout_seconds, mime_type, settings.gemini_model))
        elif provider == "groq": results.append(groq(data, credentials[provider], settings.ocr_cloud_timeout_seconds, mime_type, settings.groq_vision_model))
    return results

def effective_cloud_selection(settings: Settings) -> tuple[str, list[str]]:
    configured = {"gemini": bool(settings.gemini_api_key), "groq": bool(settings.groq_api_key)}
    if settings.ocr_processing_mode == "LOCAL_ONLY": return "LOCAL_ONLY", []
    if settings.ocr_processing_mode == "SINGLE_PROVIDER":
        provider = settings.ocr_single_provider
        return "SINGLE_PROVIDER", [provider] if provider in configured and configured[provider] else []
    if settings.ocr_processing_mode == "MULTI_PROVIDER":
        return "MULTI_PROVIDER", [p for p in ("gemini", "groq") if configured[p]]
    return "LOCAL_ONLY", []
