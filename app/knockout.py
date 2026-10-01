"""Cross-group knockout first-round pairing.

Given 2-8 round-robin groups (each with its own teams, completed
matches and number of qualifiers), every group is ranked with the same
recursive tie-break rules as :mod:`app.standings`, and its top teams
advance. The advancing teams must be a power of two between 4 and 16.

The advancing teams are seeded by (group rank, group id, team id) and
paired into a perfect matching. Hard constraints:

- every team plays exactly once;
- two teams from the same group never meet;
- a cross-group pair listed in ``played_pairs`` is never replayed.

Among all feasible perfect matchings the one minimizing the sum of the
paired teams' seed-number differences is returned. Ties are broken by
the lexicographically smallest sorted list of paired team ids, which
makes the result unique.

The optimum is found with subset dynamic programming over the at most
16 seeded teams (``best[mask]`` = minimum seed-difference sum for a
perfect matching of ``mask``); the lexicographic tie-break is obtained
by greedily reconstructing the matching pair by pair in team-id order.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from .standings import MatchLike, compute_standings

MIN_GROUPS = 2
MAX_GROUPS = 8
MIN_QUALIFIED = 4
MAX_QUALIFIED = 16

RULE_DIFFERENT_GROUP = "different_group"
RULE_NO_REPLAY = "no_replay"


class KnockoutValidationError(ValueError):
    """Raised when the knockout request is inconsistent or unsolvable."""


class GroupLike(Protocol):
    group_id: str
    teams: Sequence[str]
    matches: Sequence[MatchLike]
    qualifiers: int


class PairLike(Protocol):
    team_a: str
    team_b: str


@dataclass(frozen=True)
class GroupQualifiers:
    """The teams advancing from one group, in group-rank order."""

    group_id: str
    qualifiers: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class SeededTeam:
    """One advancing team together with its seed number."""

    seed: int
    team: str
    group_id: str
    group_rank: int


@dataclass(frozen=True)
class KnockoutMatch:
    """One first-round pairing (``team_a`` < ``team_b`` by id)."""

    team_a: str
    team_b: str
    seed_a: int
    seed_b: int
    group_a: str
    group_b: str

    @property
    def seed_difference(self) -> int:
        return abs(self.seed_a - self.seed_b)


@dataclass(frozen=True)
class KnockoutPlan:
    groups: tuple[GroupQualifiers, ...]
    seeds: tuple[SeededTeam, ...]
    matches: tuple[KnockoutMatch, ...]
    seed_difference_sum: int
    played_pair_count: int


def _power_of_two_between(value: int, low: int, high: int) -> bool:
    return low <= value <= high and (value & (value - 1)) == 0


def _validate(
    groups: Sequence[GroupLike], played_pairs: Sequence[PairLike]
) -> tuple[dict[str, str], set[frozenset[str]]]:
    """Validate request shape, returning team->group and played-pair sets."""
    if not MIN_GROUPS <= len(groups) <= MAX_GROUPS:
        raise KnockoutValidationError(
            f"between {MIN_GROUPS} and {MAX_GROUPS} groups are required, "
            f"got {len(groups)}"
        )

    seen_groups: set[str] = set()
    group_of: dict[str, str] = {}
    total = 0
    for group in groups:
        if not group.group_id:
            raise KnockoutValidationError("group ids must not be empty")
        if group.group_id in seen_groups:
            raise KnockoutValidationError(
                f"duplicate group id: '{group.group_id}'"
            )
        seen_groups.add(group.group_id)

        if not group.teams:
            raise KnockoutValidationError(
                f"group '{group.group_id}' must contain at least one team"
            )
        if len(set(group.teams)) != len(group.teams):
            raise KnockoutValidationError(
                f"duplicate team identifiers in group '{group.group_id}'"
            )
        for team in group.teams:
            if team in group_of:
                raise KnockoutValidationError(
                    f"team '{team}' is listed in more than one group "
                    f"('{group_of[team]}' and '{group.group_id}')"
                )
            group_of[team] = group.group_id

        if not 1 <= group.qualifiers <= len(group.teams):
            raise KnockoutValidationError(
                f"group '{group.group_id}' has {len(group.teams)} teams, so "
                f"its number of qualifiers must be between 1 and "
                f"{len(group.teams)}, got {group.qualifiers}"
            )
        total += group.qualifiers

    if not _power_of_two_between(total, MIN_QUALIFIED, MAX_QUALIFIED):
        raise KnockoutValidationError(
            f"the number of advancing teams must be a power of two between "
            f"{MIN_QUALIFIED} and {MAX_QUALIFIED}, got {total}"
        )

    played: set[frozenset[str]] = set()
    for pair in played_pairs:
        if pair.team_a == pair.team_b:
            raise KnockoutValidationError(
                f"team '{pair.team_a}' cannot play against itself"
            )
        for side in (pair.team_a, pair.team_b):
            if side not in group_of:
                raise KnockoutValidationError(f"unknown team: '{side}'")
        if group_of[pair.team_a] == group_of[pair.team_b]:
            raise KnockoutValidationError(
                f"played pair '{pair.team_a}' vs '{pair.team_b}' is not a "
                "cross-group matchup: both teams are from group "
                f"'{group_of[pair.team_a]}'"
            )
        key = frozenset((pair.team_a, pair.team_b))
        if key in played:
            raise KnockoutValidationError(
                f"duplicate played cross-group pair: '{pair.team_a}' vs "
                f"'{pair.team_b}'"
            )
        played.add(key)

    return group_of, played


def _seed_teams(
    ranked_groups: Sequence[tuple[str, Sequence[str], int]],
) -> list[SeededTeam]:
    """Seed the advancers by (group rank, group id, team id).

    Each input entry is (group id, full group ranking, qualifiers).
    """
    ranked: list[tuple[int, str, str]] = []
    for group_id, ordered, qualifiers in ranked_groups:
        for group_rank, team in enumerate(ordered[:qualifiers], start=1):
            ranked.append((group_rank, group_id, team))
    ranked.sort()
    return [
        SeededTeam(seed=index, team=team, group_id=group_id, group_rank=group_rank)
        for index, (group_rank, group_id, team) in enumerate(ranked, start=1)
    ]


def _optimal_matching(
    seeds: Sequence[SeededTeam],
    group_of: dict[str, str],
    played: set[frozenset[str]],
) -> tuple[list[tuple[int, int]], int]:
    """Return the lexicographically smallest minimum-cost perfect matching.

    Teams are referenced by their zero-based seed index; the edge cost is
    the absolute seed-number difference.
    """
    n = len(seeds)
    allowed = [[False] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            left, right = seeds[i], seeds[j]
            if left.group_id != right.group_id and frozenset(
                (left.team, right.team)
            ) not in played:
                allowed[i][j] = allowed[j][i] = True

    inf = 10**9
    size = 1 << n
    best = [inf] * size
    best[0] = 0
    for mask in range(1, size):
        if mask.bit_count() & 1:
            continue
        anchor_bit = mask & -mask
        anchor = anchor_bit.bit_length() - 1
        remain = mask ^ anchor_bit
        bits = remain
        value = inf
        while bits:
            bit = bits & -bits
            bits ^= bit
            other = bit.bit_length() - 1
            if allowed[anchor][other]:
                candidate = abs(anchor - other) + best[remain ^ bit]
                if candidate < value:
                    value = candidate
        best[mask] = value

    full = size - 1
    if best[full] >= inf:
        raise KnockoutValidationError(
            "no feasible first-round pairing exists: every perfect matching "
            "either pairs two teams from the same group or replays a played "
            "cross-group pair"
        )

    # Reconstruct lexicographically: always pair the smallest team id left
    # in the mask, trying partners by team id, keeping a pair only when a
    # minimum-cost completion remains.
    pairs: list[tuple[int, int]] = []
    mask = full
    while mask:
        # Expand the remaining teams once per round.
        member_bits = mask
        members: list[int] = []
        while member_bits:
            bit = member_bits & -member_bits
            member_bits ^= bit
            members.append(bit.bit_length() - 1)
        left = min(members, key=lambda idx: seeds[idx].team)
        partners = sorted(
            (idx for idx in members if idx != left),
            key=lambda idx: seeds[idx].team,
        )
        rest_without_left = mask ^ (1 << left)
        for right in partners:
            if not allowed[left][right]:
                continue
            submask = rest_without_left ^ (1 << right)
            if abs(left - right) + best[submask] == best[mask]:
                pairs.append((left, right))
                mask = submask
                break
        else:  # pragma: no cover - best[full] was finite, so a pair exists
            raise KnockoutValidationError(
                "internal error while reconstructing the first-round pairing"
            )
    return pairs, best[full]


def compute_knockout_pairings(
    groups: Sequence[GroupLike], played_pairs: Sequence[PairLike]
) -> KnockoutPlan:
    """Rank the groups, seed the advancers and build the optimal pairing."""
    group_of, played = _validate(groups, played_pairs)

    per_group: list[GroupQualifiers] = []
    ranked_groups: list[tuple[str, Sequence[str], int]] = []
    for group in groups:
        ordered, _ = compute_standings(group.teams, group.matches)
        ranked_groups.append((group.group_id, ordered, group.qualifiers))
        per_group.append(
            GroupQualifiers(
                group_id=group.group_id,
                qualifiers=tuple(
                    (rank, team)
                    for rank, team in enumerate(
                        ordered[: group.qualifiers], start=1
                    )
                ),
            )
        )

    seeds = _seed_teams(ranked_groups)
    index_pairs, total = _optimal_matching(seeds, group_of, played)

    matches: list[KnockoutMatch] = []
    for left, right in index_pairs:
        a, b = seeds[left], seeds[right]
        # The reconstruction always picks the smallest remaining team id
        # first, but keep the ordering explicit.
        if a.team > b.team:
            a, b = b, a
        matches.append(
            KnockoutMatch(
                team_a=a.team,
                team_b=b.team,
                seed_a=a.seed,
                seed_b=b.seed,
                group_a=a.group_id,
                group_b=b.group_id,
            )
        )

    return KnockoutPlan(
        groups=tuple(per_group),
        seeds=tuple(seeds),
        matches=tuple(matches),
        seed_difference_sum=total,
        played_pair_count=len(played),
    )
