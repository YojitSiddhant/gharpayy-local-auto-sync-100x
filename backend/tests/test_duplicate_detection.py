from backend.app.core.config import Settings
from backend.app.ocr.duplicate_detection import eligible_phone, phone_fingerprint


def settings(key: str = "k" * 32) -> Settings:
    return Settings(duplicate_hmac_key=key)


def test_fingerprint_is_deterministic_and_key_separated():
    assert phone_fingerprint("+919876543210", settings()) == phone_fingerprint("+919876543210", settings())
    assert phone_fingerprint("+919876543210", settings("x" * 32)) != phone_fingerprint("+919876543210", settings())


def test_missing_key_fails_closed():
    assert phone_fingerprint("+919876543210", settings("")) is None


def test_only_complete_header_candidates_are_eligible():
    assert eligible_phone("+919876543210", "candidate", False, "+91 98765 43210")
    assert not eligible_phone("+919876543210", "ambiguous", False, "+91 98765 43210")
    assert not eligible_phone("+919876543210", "candidate", True, "+91 98765 43210")
    assert not eligible_phone("+919876543210", "candidate", False, None)
    assert not eligible_phone("+91987654321", "candidate", False, "+91 98765 4321")
