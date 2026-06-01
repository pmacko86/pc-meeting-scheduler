"""Scheduling result types, abstract algorithm interface, and report printer."""

import csv
import html as _html
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

    Picks the slot — existing open session or new — that maximizes reviewer
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
    import colors as C

    total_assigned = sum(len(s.papers) for s in result.sessions)
    total_warnings = sum(
        1 for s in result.sessions for sp in s.papers if sp.missing_reviewers
    )
    print(f"\n{C.HEADER}=== Schedule: {len(result.sessions)} session(s), "
          f"{total_assigned} paper(s) assigned"
          + (f", {total_warnings} reviewer warning(s)" if total_warnings else "")
          + f" ==={C.RESET}")

    for sess in result.sessions:
        print(f"\n{C.SESSION_TITLE}{sess}{C.RESET}  [{len(sess.papers)}/{sess.capacity_papers} papers]")
        if len(sess.papers) < config.min_papers_per_session:
            print(f"  {C.NOTE}NOTE: only {len(sess.papers)} paper(s) — "
                  f"below minimum of {config.min_papers_per_session}{C.RESET}")
        for sp in sess.papers:
            labels = []
            if _has_tag(sp.paper, config.attention_tags):
                labels.append(f"{C.LABEL_ATTN}attention{C.RESET}")
            if _has_tag(sp.paper, config.one_shot_tags):
                labels.append(f"{C.LABEL_SHOT}one-shot{C.RESET}")
            label_str = f" [{', '.join(labels)}]" if labels else ""

            title = sp.paper.title
            if len(title) > 52:
                title = title[:49] + "..."

            parts = []
            if sp.missing_reviewers:
                names = ", ".join(r.canonical_name for r in sp.missing_reviewers)
                parts.append(f"{C.WARN}unavailable: {names}{C.RESET}")
            if sp.unmatched_count:
                parts.append(f"{C.DIM_TEXT}{sp.unmatched_count} missing{C.RESET}")
            status = "; ".join(parts) if parts else f"{C.OK}all present{C.RESET}"
            if sp.best_effort:
                status = f"{C.BEST_EFFORT}best effort — {C.RESET}" + status

            print(f"  {C.PID}#{sp.paper.pid:3d}{C.RESET}  {title:52s}  [{status}]{label_str}")

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
        print(f"\n{C.HEADER}=== Reviewer Attendance: all reviewers can attend their assigned sessions ==={C.RESET}")
    else:
        total = len(sched_conflicts) + len(no_session_overlap) + len(no_slots)
        print(f"\n{C.HEADER}=== Reviewer Attendance Issues ({total} reviewer(s)) ==={C.RESET}")

        if sched_conflicts:
            print(f"\n  {C.CONFLICT}Scheduling conflicts{C.RESET} — available at other times, "
                  f"could attend a different session ({len(sched_conflicts)} reviewer(s)):")
            for name in sorted(sched_conflicts):
                for sess, sp in sched_conflicts[name]:
                    title = sp.paper.title if len(sp.paper.title) <= 50 else sp.paper.title[:47] + "..."
                    print(f"    {C.RV_NAME}{name}{C.RESET}  —  {sess}  /  #{sp.paper.pid}: {title}")

        if no_session_overlap:
            print(f"\n  {C.NO_OVERLAP}Cannot attend any scheduled session{C.RESET} — "
                  f"availability does not overlap with any session ({len(no_session_overlap)} reviewer(s)):")
            for name in sorted(no_session_overlap):
                pids = sorted({sp.paper.pid for _, sp in no_session_overlap[name]})
                print(f"    {C.RV_NAME}{name}{C.RESET}  (papers: {', '.join(f'#{p}' for p in pids)})")

        if no_slots:
            print(f"\n  {C.NO_SLOTS}No available slots{C.RESET} — reviewer marked no availability at all "
                  f"({len(no_slots)} reviewer(s)):")
            for name in sorted(no_slots):
                pids = sorted({sp.paper.pid for _, sp in no_slots[name]})
                print(f"    {C.RV_NAME}{name}{C.RESET}  (papers: {', '.join(f'#{p}' for p in pids)})")

    if result.unscheduled_papers:
        print(f"\n{C.UNSCHEDULED}=== Unscheduled Papers ({len(result.unscheduled_papers)}) ==={C.RESET}")
        for paper, reason in result.unscheduled_papers:
            title = paper.title if len(paper.title) <= 60 else paper.title[:57] + "..."
            print(f"  {C.PID}#{paper.pid:3d}{C.RESET}  {title}  [{reason}]")

    if result.skipped_papers:
        print(f"\n{C.SKIPPED}=== Skipped Papers ({len(result.skipped_papers)}) ==={C.RESET}")
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


