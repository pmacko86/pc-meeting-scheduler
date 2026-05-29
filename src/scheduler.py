"""Scheduling result types, abstract algorithm interface, and report printer."""

import csv
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from config import Config
from papers import Paper
from reviewers import Availability, Reviewer, ReviewerAvailability, TimeSlot
from schedule import SchedulingPreferences


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ScheduledPaper:
    paper: Paper
    best_effort: bool = False             # True when placed below reviewer threshold
    # Populated by compute_reviewer_coverage() after scheduling:
    available_reviewers: list[Reviewer]   = field(default_factory=list)
    missing_reviewers: list[Reviewer]     = field(default_factory=list)
    unmatched_count: int = 0

    def __str__(self) -> str:
        return f"#{self.paper.pid}: {self.paper.title}"

    def __repr__(self) -> str:
        return (f"ScheduledPaper(pid={self.paper.pid}, "
                f"available={len(self.available_reviewers)}, "
                f"missing={len(self.missing_reviewers)}, "
                f"unmatched={self.unmatched_count}, "
                f"best_effort={self.best_effort})")


@dataclass
class ScheduledSession:
    session_id: int
    slots: list[TimeSlot]
    capacity_papers: int
    papers: list[ScheduledPaper] = field(default_factory=list)

    @property
    def remaining_papers(self) -> int:
        return self.capacity_papers - len(self.papers)

    def __str__(self) -> str:
        start = self.slots[0]
        h_end = self.slots[-1].time.hour + 1
        return (f"Session {self.session_id}: "
                f"{start.date.strftime('%a %b')} {start.date.day}, "
                f"{_fmt_hour(start.time.hour)}–{_fmt_hour(h_end)}")

    def __repr__(self) -> str:
        return (f"ScheduledSession(id={self.session_id}, start={self.slots[0]!r}, "
                f"papers={len(self.papers)}/{self.capacity_papers})")


@dataclass
class ScheduleResult:
    sessions: list[ScheduledSession]
    skipped_papers: list[Paper]
    unscheduled_papers: list[tuple[Paper, str]]   # (paper, reason)

    def __repr__(self) -> str:
        return (f"ScheduleResult(sessions={len(self.sessions)}, "
                f"skipped={len(self.skipped_papers)}, "
                f"unscheduled={len(self.unscheduled_papers)})")


# ---------------------------------------------------------------------------
# Abstract algorithm interface
# ---------------------------------------------------------------------------

class SchedulingAlgorithm(ABC):
    """Abstract base class for PC meeting scheduling algorithms.

    Subclass this and implement :meth:`schedule` to plug in a different
    assignment strategy without touching the rest of the pipeline.
    """

    @abstractmethod
    def schedule(
        self,
        papers: list[Paper],
        prefs: SchedulingPreferences,
        reviewers: list[Reviewer],
        config: Config,
    ) -> ScheduleResult:
        """Assign papers to sessions and return the result."""
        ...


# ---------------------------------------------------------------------------
# Shared utilities (used by algorithms and the report)
# ---------------------------------------------------------------------------

def _fmt_hour(h: int) -> str:
    if h == 0:   return "12am"
    if h < 12:   return f"{h}am"
    if h == 12:  return "12pm"
    return f"{h - 12}pm"


def _tag_base(tag: str) -> str:
    """Strip HotCRP '#weight' suffix: 'pre-accept#0' → 'pre-accept'."""
    return tag.split('#')[0]


def _has_tag(paper: Paper, tag_names: list[str]) -> bool:
    tag_set = {_tag_base(t) for t in paper.tags}
    return bool(tag_set & set(tag_names))


def _slots_consecutive(slots: list[TimeSlot]) -> bool:
    for i in range(1, len(slots)):
        prev, cur = slots[i - 1], slots[i]
        if cur.date != prev.date or cur.time.hour != prev.time.hour + 1:
            return False
    return True


def _generate_candidates(slots: list[TimeSlot], n: int) -> list[list[TimeSlot]]:
    """All windows of n consecutive same-day hourly slots.

    Sessions may start at any hour (8am, 9am, 10am, 11am, …), not just even
    hours.  The algorithm picks among all of these based on reviewer fit.
    """
    return [
        slots[i:i + n]
        for i in range(len(slots) - n + 1)
        if _slots_consecutive(slots[i:i + n])
    ]


