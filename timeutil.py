"""Process timezone pinning.

The database stores *naive local* timestamps (`datetime('now','localtime')`),
and the front-end renders them as browser-local wall clock. So the process
timezone has to match the user's clock — otherwise every stored timestamp is
silently offset.

A Docker container without a TZ env var runs on UTC, which made the whole KB
show timestamps 8 hours behind Asia/Shanghai (the host and the browser are
both CST).

Importing this module pins the timezone:

    import timeutil   # noqa: F401  — importing is the point

`time.tzset()` re-reads TZ for the C library, and SQLite's `localtime`
modifier follows the C library, so both Python and SQLite agree afterwards.

Override the zone with the KB_TZ env var (IANA name, e.g. "Asia/Tokyo").
"""
import os
import time

# The deployment's home timezone. Chosen to match the host clock and the
# browser, so naive timestamps in the DB read correctly without conversion.
DEFAULT_TZ = "Asia/Shanghai"


def init_timezone(tz: str | None = None) -> str:
    """Pin the process timezone and return the zone name that was applied.

    Args:
        tz: IANA zone name. Defaults to $KB_TZ, then to DEFAULT_TZ.
    """
    name = tz or os.environ.get("KB_TZ") or DEFAULT_TZ
    os.environ["TZ"] = name
    if hasattr(time, "tzset"):
        time.tzset()
    return name


# Pin on import — every entry point (app, MCP server, importer, exporter)
# imports this before touching the database.
TZ_NAME = init_timezone()
