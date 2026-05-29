"""Population-based scheduler utilities.

Shared by hill-climbing, genetic, and any future population schedulers.
"""

import random
from collections import defaultdict
from typing import Any, Callable, Optional

from papers import Paper
from reviewers import Reviewer, ReviewerAvailability, TimeSlot
from scheduler import (
    ScheduleResult,
    ScheduledPaper,
    ScheduledSession,
    _score_paper_in_session,
)

FitnessFunction = Callable[["Assignment"], Any]

# pid → candidate slot key (tuple of TimeSlots), or None if unscheduled
Assignment = dict[int, Optional[tuple]]


def _fitness(
    assignment: Assignment,
    viable_keys: dict[int, set[tuple]],
    min_papers_per_session: int,
) -> tuple:
    """Fitness tuple (lower is better, compared lexicographically).

    Components:
      1. unscheduled papers  — must reach zero before anything else matters
      2. number of sessions  — primary minimisation target
      3. best-effort papers  — placed below reviewer threshold
      4. sessions below min  — under-filled sessions
    """
    slot_counts: dict[tuple, int] = {}
    n_best_effort = 0
    n_unscheduled = 0
    for pid, slot_key in assignment.items():
        if slot_key is None:
            n_unscheduled += 1
        else:
            slot_counts[slot_key] = slot_counts.get(slot_key, 0) + 1
            if slot_key not in viable_keys.get(pid, set()):
                n_best_effort += 1
    n_sessions = len(slot_counts)
    n_below_min = sum(1 for c in slot_counts.values() if c < min_papers_per_session)
    return (n_unscheduled, n_sessions, n_best_effort, n_below_min)


def _distance(a1: Assignment, a2: Assignment) -> int:
    """Hamming distance: number of papers assigned to different slots."""
    return sum(1 for pid in a1 if a1.get(pid) != a2.get(pid))


# ---------------------------------------------------------------------------
# Fitness factories
# ---------------------------------------------------------------------------

def compute_reviewer_counts(
    papers: list[Paper],
    candidates: list[list[TimeSlot]],
    reviewer_by_email: dict[str, Reviewer],
    avail_by_name: dict[str, ReviewerAvailability],
) -> tuple[dict[int, dict[tuple, int]], dict[int, int]]:
    """Pre-compute reviewer availability counts for every (paper, candidate) pair.

    Returns:
      reviewer_counts  — pid → slot_key → number of matched reviewers available
      total_matched    — pid → total number of matched reviewers for the paper
    """
    reviewer_counts: dict[int, dict[tuple, int]] = {}
    total_matched: dict[int, int] = {}
    for paper in papers:
        matched = sum(
            1 for arv in paper.reviewers
            if arv.email in reviewer_by_email
            and reviewer_by_email[arv.email].schedule_name is not None
        )
        total_matched[paper.pid] = matched
        reviewer_counts[paper.pid] = {
            tuple(cand): len(_score_paper_in_session(
                paper, cand, reviewer_by_email, avail_by_name
            )[0])
            for cand in candidates
        }
    return reviewer_counts, total_matched


def make_tuple_fitness(
    viable_keys: dict[int, set[tuple]],
    min_papers_per_session: int,
) -> FitnessFunction:
    """Original tuple fitness — lexicographic comparison, sessions prioritized.

    Components (lower is better):
      1. unscheduled papers
      2. number of sessions
      3. best-effort placements (below reviewer threshold)
      4. sessions below min_papers_per_session
    """
    def _fn(assignment: Assignment) -> tuple:
        slot_counts: dict[tuple, int] = {}
        n_best_effort = 0
        n_unscheduled = 0
        for pid, slot_key in assignment.items():
            if slot_key is None:
                n_unscheduled += 1
            else:
                slot_counts[slot_key] = slot_counts.get(slot_key, 0) + 1
                if slot_key not in viable_keys.get(pid, set()):
                    n_best_effort += 1
        n_sessions = len(slot_counts)
        n_below_min = sum(1 for c in slot_counts.values() if c < min_papers_per_session)
        return (n_unscheduled, n_sessions, n_best_effort, n_below_min)
    return _fn


