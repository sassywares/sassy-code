---
title: Parse session timestamps as UTC, never as local time
category: tui
tags: [timestamps, timezone, utc, rel-time, session-store]
created: 2026-10-23
updated: 2026-10-23
---

## Problem

Every age in the panel was off by the machine's UTC offset. A session created
seconds ago displayed `5h` on a `UTC+5` machine, and after `/handoff` the new
session showed the same age as the session it replaced, because both were
shifted into the same bucket. Any rule that compares activity times — the 5
minute **active** window, sorting, handoff detection — inherited the shift.

## Root cause

Pi and Claude Code both write ISO-8601 timestamps in UTC
(`2026-10-23T04:26:42.592Z`). The parser did:

```python
time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
```

`strptime` produces a naive `struct_time` and `mktime` interprets it as **local**
time, so every instant was treated as if the UTC clock reading were local.
`strptime` also dropped the fractional seconds.

## Solution

`iso_to_epoch()` converts a trailing `Z` to `+00:00`, uses
`datetime.fromisoformat` (keeping microseconds), and treats a naive result as
UTC:

```python
if value[-1:] in ("Z", "z"):
    value = value[:-1] + "+00:00"
dt = datetime.fromisoformat(value)
if dt.tzinfo is None:
    dt = dt.replace(tzinfo=timezone.utc)
return dt.timestamp()
```

The panel renders `rel_time()` from these epochs; comparing `last_ts` values now
also makes cross-session decisions (active window, handoff successors) correct.

## Prevention

`tests/test_sessions.py` asserts a `Z` timestamp equals the same instant built
with `datetime(..., tzinfo=timezone.utc)`, that a naive stamp is read as UTC,
that an empty timestamp stays unknown (`?`), and that a just-created session
renders as `now`. Keep timestamps as absolute UTC epochs internally and only
format them at draw time.
