from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.auth import Zone

INITIAL_ZONES = ("central students", "kora homes", "bellandur homes", "kora core", "MTP pg", "MWB more", "sju pg", "ypr pg")


def slugify(name: str) -> str:
    return "-".join(name.casefold().split())


def seed_initial_zones(db: Session) -> int:
    created = 0
    for name in INITIAL_ZONES:
        if db.scalar(select(Zone).where(Zone.slug == slugify(name))) is None:
            db.add(Zone(name=name, slug=slugify(name))); created += 1
    db.commit()
    return created
