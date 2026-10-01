"""Cross-group knockout first-round pairing.

Given 2-8 round-robin groups (each with its own teams, matches and
number of qualification slots), every group is ranked with the same
recursive tie-breaking rules as :mod:`app.standings` and its top teams
advance, producing 4-16 qualified teams (a power of two).

The qualified teams are seeded by (in-group rank, group id, team id).
The first round is a perfect matching of the seeds under two hard
constraints:

- ``different_group``: the two sides must come from different groups.
- ``no_replay``: the two sides must not be a pair that already met
  cross-group (listed in ``played_pairs``).

Among all feasible perfect matchings the one minimizing the sum of the
two sides' seed differences is returned; ties are broken by the
lexicographically smallest list of team-id-sorted pairs, so the same
request always yields the same answer. If no such matching exists (or
the qualified field is not a power of two between 4 and 16), a
:class:`KnockoutValidationError` is raised and the endpoint returns 422.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol, Sequence

from .standings import MatchLike, StandingsValidationError, compute_standings

MIN_GROUPS = 2
MAX_GROUPS = 8
MIN_QUALIFIED = 4
MAX_QUALIFIED = 16

CONSTRAINT_DIFFERENT_GROUP = "different_group"
CONSTRAINT_NO_REPLAY = "no_replay"

#: Constraints every first-round match is generated under, reported on
#: each pairing (fixed order for a byte-stable response).
MATCH_CONSTRAINTS = (CONSTRAINT_DIFFERENT_GROUP, CONSTRAINT_NO_REPLAY)


class GroupLike(Protocol):
    group_id: str
    teams: Sequence[str]
    matches: Sequence[MatchLike]
    qualify_slots: int


class PlayedPairLike(Protocol):
    home: str
    away: str


class KnockoutValidationError(ValueError):
    """Raised when the knockout request is inconsistent or unsolvable."""


@dataclass(frozen=True)
class SeededTeam:
    """One qualified team together with its seeding information."""

    seed: int
    team: str
    group_id: str
    group_rank: int


@dataclass(frozen=True)
class KnockoutPairing:
    """One first-round fixture (team ids ordered lexicographically)."""

    team_a: str
    team_b: str
    seed_a: int
    seed_b: int
    group_a: str
    group_b: str
    constraints: tuple[str, ...]


def is_power_of_two(value: int) -> bool:
    return value >= 1 and (value & (value - 1)) == 0


def _validate_groups(
    groups: Sequence[GroupLike],
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Validate group structure and rank every group.

    Returns the final in-group order per group id and the group lookup
    for every known team.
    """
    if not MIN_GROUPS <= len(groups) <= MAX_GROUPS:
        raise KnockoutValidationError(
            f"between {MIN_GROUPS} and {MAX_GROUPS} groups are required, "
            f"got {len(groups)}"
        )

    seen_group_ids: set[str] = set()
    team_group: dict[str, str] = {}
    ordered_by_group: dict[str, list[str]] = {}

    for group in groups:
        group_id = group.group_id
        if not group_id:
            raise KnockoutValidationError("group id must not be empty")
        if group_id in seen_group_ids:
            raise KnockoutValidationError(
                f"duplicate group id: '{group_id}'"
            )
        seen_group_ids.add(group_id)

        # Reuses the existing standings validation: unknown matchup
        # teams, self matches, duplicate pairings, negative scores, ...
        # are all rejected here as 422.
        ordered, _stats = compute_standings(group.teams, group.matches)

        slots = group.qualify_slots
        if not 1 <= slots <= len(group.teams):
            raise KnockoutValidationError(
                f"qualify_slots for group '{group_id}' must be between 1 "
                f"and {len(group.teams)} (its number of teams), got {slots}"
            )

        for team in group.teams:
            if team in team_group:
                raise KnockoutValidationError(
                    f"team '{team}' appears in both group "
                    f"'{team_group[team]}' and group '{group_id}'"
                )
            team_group[team] = group_id
        ordered_by_group[group_id] = ordered

    return ordered_by_group, team_group


