"""Session-first scheduling algorithm."""

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
    _score_paper_in_session,
    _viable_sessions,
)


class SessionFirstScheduler(SchedulingAlgorithm):
    """Session-centric greedy: fills sessions in order of reviewer-coverage
    popularity.

    The GreedyScheduler processes papers most-constrained-first, opening
    sessions wherever each paper needs them.  This scheduler instead asks
    "which slot do the most papers want?" and fills those slots first,
    concentrating papers into shared availability windows and potentially
    reducing the total number of sessions.
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

        paper_by_pid: dict[int, Paper] = {p.pid: p for p in to_schedule}

        # Precompute which candidate slots each paper is viable for.
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

        open_sessions: list[ScheduledSession] = []
        used_slots: set[TimeSlot] = set()
        unassigned: set[int] = {p.pid for p in to_schedule}
        unscheduled: list[tuple[Paper, str]] = []

        # --- Session-first assignment ---
        while unassigned:
            # Pick the unused candidate slot that the most unassigned papers
            # are viable for. Break ties by earliest date/time.
            best_cand: list[TimeSlot] | None = None
            best_count = 0

            for cand in candidates:
                if any(slot in used_slots for slot in cand):
                    continue
                count = sum(1 for pid in unassigned if tuple(cand) in viable_keys[pid])
                if count > best_count or (
                    count == best_count and best_count > 0 and best_cand is not None
                    and (cand[0].date, cand[0].time) < (best_cand[0].date, best_cand[0].time)
                ):
                    best_count = count
                    best_cand = cand

            if best_cand is None or best_count == 0:
                break  # no viable sessions remain

            cand_key = tuple(best_cand)
            sess = ScheduledSession(
                session_id=0,  # renumbered at end
                slots=best_cand,
                capacity_papers=config.papers_per_session,
            )
            used_slots.update(best_cand)

            # Precompute per-paper scores for this session, then sort.
            viable_pids = [pid for pid in unassigned if cand_key in viable_keys[pid]]
            scores: dict[int, tuple[list[Reviewer], list[Reviewer]]] = {
                pid: _score_paper_in_session(
                    paper_by_pid[pid], best_cand, reviewer_by_email, avail_by_name
                )
                for pid in viable_pids
            }
            viable_pids.sort(key=lambda pid: (len(scores[pid][1]), pid))

            for pid in viable_pids:
                if sess.remaining_papers >= 1:
                    sess.papers.append(ScheduledPaper(paper=paper_by_pid[pid]))
                    unassigned.remove(pid)

            open_sessions.append(sess)

        # --- Best-effort for unassigned papers ---
        for pid in sorted(unassigned):
            if not _best_effort_place(
                paper_by_pid[pid], candidates, open_sessions, used_slots,
                reviewer_by_email, avail_by_name, config.papers_per_session,
            ):
                unscheduled.append((paper_by_pid[pid], "no available time slots"))

        # --- Sort chronologically and renumber ---
        open_sessions.sort(key=lambda s: (s.slots[0].date, s.slots[0].time))
        for i, sess in enumerate(open_sessions, 1):
            sess.session_id = i

        return ScheduleResult(
            sessions=open_sessions,
            skipped_papers=skipped,
            unscheduled_papers=unscheduled,
        )
