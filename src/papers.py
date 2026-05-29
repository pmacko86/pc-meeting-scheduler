"""Paper data model, HotCRP JSON parsing, and reviewer extraction."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from reviewers import AssignmentReviewer


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Paper:
    pid: int
    title: str
    tags: list[str]
    reviewers: list[AssignmentReviewer]

    def __str__(self) -> str:
        return f"#{self.pid}: {self.title}"

    def __repr__(self) -> str:
        tags = ", ".join(self.tags)
        return f"Paper(pid={self.pid}, title={self.title!r}, tags=[{tags}], reviewers=<{len(self.reviewers)}>)"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _extract_display_name(raw_html: str) -> str:
    """Extract the reviewer's plain name from a HotCRP HTML reviewer string."""
    # Name appears before the tagdecoration span (badges)
    name = re.sub(r'<span[^>]*class="tagdecoration"[^>]*>.*', "", raw_html, flags=re.DOTALL)
    # Strip any remaining HTML tags (e.g. highlighted name spans)
    name = re.sub(r"<[^>]+>", "", name).strip()
    return name


def parse_hotcrp_json(path: Path) -> list[Paper]:
    """Parse a HotCRP JSON export (JSON for reviewqualitycollector.org)."""
    with open(path) as f:
        data = json.load(f)

    seen_reviewers: dict[str, AssignmentReviewer] = {}
    papers: list[Paper] = []

    for p in data.get("papers", []):
        reviewers: list[AssignmentReviewer] = []
        for r in p.get("reviews", []):
            email = r.get("reviewer_email", "")
            if not email:
                continue
            if email not in seen_reviewers:
                seen_reviewers[email] = AssignmentReviewer(
                    email=email,
                    display_name=_extract_display_name(r.get("reviewer", email)),
                )
            reviewers.append(seen_reviewers[email])

        tags = [t.strip() for t in p.get("tags", []) if t.strip()]
        papers.append(Paper(pid=p["pid"], title=p["title"], tags=tags, reviewers=reviewers))

    return papers


# ---------------------------------------------------------------------------
# Reviewer extraction
# ---------------------------------------------------------------------------

def extract_assignment_reviewers(papers: list[Paper]) -> list[AssignmentReviewer]:
    """Deduplicated list of reviewers from the HotCRP assignments."""
    seen: dict[str, AssignmentReviewer] = {}
    for paper in papers:
        for rv in paper.reviewers:
            seen.setdefault(rv.email, rv)
    return sorted(seen.values(), key=lambda r: r.display_name)