def _validate_played_pairs(
    played_pairs: Sequence[PlayedPairLike], team_group: dict[str, str]
) -> set[frozenset[str]]:
    """Validate already-played cross-group pairs and return their set."""
    blocked: set[frozenset[str]] = set()
    for pair in played_pairs:
        if pair.home == pair.away:
            raise KnockoutValidationError(
                f"team '{pair.home}' cannot play against itself"
            )
        for side in (pair.home, pair.away):
            if side not in team_group:
                raise KnockoutValidationError(f"unknown team: '{side}'")
        key = frozenset((pair.home, pair.away))
        if key in blocked:
            raise KnockoutValidationError(
                f"duplicate played pair: '{pair.home}' vs '{pair.away}'"
            )
        blocked.add(key)
    return blocked


def _minimum_seed_matching(
    seed_teams: Sequence[str],
    team_group: dict[str, str],
    played: set[frozenset[str]],
) -> tuple[int, tuple[tuple[str, str], ...]] | None:
    """Find the cost-minimal feasible perfect matching.

    Cost is the sum of seed differences (seed numbers are 1-based, the
    array indices used here are 0-based, which preserves differences).
    Matchings tied on cost are broken by the lexicographically smallest
    tuple of team-id-sorted pairs. Returns ``None`` when no feasible
    perfect matching exists.
    """
    n = len(seed_teams)
    full = (1 << n) - 1

    def forbidden(i: int, j: int) -> bool:
        team_i, team_j = seed_teams[i], seed_teams[j]
        if team_group[team_i] == team_group[team_j]:
            return True
        return frozenset((team_i, team_j)) in played

    @lru_cache(maxsize=None)
    def solve(
        mask: int,
    ) -> tuple[int, tuple[tuple[str, str], ...]] | None:
        if mask == 0:
            return 0, ()
        # Always match the smallest seed still free; every perfect
        # matching has exactly one edge incident to it.
        low = mask & -mask
        i = low.bit_length() - 1
        rest = mask ^ low
        candidates = rest
        best: tuple[int, tuple[tuple[str, str], ...]] | None = None
        while candidates:
            bit = candidates & -candidates
            candidates ^= bit
            j = bit.bit_length() - 1
            if forbidden(i, j):
                continue
            sub = solve(rest ^ bit)
            if sub is None:
                continue
            edge = tuple(sorted((seed_teams[i], seed_teams[j])))
            cost = sub[0] + (j - i)
            # Inserting the same edge into the sorted sub-solution keeps
            # lexicographic order, so the memoized sub-optimum suffices.
            pairing = tuple(sorted((edge, *sub[1])))
            candidate = (cost, pairing)
            if best is None or candidate < best:
                best = candidate
        return best

    return solve(full)


def compute_knockout_first_round(
    groups: Sequence[GroupLike], played_pairs: Sequence[PlayedPairLike] = ()
) -> tuple[list[SeededTeam], list[KnockoutPairing]]:
    """Rank every group, seed the qualifiers and pair the first round.

    See the module docstring for the seeding, optimization and 422
    rules.
    """
    ordered_by_group, team_group = _validate_groups(groups)
    played = _validate_played_pairs(played_pairs, team_group)

    qualified: list[tuple[int, str, str]] = []
    for group in groups:
        ordered = ordered_by_group[group.group_id]
        for rank, team in enumerate(ordered[: group.qualify_slots], start=1):
            qualified.append((rank, group.group_id, team))

    count = len(qualified)
    if not (
        MIN_QUALIFIED <= count <= MAX_QUALIFIED and is_power_of_two(count)
    ):
        raise KnockoutValidationError(
            f"the number of qualified teams must be a power of two "
            f"between {MIN_QUALIFIED} and {MAX_QUALIFIED}, got {count}"
        )

    # Seed order: in-group rank, then group id, then team id.
    qualified.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
    seeded = [
        SeededTeam(seed=index + 1, team=team, group_id=group_id, group_rank=rank)
        for index, (rank, group_id, team) in enumerate(qualified)
    ]
    seed_teams = [entry.team for entry in seeded]

    solution = _minimum_seed_matching(seed_teams, team_group, played)
    if solution is None:
        raise KnockoutValidationError(
            "no feasible first-round pairing exists: every pairing would "
            "either join teams from the same group or repeat a played "
            "cross-group pair"
        )

    seed_by_team = {entry.team: entry.seed for entry in seeded}
    pairings = [
        KnockoutPairing(
            team_a=team_a,
            team_b=team_b,
            seed_a=seed_by_team[team_a],
            seed_b=seed_by_team[team_b],
            group_a=team_group[team_a],
            group_b=team_group[team_b],
            constraints=MATCH_CONSTRAINTS,
        )
        for team_a, team_b in solution[1]
    ]
    return seeded, pairings
