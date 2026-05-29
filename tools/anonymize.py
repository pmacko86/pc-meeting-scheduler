#!/usr/bin/env python3
"""
Anonymize HotCRP RQC JSON and Xoyondo CSV inputs for testing.

Replaces all identifying information (paper IDs, titles, authors,
reviewer names, emails, affiliations) with convincingly fake data,
keeping only the fields the scheduler actually uses.  Optionally
applies random permutations to vary the dataset.
"""

import argparse
import csv
import datetime
import json
import os
import random
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Re-exec with the project venv if not already running inside it.
_VENV_DIR    = Path(__file__).parent.parent / ".venv"
_VENV_PYTHON = _VENV_DIR / "bin" / "python3"
if _VENV_PYTHON.exists() and Path(sys.prefix) != _VENV_DIR:
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON)] + sys.argv)

# Make src/ importable when the tool is run from the project root.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from main import detect_inputs, parse_config      # noqa: E402
from scheduler import _tag_base                   # noqa: E402


# ---------------------------------------------------------------------------
# Word lists for fake data generation
# ---------------------------------------------------------------------------

_FIRST_NAMES = [
    "Alice", "Bob", "Carlos", "Diana", "Eric", "Fatima", "George", "Helen",
    "Ivan", "Julia", "Kevin", "Lily", "Marco", "Nadia", "Oscar", "Priya",
    "Quinn", "Rachel", "Stefan", "Tara", "Umar", "Vera", "Wei", "Xin",
    "Yuki", "Zara", "Amit", "Bianca", "Chloe", "Darius", "Elena", "Finn",
    "Greta", "Hiro", "Ingrid", "Jonas", "Kira", "Luca", "Maya", "Niall",
]

_LAST_NAMES = [
    "Smith", "Jones", "Brown", "Chen", "Kim", "Patel", "Williams", "Davis",
    "Garcia", "Rodriguez", "Anderson", "Johnson", "Taylor", "Thomas",
    "Jackson", "White", "Harris", "Martin", "Thompson", "Lewis", "Lee",
    "Walker", "Hall", "Young", "King", "Wright", "Scott", "Torres",
    "Nguyen", "Okafor", "Mueller", "Ivanov", "Suzuki", "Rossi", "Santos",
    "Eriksson", "Nakamura", "Dubois", "Kowalski", "Ferreira",
]

_INSTITUTIONS = [
    "Northbrook University",
    "Southern Technical Institute",
    "Eastern Research University",
    "Westfield University",
    "Pacific Institute of Technology",
    "Lakeside University",
    "Mountain State University",
    "Harbor Computing Institute",
    "Valley Technical University",
    "Summit University",
    "Coastal Research Center",
    "Highland Institute of Science",
    "Riverside University",
    "Meadowbrook Technical University",
    "Oceanside Research Institute",
]

_SYS_NAMES = [
    "Nexus", "Hydra", "Orion", "Atlas", "Titan", "Vega", "Nova", "Apex",
    "Echo", "Flux", "Iris", "Lynx", "Bolt", "Zeta", "Crux", "Helix",
    "Prism", "Nimbus", "Stratus", "Vortex", "Zenith", "Solaris", "Pulsar",
    "Quorum", "Synapse", "Helios", "Aether", "Kronos", "Cipher", "Vector",
]

_ADJECTIVES = [
    "Efficient", "Scalable", "Adaptive", "Robust", "Lightweight", "Unified",
    "Fast", "Distributed", "Concurrent", "Persistent", "Elastic",
    "Transparent", "Practical", "Reliable", "Compact", "Flexible",
    "Intelligent", "Hierarchical", "Incremental", "Transactional",
    "Disaggregated", "Memory-Efficient", "Fault-Tolerant", "Low-Latency",
]

_OPERATIONS = [
    "Storage", "Caching", "Indexing", "Compression", "Scheduling",
    "Replication", "Deduplication", "Migration", "Prefetching", "Eviction",
    "Partitioning", "Recovery", "Checkpointing", "Compaction", "Buffering",
    "Erasure Coding", "Load Balancing", "Data Placement", "Tiering",
    "Snapshotting",
]

