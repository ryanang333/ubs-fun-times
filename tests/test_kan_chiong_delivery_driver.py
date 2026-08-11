import json

from app import app
from kan_chiong_delivery_driver import solve


def test_obstruction_can_begin_during_traversal():
    data = {
        "start_coordinate": [0, 0],
        "end_coordinate": [1, 0],
        "start_time": "2026-06-10T08:30:00Z",
        "nodes": [[0, 0], [1, 0]],
        "edges": [
            {
                "edge_id": "edge",
                "node1": [0, 0],
                "node2": [1, 0],
                "base_duration_sec": 30,
            }
        ],
        "obstructions": [
            {
                "edge_id": "edge",
                "edge": {"from": [0, 0], "to": [1, 0]},
                "start_time": "2026-06-10T08:30:10Z",
                "end_time": "2026-06-10T09:00:00Z",
                "speed_factor": 0.5,
            }
        ],
    }

    assert json.loads(solve(json.dumps(data))) == {
        "total_duration_sec": 50,
        "arrival_time": "2026-06-10T08:30:50Z",
        "path": ["edge"],
    }


def test_no_wait_route_can_revisit_nodes():
    data = {
        "start_coordinate": [0, 0],
        "end_coordinate": [2, 0],
        "start_time": "2026-06-10T08:30:00Z",
        "nodes": [[0, 0], [1, 0], [2, 0]],
        "edges": [
            {"edge_id": "edge_0", "node1": [0, 0], "node2": [1, 0], "base_duration_sec": 10},
            {"edge_id": "edge_1", "node1": [1, 0], "node2": [2, 0], "base_duration_sec": 10},
            {"edge_id": "edge_2", "node1": [0, 0], "node2": [2, 0], "base_duration_sec": 20},
        ],
        "obstructions": [
            {
                "edge_id": "edge_1",
                "edge": {"from": [1, 0], "to": [2, 0]},
                "start_time": start,
                "end_time": end,
                "speed_factor": 0.0,
            }
            for start, end in [
                ("2026-06-10T08:30:10Z", "2026-06-10T08:30:20Z"),
                ("2026-06-10T08:30:30Z", "2026-06-10T08:30:40Z"),
            ]
        ]
        + [
            {
                "edge_id": "edge_2",
                "edge": {"from": [0, 0], "to": [2, 0]},
                "start_time": "2026-06-10T08:30:00Z",
                "end_time": "2026-06-10T08:32:00Z",
                "speed_factor": 0.2,
            }
        ],
    }

    assert json.loads(solve(json.dumps(data))) == {
        "total_duration_sec": 60,
        "arrival_time": "2026-06-10T08:31:00Z",
        "path": ["edge_0", "edge_0", "edge_0", "edge_0", "edge_0", "edge_1"],
    }


def test_delivery_driver_endpoint():
    data = {
        "start_coordinate": [0, 0],
        "end_coordinate": [1, 0],
        "start_time": "2026-06-10T08:30:00Z",
        "nodes": [[0, 0], [1, 0]],
        "edges": [
            {
                "edge_id": "edge_0",
                "node1": [0, 0],
                "node2": [1, 0],
                "base_duration_sec": 60,
            }
        ],
        "obstructions": [],
    }

    with app.test_client() as client:
        response = client.post("/kan-cheong-delivery-driver", json=data)

    assert response.status_code == 200
    assert response.get_json() == {
        "total_duration_sec": 60,
        "arrival_time": "2026-06-10T08:31:00Z",
        "path": ["edge_0"],
    }
