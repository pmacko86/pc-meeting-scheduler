"""Genetic algorithm scheduler with deterministic niching."""

import random
from typing import Optional

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
    _distance,
    _random_seed,
    _result_to_assignment,
    compute_reviewer_counts,
    make_weighted_fitness,
)
from .session_first import SessionFirstScheduler


# ---------------------------------------------------------------------------
# Genetic operators
# ---------------------------------------------------------------------------

def _crossover(
    parent1: Assignment,
    parent2: Assignment,
    rng: random.Random,
) -> Assignment:
    """Uniform crossover: each paper inherits its slot from either parent."""
    return {
        pid: (parent1[pid] if rng.random() < 0.5 else parent2[pid])
        for pid in parent1
    }


def _repair(
    assignment: Assignment,
    papers_per_session: int,
    viable_keys: dict[int, set[tuple]],
    all_slot_keys: list[tuple],
    rng: random.Random,
) -> Assignment:
    """Restore capacity constraints violated by crossover.

    Over-capacity slots have their excess papers evicted at random, then
    the evicted papers are greedily reassigned to viable slots with space.
    """
    result = dict(assignment)

    # Group papers by slot to find over-capacity violations.
    slot_to_pids: dict[Optional[tuple], list[int]] = {}
    for pid, slot in result.items():
        slot_to_pids.setdefault(slot, []).append(pid)

    overflow: list[int] = []
    for slot, pids in slot_to_pids.items():
        if slot is None or len(pids) <= papers_per_session:
            continue
        rng.shuffle(pids)
        for pid in pids[papers_per_session:]:
            result[pid] = None
            overflow.append(pid)

    if not overflow:
        return result

    slot_counts: dict[tuple, int] = {}
    for pid, slot in result.items():
        if slot is not None:
            slot_counts[slot] = slot_counts.get(slot, 0) + 1

    rng.shuffle(overflow)
    for pid in overflow:
        viable = list(viable_keys.get(pid, set()))
        rng.shuffle(viable)
        placed = False
        for slot in viable:
            if slot_counts.get(slot, 0) < papers_per_session:
                result[pid] = slot
                slot_counts[slot] = slot_counts.get(slot, 0) + 1
                placed = True
                break
        if not placed:
            rest = list(all_slot_keys)
            rng.shuffle(rest)
            for slot in rest:
                if slot_counts.get(slot, 0) < papers_per_session:
                    result[pid] = slot
                    slot_counts[slot] = slot_counts.get(slot, 0) + 1
                    placed = True
                    break
        if not placed:
            result[pid] = None

    return result


def _mutate(
    assignment: Assignment,
    viable_keys: dict[int, set[tuple]],
    all_slot_keys: list[tuple],
    papers_per_session: int,
    mutation_rate: float,
    rng: random.Random,
) -> Assignment:
    """Randomly relocate each paper with probability mutation_rate."""
    result = dict(assignment)
    slot_counts: dict[tuple, int] = {}
    for slot in result.values():
        if slot is not None:
            slot_counts[slot] = slot_counts.get(slot, 0) + 1

    for pid in list(result.keys()):
        if rng.random() >= mutation_rate:
            continue
        old_slot = result[pid]
        if old_slot is not None:
            slot_counts[old_slot] -= 1

        viable = list(viable_keys.get(pid, set()))
        rng.shuffle(viable)
        placed = False
        for slot in viable:
            if slot_counts.get(slot, 0) < papers_per_session:
                result[pid] = slot
                slot_counts[slot] = slot_counts.get(slot, 0) + 1
                placed = True
                break
        if not placed:
            rest = list(all_slot_keys)
            rng.shuffle(rest)
            for slot in rest:
                if slot_counts.get(slot, 0) < papers_per_session:
                    result[pid] = slot
                    slot_counts[slot] = slot_counts.get(slot, 0) + 1
                    placed = True
                    break
        if not placed:
            result[pid] = None

    return result


def _tournament_select(
    population: list[Assignment],
    fitness_list: list[tuple],
    size: int,
    rng: random.Random,
) -> Assignment:
    """Return a copy of the fittest individual from a random tournament."""
    indices = rng.sample(range(len(population)), min(size, len(population)))
    best = min(indices, key=lambda i: fitness_list[i])
    return dict(population[best])