_DOMAINS = [
    "Flash Storage", "NVM Devices", "Cloud Storage", "Key-Value Stores",
    "Distributed File Systems", "Object Stores", "Block Devices",
    "In-Memory Databases", "Persistent Memory", "CXL-Attached Memory",
    "GPU Storage", "Edge Computing", "Disaggregated Storage",
    "Large-Scale Clusters", "Datacenter Networks",
]

_TECHNIQUES = [
    "LSM Trees", "B-Trees", "Bloom Filters", "Learned Indexes",
    "Machine Learning", "Sampling", "Graph Processing", "Deep Learning",
    "Reinforcement Learning", "Approximate Computing", "RDMA",
    "Hardware Offloading", "Near-Data Processing", "Vectorization",
    "Sketching", "Probabilistic Data Structures",
]

_TITLE_TEMPLATES = [
    "{sys}: {adj} {op} for {domain}",
    "{adj} {op} for {domain} via {tech}",
    "Toward {adj} {op} in {domain}",
    "{sys}: A {adj} Approach to {op}",
    "Rethinking {op} for {domain}",
    "{tech}-Driven {op} for {domain}",
    "Efficient {op} with {tech}",
    "{sys}: {op} at Scale",
    "Optimizing {op} for {domain} with {tech}",
    "{adj} {op} Using {tech}",
    "Short Paper: {adj} {op} for {domain}",
    "Deployed System: {sys} — {adj} {op} at Scale",
]

def _extract_badges(raw_html: str) -> list[str]:
    """Extract badge names from a HotCRP reviewer HTML string."""
    return re.findall(r'<span class="badge[^"]*">([^<]+)</span>', raw_html)


# ---------------------------------------------------------------------------
# Fake data generators
# ---------------------------------------------------------------------------

def _fake_name(rng: random.Random) -> tuple[str, str]:
    return rng.choice(_FIRST_NAMES), rng.choice(_LAST_NAMES)


def _fake_email(first: str, last: str, institution_idx: int) -> str:
    domain = _INSTITUTIONS[institution_idx % len(_INSTITUTIONS)]
    domain_slug = domain.lower().replace(" ", "-").replace("'", "")
    return f"{first.lower()}.{last.lower()}@{domain_slug}.edu"


def _fake_title(rng: random.Random, used: set[str]) -> str:
    for _ in range(50):
        tmpl = rng.choice(_TITLE_TEMPLATES)
        title = tmpl.format(
            sys=rng.choice(_SYS_NAMES),
            adj=rng.choice(_ADJECTIVES),
            op=rng.choice(_OPERATIONS),
            domain=rng.choice(_DOMAINS),
            tech=rng.choice(_TECHNIQUES),
        )
        if title not in used:
            used.add(title)
            return title
    # Fallback: append a suffix to guarantee uniqueness
    base = _fake_title.__wrapped__(rng, set())
    return f"{base} (Revisited)"


_fake_title.__wrapped__ = _fake_title  # for the fallback


def _fake_reviewer_html(name: str, original_badges: list[str]) -> str:
    """Build a reviewer HTML string, keeping only the pc-external badge."""
    kept = [b for b in original_badges if b == "pc-external"]
    if not kept:
        return name
    spans = " ".join(f'<span class="badge">{b}</span>' for b in kept)
    return f'{name}<span class="tagdecoration"> {spans}</span>'


# ---------------------------------------------------------------------------
# Anonymizer
# ---------------------------------------------------------------------------

