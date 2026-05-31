#!/usr/bin/env python3
"""Show the scheduling availability of a paper's reviewers across all time slots."""

import argparse
import os
import sys
from pathlib import Path

# Re-exec with the project venv if not already running inside it.
_VENV_DIR    = Path(__file__).parent.parent / ".venv"
_VENV_PYTHON = _VENV_DIR / "bin" / "python3"
if _VENV_PYTHON.exists() and Path(sys.prefix) != _VENV_DIR:
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON)] + sys.argv)

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import colors as C                                             # noqa: E402
from main import detect_inputs, parse_config                   # noqa: E402
from papers import parse_hotcrp_json                           # noqa: E402
from papers import extract_assignment_reviewers                # noqa: E402
from reviewers import Availability, match_reviewers            # noqa: E402
from schedule import parse_xoyondo_csv                         # noqa: E402
from scheduler import _tag_base                                # noqa: E402


# ---------------------------------------------------------------------------
# Availability helpers
# ---------------------------------------------------------------------------

def _avail_str(a: Availability) -> str:
    if a == Availability.YES:
        return f"{C.OK}Yes  {C.RESET}"
    if a == Availability.MAYBE:
        return f"{C.WARN}Maybe{C.RESET}"
    return f"{C.DIM_TEXT}No   {C.RESET}"


def _avail_char(a: Availability) -> str:
    if a == Availability.YES:
        return f"{C.OK}●{C.RESET}"
    if a == Availability.MAYBE:
        return f"{C.WARN}◐{C.RESET}"
    return f"{C.DIM_TEXT}○{C.RESET}"


def _count_color(n_yes: int, n_maybe: int, total: int) -> str:
    if total == 0:
        return C.DIM_TEXT
    if n_yes + n_maybe == total:
        return C.OK
    if n_yes + n_maybe == 0:
        return C.DIM_TEXT
    return C.WARN


# ---------------------------------------------------------------------------
# Main display
# ---------------------------------------------------------------------------

