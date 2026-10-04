from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any, Deque, Dict, List, Set


class EventBus:
    """In-process event bus with async websocket fan-out support."""

    def __init__(self, max_events: int = 500) -> None:
        self._history: Deque[Dict[str, Any]] = deque(maxlen=max_events)
        self._subscribers: Set[asyncio.Queue] = set()

    def publish(self, event: Dict[str, Any]) -> None:
        self._history.append(event)
        dead: List[asyncio.Queue] = []
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except Exception:
                dead.append(q)
        for q in dead:
            self._subscribers.discard(q)

    def history(self) -> List[Dict[str, Any]]:
        return list(self._history)

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)


BUS = EventBus()


def serialize_event(event: Dict[str, Any]) -> str:
    def _default(obj: Any):
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return str(obj)

    return json.dumps(event, default=_default)
