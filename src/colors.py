"""ANSI color constants. Controlled by configure() or auto-detected from TTY."""

import sys

# Maps constant name → ANSI code integers
_CODES: dict[str, tuple[int, ...]] = {
    "RESET":         (0,),
    "BOLD":          (1,),
    "DIM":           (2,),
    "HEADER":        (1, 34),   # bold blue   — section headers (=== ... ===)
    "SESSION_TITLE": (1,),      # bold        — session name
    "PID":           (2,),      # dim         — paper IDs
    "OK":            (32,),     # green       — all reviewers present
    "WARN":          (33,),     # yellow      — unavailable reviewers
    "DIM_TEXT":      (2,),      # dim         — low-priority info (missing count)
    "BEST_EFFORT":   (33,),     # yellow      — best-effort placement indicator
    "LABEL_ATTN":    (1, 33),   # bold yellow — [attention] label
    "LABEL_SHOT":    (1, 36),   # bold cyan   — [one-shot] label
    "LABEL_BEST":    (1, 31),   # bold red    — [best effort] label
    "NOTE":          (33,),     # yellow      — below-minimum session note
    "CONFLICT":      (33,),     # yellow      — scheduling conflict header
    "NO_OVERLAP":    (91,),     # bright red  — no session overlap header
    "NO_SLOTS":      (31,),     # red         — no availability header
    "RV_NAME":       (1,),      # bold        — reviewer name in attendance list
    "TOTAL":         (1,),      # bold        — summary / total lines
    "UNSCHEDULED":   (91,),     # bright red  — unscheduled papers header
    "SKIPPED":       (2,),      # dim         — skipped papers header
    "ALGO_HEADER":   (1, 36),   # bold cyan   — algorithm separator
}

# Placeholders — populated by _apply() below
RESET = BOLD = DIM = ""
HEADER = SESSION_TITLE = PID = ""
OK = WARN = DIM_TEXT = BEST_EFFORT = ""
LABEL_ATTN = LABEL_SHOT = LABEL_BEST = ""
NOTE = CONFLICT = NO_OVERLAP = NO_SLOTS = ""
RV_NAME = TOTAL = UNSCHEDULED = SKIPPED = ALGO_HEADER = ""


def _apply(on: bool) -> None:
    g = globals()
    for name, codes in _CODES.items():
        g[name] = f"\033[{';'.join(str(c) for c in codes)}m" if on else ""


def configure(mode: str) -> None:
    """Set color mode: 'always', 'auto' (TTY detection), or 'never'."""
    if mode == "always":
        _apply(True)
    elif mode == "never":
        _apply(False)
    else:  # "auto"
        _apply(sys.stdout.isatty())


# Initialise with auto-detection.
configure("auto")
