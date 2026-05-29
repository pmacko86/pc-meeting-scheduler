"""Scheduling configuration data model."""

from dataclasses import dataclass, field


@dataclass
class Config:
    """Scheduling configuration loaded from YAML/JSON.

    Tag names are matched without the HotCRP '#weight' suffix:
    config tag "pre-accept" matches paper tag "pre-accept#0".
    """
    skip_tags: list[str]                = field(default_factory=list)
    attention_tags: list[str]           = field(default_factory=list)
    one_shot_tags: list[str]            = field(default_factory=list)
    minutes_per_paper: int              = 15
    min_reviewers_per_slot: int         = 3
    session_length: int                 = 120
    min_papers_per_session: int         = 4
    algorithm: str                      = "greedy"

    @property
    def papers_per_session(self) -> int:
        """Maximum papers per session, derived from session_length / minutes_per_paper."""
        return max(1, self.session_length // self.minutes_per_paper)

    def __str__(self) -> str:
        return (f"skip={self.skip_tags}, attention={self.attention_tags}, "
                f"one_shot={self.one_shot_tags}, "
                f"{self.minutes_per_paper}min/paper, "
                f"min_reviewers={self.min_reviewers_per_slot}, "
                f"session={self.session_length}min, "
                f"min_papers={self.min_papers_per_session}, "
                f"algorithm={self.algorithm}")

    def __repr__(self) -> str:
        return (f"Config(skip_tags={self.skip_tags!r}, "
                f"attention_tags={self.attention_tags!r}, "
                f"one_shot_tags={self.one_shot_tags!r}, "
                f"minutes_per_paper={self.minutes_per_paper}, "
                f"min_reviewers_per_slot={self.min_reviewers_per_slot}, "
                f"session_length={self.session_length}, "
                f"min_papers_per_session={self.min_papers_per_session}, "
                f"algorithm={self.algorithm!r})")