class Anonymizer:
    def __init__(
        self,
        seed: Optional[int] = None,
        permute: bool = True,
        known_tags: Optional[set[str]] = None,
    ):
        self.rng = random.Random(seed)
        self.permute = permute
        # When set, paper tags are filtered to only these base names.
        self.known_tags = known_tags

        # Consistent mappings built during anonymize_hotcrp
        self._email_to_fake: dict[str, tuple[str, str, int]] = {}  # → (first, last, inst_idx)
        self._name_to_email: dict[str, str] = {}   # display name → canonical email
        self._pid_map: dict[int, int] = {}
        self._used_names: set[tuple[str, str]] = set()
        self._used_titles: set[str] = set()

    # ── Reviewer identity helpers ──────────────────────────────────────────

    def _display_name_to_email(self, raw: str, email: str) -> str:
        """Clean the HTML reviewer string to a plain name, register mapping."""
        name = re.sub(r'<span[^>]*class="tagdecoration"[^>]*>.*', "", raw, flags=re.DOTALL)
        name = re.sub(r"<[^>]+>", "", name).strip()
        if email and email not in self._email_to_fake:
            self._register_reviewer(email)
        if name and name not in self._name_to_email:
            self._name_to_email[name] = email
        return email or name

    def _register_reviewer(self, email: str) -> None:
        if email in self._email_to_fake:
            return
        for _ in range(100):
            first, last = _fake_name(self.rng)
            if (first, last) not in self._used_names:
                self._used_names.add((first, last))
                inst_idx = len(self._email_to_fake)
                self._email_to_fake[email] = (first, last, inst_idx)
                return
        raise RuntimeError("Exhausted fake name combinations")

    def _fake_for(self, email: str) -> tuple[str, str, str]:
        """Return (fake_display_name, fake_email, first_last_str) for an email."""
        first, last, inst = self._email_to_fake[email]
        return (
            f"{first} {last}",
            _fake_email(first, last, inst),
            f"{first} {last}",
        )

    # ── HotCRP JSON ────────────────────────────────────────────────────────

    def anonymize_hotcrp(self, data: dict) -> dict:
        papers = data.get("papers", [])

        # First pass: collect all reviewer emails so mappings are consistent
        for p in papers:
            for r in p.get("reviews", []):
                email = r.get("reviewer_email", "")
                raw = r.get("reviewer", "")
                if email:
                    self._register_reviewer(email)
                self._display_name_to_email(raw, email)

        # Assign fake PIDs (shuffle order if permuting)
        pids = [p["pid"] for p in papers]
        fake_pids = list(range(1, len(pids) + 1))
        if self.permute:
            self.rng.shuffle(fake_pids)
        self._pid_map = dict(zip(pids, fake_pids))

        # Filter paper tags to only those mentioned in the config, then redistribute.
        def _filter(tags: list[str]) -> list[str]:
            if self.known_tags is None:
                return tags
            return [t for t in tags if _tag_base(t) in self.known_tags]

        all_tags: list[list[str]] = [_filter(p.get("tags", [])) for p in papers]
        if self.permute:
            all_tags = self._redistribute_tags(all_tags)

        # Build anonymized papers
        out_papers = []
        for p, tags in zip(papers, all_tags):
            reviews = p.get("reviews", [])
            if self.permute:
                reviews = self._permute_reviews(reviews, papers)

            fake_reviews = []
            for r in reviews:
                email = r.get("reviewer_email", "")
                if email and email in self._email_to_fake:
                    disp, fake_email, _ = self._fake_for(email)
                    original_badges = _extract_badges(r.get("reviewer", ""))
                    fake_reviews.append({
                        "object": "review",
                        "pid": self._pid_map[p["pid"]],
                        "reviewer": _fake_reviewer_html(disp, original_badges),
                        "reviewer_email": fake_email,
                    })

            out_papers.append({
                "object": "paper",
                "pid": self._pid_map[p["pid"]],
                "title": _fake_title(self.rng, self._used_titles),
                "tags": tags,
                "reviews": fake_reviews,
            })

        if self.permute:
            out_papers.sort(key=lambda p: p["pid"])

        return {"hotcrp_version": "anonymized", "papers": out_papers}

    def _redistribute_tags(self, tag_lists: list[list[str]]) -> list[list[str]]:
        """Shuffle which papers hold which tags, preserving per-tag total counts."""
        # Collect all tag occurrences (one entry per occurrence)
        all_occurrences: list[str] = []
        for tags in tag_lists:
            all_occurrences.extend(tags)

        # Shuffle occurrences, then redistribute greedily (max 2 tags per paper)
        self.rng.shuffle(all_occurrences)
        result: list[list[str]] = [[] for _ in tag_lists]
        for tag in all_occurrences:
            eligible = [i for i, t in enumerate(result) if len(t) < 2 and tag not in t]
            if not eligible:
                eligible = [i for i, t in enumerate(result) if tag not in t]
            if eligible:
                result[self.rng.choice(eligible)].append(tag)
        return result

    def _permute_reviews(
        self, reviews: list[dict], all_papers: list[dict]
    ) -> list[dict]:
        """Randomly swap ~15% of reviewers between papers."""
        if not reviews or self.rng.random() > 0.15:
            return reviews
        # Pick a random other paper and swap one reviewer
        other = self.rng.choice(all_papers)
        other_reviews = other.get("reviews", [])
        if not other_reviews:
            return reviews
        reviews = list(reviews)
        idx_a = self.rng.randrange(len(reviews))
        idx_b = self.rng.randrange(len(other_reviews))
        reviews[idx_a], other_reviews[idx_b] = other_reviews[idx_b], reviews[idx_a]
        return reviews

    # ── Xoyondo CSV ────────────────────────────────────────────────────────

    def anonymize_xoyondo(self, rows: list[list[str]]) -> list[list[str]]:
        """Anonymize a Xoyondo CSV (list-of-rows representation)."""
        out = []
        reviewer_rows: list[int] = []

        for i, row in enumerate(rows):
            first_cell = row[0].strip().strip('"')
            # Reviewer rows: non-empty first cell that isn't a number and isn't
            # the meeting title or a header
            if (first_cell
                    and not re.match(r"^\d+$", first_cell)
                    and not any(k in first_cell for k in ["Meeting", "FAST", "PC"])):
                reviewer_rows.append(i)

        # Anonymize reviewer names (using the mapping built from the JSON)
        used_csv_names: set[str] = set()
        for i, row in enumerate(rows):
            new_row = list(row)
            if i in reviewer_rows:
                real_name = row[0].strip().strip('"')
                # Look up canonical email via display name
                email = self._name_to_email.get(real_name)
                if email and email in self._email_to_fake:
                    first, last, _ = self._email_to_fake[email]
                    fake = f"{first} {last}"
                else:
                    # Reviewer not in assignments — generate a fresh fake name
                    for _ in range(100):
                        first, last = _fake_name(self.rng)
                        fake = f"{first} {last}"
                        if fake not in used_csv_names:
                            break
                used_csv_names.add(fake)
                new_row[0] = fake

            out.append(new_row)

        if self.permute:
            out = self._permute_xoyondo(out, reviewer_rows)

        return out

    def _permute_xoyondo(
        self, rows: list[list[str]], reviewer_rows: list[int]
    ) -> list[list[str]]:
        """Shuffle reviewer order and fuzz availability values."""
        # Shuffle reviewer rows in place
        reviewer_data = [rows[i] for i in reviewer_rows]
        self.rng.shuffle(reviewer_data)
        for idx, i in enumerate(reviewer_rows):
            rows[i] = reviewer_data[idx]

        # Fuzz availability: change ~15% of slots by one step
        steps = {"Yes": ["Maybe", "Yes", "Yes"],
                 "Maybe": ["No", "Yes", "Maybe"],
                 "No": ["No", "No", "Maybe"]}
        for i in reviewer_rows:
            row = rows[i]
            for j in range(1, len(row)):
                val = row[j].strip().strip('"')
                if val in steps and self.rng.random() < 0.15:
                    row[j] = self.rng.choice(steps[val])

        return rows


