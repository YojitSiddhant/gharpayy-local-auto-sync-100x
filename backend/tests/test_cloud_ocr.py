from types import SimpleNamespace
from backend.app.ocr import cloud
from backend.app.api.evidence import _safe_message_cleanup, _safe_phone_format

def settings(**kw):
    values = dict(ocr_processing_mode="MULTI_PROVIDER", ocr_single_provider=None,
                  gemini_api_key="g", groq_api_key="q", ocr_external_transmission_enabled=True,
                  ocr_cloud_request_budget=2, ocr_cloud_timeout_seconds=2,
                  gemini_model="gemini-2.5-flash", groq_vision_model="qwen/qwen3.8-27b")
    values.update(kw)
    return SimpleNamespace(**values)

def test_modes_and_consent():
    assert cloud.effective_cloud_selection(settings()) == ("MULTI_PROVIDER", ["gemini", "groq"])
    assert cloud.effective_cloud_selection(settings(ocr_processing_mode="LOCAL_ONLY")) == ("LOCAL_ONLY", [])
    assert cloud.effective_cloud_selection(settings(ocr_processing_mode="SINGLE_PROVIDER", ocr_single_provider="groq")) == ("SINGLE_PROVIDER", ["groq"])
    assert cloud.run_cloud(settings(ocr_external_transmission_enabled=False), b"x", ["gemini"])[0].error == "consent_required"

def test_gemini_request_and_structured_response(monkeypatch):
    class Response:
        status_code = 200
        def json(self): return {"candidates": [{"content": {"parts": [{"text": '{"raw_text":"hello","rows":[{"phone":null,"message":"hello","timestamp":null,"uncertain":true}]}' }]}}]}
    seen = {}
    def post(*args, **kwargs):
        seen.update(kwargs); return Response()
    monkeypatch.setattr(cloud.httpx, "post", post)
    result = cloud.gemini(b"img", "secret", 1, "image/jpeg", "gemini-2.5-flash")
    assert result.status == "completed" and result.observations[0]["uncertain"]
    assert seen["json"]["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "image/jpeg"
    assert seen["json"]["generationConfig"]["responseMimeType"] == "application/json"

def test_gemini_rejects_empty_and_blocked_candidates(monkeypatch):
    class Response:
        status_code = 200
        def __init__(self, payload): self.payload = payload
        def json(self): return self.payload
    for payload, expected in (({"candidates": []}, "empty_candidates"),
                              ({"candidates": [{"finishReason": "SAFETY"}]}, "blocked_safety"),
                              ({"candidates": [{"content": {"parts": []}}]}, "empty_content")):
        monkeypatch.setattr(cloud.httpx, "post", lambda *a, payload=payload, **k: Response(payload))
        result = cloud.gemini(b"img", "secret", 1, "image/png", "gemini-2.5-flash")
        assert result.status == "failed" and result.error == expected

def test_groq_request_and_secret_safe_failure(monkeypatch):
    class Response:
        status_code = 429
        def json(self): return {"error": {"message": "secret customer text", "code": "rate_limit"}}
    monkeypatch.setattr(cloud.httpx, "post", lambda *a, **k: Response())
    result = cloud.groq(b"img", "secret-key", 1, "image/png", "qwen/qwen3.8-27b")
    assert result.error == "quota" and "secret" not in str(result.__dict__)

def test_groq_uses_json_mode(monkeypatch):
    class Response:
        status_code = 200
        def json(self): return {"choices": [{"message": {"content": '{"raw_text":"","rows":[]}'}}]}
    seen = {}
    def post(*args, **kwargs):
        seen.update(kwargs); return Response()
    monkeypatch.setattr(cloud.httpx, "post", post)
    assert cloud.groq(b"img", "secret-key", 1, "image/png", "qwen/qwen3.8-27b").status == "completed"
    assert seen["json"]["response_format"] == {"type": "json_object"}

def test_clear_message_requires_known_unique_positions(monkeypatch):
    class Response:
        status_code = 200
        def __init__(self, content): self.content = content
        def json(self): return {"choices": [{"message": {"content": self.content}}]}
    seen = []
    def post(*args, **kwargs):
        seen.append(kwargs)
        return Response('{"raw_text":"","rows":[{"position":2,"phone":null,"message":"fixed","timestamp":null}]}')
    monkeypatch.setattr(cloud.httpx, "post", post)
    result = cloud.groq(b"img", "secret-key", 1, "image/png", "qwen/qwen3.8-27b", "[]", required_positions={1, 2})
    assert result.status == "completed" and result.observations[0]["position"] == 2
    assert "position" in seen[0]["json"]["messages"][0]["content"][0]["text"]

def test_clear_message_rejects_missing_unknown_and_duplicate_positions(monkeypatch):
    class Response:
        status_code = 200
        def __init__(self, content): self.content = content
        def json(self): return {"choices": [{"message": {"content": self.content}}]}
    payloads = [
        '{"raw_text":"","rows":[{"phone":null,"message":"x"}]}',
        '{"raw_text":"","rows":[{"position":9,"phone":null,"message":"x"}]}',
        '{"raw_text":"","rows":[{"position":1,"phone":null,"message":"x"},{"position":1,"phone":null,"message":"y"}]}',
    ]
    for content, expected in zip(payloads, ("missing=1", "unknown=1", "duplicate=1")):
        monkeypatch.setattr(cloud.httpx, "post", lambda *a, content=content, **k: Response(content))
        result = cloud.groq(b"img", "secret-key", 1, "image/png", "qwen/qwen3.8-27b", required_positions={1})
        assert result.status == "failed" and expected in result.error

def test_gemini_model_listing(monkeypatch):
    class Response:
        status_code = 200
        def __init__(self, payload): self.payload = payload
        def json(self): return self.payload
    monkeypatch.setattr(cloud.httpx, "get", lambda *a, **k: Response({"models": [
        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]}]}))
    assert cloud.gemini_model_status("secret-key", 1, "gemini-2.5-flash").status == "completed"
    monkeypatch.setattr(cloud.httpx, "get", lambda *a, **k: Response({"models": []}))
    assert cloud.gemini_model_status("secret-key", 1, "gemini-2.5-flash").error == "model_unavailable"