def _reviewer_present(ra: ReviewerAvailability, session_slots: list[TimeSlot]) -> bool:
    """True if the reviewer is YES or MAYBE for every slot in the session."""
    return all(
        ra.availability.get(slot, Availability.UNKNOWN)
        in (Availability.YES, Availability.MAYBE)
        for slot in session_slots
    )


def _score_paper_in_session(
    paper: Paper,
    session_slots: list[TimeSlot],
    reviewer_by_email: dict[str, Reviewer],
    avail_by_name: dict[str, ReviewerAvailability],
) -> tuple[list[Reviewer], list[Reviewer]]:
    """Return (available, missing) matched reviewers for a paper in a session.

    Reviewers with no schedule entry are excluded from both lists (per config
    description: 'reviewers not matched in the schedule are ignored').
    """
    available: list[Reviewer] = []
    missing: list[Reviewer] = []
    for arv in paper.reviewers:
        rv = reviewer_by_email.get(arv.email)
        if rv is None or rv.schedule_name is None:
            continue
        ra = avail_by_name.get(rv.schedule_name)
        if ra is not None and _reviewer_present(ra, session_slots):
            available.append(rv)
        else:
            missing.append(rv)
    return available, missing


def _viable_sessions(
    paper: Paper,
    candidates: list[list[TimeSlot]],
    reviewer_by_email: dict[str, Reviewer],
    avail_by_name: dict[str, ReviewerAvailability],
    min_reviewers: int,
) -> list[tuple[list[TimeSlot], list[Reviewer], list[Reviewer]]]:
    """Candidate sessions where the paper has enough reviewers available.

    Threshold: min(total_matched, min_reviewers) so papers with fewer matched
    reviewers require all of them to be present.
    """
    result = []
    for session_slots in candidates:
        available, missing = _score_paper_in_session(
            paper, session_slots, reviewer_by_email, avail_by_name
        )
        total = len(available) + len(missing)
        threshold = min(total, min_reviewers)
        if len(available) >= threshold:
            result.append((session_slots, available, missing))
    return result


def _score_all_candidates(
    paper: Paper,
    candidates: list[list[TimeSlot]],
    reviewer_by_email: dict[str, Reviewer],
    avail_by_name: dict[str, ReviewerAvailability],
) -> list[tuple[list[TimeSlot], list[Reviewer], list[Reviewer]]]:
    """Score every candidate session without threshold filtering.

    Used for best-effort placement when no threshold-compliant session exists.
    """
    return [
        (s, *_score_paper_in_session(paper, s, reviewer_by_email, avail_by_name))
        for s in candidates
    ]


def _count_unmatched(paper: Paper, reviewer_by_email: dict[str, Reviewer]) -> int:
    """Number of paper reviewers who have no schedule entry."""
    return sum(
        1 for arv in paper.reviewers
        if arv.email not in reviewer_by_email
        or reviewer_by_email[arv.email].schedule_name is None
    )


def _best_effort_place(
    paper: Paper,
    candidates: list[list[TimeSlot]],
    open_sessions: list[ScheduledSession],
    used_slots: set[TimeSlot],
    reviewer_by_email: dict[str, Reviewer],
    avail_by_name: dict[str, ReviewerAvailability],
    capacity_papers: int,
) -> bool:
    """Place a paper in best-effort mode (below reviewer threshold).

    Picks the slot — existing open session or new — that maximises reviewer
    coverage.  An existing session is only reused when it matches the best
    available reviewer count from unused slots; otherwise a new session is
    opened so the paper lands in its most covered slot.

    Mutates open_sessions and used_slots if a new session is opened.
    Returns True if placed, False if no time slot was available at all.
    """
    all_map: dict[tuple, tuple[list[Reviewer], list[Reviewer]]] = {
        tuple(s): (av, mi)
        for s, av, mi in _score_all_candidates(paper, candidates, reviewer_by_email, avail_by_name)
    }
    unused_be = {
        k: v for k, v in all_map.items()
        if not any(slot in used_slots for slot in k)
    }
    best_unused_count = max((len(v[0]) for v in unused_be.values()), default=-1)

    fitting_be = [
        s for s in open_sessions
        if tuple(s.slots) in all_map
        and s.remaining_papers >= 1
        and len(all_map[tuple(s.slots)][0]) >= best_unused_count
    ]
    fitting_be.sort(key=lambda s: -len(all_map[tuple(s.slots)][0]))

    if fitting_be:
        fitting_be[0].papers.append(ScheduledPaper(paper=paper, best_effort=True))
        return True

    if unused_be:
        best_key = max(unused_be, key=lambda k: len(unused_be[k][0]))
        new_sess = ScheduledSession(
            session_id=0,  # renumbered at end of schedule()
            slots=list(best_key),
            capacity_papers=capacity_papers,
        )
        new_sess.papers.append(ScheduledPaper(paper=paper, best_effort=True))
        open_sessions.append(new_sess)
        used_slots.update(best_key)
        return True

    return False