def make_weighted_fitness(
    reviewer_counts: dict[int, dict[tuple, int]],
    total_matched: dict[int, int],
    min_papers_per_session: int,
    w_missing_reviewer: float = 100.0,
    w_session: float = 10.0,
    w_below_min: float = 1.0,
    w_unscheduled: float = 10000.0,
) -> FitnessFunction:
    """Weighted scalar fitness — reviewer attendance has the highest weight.

    Each matched reviewer who cannot attend their paper's session costs
    w_missing_reviewer points; this outweighs all other objectives so the
    algorithm concentrates first on maximising reviewer coverage, then on
    minimising session count.

    Score (lower is better):
      w_unscheduled   × unscheduled papers
      w_missing_reviewer × total missed reviewer–paper pairs
      w_session       × number of sessions
      w_below_min     × sessions below min_papers_per_session
    """
    def _fn(assignment: Assignment) -> float:
        slot_counts: dict[tuple, int] = {}
        total_missing = 0
        n_unscheduled = 0
        for pid, slot_key in assignment.items():
            if slot_key is None:
                n_unscheduled += 1
            else:
                slot_counts[slot_key] = slot_counts.get(slot_key, 0) + 1
                available = reviewer_counts.get(pid, {}).get(slot_key, 0)
                total_missing += total_matched.get(pid, 0) - available
        n_sessions = len(slot_counts)
        n_below_min = sum(1 for c in slot_counts.values() if c < min_papers_per_session)
        return (
            w_unscheduled * n_unscheduled
            + w_missing_reviewer * total_missing
            + w_session * n_sessions
            + w_below_min * n_below_min
        )
    return _fn


def _result_to_assignment(result: ScheduleResult, all_pids: set[int]) -> Assignment:
    """Convert a ScheduleResult to an Assignment dict."""
    assignment: Assignment = {}
    for sess in result.sessions:
        key = tuple(sess.slots)
        for sp in sess.papers:
            assignment[sp.paper.pid] = key
    for paper, _ in result.unscheduled_papers:
        assignment[paper.pid] = None
    for pid in all_pids:
        assignment.setdefault(pid, None)
    return assignment


def _random_seed(
    pids: list[int],
    viable_keys: dict[int, set[tuple]],
    all_slot_keys: list[tuple],
    papers_per_session: int,
    rng: random.Random,
) -> Assignment:
    """Random assignment: shuffle paper order, then place greedily."""
    slot_counts: dict[tuple, int] = {}
    assignment: Assignment = {}
    order = list(pids)
    rng.shuffle(order)
    for pid in order:
        viable = list(viable_keys.get(pid, set()))
        rng.shuffle(viable)
        placed = False
        for slot_key in viable:
            if slot_counts.get(slot_key, 0) < papers_per_session:
                assignment[pid] = slot_key
                slot_counts[slot_key] = slot_counts.get(slot_key, 0) + 1
                placed = True
                break
        if not placed:
            rest = list(all_slot_keys)
            rng.shuffle(rest)
            for slot_key in rest:
                if slot_counts.get(slot_key, 0) < papers_per_session:
                    assignment[pid] = slot_key
                    slot_counts[slot_key] = slot_counts.get(slot_key, 0) + 1
                    placed = True
                    break
        if not placed:
            assignment[pid] = None
    return assignment


def _assignment_to_result(
    assignment: Assignment,
    paper_by_pid: dict[int, Paper],
    viable_keys: dict[int, set[tuple]],
    candidate_by_key: dict[tuple, list[TimeSlot]],
    skipped: list[Paper],
    papers_per_session: int,
) -> ScheduleResult:
    """Convert an Assignment back to a ScheduleResult."""
    slot_to_pids: dict[tuple, list[int]] = defaultdict(list)
    unscheduled_pids: list[int] = []
    for pid, slot_key in assignment.items():
        if slot_key is None:
            unscheduled_pids.append(pid)
        else:
            slot_to_pids[slot_key].append(pid)
    sessions: list[ScheduledSession] = []
    for slot_key, pids in slot_to_pids.items():
        sess = ScheduledSession(
            session_id=0,
            slots=candidate_by_key[slot_key],
            capacity_papers=papers_per_session,
        )
        for pid in sorted(pids):
            sess.papers.append(ScheduledPaper(
                paper=paper_by_pid[pid],
                best_effort=(slot_key not in viable_keys.get(pid, set())),
            ))
        sessions.append(sess)
    sessions.sort(key=lambda s: (s.slots[0].date, s.slots[0].time))
    for i, sess in enumerate(sessions, 1):
        sess.session_id = i
    return ScheduleResult(
        sessions=sessions,
        skipped_papers=skipped,
        unscheduled_papers=[
            (paper_by_pid[pid], "no available time slots")
            for pid in sorted(unscheduled_pids)
        ],
    )
