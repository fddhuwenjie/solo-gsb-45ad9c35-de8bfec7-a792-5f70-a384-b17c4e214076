"""End-to-end tests for the cross-group knockout pairing API."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def post(groups, played_pairs=()):
    return client.post(
        "/knockout-pairings",
        json={"groups": groups, "played_pairs": list(played_pairs)},
    )


def group(group_id, teams, qualifiers, matches=()):
    return {
        "group_id": group_id,
        "teams": teams,
        "qualifiers": qualifiers,
        "matches": [
            {"home": h, "away": a, "home_score": hs, "away_score": as_}
            for h, a, hs, as_ in matches
        ],
    }


def pair(a, b):
    return {"team_a": a, "team_b": b}


FOUR_SINGLE_QUALIFIERS = [
    group("A", ["a1", "a2"], 1, [("a1", "a2", 1, 0)]),
    group("B", ["b1", "b2"], 1, [("b1", "b2", 1, 0)]),
    group("C", ["c1", "c2"], 1, [("c1", "c2", 1, 0)]),
    group("D", ["d1", "d2"], 1, [("d1", "d2", 1, 0)]),
]


def assert_valid_pairing(body):
    teams = [s["team"] for s in body["seeds"]]
    assert body["advancing_teams"] == len(teams)
    assert len(teams) == 2 * len(body["matches"])
    assert [s["seed"] for s in body["seeds"]] == list(
        range(1, len(teams) + 1)
    )
    # Seeds follow (group rank, group id, team id).
    assert [(s["group_rank"], s["group_id"], s["team"]) for s in body["seeds"]] == sorted(
        (s["group_rank"], s["group_id"], s["team"]) for s in body["seeds"]
    )
    seed_of = {s["team"]: s["seed"] for s in body["seeds"]}
    group_of = {s["team"]: s["group_id"] for s in body["seeds"]}

    used = set()
    total = 0
    for match in body["matches"]:
        assert match["team_a"] < match["team_b"]
        assert match["team_a"] in seed_of and match["team_b"] in seed_of
        assert match["seed_a"] == seed_of[match["team_a"]]
        assert match["seed_b"] == seed_of[match["team_b"]]
        assert match["group_a"] == group_of[match["team_a"]]
        assert match["group_b"] == group_of[match["team_b"]]
        assert match["seed_difference"] == abs(
            match["seed_a"] - match["seed_b"]
        )
        # Every team appears exactly once.
        used |= {match["team_a"], match["team_b"]}
        total += match["seed_difference"]
        rules = {c["rule"]: c for c in match["constraints"]}
        assert set(rules) == {"different_group", "no_replay"}
        assert rules["different_group"]["involved_pairs"] == []
        assert match["group_a"] != match["group_b"]
    assert used == set(teams)
    assert total == body["seed_difference_sum"]


def test_four_single_qualifiers_minimum_seed_gap_pairing():
    response = post(FOUR_SINGLE_QUALIFIERS)
    assert response.status_code == 200
    body = response.json()
    assert body["advancing_teams"] == 4
    assert [(s["seed"], s["team"]) for s in body["seeds"]] == [
        (1, "a1"),
        (2, "b1"),
        (3, "c1"),
        (4, "d1"),
    ]
    assert [(m["team_a"], m["team_b"]) for m in body["matches"]] == [
        ("a1", "b1"),
        ("c1", "d1"),
    ]
    assert body["seed_difference_sum"] == 2
    assert_valid_pairing(body)


def test_seed_order_uses_group_rank_then_group_id_then_team_id():
    groups = [
        # Winners zt and ma advance (one each); rank 1 seeds sort by
        # group id regardless of team id.
        group("A", ["zt", "zb"], 1, [("zt", "zb", 1, 0)]),
        group("B", ["ma", "mb"], 1, [("ma", "mb", 1, 0)]),
        group("C", ["ct", "cb"], 1, [("ct", "cb", 1, 0)]),
        group("D", ["dt", "db"], 1, [("dt", "db", 1, 0)]),
    ]
    body = post(groups).json()
    assert [s["team"] for s in body["seeds"]] == ["zt", "ma", "ct", "dt"]


def test_two_qualifiers_per_group_seed_rank_first():
    groups = [
        group(
            "A",
            ["a1", "a2", "a3"],
            2,
            [("a1", "a2", 1, 0), ("a2", "a3", 1, 0), ("a1", "a3", 0, 1)],
        ),
        group(
            "B",
            ["b1", "b2", "b3"],
            2,
            [("b1", "b2", 2, 0), ("b2", "b3", 0, 2), ("b1", "b3", 0, 1)],
        ),
    ]
    response = post(groups)
    assert response.status_code == 200
    body = response.json()
    assert body["advancing_teams"] == 4
    # Rank-1 teams seed before rank-2 teams.
    assert [s["group_rank"] for s in body["seeds"]] == [1, 1, 2, 2]
    assert all(m["group_a"] != m["group_b"] for m in body["matches"])
    assert_valid_pairing(body)


def test_group_ranking_reuses_recursive_standings_rules():
    # All games drawn inside group A: ranking falls through to the final
    # verdict and resolves ties by team id.
    groups = [
        group(
            "A",
            ["za", "ab", "ac"],
            2,
            [("za", "ab", 0, 0), ("ab", "ac", 0, 0), ("za", "ac", 0, 0)],
        ),
        group("B", ["b1", "b2"], 2, [("b1", "b2", 1, 0)]),
    ]
    body = post(groups).json()
    group_a = next(g for g in body["groups"] if g["group_id"] == "A")
    assert [row["team"] for row in group_a["qualifiers"]] == ["ab", "ac"]
    assert [s["team"] for s in body["seeds"]] == ["ab", "b1", "ac", "b2"]


def test_played_pair_forces_a_different_optimum():
    # Blocking the cost-2 matching (a1-b1, c1-d1) leaves two cost-4
    # matchings; a1-c1 already played forces a1-b1/c1-d1 to be blocked
    # and selects (a1, d1) + (b1, c1).
    response = post(
        FOUR_SINGLE_QUALIFIERS,
        [pair("a1", "b1"), pair("a1", "c1"), pair("c1", "d1")],
    )
    assert response.status_code == 200
    body = response.json()
    assert [(m["team_a"], m["team_b"]) for m in body["matches"]] == [
        ("a1", "d1"),
        ("b1", "c1"),
    ]
    assert body["seed_difference_sum"] == 4
    assert body["played_pair_count"] == 3
    by_teams = {
        (m["team_a"], m["team_b"]): m for m in body["matches"]
    }
    # (a1, d1) is incident on the played a1-b1 and a1-c1 pairs (via a1)
    # and c1-d1 (via d1), but never lists a1-d1 itself.
    no_replay = next(
        c
        for c in by_teams[("a1", "d1")]["constraints"]
        if c["rule"] == "no_replay"
    )
    assert no_replay["involved_pairs"] == [
        ["a1", "b1"],
        ["a1", "c1"],
        ["c1", "d1"],
    ]
    for match in body["matches"]:
        no_replay = next(
            c for c in match["constraints"] if c["rule"] == "no_replay"
        )
        assert [match["team_a"], match["team_b"]] not in no_replay[
            "involved_pairs"
        ]


def test_lexicographic_tie_break_is_unique():
    # Both remaining perfect matchings have cost 4:
    # (a1,c1)+(b1,d1) and (a1,d1)+(b1,c1). The smallest paired id
    # (a1, c1) < (a1, d1) decides.
    response = post(
        FOUR_SINGLE_QUALIFIERS, [pair("a1", "b1"), pair("c1", "d1")]
    )
    body = response.json()
    assert [(m["team_a"], m["team_b"]) for m in body["matches"]] == [
        ("a1", "c1"),
        ("b1", "d1"),
    ]


def test_result_is_stable_for_the_same_request():
    first = post(FOUR_SINGLE_QUALIFIERS, [pair("a1", "c1")]).json()
    second = post(FOUR_SINGLE_QUALIFIERS, [pair("a1", "c1")]).json()
    assert first == second


def test_infeasible_pairing_returns_422():
    # a1 blocked against all three other groups; it cannot be paired.
    response = post(
        FOUR_SINGLE_QUALIFIERS,
        [pair("a1", "b1"), pair("a1", "c1"), pair("a1", "d1")],
    )
    assert response.status_code == 422
    assert "no feasible first-round pairing" in response.json()["detail"]


def test_advancing_count_not_power_of_two_returns_422():
    groups = [
        group("A", ["a1"], 1),
        group("B", ["b1"], 1),
        group("C", ["c1"], 1),
    ]
    response = post(groups)
    assert response.status_code == 422
    assert "power of two" in response.json()["detail"]


def test_advancing_count_below_four_returns_422():
    groups = [group("A", ["a1"], 1), group("B", ["b1"], 1)]
    response = post(groups)
    assert response.status_code == 422
    assert "4" in response.json()["detail"]


def test_advancing_count_above_sixteen_returns_422():
    groups = [
        group("A", [f"a{i}" for i in range(9)], 9),
        group("B", [f"b{i}" for i in range(9)], 9),
    ]
    response = post(groups)
    assert response.status_code == 422


def test_too_few_groups_returns_422():
    response = post([group("A", ["a1", "a2"], 2)])
    assert response.status_code == 422


def test_too_many_groups_returns_422():
    response = post(
        [group(chr(65 + i), [f"{chr(97 + i)}1"], 1) for i in range(9)]
    )
    assert response.status_code == 422


def test_duplicate_group_id_returns_422():
    response = post(
        [
            group("A", ["a1"], 1),
            group("A", ["b1"], 1),
            group("C", ["c1"], 1),
            group("D", ["d1"], 1),
        ]
    )
    assert response.status_code == 422
    assert "duplicate group id" in response.json()["detail"]


def test_team_in_multiple_groups_returns_422():
    response = post(
        [
            group("A", ["shared", "a2"], 1),
            group("B", ["shared", "b2"], 1),
            group("C", ["c1"], 1),
            group("D", ["d1"], 1),
        ]
    )
    assert response.status_code == 422
    assert "more than one group" in response.json()["detail"]


def test_qualifiers_above_team_count_returns_422():
    response = post(
        [
            group("A", ["a1"], 2),
            group("B", ["b1", "b2"], 2),
        ]
    )
    assert response.status_code == 422
    assert "qualifiers" in response.json()["detail"]


def test_invalid_group_match_returns_422():
    bad = group("A", ["a1", "a2"], 1, [("a1", "ghost", 1, 0)])
    response = post(
        [
            bad,
            group("B", ["b1"], 1),
            group("C", ["c1"], 1),
            group("D", ["d1"], 1),
        ]
    )
    assert response.status_code == 422
    assert "unknown team" in response.json()["detail"]


def test_unknown_team_in_played_pair_returns_422():
    response = post(FOUR_SINGLE_QUALIFIERS, [pair("a1", "ghost")])
    assert response.status_code == 422
    assert "unknown team" in response.json()["detail"]


def test_same_group_played_pair_returns_422():
    response = post(FOUR_SINGLE_QUALIFIERS, [pair("a1", "a2")])
    assert response.status_code == 422
    assert "cross-group" in response.json()["detail"]


def test_duplicate_played_pair_returns_422():
    response = post(
        FOUR_SINGLE_QUALIFIERS, [pair("a1", "b1"), pair("b1", "a1")]
    )
    assert response.status_code == 422
    assert "duplicate played cross-group pair" in response.json()["detail"]


def test_self_match_in_played_pair_returns_422():
    response = post(FOUR_SINGLE_QUALIFIERS, [pair("a1", "a1")])
    assert response.status_code == 422


def test_eight_groups_sixteen_advancers():
    groups = [
        group(chr(65 + i), [f"{chr(97 + i)}1", f"{chr(97 + i)}2"], 2)
        for i in range(8)
    ]
    response = post(groups)
    assert response.status_code == 200
    body = response.json()
    assert body["advancing_teams"] == 16
    assert len(body["matches"]) == 8
    assert_valid_pairing(body)
    # Same-group teams occupy paired seed positions and never meet.
    seen = [
        tuple(sorted((m["team_a"][0], m["team_b"][0])))
        for m in body["matches"]
    ]
    assert all(a != b for a, b in seen)


def test_openapi_documents_the_knockout_endpoint():
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    assert "/knockout-pairings" in paths
    operation = paths["/knockout-pairings"]["post"]
    assert "KnockoutPairingRequest" in str(operation["requestBody"])
    assert "KnockoutPairingResponse" in str(operation["responses"])
    assert "422" in operation["responses"]