# ---------------------------------------------------------------------------
# Statistical profile and synthetic generator
# ---------------------------------------------------------------------------

@dataclass
class StatsProfile:
    """Statistical properties of a dataset, used for pure synthetic generation."""
    n_papers:              int
    n_reviewers:           int               # unique reviewers in assignments
    n_schedule_reviewers:  int               # reviewers in the schedule
    reviews_per_paper:     list[int]         # empirical distribution of review counts
    tag_rates:             dict[str, float]  # {tag_base: fraction of papers}
    n_days:                int               # days in the scheduling window
    slots_per_day:         int               # hourly slots per day
    yes_rate:              float             # overall fraction of Yes slots
    maybe_rate:            float             # overall fraction of Maybe slots


_DEFAULT_PROFILE = StatsProfile(
    n_papers=50,
    n_reviewers=15,
    n_schedule_reviewers=12,
    reviews_per_paper=[3, 4, 4, 5, 5, 5, 6],
    tag_rates={},
    n_days=2,
    slots_per_day=10,
    yes_rate=0.45,
    maybe_rate=0.15,
)

# First Monday of a clearly fictional future month used for generated schedules.
_GENERATED_BASE_DATE = datetime.date(2030, 1, 7)
_SLOT_HOURS = ["8am", "9am", "10am", "11am", "12pm", "1pm", "2pm", "3pm", "4pm", "5pm"]


