"""End-to-end tests for the cross-group knockout first-round API."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def post(body):
    return client.post("/knockout-first-round", json=body)


def two_groups_of_three(played_pairs=()):
    return {
        "groups": [
            {
                "group_id": "A",
                "teams": ["a1", "a2", "a3"],
                "matches": [
                    {"home": "a1", "away": "a2", "home_score": 2, "away_score": 0},
                    {"home": "a2", "away": "a3", "home_score": 1, "away_score": 1},
                    {"home": "a1", "away": "a3", "home_score": 0, "away_score": 0},
                ],
                "qualify_slots": 2,
            },
            {
                "group_id": "B",
                "teams": ["b1", "b2", "b3"],
                "matches": [
                    {"home": "b1", "away": "b2", "home_score": 2, "away_score": 0},
                    {"home": "b2", "away": "b3", "home_score": 1, "away_score": 1},
                    {"home": "b1", "away": "b3", "home_score": 0, "away_score": 0},
                ],
                "qualify_slots": 2,
            },
        ],
        "played_pairs": list(played_pairs),
    }


def assert_valid_first_round(body, expected_team_count):
    seeds = body["seeds"]
    pairings = body["pairings"]
    assert len(seeds) == expected_team_count
    assert [entry["seed"] for entry in seeds] == list(
        range(1, expected_team_count + 1)
    )
    # Seeds are sorted by (in-group rank, group id, team id).
    keys = [(s["group_rank"], s["group_id"], s["team"]) for s in seeds]
    assert keys == sorted(keys)

    # Perfect matching: every team exactly once.
    sides = sorted(
        side
        for pairing in pairings
        for side in (pairing["team_a"], pairing["team_b"])
    )
    assert sides == sorted(entry["team"] for entry in seeds)

    seed_of = {entry["team"]: entry["seed"] for entry in seeds}
    group_of = {entry["team"]: entry["group_id"] for entry in seeds}
    pair_keys = []
    cost = 0
    for pairing in pairings:
        # Teams inside a pair are lexicographically ordered.
        assert pairing["team_a"] < pairing["team_b"]
        assert pairing["seed_a"] == seed_of[pairing["team_a"]]
        assert pairing["seed_b"] == seed_of[pairing["team_b"]]
        assert pairing["group_a"] == group_of[pairing["team_a"]]
        assert pairing["group_b"] == group_of[pairing["team_b"]]
        # Hard constraints: different groups and no replays.
        assert pairing["group_a"] != pairing["group_b"]
        assert pairing["constraints"] == ["different_group", "no_replay"]
        cost += abs(pairing["seed_b"] - pairing["seed_a"])
        pair_keys.append((pairing["team_a"], pairing["team_b"]))

    # Pairings themselves are lexicographically ordered.
    assert pair_keys == sorted(pair_keys)
    return cost


def test_seeds_follow_group_ranking():
    response = post(two_groups_of_three())
    assert response.status_code == 200
    body = response.json()
    # Group order: a1(4 pts), a3(2), a2(1); same for B.
    assert [(s["seed"], s["team"], s["group_id"], s["group_rank"]) for s in body["seeds"]] == [
        (1, "a1", "A", 1),
        (2, "b1", "B", 1),
        (3, "a3", "A", 2),
        (4, "b3", "B", 2),
    ]


def test_unrestricted_minimum_matches_adjacent_seeds():
    response = post(two_groups_of_three())
    assert response.status_code == 200
    body = response.json()
    cost = assert_valid_first_round(body, 4)
    assert [(p["team_a"], p["team_b"]) for p in body["pairings"]] == [
        ("a1", "b1"),
        ("a3", "b3"),
    ]
    assert cost == 2


def test_played_pair_forces_repair_with_optimal_cost():
    # a1 vs b1 already happened; adjacent cross-group pairs are then
    # same-group, so the criss-cross (cost 4) is optimal.
    body = two_groups_of_three(played_pairs=[{"home": "a1", "away": "b1"}])
    response = post(body)
    assert response.status_code == 200
    result = response.json()
    cost = assert_valid_first_round(result, 4)
    assert [(p["team_a"], p["team_b"]) for p in result["pairings"]] == [
        ("a1", "b3"),
        ("a3", "b1"),
    ]
    assert cost == 4


def test_played_pair_is_unordered():
    body = two_groups_of_three(played_pairs=[{"home": "b1", "away": "a1"}])
    response = post(body)
    assert response.status_code == 200
    pairs = [
        (p["team_a"], p["team_b"]) for p in response.json()["pairings"]
    ]
    assert ("a1", "b1") not in pairs


def test_cost_tie_broken_by_team_id_lexicographic_order():
    body = {
        "groups": [
            {"group_id": "A", "teams": ["az", "za"], "matches": [],
             "qualify_slots": 2},
            {"group_id": "B", "teams": ["by", "yb"], "matches": [],
             "qualify_slots": 2},
        ],
        "played_pairs": [
            {"home": "az", "away": "by"},
            {"home": "za", "away": "yb"},
        ],
    }
    response = post(body)
    assert response.status_code == 200
    result = response.json()
    # Two criss-cross matchings both cost 4; the lex smaller pair list
    # starts with (az, yb).
    assert [(p["team_a"], p["team_b"]) for p in result["pairings"]] == [
        ("az", "yb"),
        ("by", "za"),
    ]
    assert_valid_first_round(result, 4)


def test_four_one_slot_groups():
    body = {
        "groups": [
            {"group_id": gid, "teams": [f"{gid.lower()}1"], "matches": [],
             "qualify_slots": 1}
            for gid in ("A", "B", "C", "D")
        ],
        "played_pairs": [],
    }
    response = post(body)
    assert response.status_code == 200
    result = response.json()
    assert [(s["seed"], s["team"]) for s in result["seeds"]] == [
        (1, "a1"), (2, "b1"), (3, "c1"), (4, "d1")
    ]
    cost = assert_valid_first_round(result, 4)
    assert cost == 2


def test_same_group_teams_can_never_meet():
    # 3 qualifiers from A and 1 from B: a1 must take b1, leaving two
    # A teams that cannot pair -> infeasible.
    body = {
        "groups": [
            {"group_id": "A", "teams": ["a1", "a2", "a3"], "matches": [],
             "qualify_slots": 3},
            {"group_id": "B", "teams": ["b1"], "matches": [],
             "qualify_slots": 1},
        ]
    }
    response = post(body)
    assert response.status_code == 422
    assert "no feasible" in response.json()["detail"]


def test_all_cross_group_pairs_played_is_infeasible():
    body = {
        "groups": [
            {"group_id": gid, "teams": [f"{gid.lower()}1"], "matches": [],
             "qualify_slots": 1}
            for gid in ("A", "B", "C", "D")
        ],
        "played_pairs": [
            {"home": "a1", "away": "b1"},
            {"home": "a1", "away": "c1"},
            {"home": "a1", "away": "d1"},
            {"home": "b1", "away": "c1"},
            {"home": "b1", "away": "d1"},
            {"home": "c1", "away": "d1"},
        ],
    }
    response = post(body)
    assert response.status_code == 422
    assert "no feasible" in response.json()["detail"]


def test_non_power_of_two_field_is_rejected():
    # 3 + 2 = 6 qualifiers.
    body = {
        "groups": [
            {"group_id": "A", "teams": ["a1", "a2", "a3"], "matches": [],
             "qualify_slots": 3},
            {"group_id": "B", "teams": ["b1", "b2", "b3"], "matches": [],
             "qualify_slots": 2},
        ]
    }
    response = post(body)
    assert response.status_code == 422
    assert "power of two" in response.json()["detail"]


def test_two_qualifiers_below_four_is_rejected():
    body = {
        "groups": [
            {"group_id": "A", "teams": ["a1"], "matches": [],
             "qualify_slots": 1},
            {"group_id": "B", "teams": ["b1"], "matches": [],
             "qualify_slots": 1},
        ]
    }
    response = post(body)
    assert response.status_code == 422
    assert "power of two" in response.json()["detail"]


def test_too_few_groups_is_rejected_by_schema():
    body = {
        "groups": [
            {"group_id": "A", "teams": ["a1"], "matches": [],
             "qualify_slots": 1},
        ]
    }
    assert post(body).status_code == 422


def test_more_than_eight_groups_is_rejected_by_schema():
    body = {
        "groups": [
            {"group_id": gid, "teams": [f"{gid.lower()}1"], "matches": [],
             "qualify_slots": 2 if gid in ("A", "B") else 1}
            for gid in ("A", "B", "C", "D", "E", "F", "G", "H", "I")
        ]
    }
    assert post(body).status_code == 422


def test_duplicate_group_id_is_rejected():
    body = {
        "groups": [
            {"group_id": "A", "teams": ["a1", "a2"], "matches": [],
             "qualify_slots": 2},
            {"group_id": "A", "teams": ["b1", "b2"], "matches": [],
             "qualify_slots": 2},
        ]
    }
    response = post(body)
    assert response.status_code == 422
    assert "duplicate group" in response.json()["detail"]


def test_team_in_two_groups_is_rejected():
    body = {
        "groups": [
            {"group_id": "A", "teams": ["shared", "a2"], "matches": [],
             "qualify_slots": 2},
            {"group_id": "B", "teams": ["shared", "b2"], "matches": [],
             "qualify_slots": 2},
        ]
    }
    response = post(body)
    assert response.status_code == 422
    assert "appears in both" in response.json()["detail"]


def test_slots_above_group_size_is_rejected():
    body = {
        "groups": [
            {"group_id": "A", "teams": ["a1"], "matches": [],
             "qualify_slots": 1},
            {"group_id": "B", "teams": ["b1", "b2"], "matches": [],
             "qualify_slots": 1},
            {"group_id": "C", "teams": ["c1", "c2"], "matches": [],
             "qualify_slots": 1},
            {"group_id": "D", "teams": ["d1", "d2", "d3"], "matches": [],
             "qualify_slots": 4},
        ]
    }
    response = post(body)
    assert response.status_code == 422
    assert "qualify_slots" in response.json()["detail"]


def test_unknown_team_in_played_pair_is_rejected():
    body = two_groups_of_three(
        played_pairs=[{"home": "a1", "away": "ghost"}]
    )
    response = post(body)
    assert response.status_code == 422
    assert "unknown team" in response.json()["detail"]


def test_duplicate_played_pair_is_rejected():
    body = two_groups_of_three(
        played_pairs=[
            {"home": "a1", "away": "b1"},
            {"home": "b1", "away": "a1"},
        ]
    )
    response = post(body)
    assert response.status_code == 422
    assert "duplicate played pair" in response.json()["detail"]


def test_self_played_pair_is_rejected():
    body = two_groups_of_three(
        played_pairs=[{"home": "a1", "away": "a1"}]
    )
    response = post(body)
    assert response.status_code == 422


def test_invalid_group_match_is_rejected():
    body = two_groups_of_three()
    body["groups"][0]["matches"].append(
        {"home": "a1", "away": "ghost", "home_score": 1, "away_score": 0}
    )
    response = post(body)
    assert response.status_code == 422
    assert "unknown team" in response.json()["detail"]


def test_same_group_played_pair_does_not_block_cross_pairing():
    # A same-group pair in played_pairs is redundant with the group
    # matches and never matches a cross-group first-round fixture.
    body = two_groups_of_three(
        played_pairs=[{"home": "a1", "away": "a3"}]
    )
    response = post(body)
    assert response.status_code == 200
    pairs = [
        (p["team_a"], p["team_b"]) for p in response.json()["pairings"]
    ]
    assert pairs == [("a1", "b1"), ("a3", "b3")]


def test_sixteen_team_field_is_solved():
    body = {
        "groups": [
            {
                "group_id": chr(65 + i),
                "teams": [f"{chr(97 + i)}1", f"{chr(97 + i)}2"],
                "matches": [],
                "qualify_slots": 2,
            }
            for i in range(8)
        ]
    }
    response = post(body)
    assert response.status_code == 200
    result = response.json()
    assert_valid_first_round(result, 16)


def test_same_request_is_stable():
    body = two_groups_of_three(played_pairs=[{"home": "a1", "away": "b1"}])
    assert post(body).json() == post(body).json()


def test_existing_endpoints_are_unchanged():
    standings = client.post(
        "/standings",
        json={
            "teams": ["alpha", "bravo"],
            "matches": [
                {"home": "alpha", "away": "bravo",
                 "home_score": 1, "away_score": 0}
            ],
        },
    )
    assert standings.status_code == 200
    assert standings.json()["standings"][0]["team"] == "alpha"

    schedule = client.post(
        "/round-robin-schedules",
        json={"teams": ["alpha", "bravo"], "rounds": 1},
    )
    assert schedule.status_code == 200


def test_openapi_documents_the_knockout_endpoint():
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    assert "/knockout-first-round" in paths
    operation = paths["/knockout-first-round"]["post"]
    assert "KnockoutFirstRoundRequest" in str(operation["requestBody"])
    assert "KnockoutFirstRoundResponse" in str(operation["responses"])
    assert "422" in operation["responses"]