def test_gemini_model_listing_http_error_is_sanitized(monkeypatch):
    class Response:
        status_code = 401
        def json(self): return {"error": {"message": "secret key"}}
    monkeypatch.setattr(cloud.httpx, "get", lambda *a, **k: Response())
    result = cloud.gemini_model_status("secret-key", 1, "gemini-2.5-flash")
    assert result.error == "invalid_credentials" and "secret" not in str(result.__dict__)

def test_gemini_404_is_classified_as_model_unavailable(monkeypatch):
    class Response:
        status_code = 404
        def json(self): return {"error": {"message": "model unavailable"}}
    monkeypatch.setattr(cloud.httpx, "post", lambda *a, **k: Response())
    result = cloud.gemini(b"img", "secret-key", 1, "image/png", "missing-model")
    assert result.error == "model_unavailable" and result.status_code == 404

def test_provider_model_retirement_message_is_not_misclassified(monkeypatch):
    class Response:
        status_code = 404
        def json(self): return {"error": {"code": 404, "status": "NOT_FOUND", "message": "model is no longer available to new users"}}
    monkeypatch.setattr(cloud.httpx, "post", lambda *a, **k: Response())
    result = cloud.gemini(b"img", "secret-key", 1, "image/png", "gemini-2.5-flash")
    assert result.error == "model_unavailable" and result.status_code == 404

def test_malformed_output_and_missing_credentials(monkeypatch):
    class Response:
        status_code = 200
        def json(self): return {"choices": [{"message": {"content": "not json"}}]}
    monkeypatch.setattr(cloud.httpx, "post", lambda *a, **k: Response())
    assert cloud.groq(b"x", "q", 1, "image/png", "m").error == "malformed_output"
    result = cloud.run_cloud(settings(gemini_api_key=""), b"x", ["gemini"])
    assert result[0].error == "not_configured"

def test_safe_clear_message_normalization_does_not_change_digits_or_meaning():
    assert _safe_phone_format("+91 94464 41251", "+919446441251") == "+919446441251"
    assert _safe_phone_format("+91 94464 41251", "+91 94464 41252") is None
    assert _safe_message_cleanup("Hello   there", " Hello there ", False) == "Hello there"
    assert _safe_message_cleanup("Hello 123", "Hello 124", False) is None
    assert _safe_message_cleanup("VW Hello there", "Hello there", False) == "Hello there"
    assert _safe_message_cleanup("Hello there", "Hello", False) is None

def test_groq_text_cleanup_sends_no_image_and_validates_row_ids(monkeypatch):
    class Response:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": '{"rows":[{"row_id":"r1","phone":"+91 98765 43210","message":"Hello there","uncertain":false}]}'}}]}
    seen = {}
    def post(*args, **kwargs):
        seen.update(kwargs)
        return Response()
    monkeypatch.setattr(cloud.httpx, "post", post)
    result = cloud.groq_text_cleanup("secret-key", 1, "qwen/qwen3.8-27b", [{"row_id": "r1", "phone": "9876543210", "message": "Hello  there"}])
    assert result.status == "completed"
    request = seen["json"]
    assert request["messages"][0]["content"] and "image_url" not in str(request)
    assert '"row_id": "r1"' in request["messages"][0]["content"]

def test_groq_text_cleanup_rejects_missing_or_duplicate_ids(monkeypatch):
    class Response:
        status_code = 200
        def __init__(self, content): self.content = content
        def json(self): return {"choices": [{"message": {"content": self.content}}]}
    for content in ('{"rows":[]}', '{"rows":[{"row_id":"r1"},{"row_id":"r1"}]}'):
        monkeypatch.setattr(cloud.httpx, "post", lambda *a, content=content, **k: Response(content))
        result = cloud.groq_text_cleanup("secret-key", 1, "qwen/qwen3.8-27b", [{"row_id": "r1", "phone": None, "message": None}])
        assert result.status == "failed" and result.error == "invalid_row_ids"