# ---------------------------------------------------------------------------
# Post-scheduling reviewer analysis
# ---------------------------------------------------------------------------

def compute_reviewer_coverage(
    result: ScheduleResult,
    prefs: SchedulingPreferences,
    reviewers: list[Reviewer],
) -> None:
    """Fill reviewer availability fields on every ScheduledPaper in-place.

    Call this after scheduling and before printing the report.  Separating
    this step from the scheduling algorithm means any algorithm implementation
    gets reviewer analysis for free without reimplementing it.
    """
    reviewer_by_email = {rv.assignment.email: rv for rv in reviewers if rv.assignment}
    avail_by_name = {ra.reviewer_name: ra for ra in prefs.reviewers}

    for sess in result.sessions:
        for sp in sess.papers:
            av, mi = _score_paper_in_session(
                sp.paper, sess.slots, reviewer_by_email, avail_by_name
            )
            sp.available_reviewers = av
            sp.missing_reviewers = mi
            sp.unmatched_count = _count_unmatched(sp.paper, reviewer_by_email)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _has_any_availability(rv: Reviewer, prefs: SchedulingPreferences) -> bool:
    """True if the reviewer marked YES or MAYBE for at least one slot."""
    ra = next((r for r in prefs.reviewers if r.reviewer_name == rv.schedule_name), None)
    if ra is None:
        return False
    return any(
        a in (Availability.YES, Availability.MAYBE)
        for a in ra.availability.values()
    )


def _can_attend_any_session(rv: Reviewer, result: ScheduleResult, prefs: SchedulingPreferences) -> bool:
    """True if the reviewer can attend at least one session in the schedule."""
    ra = next((r for r in prefs.reviewers if r.reviewer_name == rv.schedule_name), None)
    if ra is None:
        return False
    return any(
        all(
            ra.availability.get(slot, Availability.UNKNOWN) in (Availability.YES, Availability.MAYBE)
            for slot in sess.slots
        )
        for sess in result.sessions
    )