def _niching_replace(
    population: list[Assignment],
    fitness_list: list[tuple],
    offspring: Assignment,
    offspring_fitness: tuple,
) -> None:
    """Deterministic niching: replace the most similar individual if offspring is fitter.

    The offspring competes only against the population member it most resembles
    (minimum Hamming distance).  If it is fitter, it replaces that member;
    otherwise it is discarded.  This preserves diversity by keeping multiple
    fitness peaks alive in the population instead of letting the best solution
    crowd out all others.
    """
    closest = min(range(len(population)), key=lambda i: _distance(population[i], offspring))
    if offspring_fitness < fitness_list[closest]:
        population[closest] = offspring
        fitness_list[closest] = offspring_fitness


# ---------------------------------------------------------------------------
# Algorithm
# ---------------------------------------------------------------------------

class GeneticScheduler(SchedulingAlgorithm):
    """Genetic algorithm scheduler with deterministic niching.

    Evolves a population of complete paper-to-session assignments over many
    generations.  Deterministic niching (crowding) is used for replacement:
    each offspring only competes against the most similar individual in the
    population, preserving diversity and preventing premature convergence.

    Operators:
      - Selection:  tournament (configurable size)
      - Crossover:  uniform — each paper independently inherits from either parent
      - Repair:     fix over-capacity slots introduced by crossover
      - Mutation:   random per-paper relocation at mutation_rate probability
      - Replacement: deterministic niching

    Fitness: weighted scalar — reviewer attendance weighted most highly,
    then session count, then sessions below minimum.
    """

    def __init__(
        self,
        population_size: int = 50,
        n_generations: int = 100,
        crossover_rate: float = 0.8,
        mutation_rate: float = 0.05,
        tournament_size: int = 3,
    ):
        self.population_size = population_size
        self.n_generations = n_generations
        self.crossover_rate = crossover_rate
        self.mutation_rate = mutation_rate
        self.tournament_size = tournament_size

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

        pids = list(all_pids)
        rng = random.Random(42)

        reviewer_counts, total_matched = compute_reviewer_counts(
            to_schedule, candidates, reviewer_by_email, avail_by_name,
        )
        fitness_fn: FitnessFunction = make_weighted_fitness(
            reviewer_counts, total_matched, config.min_papers_per_session,
        )

        # --- Initial population: deterministic seeds + random fill ---
        population: list[Assignment] = []
        for Scheduler in (GreedyScheduler, SessionFirstScheduler):
            result = Scheduler().schedule(papers, prefs, reviewers, config)
            population.append(_result_to_assignment(result, all_pids))
        while len(population) < self.population_size:
            population.append(_random_seed(
                pids, viable_keys, all_slot_keys, config.papers_per_session, rng,
            ))

        fitness_list = [fitness_fn(ind) for ind in population]

        # --- Evolution ---
        best_fitness_seen = min(fitness_list)
        last_improvement_gen = -1

        for gen in range(self.n_generations):
            for _ in range(self.population_size):
                parent1 = _tournament_select(population, fitness_list, self.tournament_size, rng)
                parent2 = _tournament_select(population, fitness_list, self.tournament_size, rng)

                if rng.random() < self.crossover_rate:
                    offspring = _crossover(parent1, parent2, rng)
                    offspring = _repair(
                        offspring, config.papers_per_session,
                        viable_keys, all_slot_keys, rng,
                    )
                else:
                    offspring = parent1

                offspring = _mutate(
                    offspring, viable_keys, all_slot_keys,
                    config.papers_per_session, self.mutation_rate, rng,
                )

                _niching_replace(population, fitness_list, offspring, fitness_fn(offspring))

            new_best = min(fitness_list)
            if new_best < best_fitness_seen:
                best_fitness_seen = new_best
                last_improvement_gen = gen

        # Warn if the population was still improving when the limit was reached.
        # We consider "still improving" to mean the last improvement happened in
        # the final 10 % of generations.
        still_improving_threshold = self.n_generations - max(1, self.n_generations // 10)
        if last_improvement_gen >= still_improving_threshold:
            print(
                f"WARNING: genetic algorithm was still improving at generation "
                f"{last_improvement_gen + 1}/{self.n_generations} — "
                f"consider increasing n_generations (currently {self.n_generations})."
            )

        # --- Return best individual ---
        best_idx = min(range(len(population)), key=lambda i: fitness_list[i])
        return _assignment_to_result(
            population[best_idx], paper_by_pid, viable_keys,
            candidate_by_key, skipped, config.papers_per_session,
        )
