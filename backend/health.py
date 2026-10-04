from __future__ import annotations

from database.db_manager import get_system_health


def health_snapshot() -> dict:
    return get_system_health()
