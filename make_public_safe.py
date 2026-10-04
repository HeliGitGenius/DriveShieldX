"""Replace personal details in the project files with dummy values before making the repo public.

Your real values stay in your own .env (which is never uploaded), so the app keeps working for you.

    python make_public_safe.py
"""
from pathlib import Path

REPLACE = {
    "driveshieldx%40upi": "driveshieldx%40upi",
    "driveshieldx@upi": "driveshieldx@upi",
    "+91-9000000000": "+91-9000000000",
    "9000000004": "9000000004",
    'git config --global user.email "you@example.com"': 'git config --global user.email "you@example.com"',
}
SKIP = {".git", ".venv", "__pycache__", "models", "database", "evaluation/results"}
TEXT = {".py", ".md", ".txt", ".ps1", ".sh", ".example", ".json", ".csv", ".sql", ".yaml", ".yml", ".toml"}

changed = 0
for p in Path(".").rglob("*"):
    rel = p.as_posix()
    if not p.is_file() or any(rel == s or rel.startswith(s + "/") or f"/{s}/" in f"/{rel}" for s in SKIP):
        continue
    if p.suffix.lower() not in TEXT and p.name != ".env.example":
        continue
    try:
        s = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    new = s
    for a, b in REPLACE.items():
        new = new.replace(a, b)
    if new != s:
        p.write_text(new, encoding="utf-8", newline="")
        changed += 1
        print("cleaned", rel)
print(f"done: {changed} file(s) changed")
