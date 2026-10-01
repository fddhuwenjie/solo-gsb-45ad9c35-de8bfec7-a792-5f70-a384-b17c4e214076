"""Pydantic schemas for the standings API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .knockout import MAX_GROUPS, MAX_QUALIFIED, MIN_GROUPS
from .scheduling import MAX_ROUNDS, MAX_TEAMS


class MatchIn(BaseModel):
    """A completed match between two teams."""

    home: str = Field(min_length=1, examples=["alpha"])
    away: str = Field(min_length=1, examples=["bravo"])
    home_score: int = Field(ge=0, examples=[2])
    away_score: int = Field(ge=0, examples=[1])


class StandingsRequest(BaseModel):
    """Teams participating in the round-robin and the completed matches."""

    teams: list[str] = Field(min_length=1, examples=[["alpha", "bravo", "charlie"]])
    matches: list[MatchIn] = Field(default_factory=list)


class StandingEntry(BaseModel):
    """One row of the final table."""

    rank: int
    team: str
    played: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    goal_difference: int
    points: int


class StandingsResponse(BaseModel):
    standings: list[StandingEntry]


class MiniLeagueRowOut(BaseModel):
    """One team's line in a head-to-head mini-league table."""

    team: str
    points: int
    goal_difference: int
    goals_for: int


class FinalVerdictRowOut(BaseModel):
    """One team's line when overall criteria settle a still-tied block.

    Teams are ordered by goal difference, then goals scored, then team id.
    """

    team: str
    goal_difference: int
    goals_for: int


class DecisionPartitionOut(BaseModel):
    """One ordered block at a mini-league level.

    Exactly one resolution is present: a lone ``team`` when the
    mini-league already decided the block, a nested ``mini_league``
    rebuilt from the block's mutual matches, or a ``final`` verdict
    using overall goal difference, overall goals scored and team id.
    Concatenating every partition's members reproduces the parent
    group's member order.
    """

    kind: Literal["single", "mini_league", "final"]
    members: list[str]
    team: str | None = None
    partitions: list["DecisionPartitionOut"] | None = None
    mini_table: list[MiniLeagueRowOut] | None = None
    final_table: list[FinalVerdictRowOut] | None = None


class PointsGroupOut(BaseModel):
    """All teams sharing the same total points, in final ranking order."""

    points: int
    members: list[str]
    mini_table: list[MiniLeagueRowOut]
    partitions: list[DecisionPartitionOut]


class RankingExplanationOut(BaseModel):
    """Machine-readable ranking decision tree for the standings.

    Groups are ordered by total points; within a group the ordered
    partitions (recursively) give the exact final order.
    """

    criterion: Literal["total_points"] = "total_points"
    members: list[str]
    groups: list[PointsGroupOut]


class StandingsExplanationResponse(BaseModel):
    """The /standings table plus the decision tree behind its ordering."""

    standings: list[StandingEntry]
    explanation: RankingExplanationOut


DecisionPartitionOut.model_rebuild()


class ScoreCandidate(BaseModel):
    """One possible score for an unplayed fixture."""

    home_score: int = Field(ge=0, examples=[2])
    away_score: int = Field(ge=0, examples=[1])


class RemainingFixture(BaseModel):
    """An unplayed fixture together with its finite candidate scores."""

    home: str = Field(min_length=1, examples=["delta"])
    away: str = Field(min_length=1, examples=["alpha"])
    candidates: list[ScoreCandidate] = Field(min_length=1)


class QualificationRequest(BaseModel):
    """Completed matches plus candidate scores for every unplayed fixture."""

    teams: list[str] = Field(min_length=1, examples=[["alpha", "bravo", "charlie"]])
    matches: list[MatchIn] = Field(default_factory=list)
    top_k: int = Field(ge=1, examples=[2])
    remaining: list[RemainingFixture] = Field(default_factory=list)


class ScenarioEntry(BaseModel):
    """Best/worst reachable rank for one team, each with a witness.

    A witness is a complete set of results for the unplayed fixtures
    (one entry per fixture, in request order) under which the team
    finishes at the corresponding extreme rank.
    """

    team: str
    status: Literal["guaranteed", "possible", "eliminated"]
    best_rank: int
    worst_rank: int
    best_witness: list[MatchIn]
    worst_witness: list[MatchIn]


