"""Reviewer data models, name matching, and reporting."""

import datetime
import difflib
import enum
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class AssignmentReviewer:
    """A reviewer as identified in the HotCRP assignments JSON."""
    email: str
    display_name: str   # cleaned, badge-free name from HotCRP

    def __str__(self) -> str:
        return f"{self.display_name} <{self.email}>"

    def __repr__(self) -> str:
        return f"AssignmentReviewer({self.display_name!r}, {self.email!r})"


class Availability(enum.Enum):
    YES     = "Yes"
    NO      = "No"
    MAYBE   = "Maybe"
    UNKNOWN = ""

    def __str__(self) -> str:
        return self.value if self.value else "?"

    def __repr__(self) -> str:
        return f"Availability.{self.name}"

    @classmethod
    def parse(cls, s: str) -> "Availability":
        s = s.strip()
        for member in cls:
            if member.value.lower() == s.lower():
                return member
        return cls.UNKNOWN


@dataclass(frozen=True)
class TimeSlot:
    date: datetime.date
    time: datetime.time

    def __str__(self) -> str:
        h = self.time.hour
        if h == 0:
            t = "12am"
        elif h < 12:
            t = f"{h}am"
        elif h == 12:
            t = "12pm"
        else:
            t = f"{h - 12}pm"
        return f"{self.date.strftime('%a %b')} {self.date.day}, {t}"

    def __repr__(self) -> str:
        return f"TimeSlot({self.date}, {self.time.strftime('%H:%M')})"


@dataclass
class ReviewerAvailability:
    reviewer_name: str
    availability: dict[TimeSlot, Availability]

    def __str__(self) -> str:
        yes   = sum(1 for a in self.availability.values() if a == Availability.YES)
        maybe = sum(1 for a in self.availability.values() if a == Availability.MAYBE)
        total = len(self.availability)
        return f"{self.reviewer_name} ({yes}Y / {maybe}M / {total} slots)"

    def __repr__(self) -> str:
        return f"ReviewerAvailability({self.reviewer_name!r}, {len(self.availability)} slots)"


@dataclass
class Reviewer:
    """Consolidated reviewer after cross-source matching."""
    reviewer_id: int
    canonical_name: str
    assignment: Optional[AssignmentReviewer] = None  # None if not in HotCRP
    schedule_name: Optional[str] = None              # None if not in schedule

    def __str__(self) -> str:
        name = f"[{self.reviewer_id}] {self.canonical_name}"
        if self.assignment:
            return f"{name} <{self.assignment.email}>"
        return name

    def __repr__(self) -> str:
        email = self.assignment.email if self.assignment else None
        return (f"Reviewer(id={self.reviewer_id}, name={self.canonical_name!r}, "
                f"email={email!r}, in_schedule={self.schedule_name is not None})")


# ---------------------------------------------------------------------------
# Name matching utilities
# ---------------------------------------------------------------------------

