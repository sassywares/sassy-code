---
title: Precompute every trash manifest entry before the first move
category: python
tags: [trash, cascade, transactions, rollback, state]
created: 2026-11-23
---

## Context

A goal/slice delete (`trash.delete_subtree()`) moves a whole subtree of
transcripts out of Pi/Claude storage and then rewrites `trash/index.json`,
`sessions.json` and `goals.json`. Any of those writes can fail on a full disk, a
permission change or a corrupt store, halfway through.

## Guidance

Order the cascade so that no irreversible step happens before all the
information needed to recover exists, and re-read the source of truth instead
of trusting the caller's snapshot:

- `_fresh_group(group, goals_data)` re-reads `goals.json` and re-derives the
  subtree, so the members moved are the members stored now, not the ones the
  drawing was built from. Deleting against a stale in-memory `Group` could
  trash a session another process just filed elsewhere.
- Preflight every member with `busy_reason()` before stopping anything, and
  stop the whole subtree before moving any file. One busy, external or unknown
  member refuses the whole delete and names it.
- Build the complete `entries` list — including each unique
  `_unique_trash_path(session.id, now)` — before the first `shutil.move`. If
  entry construction raises, nothing has moved and the footer says so.
- Then move the files, then write the records. On a move or write failure,
  `_rollback_moves(moved)` puts the moved transcripts back and `_append_entries`
  still records any file that could not move back, so no transcript is lost and
  no moved file is left unindexed.

## Applicability

Applies to any multi-file move that must be all-or-nothing and whose records
live in a separate store. It does not replace the rollback: a rare failure
between moving a file and writing the index is repaired by indexing the
leftover, never by assuming the move succeeded. A single-session delete uses the
same shape with one entry.