_HTML_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f0f4f8; color: #2d3748; padding: 2rem 1rem;
}
.container { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 1.75rem; color: #1a365d; margin-bottom: 1.5rem; }
h2 { font-size: 1.1rem; font-weight: 700; color: #2d3748;
     text-transform: uppercase; letter-spacing: .06em;
     margin: 2rem 0 .75rem; padding-bottom: .3rem;
     border-bottom: 2px solid #e2e8f0; }
h3 { font-size: .8rem; font-weight: 700; text-transform: uppercase;
     letter-spacing: .06em; margin-bottom: .5rem;
     padding: .35rem .75rem; border-radius: .25rem; }

.summary {
    display: flex; gap: 2rem; flex-wrap: wrap;
    background: #2b6cb0; color: #fff;
    padding: 1rem 1.5rem; border-radius: .5rem; margin-bottom: 2rem;
}
.stat-value { font-size: 1.6rem; font-weight: 700; line-height: 1; }
.stat-label { font-size: .7rem; opacity: .8; text-transform: uppercase;
              letter-spacing: .06em; margin-top: .2rem; }

.session { background: #fff; border-radius: .5rem;
           box-shadow: 0 1px 4px rgba(0,0,0,.08); margin-bottom: 1.5rem;
           overflow: hidden; }
.session-header { background: #ebf8ff; border-bottom: 2px solid #bee3f8;
                  padding: .7rem 1.25rem;
                  display: flex; justify-content: space-between; align-items: center; }
.session-title { font-weight: 700; color: #2b6cb0; font-size: 1rem; }
.session-meta  { color: #4a5568; font-size: .8rem; }
.session-note  { background: #fffbeb; border-bottom: 1px solid #f6e05e;
                 padding: .35rem 1.25rem; font-size: .8rem; color: #744210; }

table.papers { width: 100%; border-collapse: collapse; }
table.papers thead tr { border-bottom: 2px solid #e2e8f0; }
table.papers th { background: #f7fafc; padding: .45rem .75rem; text-align: left;
                  font-size: .68rem; font-weight: 700; text-transform: uppercase;
                  letter-spacing: .06em; color: #718096; white-space: nowrap; }
table.papers td { padding: .45rem .75rem; font-size: .84rem;
                  border-bottom: 1px solid #f0f4f8; vertical-align: middle; }
table.papers tbody tr:last-child td { border-bottom: none; }
table.papers tbody tr:hover { background: #f7fafc; }

.pid   { font-family: monospace; color: #718096; white-space: nowrap; }
.title { }
th.num, td.num { text-align: center; }
.avail   { font-weight: 700; }
.avail.pos { color: #276749; }
.avail.zero { color: #a0aec0; }
.unavail { font-weight: 700; }
.unavail.pos  { color: #c05621; background: #fffbeb; border-radius: .25rem;
                padding: .1rem .4rem; }
.unavail.zero { color: #a0aec0; }
.missing      { color: #a0aec0; }
.missing.pos  { color: #718096; }

.lbl { display: inline-block; border-radius: .25rem;
       padding: .1rem .45rem; font-size: .68rem; font-weight: 700;
       text-transform: uppercase; letter-spacing: .04em; margin-right: .2rem; }
.lbl-attention  { background: #fefcbf; color: #744210; }
.lbl-one-shot   { background: #c6f6d5; color: #276749; }
.lbl-best-effort{ background: #fed7d7; color: #c53030; }

.attendance-group { background: #fff; border-radius: .5rem;
                    box-shadow: 0 1px 4px rgba(0,0,0,.08);
                    margin-bottom: 1rem; overflow: hidden; }
.attendance-group.conflicts   h3 { background: #fffbeb; color: #7b341e; }
.attendance-group.no-overlap  h3 { background: #fef3c7; color: #92400e; }
.attendance-group.no-slots    h3 { background: #fee2e2; color: #991b1b; }
.att-list { list-style: none; }
.att-list li { padding: .4rem 1rem; font-size: .84rem;
               border-bottom: 1px solid #f0f4f8; display: flex;
               gap: .75rem; align-items: baseline; }
.att-list li:last-child { border-bottom: none; }
.rv-name   { font-weight: 600; white-space: nowrap; }
.rv-detail { color: #718096; font-size: .8rem; }

.misc-list { list-style: none; background: #fff; border-radius: .5rem;
             box-shadow: 0 1px 4px rgba(0,0,0,.08); overflow: hidden;
             margin-bottom: 1rem; }
.misc-list li { padding: .5rem 1.25rem; font-size: .84rem;
                border-bottom: 1px solid #f0f4f8;
                display: flex; gap: 1rem; align-items: baseline; }
.misc-list li:last-child { border-bottom: none; }
.misc-reason { color: #718096; font-size: .8rem; }
.misc-tags   { color: #c05621; font-size: .78rem; }
.ok-msg { color: #276749; font-style: italic; margin-bottom: 1rem; }
"""


def write_schedule_html(
    result: ScheduleResult,
    config: Config,
    prefs: SchedulingPreferences,
    path: Path,
    include_details: bool = False,
    timezone: str = "",
) -> None:
    """Write the schedule as a self-contained HTML page (or stdout if path is '-')."""

    def e(s: object) -> str:
        return _html.escape(str(s))

    total_assigned = sum(len(s.papers) for s in result.sessions)
    total_warnings = sum(
        1 for s in result.sessions for sp in s.papers if sp.missing_reviewers
    )

    # Reviewer attendance groups (same logic as print_schedule_report)
    sched_conflicts:    dict[str, list[tuple[ScheduledSession, ScheduledPaper]]] = {}
    no_session_overlap: dict[str, list[tuple[ScheduledSession, ScheduledPaper]]] = {}
    no_slots:           dict[str, list[tuple[ScheduledSession, ScheduledPaper]]] = {}
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

    def cnt(n: int, cls: str) -> str:
        nz = "pos" if n > 0 else "zero"
        return f'<td class="num {cls} {nz}">{n}</td>'

    out: list[str] = []

    out.append(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>PC Meeting Schedule</title>
  <style>{_HTML_CSS}</style>
</head>
<body>
<div class="container">
<h1>PC Meeting Schedule</h1>
<div class="summary">
  <div class="stat"><div class="stat-value">{len(result.sessions)}</div>
    <div class="stat-label">Sessions</div></div>
  <div class="stat"><div class="stat-value">{total_assigned}</div>
    <div class="stat-label">Papers Assigned</div></div>
  <div class="stat"><div class="stat-value">{total_warnings}</div>
    <div class="stat-label">Reviewer Warnings</div></div>
  {f'<div class="stat"><div class="stat-value">{e(timezone)}</div><div class="stat-label">Timezone</div></div>' if timezone else ""}
</div>
""")

    # ── Sessions ──────────────────────────────────────────────────────────────
    out.append("<h2>Sessions</h2>\n")
    for sess in result.sessions:
        out.append(
            f'<div class="session">\n'
            f'  <div class="session-header">'
            f'<span class="session-title">{e(sess)}</span>'
            f'<span class="session-meta">'
            f'{len(sess.papers)}&nbsp;/&nbsp;{sess.capacity_papers} papers'
            f'</span></div>\n'
        )
        if len(sess.papers) < config.min_papers_per_session:
            out.append(
                f'  <div class="session-note">Only {len(sess.papers)} paper(s) '
                f'— below minimum of {config.min_papers_per_session}</div>\n'
            )
        out.append(
            '  <table class="papers">\n'
            '    <thead><tr>'
            '<th>#</th><th>Title</th>'
            '<th class="num">Available</th>'
            '<th class="num">Unavailable</th>'
            '<th class="num">Missing</th>'
            '<th>Labels</th>'
            '</tr></thead>\n    <tbody>\n'
        )
        for sp in sess.papers:
            labels: list[str] = []
            if _has_tag(sp.paper, config.attention_tags):
                labels.append('<span class="lbl lbl-attention">Attention</span>')
            if _has_tag(sp.paper, config.one_shot_tags):
                labels.append('<span class="lbl lbl-one-shot">One-shot</span>')
            if sp.best_effort and include_details:
                labels.append('<span class="lbl lbl-best-effort">Best effort</span>')
            out.append(
                f'      <tr>'
                f'<td class="pid">#{sp.paper.pid}</td>'
                f'<td class="title">{e(sp.paper.title)}</td>'
                + cnt(len(sp.available_reviewers), "avail")
                + cnt(len(sp.missing_reviewers),   "unavail")
                + cnt(sp.unmatched_count,            "missing")
                + f'<td class="labels">{"".join(labels)}</td>'
                f'</tr>\n'
            )
        out.append('    </tbody>\n  </table>\n</div>\n')

    # ── Reviewer attendance ───────────────────────────────────────────────────
    if not include_details:
        out.append('</div>\n</body>\n</html>\n')
        content = "".join(out)
        if str(path) == "-":
            sys.stdout.write(content)
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        return

    out.append("<h2>Reviewer Attendance</h2>\n")
    if not sched_conflicts and not no_session_overlap and not no_slots:
        out.append('<p class="ok-msg">All reviewers can attend their assigned sessions.</p>\n')
    else:
        def _att_group(bucket, css_cls, title):
            if not bucket:
                return
            out.append(
                f'<div class="attendance-group {css_cls}">\n'
                f'  <h3>{e(title)} ({len(bucket)})</h3>\n'
                f'  <ul class="att-list">\n'
            )
            for name in sorted(bucket):
                for sess, sp in bucket[name]:
                    title_short = sp.paper.title if len(sp.paper.title) <= 55 else sp.paper.title[:52] + "…"
                    out.append(
                        f'    <li><span class="rv-name">{e(name)}</span>'
                        f'<span class="rv-detail">{e(sess)}'
                        f' &mdash; #{sp.paper.pid}: {e(title_short)}'
                        f'</span></li>\n'
                    )
            out.append('  </ul>\n</div>\n')

        _att_group(sched_conflicts,   "conflicts",
                   "Scheduling conflicts — available at other times, could attend a different session")
        _att_group(no_session_overlap, "no-overlap",
                   "Cannot attend any scheduled session — availability does not overlap with any session")
        _att_group(no_slots,          "no-slots",
                   "No available slots — reviewer marked no availability at all")

    # ── Unscheduled ───────────────────────────────────────────────────────────
    if result.unscheduled_papers:
        out.append(f"<h2>Unscheduled Papers ({len(result.unscheduled_papers)})</h2>\n"
                   '<ul class="misc-list">\n')
        for paper, reason in result.unscheduled_papers:
            out.append(
                f'  <li><span class="pid">#{paper.pid}</span>'
                f'<span>{e(paper.title)}</span>'
                f'<span class="misc-reason">{e(reason)}</span></li>\n'
            )
        out.append('</ul>\n')

    # ── Skipped ───────────────────────────────────────────────────────────────
    if result.skipped_papers:
        out.append(f"<h2>Skipped Papers ({len(result.skipped_papers)})</h2>\n"
                   '<ul class="misc-list">\n')
        for paper in result.skipped_papers:
            matching = sorted({_tag_base(t) for t in paper.tags} & set(config.skip_tags))
            out.append(
                f'  <li><span class="pid">#{paper.pid}</span>'
                f'<span>{e(paper.title)}</span>'
                f'<span class="misc-tags">{e(", ".join(matching))}</span></li>\n'
            )
        out.append('</ul>\n')

    out.append('</div>\n</body>\n</html>\n')
    content = "".join(out)

    if str(path) == "-":
        sys.stdout.write(content)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
