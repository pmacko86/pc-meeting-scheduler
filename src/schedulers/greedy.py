"""Greedy scheduling algorithm: most-constrained-first with look-ahead."""

from config import Config
from papers import Paper
from reviewers import Reviewer, ReviewerAvailability, TimeSlot
from schedule import SLOT_DURATION_MINUTES, SchedulingPreferences
from scheduler import (
    ScheduleResult,
    ScheduledPaper,
    ScheduledSession,
    SchedulingAlgorithm,
    _best_effort_place,
    _generate_candidates,
    _has_tag,
    _viable_sessions,
)


class GreedyScheduler(SchedulingAlgorithm):
    """Greedy scheduler: most-constrained-first with one-step look-ahead.

    Strategy:
    1. Filter out papers whose tags match config.skip_tags.
    2. For each remaining paper compute which candidate sessions satisfy the
       reviewer-availability threshold.
    3. Sort papers most-constrained-first (fewest viable sessions).
    4. For each paper in order:
       a. Fit into an existing open session (tightest fit).
       b. Open a new session in the unused slot that the most future papers
          can also use (look-ahead to minimize total sessions).
       c. Best-effort fallback: pick the globally best slot by reviewer count
          — existing or new, whichever covers more reviewers.
    5. Sort sessions chronologically and renumber.
    """

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

        # --- Candidate sessions ---
        n_slots = max(1, round(config.session_length / SLOT_DURATION_MINUTES))
        candidates = _generate_candidates(prefs.slots, n_slots)

        if not candidates:
            return ScheduleResult(
                sessions=[],
                skipped_papers=skipped,
                unscheduled_papers=[(p, "no time slots available") for p in to_schedule],
            )

        # --- Viable-session map ---
        viables: dict[int, list[tuple[list[TimeSlot], list[Reviewer], list[Reviewer]]]] = {
            p.pid: _viable_sessions(
                p, candidates, reviewer_by_email, avail_by_name, config.min_reviewers_per_slot
            )
            for p in to_schedule
        }

        # --- Sort: most constrained first ---
        to_schedule.sort(key=lambda p: (len(viables[p.pid]), p.pid))

        # --- Greedy assignment ---
        open_sessions: list[ScheduledSession] = []
        used_slots: set[TimeSlot] = set()
        unscheduled: list[tuple[Paper, str]] = []
        session_counter = 1

        for idx, paper in enumerate(to_schedule):
            viable_map: dict[tuple, tuple[list[Reviewer], list[Reviewer]]] = {
                tuple(s): (av, mi) for s, av, mi in viables[paper.pid]
            }

            # (a) Fit into an existing open session — prefer least remaining space.
            fitting = [
                s for s in open_sessions
                if tuple(s.slots) in viable_map and s.remaining_papers >= 1
            ]
            if fitting:
                sess = min(fitting, key=lambda s: s.remaining_papers)
                sess.papers.append(ScheduledPaper(paper=paper))
                continue

            # (b) Open a new session in an unused viable slot.
            #     Look-ahead: prefer the slot that the most future papers can also use.
            future_viable_keys = [
                {tuple(s) for s, _, _ in viables[fp.pid]}
                for fp in to_schedule[idx + 1:]
            ]
            unused_viable = {
                k: v for k, v in viable_map.items()
                if not any(slot in used_slots for slot in k)
            }

            if unused_viable:
                def _slot_score(key: tuple) -> tuple:
                    future_fit = sum(1 for fvk in future_viable_keys if key in fvk)
                    meets_min = (future_fit + 1) >= config.min_papers_per_session
                    missing_count = len(unused_viable[key][1])
                    return (meets_min, future_fit, -missing_count)

                best_key = max(unused_viable, key=_slot_score)
                sess = ScheduledSession(
                    session_id=session_counter,
                    slots=list(best_key),
                    capacity_papers=config.papers_per_session,
                )
                session_counter += 1
                sess.papers.append(ScheduledPaper(paper=paper))
                open_sessions.append(sess)
                used_slots.update(best_key)
                continue

            # (c)+(d) Best-effort fallback.
            if not _best_effort_place(
                paper, candidates, open_sessions, used_slots,
                reviewer_by_email, avail_by_name, config.papers_per_session,
            ):
                unscheduled.append((paper, "no available time slots"))

        # --- Sort chronologically and renumber ---
        open_sessions.sort(key=lambda s: (s.slots[0].date, s.slots[0].time))
        for i, sess in enumerate(open_sessions, 1):
            sess.session_id = i

        return ScheduleResult(
            sessions=open_sessions,
            skipped_papers=skipped,
            unscheduled_papers=unscheduled,
        )