def print_schedule_report(result: ScheduleResult, config: Config, prefs: SchedulingPreferences) -> None:
    total_assigned = sum(len(s.papers) for s in result.sessions)
    total_warnings = sum(
        1 for s in result.sessions for sp in s.papers if sp.missing_reviewers
    )
    print(f"\n=== Schedule: {len(result.sessions)} session(s), "
          f"{total_assigned} paper(s) assigned"
          + (f", {total_warnings} reviewer warning(s)" if total_warnings else "")
          + " ===")

    for sess in result.sessions:
        print(f"\n{sess}  [{len(sess.papers)}/{sess.capacity_papers} papers]")
        if len(sess.papers) < config.min_papers_per_session:
            print(f"  NOTE: only {len(sess.papers)} paper(s) — below minimum of {config.min_papers_per_session}")
        for sp in sess.papers:
            labels = []
            if _has_tag(sp.paper, config.attention_tags):
                labels.append("attention")
            if _has_tag(sp.paper, config.one_shot_tags):
                labels.append("one-shot")
            label_str = f" [{', '.join(labels)}]" if labels else ""

            title = sp.paper.title
            if len(title) > 52:
                title = title[:49] + "..."

            parts = []
            if sp.missing_reviewers:
                names = ", ".join(r.canonical_name for r in sp.missing_reviewers)
                parts.append(f"unavailable: {names}")
            if sp.unmatched_count:
                parts.append(f"{sp.unmatched_count} missing")
            status = "; ".join(parts) if parts else "all present"

            print(f"  #{sp.paper.pid:3d}  {title:52s}  [{status}]{label_str}")

    # --- Reviewer attendance summary ---
    # Three-way split of missing reviewers:
    #   - sched_conflicts: has availability AND can attend ≥1 scheduled session
    #                      → paper could be moved to a slot they can make
    #   - no_session_overlap: has some availability but none of the scheduled
    #                         sessions fall in slots they marked available
    #                         → rescheduling won't help without changing sessions
    #   - no_slots: marked themselves unavailable everywhere
    sched_conflicts: dict[str, list[tuple[ScheduledSession, ScheduledPaper]]] = {}
    no_session_overlap: dict[str, list[tuple[ScheduledSession, ScheduledPaper]]] = {}
    no_slots: dict[str, list[tuple[ScheduledSession, ScheduledPaper]]] = {}

    for sess in result.sessions:
        for sp in sess.papers:
            for rv in sp.missing_reviewers:
                if not _has_any_availability(rv, prefs):
                    bucket = no_slots
                elif _can_attend_any_session(rv, result, prefs):
                    bucket = sched_conflicts
                else:
                    bucket = no_session_overlap
                bucket.setdefault(rv.canonical_name, []).append((sess, sp))

    if not sched_conflicts and not no_session_overlap and not no_slots:
        print("\n=== Reviewer Attendance: all reviewers can attend their assigned sessions ===")
    else:
        total = len(sched_conflicts) + len(no_session_overlap) + len(no_slots)
        print(f"\n=== Reviewer Attendance Issues ({total} reviewer(s)) ===")

        if sched_conflicts:
            print(f"\n  Scheduling conflicts — available at other times, could attend a different session "
                  f"({len(sched_conflicts)} reviewer(s)):")
            for name in sorted(sched_conflicts):
                for sess, sp in sched_conflicts[name]:
                    title = sp.paper.title if len(sp.paper.title) <= 50 else sp.paper.title[:47] + "..."
                    print(f"    {name}  —  {sess}  /  #{sp.paper.pid}: {title}")

        if no_session_overlap:
            print(f"\n  Cannot attend any scheduled session — availability does not overlap "
                  f"with any session ({len(no_session_overlap)} reviewer(s)):")
            for name in sorted(no_session_overlap):
                pids = sorted({sp.paper.pid for _, sp in no_session_overlap[name]})
                print(f"    {name}  (papers: {', '.join(f'#{p}' for p in pids)})")

        if no_slots:
            print(f"\n  No available slots — reviewer marked no availability at all "
                  f"({len(no_slots)} reviewer(s)):")
            for name in sorted(no_slots):
                pids = sorted({sp.paper.pid for _, sp in no_slots[name]})
                print(f"    {name}  (papers: {', '.join(f'#{p}' for p in pids)})")

    if result.unscheduled_papers:
        print(f"\n=== Unscheduled Papers ({len(result.unscheduled_papers)}) ===")
        for paper, reason in result.unscheduled_papers:
            title = paper.title if len(paper.title) <= 60 else paper.title[:57] + "..."
            print(f"  #{paper.pid:3d}  {title}  [{reason}]")

    if result.skipped_papers:
        print(f"\n=== Skipped Papers ({len(result.skipped_papers)}) ===")
        for paper in result.skipped_papers:
            matching = sorted({_tag_base(t) for t in paper.tags} & set(config.skip_tags))
            title = paper.title if len(paper.title) <= 60 else paper.title[:57] + "..."
            print(f"  #{paper.pid:3d}  {title}  [tags: {', '.join(matching)}]")


def write_schedule_csv(result: ScheduleResult, path: Path) -> None:
    """Write the schedule to a CSV file (or stdout when path is '-'), one row per paper."""
    header = [
        "Session",
        "Paper ID",
        "Paper Title",
        "Available Reviewers",
        "Unavailable Reviewers",
        "Missing Scheduling Info",
    ]

    def _write(f) -> None:
        writer = csv.writer(f)
        writer.writerow(header)
        for sess in result.sessions:
            label = str(sess)
            for sp in sess.papers:
                writer.writerow([
                    label,
                    sp.paper.pid,
                    sp.paper.title,
                    len(sp.available_reviewers),
                    len(sp.missing_reviewers),
                    sp.unmatched_count,
                ])

    if str(path) == "-":
        _write(sys.stdout)
    else:
        with open(path, "w", newline="", encoding="utf-8") as f:
            _write(f)
