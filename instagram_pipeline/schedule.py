"""Europe/Madrid civil time, including both daylight-saving transitions."""
from datetime import datetime, time, timedelta
import time as clock
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")


def now_madrid():
    return datetime.now(MADRID)


def target_for(day):
    return datetime.combine(day, time(20, 0), tzinfo=MADRID)


def in_publication_window(now: datetime, day, grace_minutes=45):
    target = target_for(day)
    return target <= now.astimezone(MADRID) <= target + timedelta(minutes=grace_minutes)


def wait_for_publication(day, now=now_madrid, sleep=clock.sleep):
    target = target_for(day)
    while True:
        remaining = (target - now()).total_seconds()
        if remaining <= 0:
            break
        sleep(min(30, remaining))
    if not in_publication_window(now(), day):
        raise RuntimeError("La ventana de publicación terminó; se evita publicar fuera de horario.")