def show(paper_id: int, assignments_path: Path, schedule_path: Path,
         config_path) -> int:
    papers = parse_hotcrp_json(assignments_path)
    prefs  = parse_xoyondo_csv(schedule_path)

    paper = next((p for p in papers if p.pid == paper_id), None)
    if paper is None:
        print(f"error: paper #{paper_id} not found in {assignments_path}",
              file=sys.stderr)
        return 1

    # Match reviewers across both sources.
    assign_revs    = extract_assignment_reviewers(papers)
    schedule_names = [r.reviewer_name for r in prefs.reviewers]
    reviewers      = match_reviewers(assign_revs, schedule_names)
    rv_by_email    = {rv.assignment.email: rv for rv in reviewers if rv.assignment}
    avail_by_name  = {ra.reviewer_name: ra for ra in prefs.reviewers}

    # Paper's reviewers.
    paper_rvs = paper.reviewers          # list[AssignmentReviewer]
    matched   = []   # (AssignmentReviewer, ReviewerAvailability | None, any_avail: bool)
    unmatched = []   # AssignmentReviewer — no schedule entry at all

    for arv in paper_rvs:
        rv = rv_by_email.get(arv.email)
        if rv is None or rv.schedule_name is None:
            unmatched.append(arv)
        else:
            ra = avail_by_name.get(rv.schedule_name)
            any_avail = ra is not None and any(
                a in (Availability.YES, Availability.MAYBE)
                for a in ra.availability.values()
            )
            matched.append((arv, ra, any_avail))

    # ── Header ───────────────────────────────────────────────────────────────
    tags = [_tag_base(t) for t in paper.tags]
    tag_str = f"  {C.DIM_TEXT}[{', '.join(tags)}]{C.RESET}" if tags else ""
    print(f"\n{C.BOLD}#{paper.pid}: {paper.title}{C.RESET}{tag_str}")

    # ── Reviewer list ─────────────────────────────────────────────────────────
    n_matched = len(matched)
    n_unmatched = len(unmatched)
    print(f"\n{C.HEADER}Reviewers ({len(paper_rvs)} assigned, "
          f"{n_matched} with schedule data):{C.RESET}")

    name_w = max((len(arv.display_name) for arv in paper_rvs), default=20) + 2
    for arv, ra, any_avail in matched:
        if not any_avail:
            note = f"{C.NO_SLOTS}no available slots{C.RESET}"
        else:
            note = f"{C.OK}in schedule{C.RESET}"
        print(f"  {arv.display_name:{name_w}}  {C.DIM_TEXT}{arv.email}{C.RESET}  ({note})")
    for arv in unmatched:
        note = f"{C.DIM_TEXT}no availability data{C.RESET}"
        print(f"  {arv.display_name:{name_w}}  {C.DIM_TEXT}{arv.email}{C.RESET}  ({note})")

    if not matched:
        print(f"\n{C.DIM_TEXT}No reviewers have provided availability data.{C.RESET}")
        return 0

    # ── Availability grid ─────────────────────────────────────────────────────
    col_w = [max(len(arv.display_name), 5) for arv, _, _ in matched]

    # Group slots by date.
    from collections import OrderedDict
    by_date: OrderedDict = OrderedDict()
    for slot in prefs.slots:
        by_date.setdefault(slot.date, []).append(slot)

    # First pass: compute per-slot counts to find the global best.
    slot_counts: dict = {}   # slot -> (n_yes, n_maybe, n_no)
    for slots in by_date.values():
        for slot in slots:
            n_yes = n_maybe = n_no = 0
            for _, ra, _ in matched:
                a = ra.availability.get(slot, Availability.UNKNOWN) if ra else Availability.UNKNOWN
                if a == Availability.YES:
                    n_yes += 1
                elif a == Availability.MAYBE:
                    n_maybe += 1
                else:
                    n_no += 1
            slot_counts[slot] = (n_yes, n_maybe, n_no)

    best_can = max((ny + nm for ny, nm, _ in slot_counts.values()), default=0)

    print(f"\n{C.HEADER}Availability by time slot:{C.RESET}")

    for date, slots in by_date.items():
        day_str = f"{date.strftime('%a %b')} {date.day}"
        print(f"\n  {C.BOLD}{day_str}{C.RESET}")

        header = "        "
        for (arv, _, _), w in zip(matched, col_w):
            header += f"  {arv.display_name[:w]:{w}}"
        cant_hdr = "Can't"
        header += f"   {'Can':>5}  {cant_hdr:>5}"
        print(f"  {C.DIM_TEXT}{header}{C.RESET}")
        print(f"  {C.DIM_TEXT}  {'─' * (len(header) - 2)}{C.RESET}")

        for slot in slots:
            h = slot.time.hour
            time_str = f"{h}am" if h < 12 else ("12pm" if h == 12 else f"{h-12}pm")
            n_yes, n_maybe, n_no = slot_counts[slot]
            can  = n_yes + n_maybe
            is_best = best_can > 0 and can == best_can

            marker = f"{C.OK}►{C.RESET}" if is_best else " "
            row = f" {marker} {time_str:>5}   "

            for (_, ra, _), w in zip(matched, col_w):
                a = ra.availability.get(slot, Availability.UNKNOWN) if ra else Availability.UNKNOWN
                cell = _avail_str(a)
                row += f"  {cell:{w - 1 + (len(cell) - 5)}}"

            clr = _count_color(n_yes, n_maybe, n_matched)
            row += f"   {clr}{can:>5}{C.RESET}  {C.DIM_TEXT}{n_no:>5}{C.RESET}"

            if is_best:
                print(f"  {C.BOLD}{row}{C.RESET}")
            else:
                print(f"  {row}")

    print()
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="paper_schedule",
        description="Show reviewer availability for a specific paper.",
    )
    p.add_argument("paper_ids", metavar="PAPER_ID", type=int, nargs="+",
                   help="One or more paper IDs to display.")
    p.add_argument("-d", "--directory", metavar="DIR", type=Path,
                   help="Input directory (auto-detects assignments and schedule).")
    p.add_argument("-a", "--assignments", metavar="FILE", type=Path,
                   help="HotCRP RQC JSON file.")
    p.add_argument("-s", "--schedule", metavar="FILE", type=Path,
                   help="Scheduling preferences CSV.")
    p.add_argument("-c", "--config", metavar="FILE", type=Path,
                   help="Configuration YAML or JSON.")
    return p


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    assignments_path = args.assignments
    schedule_path    = args.schedule
    config_path      = args.config

    if args.directory:
        d = args.directory
        if not d.is_dir():
            print(f"error: {d} is not a directory", file=sys.stderr)
            sys.exit(1)
        auto_a, auto_s, auto_c = detect_inputs(d)
        assignments_path = assignments_path or auto_a
        schedule_path    = schedule_path    or auto_s
        config_path      = config_path      or auto_c

    # Fall back to project-root default config.
    _default = Path(__file__).parent.parent / "config.yaml"
    if config_path is None and _default.exists():
        config_path = _default

    if not assignments_path or not schedule_path:
        parser.print_help()
        sys.exit(1)

    for p_path in (assignments_path, schedule_path):
        if not p_path.exists():
            print(f"error: {p_path} not found", file=sys.stderr)
            sys.exit(1)

    rc = 0
    for pid in args.paper_ids:
        rc = max(rc, show(pid, assignments_path, schedule_path, config_path))
    sys.exit(rc)


if __name__ == "__main__":
    main()
