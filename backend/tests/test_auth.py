from backend.app.core.auth import hash_password, login_allowed, normalize_username, record_login_failure, verify_password
from backend.app.models.auth import User, UserRole


def test_passwords_use_argon2_and_verify_securely():
    encoded = hash_password("a sufficiently long test password")
    assert encoded.startswith("$argon2id$")
    assert verify_password("a sufficiently long test password", encoded)
    assert not verify_password("wrong password", encoded)


def test_username_normalization_and_login_throttle():
    assert normalize_username("  Admin  User ") == "admin user"
    key = "test-throttle-auth"
    for _ in range(5):
        assert login_allowed(key)
        record_login_failure(key)
    assert not login_allowed(key)


def test_role_zone_invariant_is_declared_on_model():
    constraint_names = {constraint.name for constraint in User.__table__.constraints}
    assert "ck_users_role_zone" in constraint_names
    assert UserRole.SUPER_ADMIN.value == "SUPER_ADMIN"
