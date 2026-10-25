"""Automatic daily backups of the SQLite database.

Uses `VACUUM INTO` (SQLite 3.27+): an atomic consistent snapshot that is
safe to take while the app is serving writes — no need to pause traffic
or checkpoint WAL first.

Runs inside the app (no host cron needed, works in Docker):
- first backup ~60s after startup, then every 24h
- keeps the newest KB_BACKUP_KEEP files (default 30)

Env:
  KB_BACKUP_ENABLED  '0' disables (default enabled)
  KB_BACKUP_DIR      default <db_dir>/backups
  KB_BACKUP_KEEP     how many daily files to keep (default 30)
"""
import asyncio
import logging
import os
import re
from datetime import datetime
from pathlib import Path

import aiosqlite

from database import DB_PATH

log = logging.getLogger("kb.backup")

BACKUP_ENABLED = os.environ.get("KB_BACKUP_ENABLED", "1") != "0"
BACKUP_KEEP = max(1, int(os.environ.get("KB_BACKUP_KEEP", "30")))
BACKUP_DIR = Path(
    os.environ.get("KB_BACKUP_DIR") or os.path.join(os.path.dirname(DB_PATH), "backups")
)

_FILENAME_RE = re.compile(r"^kb-(\d{8}-\d{6})\.db$")


def _existing_backups() -> list[Path]:
    if not BACKUP_DIR.is_dir():
        return []
    out = []
    for p in BACKUP_DIR.iterdir():
        m = _FILENAME_RE.match(p.name)
        if m:
            out.append((m.group(1), p))
    return [p for _, p in sorted(out)]


def _prune_old_backups() -> int:
    """Delete oldest backups beyond BACKUP_KEEP. Returns number deleted."""
    backups = _existing_backups()
    excess = len(backups) - BACKUP_KEEP
    if excess <= 0:
        return 0
    deleted = 0
    for p in backups[:excess]:
        try:
            p.unlink()
            deleted += 1
        except OSError as e:
            log.warning("backup prune failed for %s: %s", p, e)
    return deleted


async def run_backup() -> Path | None:
    """Take one snapshot. Returns the created file path, or None on failure."""
    if not BACKUP_ENABLED:
        return None
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = BACKUP_DIR / f"kb-{ts}.db"
        if target.exists():  # same-second retry — pick a suffix
            target = BACKUP_DIR / f"kb-{ts}-{os.getpid()}.db"
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("VACUUM INTO ?", (str(target),))
        deleted = _prune_old_backups()
        if deleted:
            log.info("backup: pruned %d old snapshot(s)", deleted)
        log.info("backup: wrote %s", target)
        return target
    except Exception:
        log.exception("backup failed")
        return None


async def backup_loop() -> None:
    """Background task: first run 60s after boot, then every 24h."""
    if not BACKUP_ENABLED:
        log.info("backup: disabled via KB_BACKUP_ENABLED=0")
        return
    while True:
        await asyncio.sleep(60)
        await run_backup()
        await asyncio.sleep(24 * 3600)