def extract_stats(
    assignments_path: Optional[Path],
    schedule_path:    Optional[Path],
    known_tags:       Optional[set[str]] = None,
) -> StatsProfile:
    """Derive a StatsProfile from real input files (falls back to defaults)."""
    from papers import parse_hotcrp_json
    from reviewers import Availability
    from schedule import parse_xoyondo_csv

    prof = _DEFAULT_PROFILE

    if assignments_path:
        papers = parse_hotcrp_json(assignments_path)
        n_papers = len(papers)
        reviews_per_paper = [len(p.reviewers) for p in papers] or prof.reviews_per_paper
        all_emails = {rv.email for p in papers for rv in p.reviewers}
        n_reviewers = len(all_emails) or prof.n_reviewers

        raw_counts: dict[str, int] = Counter(
            _tag_base(t)
            for p in papers
            for t in p.tags
            if known_tags is None or _tag_base(t) in known_tags
        )
        tag_rates = {tag: cnt / n_papers for tag, cnt in raw_counts.items() if cnt}
    else:
        n_papers, n_reviewers = prof.n_papers, prof.n_reviewers
        reviews_per_paper, tag_rates = prof.reviews_per_paper, prof.tag_rates

    if schedule_path:
        prefs = parse_xoyondo_csv(schedule_path)
        n_schedule_reviewers = len(prefs.reviewers)

        day_counts = Counter(s.date for s in prefs.slots)
        n_days = len(day_counts)
        slots_per_day = max(day_counts.values()) if day_counts else prof.slots_per_day

        total = yes_count = maybe_count = 0
        for ra in prefs.reviewers:
            for avail in ra.availability.values():
                total += 1
                if avail == Availability.YES:
                    yes_count += 1
                elif avail == Availability.MAYBE:
                    maybe_count += 1
        yes_rate   = yes_count   / total if total else prof.yes_rate
        maybe_rate = maybe_count / total if total else prof.maybe_rate
    else:
        n_schedule_reviewers = prof.n_schedule_reviewers
        n_days, slots_per_day = prof.n_days, prof.slots_per_day
        yes_rate, maybe_rate  = prof.yes_rate, prof.maybe_rate

    return StatsProfile(
        n_papers=n_papers,
        n_reviewers=n_reviewers,
        n_schedule_reviewers=n_schedule_reviewers,
        reviews_per_paper=reviews_per_paper,
        tag_rates=tag_rates,
        n_days=n_days,
        slots_per_day=slots_per_day,
        yes_rate=yes_rate,
        maybe_rate=maybe_rate,
    )


