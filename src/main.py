"""PC Meeting Scheduler — main entry point."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

# Project-root config.yaml used when no other config is specified.
_DEFAULT_CONFIG = Path(__file__).parent.parent / "config.yaml"

import yaml

from config import Config
from papers import Paper, extract_assignment_reviewers, parse_hotcrp_json
from schedulers import GeneticScheduler, GreedyScheduler, HillClimbingScheduler, SessionFirstScheduler
from reviewers import Reviewer, match_reviewers, print_reviewer_report
from schedule import SchedulingPreferences, parse_xoyondo_csv
from scheduler import (ScheduleResult, SchedulingAlgorithm, compute_reviewer_coverage,
                       print_schedule_report, write_schedule_csv, write_schedule_html)


# ---------------------------------------------------------------------------
# Config parser
# ---------------------------------------------------------------------------

def parse_config(path: Path) -> Config:
    """Parse a YAML or JSON configuration file into a Config object."""
    with open(path) as f:
        if path.suffix in (".yaml", ".yml"):
            raw = yaml.safe_load(f) or {}
        else:
            raw = json.load(f)
    return Config(
        skip_tags                   = raw.get("skip_tags", []),
        attention_tags              = raw.get("attention_tags", []),
        one_shot_tags               = raw.get("one_shot_tags", []),
        minutes_per_paper      = raw.get("minutes_per_paper", 15),
        min_reviewers_per_slot = raw.get("min_reviewers_per_slot", 3),
        session_length              = raw.get("session_length", 120),
        min_papers_per_session      = raw.get("min_papers_per_session", 4),
        algorithm                   = raw.get("algorithm", "greedy"),
    )


# ---------------------------------------------------------------------------
# Auto-detection heuristics
# ---------------------------------------------------------------------------

_HOTCRP_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r"rqc", r"hotcrp", r"assignments", r"reviews",
]]
_XOYONDO_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r"xoyondo", r"schedule", r"availab", r"preferences", r"when2meet", r"doodle",
]]
_CONFIG_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r"config", r"settings", r"options",
]]


def _score(name: str, patterns: list[re.Pattern]) -> int:
    return sum(1 for p in patterns if p.search(name))


def detect_inputs(directory: Path) -> tuple[Optional[Path], Optional[Path], Optional[Path]]:
    """Return (assignments, preferences, config) by scanning a directory."""
    json_files = list(directory.glob("*.json"))
    csv_files  = list(directory.glob("*.csv"))
    yaml_files = list(directory.glob("*.yaml")) + list(directory.glob("*.yml"))

    def best(files, patterns):
        scored = [(f, _score(f.name, patterns)) for f in files if _score(f.name, patterns) > 0]
        return max(scored, key=lambda x: x[1])[0] if scored else None

    assignments = best(json_files, _HOTCRP_PATTERNS)
    preferences = best(csv_files, _XOYONDO_PATTERNS)
    config = best(yaml_files + [f for f in json_files if f != assignments], _CONFIG_PATTERNS)
    return assignments, preferences, config


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pc-meeting-scheduler",
        description="Schedule PC meeting sessions from reviewer assignments and availability.",
    )
    p.add_argument("-d", "--directory", metavar="DIR", type=Path,
                   help="Directory to scan for input files (auto-detection by filename heuristics).")
    p.add_argument("-a", "--assignments", metavar="FILE", type=Path,
                   help="HotCRP JSON export (JSON for reviewqualitycollector.org).")
    p.add_argument("-s", "--schedule", metavar="FILE", type=Path,
                   help="Reviewer scheduling preferences CSV (e.g. from Xoyondo).")
    p.add_argument("-c", "--config", metavar="FILE", type=Path,
                   help="Configuration YAML or JSON file.")
    p.add_argument("--csv", metavar="FILE", type=Path, default=None,
                   help="Write schedule to a CSV file (use - for standard output).")
    p.add_argument("--html", metavar="FILE", type=Path, default=None,
                   help="Write schedule to an HTML file (use - for standard output).")
    p.add_argument("--algorithm", choices=["greedy", "session-first", "hill-climbing", "genetic", "all"], default=None,
                   help="Scheduling algorithm (overrides config; default: 'greedy'). "
                        "Use 'all' to run every algorithm in sequence.")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    assignments_path: Optional[Path] = args.assignments
    preferences_path: Optional[Path] = args.schedule
    config_path:      Optional[Path] = args.config

    if args.directory:
        d = args.directory
        if not d.is_dir():
            print(f"error: {d} is not a directory", file=sys.stderr)
            sys.exit(1)
        auto_a, auto_s, auto_c = detect_inputs(d)
        if assignments_path is None and auto_a:
            assignments_path = auto_a
            print(f"Auto-detected assignments: {auto_a}")
        if preferences_path is None and auto_s:
            preferences_path = auto_s
            print(f"Auto-detected schedule:     {auto_s}")
        if config_path is None and auto_c:
            config_path = auto_c
            print(f"Auto-detected config:       {auto_c}")

    if config_path is None and _DEFAULT_CONFIG.exists():
        config_path = _DEFAULT_CONFIG
        print(f"Using default config:       {_DEFAULT_CONFIG}")

    if not assignments_path and not preferences_path:
        parser.print_help()
        sys.exit(0)

    papers: Optional[list[Paper]] = None
    prefs:  Optional[SchedulingPreferences] = None
    config: Config = Config()

    if assignments_path:
        print(f"Loading assignments from {assignments_path} …")
        papers = parse_hotcrp_json(assignments_path)
        print(f"  {len(papers)} papers loaded.")

    if preferences_path:
        print(f"Loading scheduling preferences from {preferences_path} …")
        prefs = parse_xoyondo_csv(preferences_path)
        print(f"  {len(prefs.reviewers)} reviewers, {len(prefs.slots)} time slots loaded.")

    if config_path:
        print(f"Loading config from {config_path} …")
        config = parse_config(config_path)
        print(f"  {config}")

    _ALGORITHMS: dict[str, SchedulingAlgorithm] = {
        "greedy":          GreedyScheduler(),
        "session-first":   SessionFirstScheduler(),
        "hill-climbing":   HillClimbingScheduler(),
        "genetic":         GeneticScheduler(),
    }

    algorithm_name = args.algorithm or config.algorithm
    if algorithm_name == "all":
        algos_to_run = list(_ALGORITHMS.items())
    elif algorithm_name in _ALGORITHMS:
        algos_to_run = [(algorithm_name, _ALGORITHMS[algorithm_name])]
    else:
        print(f"error: unknown algorithm {algorithm_name!r}", file=sys.stderr)
        sys.exit(1)

    if papers is not None or prefs is not None:
        assign_revs    = extract_assignment_reviewers(papers) if papers else []
        schedule_names = [r.reviewer_name for r in prefs.reviewers] if prefs else []
        reviewers: list[Reviewer] = match_reviewers(assign_revs, schedule_names)
        print_reviewer_report(reviewers)

        if papers is not None and prefs is not None:
            for name, algo in algos_to_run:
                print(f"\n{'='*60}")
                print(f"Algorithm: {name}")
                print(f"{'='*60}")
                result: ScheduleResult = algo.schedule(papers, prefs, reviewers, config)
                compute_reviewer_coverage(result, prefs, reviewers)
                print_schedule_report(result, config, prefs)

                for flag, writer, label in [
                    (args.csv,  lambda r, p: write_schedule_csv(r, p),                  "csv"),
                    (args.html, lambda r, p: write_schedule_html(r, config, prefs, p),  "html"),
                ]:
                    if flag is None:
                        continue
                    if str(flag) == "-":
                        out_path = flag
                    elif len(algos_to_run) > 1:
                        out_path = flag.parent / f"{flag.stem}-{name.replace('-', '_')}{flag.suffix}"
                    else:
                        out_path = flag
                    writer(result, out_path)
                    if str(out_path) != "-":
                        print(f"Schedule written to {out_path}")


if __name__ == "__main__":
    main()