def _normalize_name(name: str) -> str:
    """Lowercase, ASCII-fold accents, strip non-alpha chars for comparison."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z\s]", "", ascii_name.lower()).strip()


def _tokens(name: str) -> list[str]:
    return _normalize_name(name).split()


def _match_score(a: str, b: str) -> float:
    """
    Return a similarity score [0, 1].

    Matching strategy (in order of confidence):
      1.0 — exact normalized match
      0.95 — last token same + first token same
      0.85 — last token same + first token starts with same letter
              (handles middle initials, e.g. "Jane A. B. Smith" vs "Jane Smith")
      0.0–0.8 — SequenceMatcher fallback; only treated as a match above 0.75
    """
    na, nb = _normalize_name(a), _normalize_name(b)
    if na == nb:
        return 1.0

    ta, tb = _tokens(a), _tokens(b)
    if ta and tb and ta[-1] == tb[-1]:   # last names match
        if ta[0] == tb[0]:               # first names match exactly
            return 0.95
        if ta[0] and tb[0] and ta[0][0] == tb[0][0]:  # first initials match
            return 0.85

    return difflib.SequenceMatcher(None, na, nb).ratio()


_MATCH_THRESHOLD = 0.75


# ---------------------------------------------------------------------------
# Matching and reporting
# ---------------------------------------------------------------------------

def match_reviewers(
    assignment_revs: list[AssignmentReviewer],
    schedule_names: list[str],
) -> list[Reviewer]:
    """
    Fuzzy-match two reviewer lists and return a consolidated list.

    Each returned Reviewer has a unique integer ID. If matched, both
    `assignment` and `schedule_name` are set; otherwise only one is.

    Uses greedy assignment (highest confidence first) so that an exact match
    like "Sam Chen"↔"Sam Chen" cannot be stolen by a weaker fuzzy match
    like "Chen Wu"↔"Sam Chen".
    """
    # Collect all pairs that exceed the threshold.
    candidates: list[tuple[float, AssignmentReviewer, str]] = []
    for arv in assignment_revs:
        for schedule_name in schedule_names:
            score = _match_score(arv.display_name, schedule_name)
            if score >= _MATCH_THRESHOLD:
                candidates.append((score, arv, schedule_name))

    # Greedy assignment: highest-confidence pairs claimed first.
    candidates.sort(key=lambda x: -x[0])
    matched_emails: set[str] = set()
    matched_schedule: set[str] = set()
    pair_by_email: dict[str, str] = {}

    for score, arv, schedule_name in candidates:
        if arv.email not in matched_emails and schedule_name not in matched_schedule:
            pair_by_email[arv.email] = schedule_name
            matched_emails.add(arv.email)
            matched_schedule.add(schedule_name)

    # Build Reviewer objects preserving assignment order, then schedule-only.
    reviewers: list[Reviewer] = []
    next_id = 1

    for arv in assignment_revs:
        schedule_name = pair_by_email.get(arv.email)
        reviewers.append(Reviewer(
            reviewer_id=next_id,
            canonical_name=arv.display_name,
            assignment=arv,
            schedule_name=schedule_name,
        ))
        next_id += 1

    for schedule_name in schedule_names:
        if schedule_name not in matched_schedule:
            reviewers.append(Reviewer(
                reviewer_id=next_id,
                canonical_name=schedule_name,
                schedule_name=schedule_name,
            ))
            next_id += 1

    return reviewers


def print_reviewer_report(reviewers: list[Reviewer]) -> None:
    only_assignment = [r for r in reviewers if r.assignment and not r.schedule_name]
    only_schedule   = [r for r in reviewers if r.schedule_name and not r.assignment]
    in_both         = [r for r in reviewers if r.assignment and r.schedule_name]

    print(f"\n=== Reviewers only in assignments ({len(only_assignment)}) ===")
    for r in sorted(only_assignment, key=lambda x: x.canonical_name):
        assert r.assignment is not None
        print(f"  [{r.reviewer_id:3d}] {r.assignment.display_name} <{r.assignment.email}>")

    print(f"\n=== Reviewers only in schedule ({len(only_schedule)}) ===")
    for r in sorted(only_schedule, key=lambda x: x.canonical_name):
        print(f"  [{r.reviewer_id:3d}] {r.schedule_name}")

    print(f"\n=== Reviewers in both ({len(in_both)}) ===")
    for r in sorted(in_both, key=lambda x: x.canonical_name):
        assert r.assignment is not None
        same = r.assignment.display_name == r.schedule_name
        print(f"  [{r.reviewer_id:3d}] {r.canonical_name}")
        if not same:
            print(f"         assignments: {r.assignment.display_name!r} <{r.assignment.email}>")
            print(f"         schedule:    {r.schedule_name!r}")
        else:
            print(f"         <{r.assignment.email}>")

    print(f"\nTotal: {len(reviewers)} reviewers "
          f"({len(in_both)} matched, {len(only_assignment)} assignments-only, "
          f"{len(only_schedule)} schedule-only)")