class StatsGenerator:
    """Generates fully synthetic RQC JSON and Xoyondo CSV from a StatsProfile.

    Unlike the Anonymizer (which preserves real structure), this class creates
    data whose *shape* matches the profile but shares no content with the input.
    """

    def __init__(
        self,
        profile:    StatsProfile,
        seed:       Optional[int] = None,
        known_tags: Optional[set[str]] = None,
        fuzz:       bool = True,
    ):
        self.rng        = random.Random(seed)
        self.profile    = self._fuzz_profile(profile) if fuzz else profile
        self.known_tags = known_tags
        self._used_names:  set[tuple[str, str]] = set()
        self._used_titles: set[str] = set()
        self._reviewers:   list[dict[str, str]] = []  # populated by generate_hotcrp

    def _fuzz_profile(self, profile: StatsProfile) -> StatsProfile:
        """Perturb numeric statistics so the output can't fingerprint the source.

        Counts are scaled by ±15 %, rates by ±25 %, and the review-per-paper
        distribution has random ±1 jitter added to each entry.  This breaks
        exact-count matches while preserving the rough shape of the data.
        """
        rng = self.rng

        def fuzz_count(n: int, lo: float = 0.85, hi: float = 1.15) -> int:
            return max(1, round(n * rng.uniform(lo, hi)))

        def fuzz_rate(r: float, lo: float = 0.75, hi: float = 1.25) -> float:
            return min(1.0, max(0.0, r * rng.uniform(lo, hi)))

        perturbed_reviews = [
            max(1, r + (rng.randint(-1, 1) if rng.random() < 0.5 else 0))
            for r in profile.reviews_per_paper
        ]

        return StatsProfile(
            n_papers             = fuzz_count(profile.n_papers),
            n_reviewers          = fuzz_count(profile.n_reviewers),
            n_schedule_reviewers = fuzz_count(profile.n_schedule_reviewers),
            reviews_per_paper    = perturbed_reviews,
            tag_rates            = {t: fuzz_rate(r) for t, r in profile.tag_rates.items()},
            n_days               = profile.n_days,
            slots_per_day        = profile.slots_per_day,
            yes_rate             = fuzz_rate(profile.yes_rate,   0.80, 1.20),
            maybe_rate           = fuzz_rate(profile.maybe_rate, 0.80, 1.20),
        )

    def _new_reviewer(self) -> dict[str, str]:
        inst_idx = len(self._reviewers)
        for _ in range(200):
            first, last = _fake_name(self.rng)
            if (first, last) not in self._used_names:
                self._used_names.add((first, last))
                return {
                    "name":  f"{first} {last}",
                    "email": _fake_email(first, last, inst_idx),
                }
        raise RuntimeError("Exhausted unique fake name combinations")

    def generate_hotcrp(self) -> dict:
        """Generate a synthetic HotCRP-style RQC JSON dict."""
        prof = self.profile
        rng  = self.rng

        self._reviewers = [self._new_reviewer() for _ in range(prof.n_reviewers)]

        applicable_tags = {
            tag: rate for tag, rate in prof.tag_rates.items()
            if self.known_tags is None or tag in self.known_tags
        }

        papers = []
        for pid in range(1, prof.n_papers + 1):
            tags = [
                f"{tag}#0"
                for tag, rate in applicable_tags.items()
                if rng.random() < rate
            ]
            n_reviews = max(1, min(rng.choice(prof.reviews_per_paper), len(self._reviewers)))
            reviews = [
                {
                    "object":         "review",
                    "pid":            pid,
                    "reviewer":       _fake_reviewer_html(rv["name"], []),
                    "reviewer_email": rv["email"],
                }
                for rv in rng.sample(self._reviewers, n_reviews)
            ]
            papers.append({
                "object":  "paper",
                "pid":     pid,
                "title":   _fake_title(rng, self._used_titles),
                "tags":    tags,
                "reviews": reviews,
            })

        return {"hotcrp_version": "generated", "papers": papers}

    def generate_xoyondo(self) -> list[list[str]]:
        """Generate a synthetic Xoyondo-format scheduling CSV."""
        prof = self.profile
        rng  = self.rng

        hours = _SLOT_HOURS[:prof.slots_per_day]
        n_slots = prof.n_days * prof.slots_per_day
        n_cols  = 1 + n_slots

        def empty() -> list[str]:
            return [""] * n_cols

        # Month / day / time header rows
        row_title = empty(); row_title[0] = "PC Meeting"
        row_month = empty()
        row_days  = empty()
        row_times = empty()

        for d in range(prof.n_days):
            date = _GENERATED_BASE_DATE + datetime.timedelta(days=d)
            col0 = 1 + d * prof.slots_per_day
            row_month[col0] = date.strftime("%B %Y")
            row_days[col0]  = f"{date.strftime('%a')} {date.day:02d} "
            for h, hour in enumerate(hours):
                row_times[col0 + h] = hour

        rows: list[list[str]] = [empty(), row_title, empty(), row_month, row_days, row_times]

        # Pick schedule reviewers from the reviewer pool (or generate fresh names)
        pool = list(self._reviewers)
        rng.shuffle(pool)
        schedule_pool = pool[:prof.n_schedule_reviewers]
        while len(schedule_pool) < prof.n_schedule_reviewers:
            schedule_pool.append(self._new_reviewer())

        # Availability values weighted by yes/maybe/no rates
        no_rate = max(0.0, 1.0 - prof.yes_rate - prof.maybe_rate)
        weights = [prof.yes_rate, prof.maybe_rate, no_rate]
        choices = ["Yes", "Maybe", "No"]

        for rv in schedule_pool:
            avail = rng.choices(choices, weights=weights, k=n_slots)
            rows.append([rv["name"]] + avail)

        # Totals row
        totals = [""] + [
            str(sum(1 for r in rows[6:] if len(r) > c and r[c] == "Yes"))
            for c in range(1, n_cols)
        ]
        rows.append(totals)

        return rows

    def generate(self) -> tuple[dict, list[list[str]]]:
        """Generate both outputs. Must call before accessing generated data."""
        hotcrp   = self.generate_hotcrp()    # populates self._reviewers
        xoyondo  = self.generate_xoyondo()
        return hotcrp, xoyondo


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _read_csv_raw(path: Path) -> list[list[str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.reader(f))


def _write_csv_raw(path: Path, rows: list[list[str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="anonymize",
        description="Anonymize HotCRP RQC JSON and Xoyondo CSV inputs for testing.",
    )
    p.add_argument("-d", "--directory", metavar="DIR", type=Path,
                   help="Input directory (auto-detects assignments and schedule).")
    p.add_argument("-a", "--assignments", metavar="FILE", type=Path,
                   help="HotCRP RQC JSON file.")
    p.add_argument("-s", "--schedule", metavar="FILE", type=Path,
                   help="Xoyondo scheduling preferences CSV.")
    p.add_argument("-c", "--config", metavar="FILE", type=Path,
                   help="Config YAML/JSON: only tags listed here are kept in output.")
    p.add_argument("-o", "--output", metavar="DIR", type=Path, default=Path("anonymized"),
                   help="Output directory (default: ./anonymized/).")
    p.add_argument("--fuzz", action="store_true", default=True,
                   help="Perturb statistics in --generate mode (±15 %% on counts, ±25 %% on "
                        "rates) so the output cannot fingerprint the source (default: on).")
    p.add_argument("--no-fuzz", dest="fuzz", action="store_false",
                   help="Disable statistical fuzzing in --generate mode.")
    p.add_argument("--generate", action="store_true", default=False,
                   help="Generate purely synthetic data from statistical properties of the "
                        "inputs (no real structure is preserved).  Input files are used only "
                        "to derive statistics; omit them to use built-in defaults.")
    p.add_argument("--permute", action="store_true", default=True,
                   help="Apply random permutations to the dataset (anonymize mode, default: on).")
    p.add_argument("--no-permute", dest="permute", action="store_false",
                   help="Disable random permutations (pure structural anonymization only).")
    p.add_argument("--seed", metavar="N", type=int, default=None,
                   help="Random seed for reproducibility (default: random).")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    assignments_path: Optional[Path] = args.assignments
    schedule_path:    Optional[Path] = args.schedule
    config_path:      Optional[Path] = args.config

    if args.directory:
        d = args.directory
        if not d.is_dir():
            print(f"error: {d} is not a directory", file=sys.stderr)
            sys.exit(1)
        auto_a, auto_s, auto_c = detect_inputs(d)
        if assignments_path is None and auto_a:
            assignments_path = auto_a
            print(f"Detected assignments: {auto_a}")
        if schedule_path is None and auto_s:
            schedule_path = auto_s
            print(f"Detected schedule:    {auto_s}")
        if config_path is None and auto_c:
            config_path = auto_c
            print(f"Detected config:      {config_path}")

    if not args.generate and not assignments_path and not schedule_path:
        parser.print_help()
        sys.exit(0)

    for p_path in (assignments_path, schedule_path):
        if p_path and not p_path.exists():
            print(f"error: {p_path} not found", file=sys.stderr)
            sys.exit(1)

    known_tags: Optional[set[str]] = None
    if config_path:
        if not config_path.exists():
            print(f"error: {config_path} not found", file=sys.stderr)
            sys.exit(1)
        cfg = parse_config(config_path)
        known_tags = set(cfg.skip_tags) | set(cfg.attention_tags) | set(cfg.one_shot_tags) or None
        if known_tags:
            print(f"Keeping tags: {sorted(known_tags)}")

    args.output.mkdir(parents=True, exist_ok=True)
    seed = args.seed if args.seed is not None else random.randint(0, 2**31)
    print(f"Seed: {seed}  (pass --seed {seed} to reproduce this output)")

    if args.generate:
        # ── Generate mode: derive stats from inputs (or use defaults), then
        #    produce fully synthetic data with no real content preserved.
        print("Extracting statistical profile …")
        profile = extract_stats(assignments_path, schedule_path, known_tags)
        print(f"  {profile.n_papers} papers, {profile.n_reviewers} reviewers, "
              f"{profile.n_schedule_reviewers} in schedule, "
              f"{profile.n_days}×{profile.slots_per_day} slots")

        gen = StatsGenerator(seed=seed, profile=profile, known_tags=known_tags, fuzz=args.fuzz)
        if args.fuzz:
            p = gen.profile
            print(f"  Fuzzed to: {p.n_papers} papers, {p.n_reviewers} reviewers, "
                  f"{p.n_schedule_reviewers} in schedule")
        hotcrp_data, xoyondo_rows = gen.generate()

        rqc_path = args.output / "rqc.json"
        with open(rqc_path, "w", encoding="utf-8") as f:
            json.dump(hotcrp_data, f, indent=2)
        print(f"  → {rqc_path}")

        sched_path = args.output / "schedule.csv"
        _write_csv_raw(sched_path, xoyondo_rows)
        print(f"  → {sched_path}")

    else:
        # ── Anonymize mode: preserve structure, replace all identifying info.
        anon = Anonymizer(seed=seed, permute=args.permute, known_tags=known_tags)

        if assignments_path:
            print(f"Anonymizing {assignments_path} …")
            with open(assignments_path, encoding="utf-8") as f:
                data = json.load(f)
            out_data = anon.anonymize_hotcrp(data)
            out_path = args.output / "rqc.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(out_data, f, indent=2)
            print(f"  {len(out_data['papers'])} papers, "
                  f"{len(anon._email_to_fake)} reviewers → {out_path}")

        if schedule_path:
            print(f"Anonymizing {schedule_path} …")
            out_rows = anon.anonymize_xoyondo(_read_csv_raw(schedule_path))
            out_path = args.output / "schedule.csv"
            _write_csv_raw(out_path, out_rows)
            print(f"  → {out_path}")

    print("Done.")


if __name__ == "__main__":
    main()
