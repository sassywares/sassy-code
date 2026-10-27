---
title: sassy-code's two-pane layout cannot be a pure extension; `switchSession` is the native path
category: pi-extension
tags: [pi-extension, tmux, session-manager, architecture]
created: 2026-10-17
updated: 2026-10-17
---

## Context

`sassy-code` is an external Python program that lists Pi sessions in one tmux pane
and presents one at a time in an adjacent pane, keeping switched-away sessions
alive in background panes. The question was whether the whole thing
could be reimplemented as a Pi extension, removing the separate process and the
bespoke session-file parsing.

## Guidance

Almost all of the panel's logic maps onto extension APIs, but the *two-pane*
shape does not. An extension runs inside a single Pi process and renders into
that process's TUI, so a simultaneous, independently focusable second pane still
requires tmux (or a second process). The important discovery is that resume does
not:

| Panel behaviour | Extension API |
|---|---|
| List every session with metadata | `SessionManager.listAll()` returns `SessionInfo` (`path`, `id`, `cwd`, `name`, `created`, `modified`, `messageCount`, `firstMessage`, `allMessagesText`) |
| Select a session in the TUI | `ctx.ui.select()` or `ctx.ui.custom()` with a list component |
| Present a chosen session | `ctx.switchSession(sessionPath)` swaps the current Pi into that session **in place** — no tmux, no new process, but no way to keep the previous session running in parallel |
| Start a new session | `ctx.newSession()` |

So there are two viable designs:

- **Native, single pane.** A `/sessions` command lists sessions and calls
  `ctx.switchSession()` to switch the running Pi to the chosen one. This loses
  the side-by-side view but removes tmux, Python, and all file parsing for the
  list.
- **Native panel, still tmux.** Run the panel as
  `pi --no-session --extension panel.ts` in the left pane; the extension renders
  the list with `ctx.ui.custom()` and drives the right pane through tmux. This
  keeps the two-pane feel but makes the panel a full Pi process.

`SessionInfo` does **not** carry `stopReason` or `isError`, so the `error` and
`active` statuses still require reading a file's tail, and `running` still
requires a process-table or tmux check. Neither `listAll` nor `switchSession`
provides those.

## Applicability

Applies when deciding how to surface Pi sessions or automate session switching.
Choose the native single-pane design when the goal is browsing and switching;
keep the tmux panel when the goal is watching one session while scanning others.
The `switchSession` path also requires command context and TUI mode, and the
original context becomes stale after a successful switch, so post-switch work
must run in the `withSession` callback.
