"""One-time interactive Super Admin bootstrap; never runs at startup or migration time."""
from getpass import getpass

from sqlalchemy import select

from backend.app.core.auth import hash_password, normalize_username
from backend.app.db.session import SessionLocal
from backend.app.models.auth import User, UserRole
from backend.app.seed_zones import seed_initial_zones


def main() -> int:
    db = SessionLocal()
    try:
        if db.scalar(select(User.id).where(User.role == UserRole.SUPER_ADMIN)):
            print("A Super Admin already exists; refusing to initialize another.")
            return 1
        username = input("Initial Super Admin username: ").strip()
        normalized = normalize_username(username)
        if not normalized:
            print("Username is required."); return 2
        if db.scalar(select(User.id).where(User.normalized_username == normalized)):
            print("That username already exists; refusing to overwrite it."); return 1
        password = getpass("Initial Super Admin password: ")
        confirm = getpass("Confirm password: ")
        if len(password) < 12 or password != confirm:
            print("Password must be at least 12 characters and match confirmation."); return 2
        seed_initial_zones(db)
        db.add(User(username=username, normalized_username=normalized, display_name=username, password_hash=hash_password(password), role=UserRole.SUPER_ADMIN, zone_id=None))
        db.commit(); print("Super Admin initialized and initial zones ensured."); return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
