from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from . import settings_store
from .models import Holiday
from .config import env


def is_office_hours(db: Session, when: datetime) -> bool:
    local = when.astimezone(ZoneInfo(env.tz))
    cfg = settings_store.get(db, "office_hours")
    if local.weekday() not in cfg["days"]:
        return False
    if db.query(Holiday).filter(Holiday.day == local.strftime("%Y-%m-%d")).first():
        return False
    hm = local.strftime("%H:%M")
    return cfg["start"] <= hm < cfg["end"]
