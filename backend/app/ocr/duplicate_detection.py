"""Privacy-preserving, conservative cross-zone phone matching."""
from __future__ import annotations
import hashlib
import hmac
import re
from backend.app.core.config import Settings

FINGERPRINT_VERSION = "phone-hmac-v1"
NORMALIZATION_VERSION = "indian-mobile-v1"
_INDIAN_MOBILE = re.compile(r"^\+91[6-9]\d{9}$")

def eligible_phone(normalized: str | None, status: str, quarantined: bool, header: str | None) -> bool:
    return bool(normalized and status == "candidate" and not quarantined and header and _INDIAN_MOBILE.fullmatch(normalized))

def phone_fingerprint(normalized: str | None, settings: Settings) -> str | None:
    if not settings.duplicate_hmac_key or len(settings.duplicate_hmac_key) < 32 or not normalized or not _INDIAN_MOBILE.fullmatch(normalized):
        return None
    message = f"{FINGERPRINT_VERSION}:{NORMALIZATION_VERSION}:{normalized}".encode()
    return hmac.new(settings.duplicate_hmac_key.encode(), message, hashlib.sha256).hexdigest()

def key_configured(settings: Settings) -> bool:
    return bool(settings.duplicate_hmac_key and len(settings.duplicate_hmac_key) >= 32)
