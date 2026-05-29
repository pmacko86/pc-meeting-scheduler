"""Hill-climbing scheduler: local search from multiple seeds."""

import random
from typing import Any, Optional

from config import Config
from papers import Paper
from reviewers import Reviewer, ReviewerAvailability
from schedule import SLOT_DURATION_MINUTES, SchedulingPreferences
from scheduler import (
    ScheduleResult,
    SchedulingAlgorithm,
    _generate_candidates,
    _has_tag,
    _viable_sessions,
)
from .greedy import GreedyScheduler
from .population import (
    Assignment,
    FitnessFunction,
    _assignment_to_result,
    _random_seed,
    _result_to_assignment,
    compute_reviewer_counts,
    make_weighted_fitness,
)
from .session_first import SessionFirstScheduler


# ---------------------------------------------------------------------------
# Hill climbing
# ---------------------------------------------------------------------------

def _hill_climb(
    assignment: Assignment,
    fitness_fn: FitnessFunction,
    all_slot_keys: list[tuple],
    papers_per_session: int,
    max_iterations: int,
) -> tuple[Assignment, Any]:
    """Improve an assignment by accepting the best neighboring move each step.

    Neighbors:
    - Relocate: move one paper to a different (non-full) slot
    - Swap: exchange two papers between different slots (no capacity issue)

    Uses in-place temporary mutation to avoid dict copies during evaluation.
    Returns (best_assignment, best_fitness).
    """
    current = dict(assignment)
    current_fitness = fitness_fn(current)
    pids = list(current.keys())

    for _ in range(max_iterations):
        slot_counts: dict[tuple, int] = {}
        for slot_key in current.values():
            if slot_key is not None:
                slot_counts[slot_key] = slot_counts.get(slot_key, 0) + 1

        best_fitness = current_fitness
        best_relocate: Optional[tuple[int, Optional[tuple]]] = None
        best_swap: Optional[tuple[int, int]] = None

        # --- Relocate moves ---
        for pid in pids:
            old_slot = current[pid]
            for new_slot in all_slot_keys:
                if new_slot == old_slot:
                    continue
                if slot_counts.get(new_slot, 0) >= papers_per_session:
                    continue
                current[pid] = new_slot
                fit = fitness_fn(current)
                current[pid] = old_slot
                if fit < best_fitness:
                    best_fitness = fit
                    best_relocate = (pid, new_slot)
                    best_swap = None

        # --- Swap moves ---
        for i, pid1 in enumerate(pids):
            for pid2 in pids[i + 1:]:
                if current[pid1] == current[pid2]:
                    continue
                current[pid1], current[pid2] = current[pid2], current[pid1]
                fit = fitness_fn(current)
                current[pid1], current[pid2] = current[pid2], current[pid1]
                if fit < best_fitness:
                    best_fitness = fit
                    best_swap = (pid1, pid2)
                    best_relocate = None

        if best_relocate is None and best_swap is None:
            break  # local optimum

        if best_relocate is not None:
            pid, new_slot = best_relocate
            current[pid] = new_slot
        else:
            assert best_swap is not None
            pid1, pid2 = best_swap
            current[pid1], current[pid2] = current[pid2], current[pid1]

        current_fitness = best_fitness

    return current, current_fitness


# ---------------------------------------------------------------------------
# Algorithm
# ---------------------------------------------------------------------------

class HillClimbingScheduler(SchedulingAlgorithm):
    """Local-search scheduler: hill-climbing from multiple seeds.

    Seeds:
      - Greedy result (most-constrained-first)
      - Session-first result
      - n_random_seeds random assignments (shuffled greedy order)

    Each seed is hill-climbed by trying every single-paper relocation and
    every pairwise paper swap, accepting whichever move most improves the
    fitness at each step.  The algorithm stops when no improving move exists
    (local optimum) or max_iterations is reached.

    Fitness: weighted scalar — reviewer attendance weighted most highly,
    then session count, then sessions below minimum.
    """

    def __init__(self, n_random_seeds: int = 3, max_iterations: int = 200):
        self.n_random_seeds = n_random_seeds
        self.max_iterations = max_iterations

    def schedule(
        self,
        papers: list[Paper],
        prefs: SchedulingPreferences,
        reviewers: list[Reviewer],
        config: Config,
    ) -> ScheduleResult:
        reviewer_by_email: dict[str, Reviewer] = {
            rv.assignment.email: rv for rv in reviewers if rv.assignment
        }
        avail_by_name: dict[str, ReviewerAvailability] = {
            ra.reviewer_name: ra for ra in prefs.reviewers
        }

        # --- Filter ---
        to_schedule: list[Paper] = []
        skipped: list[Paper] = []
        for paper in papers:
            (skipped if _has_tag(paper, config.skip_tags) else to_schedule).append(paper)

        n_slots = max(1, round(config.session_length / SLOT_DURATION_MINUTES))
        candidates = _generate_candidates(prefs.slots, n_slots)

        if not candidates:
            return ScheduleResult(
                sessions=[],
                skipped_papers=skipped,
                unscheduled_papers=[(p, "no time slots available") for p in to_schedule],
            )

        candidate_by_key = {tuple(c): c for c in candidates}
        all_slot_keys = list(candidate_by_key.keys())
        paper_by_pid = {p.pid: p for p in to_schedule}
        all_pids = set(paper_by_pid)

        viable_keys: dict[int, set[tuple]] = {
            p.pid: {
                tuple(s)
                for s, _, _ in _viable_sessions(
                    p, candidates, reviewer_by_email, avail_by_name,
                    config.min_reviewers_per_slot,
                )
            }
            for p in to_schedule
        }

        reviewer_counts, total_matched = compute_reviewer_counts(
            to_schedule, candidates, reviewer_by_email, avail_by_name,
        )
        fitness_fn = make_weighted_fitness(
            reviewer_counts, total_matched, config.min_papers_per_session,
        )

        # --- Generate seeds ---
        seeds: list[Assignment] = []
        for Scheduler in (GreedyScheduler, SessionFirstScheduler):
            result = Scheduler().schedule(papers, prefs, reviewers, config)
            seeds.append(_result_to_assignment(result, all_pids))
        for i in range(self.n_random_seeds):
            seeds.append(_random_seed(
                list(all_pids), viable_keys, all_slot_keys,
                config.papers_per_session, random.Random(i),
            ))

        # --- Hill-climb each seed; keep the best result ---
        best_assignment: Optional[Assignment] = None
        best_fitness: Optional[Any] = None
        for seed in seeds:
            assignment, fitness = _hill_climb(
                seed, fitness_fn, all_slot_keys,
                config.papers_per_session, self.max_iterations,
            )
            if best_fitness is None or fitness < best_fitness:
                best_assignment = assignment
                best_fitness = fitness

        assert best_assignment is not None
        return _assignment_to_result(
            best_assignment, paper_by_pid, viable_keys,
            candidate_by_key, skipped, config.papers_per_session,
        )
