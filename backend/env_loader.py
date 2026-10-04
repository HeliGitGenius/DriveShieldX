"""Load KEY=VALUE pairs from the project's `.env` into os.environ (no extra dependency).

Variables that are already set in the environment win, so a shell/CI value always
overrides the file. Safe to call many times."""
from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_LOADED = set()


def load_env(path: str | os.PathLike | None = None) -> None:
    p = Path(path) if path else Path(os.environ.get("DSX_ENV_FILE", _ROOT / ".env"))
    key = str(p.resolve())
    if key in _LOADED or not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        elif " #" in v:
            v = v.split(" #", 1)[0].rstrip()
        if k:
            os.environ.setdefault(k, v)
    _LOADED.add(key)
