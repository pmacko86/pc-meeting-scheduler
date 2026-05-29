"""Scheduling-preferences data model and Xoyondo CSV parser."""

import csv
import datetime
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from reviewers import Availability, ReviewerAvailability, TimeSlot

# Each column in the Xoyondo export represents one hour.
SLOT_DURATION_MINUTES: int = 60


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SchedulingPreferences:
    slots: list[TimeSlot]
    reviewers: list[ReviewerAvailability]

    def __str__(self) -> str:
        return f"{len(self.slots)} slots, {len(self.reviewers)} reviewers"

    def __repr__(self) -> str:
        return f"SchedulingPreferences(slots=<{len(self.slots)}>, reviewers=<{len(self.reviewers)}>)"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _clean_cell(s: str) -> str:
    return s.strip().strip('"')


_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def _parse_month_year(s: str) -> tuple[int, int]:
    """Parse "June 2026" → (6, 2026). Raises ValueError on failure."""
    parts = s.strip().split()
    if len(parts) != 2:
        raise ValueError(f"Expected 'Month YYYY', got {s!r}")
    month = _MONTH_NAMES.get(parts[0].lower())
    if month is None:
        raise ValueError(f"Unknown month name: {parts[0]!r}")
    return month, int(parts[1])


def _parse_time(s: str) -> datetime.time:
    """Parse "8am" / "12pm" → datetime.time."""
    s = s.strip().lower()
    if s.endswith("am"):
        h = int(s[:-2])
        if h == 12:
            h = 0   # 12am = midnight
    elif s.endswith("pm"):
        h = int(s[:-2])
        if h != 12:
            h += 12  # 1pm–11pm → 13–23; 12pm stays 12
    else:
        raise ValueError(f"Cannot parse time: {s!r}")
    return datetime.time(h, 0)


def parse_xoyondo_csv(path: Path) -> SchedulingPreferences:
    """Parse a Xoyondo scheduling CSV export."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        raw = list(csv.reader(f))

    # Locate the day-header row (contains weekday abbreviations).
    day_row_idx = None
    for i, row in enumerate(raw):
        cells = [_clean_cell(c) for c in row]
        if cells[0] == "" and any(re.search(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b", c) for c in cells[1:]):
            day_row_idx = i
            break
    if day_row_idx is None:
        raise ValueError(f"Cannot find day-header row in {path}")

    time_row_idx = day_row_idx + 1
    day_cells  = [_clean_cell(c) for c in raw[day_row_idx]]
    time_cells = [_clean_cell(c) for c in raw[time_row_idx]]

    # Build per-column month/year from the row above the day row.
    # A cell like "June 2026" starts a new month; empty cells inherit the previous.
    col_to_month_year: dict[int, tuple[int, int]] = {}
    if day_row_idx > 0:
        month_cells = [_clean_cell(c) for c in raw[day_row_idx - 1]]
        current_my: Optional[tuple[int, int]] = None
        for col, cell in enumerate(month_cells):
            if cell:
                try:
                    current_my = _parse_month_year(cell)
                except ValueError:
                    pass
            if current_my is not None:
                col_to_month_year[col] = current_my

    # Build TimeSlot list, propagating the current day label across empty cells.
    slots: list[TimeSlot] = []
    col_to_slot: dict[int, TimeSlot] = {}
    current_day_str = ""
    current_my = col_to_month_year.get(1)  # fallback to first known month

    for col in range(1, len(time_cells)):
        if col < len(day_cells) and day_cells[col]:
            current_day_str = day_cells[col]
        if col in col_to_month_year:
            current_my = col_to_month_year[col]

        time_str = time_cells[col] if col < len(time_cells) else ""
        if not time_str or current_my is None:
            continue

        day_num_match = re.search(r"\d+", current_day_str)
        if not day_num_match:
            continue

        month, year = current_my
        date = datetime.date(year, month, int(day_num_match.group()))
        slot = TimeSlot(date=date, time=_parse_time(time_str))
        slots.append(slot)
        col_to_slot[col] = slot

    # Parse reviewer rows.
    reviewers: list[ReviewerAvailability] = []
    for row in raw[time_row_idx + 1:]:
        cells = [_clean_cell(c) for c in row]
        name = cells[0]
        if not name or re.match(r"^\d+$", name):
            continue
        avail: dict[TimeSlot, Availability] = {
            slot: Availability.parse(cells[col] if col < len(cells) else "")
            for col, slot in col_to_slot.items()
        }
        reviewers.append(ReviewerAvailability(reviewer_name=name, availability=avail))

    return SchedulingPreferences(slots=slots, reviewers=reviewers)