class QualificationResponse(BaseModel):
    top_k: int
    combinations: int
    scenarios: list[ScenarioEntry]


class TeamUnavailability(BaseModel):
    """The rounds a team cannot play in (1-based round numbers)."""

    team: str = Field(min_length=1, examples=["alpha"])
    unavailable_rounds: list[int] = Field(
        default_factory=list, examples=[[2, 4]]
    )


class FixedMatchIn(BaseModel):
    """A match pinned to a specific round with a fixed home/away order."""

    home: str = Field(min_length=1, examples=["alpha"])
    away: str = Field(min_length=1, examples=["bravo"])
    round: int = Field(ge=1, le=MAX_ROUNDS, examples=[1])


class RoundRobinScheduleRequest(BaseModel):
    """Teams, total rounds, unavailability windows and pinned matches."""

    teams: list[str] = Field(
        min_length=2,
        max_length=MAX_TEAMS,
        examples=[["alpha", "bravo", "charlie", "delta"]],
    )
    rounds: int = Field(
        ge=1,
        le=MAX_ROUNDS,
        description=(
            "Total number of rounds; must be at least the single round-robin "
            "minimum (n-1 for even n, n for odd n) and at most "
            f"{MAX_ROUNDS}."
        ),
        examples=[3],
    )
    unavailability: list[TeamUnavailability] = Field(default_factory=list)
    fixed_matches: list[FixedMatchIn] = Field(default_factory=list)


class ScheduledMatchOut(BaseModel):
    """One scheduled match in a round."""

    home: str
    away: str


class ScheduleRound(BaseModel):
    """All matches of one round, sorted by (home id, away id)."""

    round: int
    matches: list[ScheduledMatchOut]


class RoundRobinScheduleResponse(BaseModel):
    teams: list[str]
    rounds: int
    schedule: list[ScheduleRound]


class KnockoutGroupIn(BaseModel):
    """One completed round-robin group and its number of qualifiers."""

    group_id: str = Field(min_length=1, examples=["A"])
    teams: list[str] = Field(min_length=1, examples=[["alpha", "bravo"]])
    matches: list[MatchIn] = Field(default_factory=list)
    qualifiers: int = Field(
        ge=1,
        le=MAX_QUALIFIED,
        description="Number of teams advancing from this group.",
        examples=[2],
    )


class PlayedPairIn(BaseModel):
    """One cross-group matchup that has already taken place."""

    team_a: str = Field(min_length=1, examples=["alpha"])
    team_b: str = Field(min_length=1, examples=["charlie"])


class KnockoutPairingRequest(BaseModel):
    """2-8 finished groups, their slots and the played cross-group pairs."""

    groups: list[KnockoutGroupIn] = Field(
        min_length=MIN_GROUPS,
        max_length=MAX_GROUPS,
    )
    played_pairs: list[PlayedPairIn] = Field(default_factory=list)


class GroupQualifierOut(BaseModel):
    """One advancing team within its group (by group-rank order)."""

    group_rank: int
    team: str


class KnockoutGroupOut(BaseModel):
    """The teams advancing from one group."""

    group_id: str
    qualifiers: list[GroupQualifierOut]


class SeedOut(BaseModel):
    """One seeded advancing team."""

    seed: int
    team: str
    group_id: str
    group_rank: int


class MatchConstraintOut(BaseModel):
    """One pairing rule applied to a knockout match.

    - ``different_group``: the two teams come from different groups.
    - ``no_replay``: the pair is not in ``played_pairs``.
      ``involved_pairs`` lists the already played cross-group pairs
      incident on either side of this match (possibly empty); the
      scheduled pair itself is always absent from that list.
    """

    rule: Literal["different_group", "no_replay"]
    detail: str
    involved_pairs: list[tuple[str, str]]


class KnockoutMatchOut(BaseModel):
    """One first-round pairing (team ids sorted lexicographically)."""

    team_a: str
    team_b: str
    seed_a: int
    seed_b: int
    group_a: str
    group_b: str
    seed_difference: int
    constraints: list[MatchConstraintOut]


class KnockoutPairingResponse(BaseModel):
    """The seeds and the unique optimal first-round pairing."""

    advancing_teams: int
    seed_difference_sum: int
    played_pair_count: int
    groups: list[KnockoutGroupOut]
    seeds: list[SeedOut]
    matches: list[KnockoutMatchOut]
