---
title: Restore a session as explicitly loose, never loose by absence
category: python
tags: [goals, trash, restore, membership, inheritance]
created: 2026-11-23
---

## Problem

A restored session that had been at the top level (or whose goal had since been
deleted) came back under the wrong goal on the next derive: the panel filed it
under a goal through its `parentSession` ancestor, even though it had never been
in one.

## Root cause

Restore only wrote goal membership when the recorded `goal_id` still existed.
With no container to file into, it wrote nothing — and "no membership" is not
the same as "top level". `goals.inherit_goal_members()` reads the absence as
"derive it", so the session inherited the container of its nearest explicitly
filed `parentSession` ancestor. The delete had also already erased every trace
of the session's previous membership, so the trash entry was the only place the
pre-delete filing could live.

## Solution

Two halves, both needed:

- Before deleting, `trash._tree_entry()` records the pre-delete membership in
  the manifest: `"loose": bool(membership.get("loose"))` alongside `goal_id`,
  `done` and `deleted_at`. The trash captures the state the delete is about to
  remove.
- On restore, `goals.file_session_on_restore(cwd, session_id, goal_id)` strips
  any current membership and, when the recorded container is missing (or
  `goal_id` is empty), calls `_add_loose(data, cwd, session_id)` instead of
  leaving the session unlisted. Explicit loose membership wins over inherited
  membership, so a later derive cannot refile it.

## Prevention

`tests/test_trash.py`:

- `test_restore_top_level_stays_loose_under_a_filed_ancestor` deletes a
  top-level session whose `parentSession` ancestor is filed in a goal, then
  asserts the session id is in `goals.json`'s `loose` list and not in any
  derived goal.
- `test_restore_with_a_missing_container_comes_back_loose` deletes the goal
  after the session and restores it.
- `test_restored_member_comes_back_loose_with_its_done_mark` covers a cascade
  member.
