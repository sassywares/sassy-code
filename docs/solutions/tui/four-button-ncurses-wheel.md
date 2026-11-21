---
title: Read wheel input only from real button masks, and debounce one notch
category: tui
tags: [curses, mouse, wheel, ncurses, terminal]
created: 2026-11-21
---

## Problem

Wheel-down did nothing on this platform, and attempts to recover it by guessing
at spare bits turned Shift-click and Alt-click into scrolls. Separately, one
physical wheel notch sometimes moved the highlight twice.

## Root cause

The system ncurses is built for four mouse buttons. On this machine:

```
BUTTON4_PRESSED   = 0x80000     # wheel up
BUTTON5_PRESSED   = 0x0         # absent: hasattr(curses, "BUTTON5_PRESSED") is False
ALL_MOUSE_EVENTS  = 0x7ffffff
REPORT_MOUSE_POSITION = 0x8000000   # the only bit outside it
```

X10 button 5 is the wheel-down code, but this library drops that event before
the application sees it. There is no bstate to decode. The only masks outside
`ALL_MOUSE_EVENTS` belong to `REPORT_MOUSE_POSITION`, and the plausible-looking
spare bits inside the range are modifier bits: treating one as a wheel aliases
`BUTTON_SHIFT`/`BUTTON_ALT`, so a Shift-click scrolls instead of selecting text.
That is worse than a missing wheel-down.

The double move has a different cause: a terminal may report one notch as more
than one `KEY_MOUSE` event (press then click, or a prolonged event), and each
was applied as a fresh step.

## Solution

- `MOUSE_WHEEL_UP`/`MOUSE_WHEEL_DOWN` (`panel.py:86`) are built only from
  `BUTTON4_*`/`BUTTON5_*` via `getattr`, so a 4-button build yields a zero
  wheel-down mask and wheel-down is inert rather than guessed.
- `mouse_kind()` (`panel.py:3978`) tests those masks only. No modifier bit is
  ever classified as a wheel; an unclassified event is `""` and ignored.
- `wheel_notch()` (`panel.py:4028`) debounces a same-direction event inside
  `WHEEL_DEBOUNCE_SECONDS` (0.05 s): it updates the clock but reports not-fresh,
  so a press/click pair is one step. A direction change is always fresh, so
  reversing the wheel is never swallowed.
- `SASSY_CODE_MOUSE_DEBUG=<path>` appends the raw `bstate` per event for pinning
  the real bit on a new terminal; it refuses paths inside the repo and never
  raises.

## Prevention

`tests/test_mouse.py` asserts that Shift, Alt and bare modifier bstates are not
wheel notches, that button-2 release is not a wheel, that wheel notches move
exactly one clamped row, and that a duplicate inside the window is not fresh
while a direction change is. Do not extend the wheel masks speculatively: if a
platform drops the event, accept the missing direction and document it.
