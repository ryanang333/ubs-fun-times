from __future__ import annotations

import heapq
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import count
from typing import Any


Coordinate = tuple[int, int]
EPSILON = 1e-9


@dataclass(frozen=True)
class Obstruction:
    start: float
    end: float
    speed_factor: float


@dataclass(frozen=True)
class DirectedEdge:
    edge_id: str
    destination: Coordinate
    base_duration: float
    obstructions: tuple[Obstruction, ...]


def solve(data: str) -> str:
    """Return the fastest no-wait route for the JSON challenge input."""
    payload = json.loads(data)
    start = _coordinate(payload["start_coordinate"])
    destination = _coordinate(payload["end_coordinate"])
    departure = _parse_time(payload["start_time"])

    if start == destination:
        return _encode_result(0.0, departure, [])

    graph, traffic_end = _build_graph(payload)
    if start not in graph:
        return _encode_unreachable()

    # Before traffic_end, a later visit to the same node can be useful because
    # an earlier visit may have every useful outgoing edge blocked. Therefore
    # each distinct (node, arrival time) is a search state. Once all traffic has
    # ended, the earliest label at a node safely dominates every later label.
    sequence = count()
    queue: list[tuple[float, int, Coordinate, tuple[str, ...]]] = [
        (departure, next(sequence), start, ())
    ]
    seen_before_clear: set[tuple[Coordinate, float]] = {
        (start, _time_key(departure))
    }
    best_after_clear: dict[Coordinate, float] = {}
    if departure >= traffic_end:
        best_after_clear[start] = departure

    while queue:
        arrival, _, node, path = heapq.heappop(queue)
        if arrival >= traffic_end:
            best = best_after_clear.get(node)
            if best is not None and arrival > best + EPSILON:
                continue

        if node == destination:
            return _encode_result(arrival - departure, arrival, list(path))

        for edge in graph.get(node, ()):
            next_arrival = _traverse(edge, arrival)
            if next_arrival is None or not math.isfinite(next_arrival):
                continue

            if next_arrival >= traffic_end:
                previous_best = best_after_clear.get(edge.destination)
                if previous_best is not None and next_arrival >= previous_best - EPSILON:
                    continue
                best_after_clear[edge.destination] = next_arrival
            else:
                state = (edge.destination, _time_key(next_arrival))
                if state in seen_before_clear:
                    continue
                seen_before_clear.add(state)

            heapq.heappush(
                queue,
                (
                    next_arrival,
                    next(sequence),
                    edge.destination,
                    path + (edge.edge_id,),
                ),
            )

    return _encode_unreachable()


def _build_graph(
    payload: dict[str, Any],
) -> tuple[dict[Coordinate, list[DirectedEdge]], float]:
    obstruction_map: dict[
        tuple[str, Coordinate, Coordinate], list[Obstruction]
    ] = defaultdict(list)
    traffic_end = float("-inf")

    for raw in payload.get("obstructions", []):
        start = _parse_time(raw["start_time"])
        end = _parse_time(raw["end_time"])
        obstruction = Obstruction(start, end, float(raw["speed_factor"]))
        key = (
            raw["edge_id"],
            _coordinate(raw["edge"]["from"]),
            _coordinate(raw["edge"]["to"]),
        )
        obstruction_map[key].append(obstruction)
        traffic_end = max(traffic_end, end)

    graph: dict[Coordinate, list[DirectedEdge]] = defaultdict(list)
    for raw in payload.get("edges", []):
        edge_id = raw["edge_id"]
        node1 = _coordinate(raw["node1"])
        node2 = _coordinate(raw["node2"])
        duration = float(raw["base_duration_sec"])
        graph[node1].append(
            DirectedEdge(
                edge_id,
                node2,
                duration,
                tuple(obstruction_map.get((edge_id, node1, node2), ())),
            )
        )
        graph[node2].append(
            DirectedEdge(
                edge_id,
                node1,
                duration,
                tuple(obstruction_map.get((edge_id, node2, node1), ())),
            )
        )

    return graph, traffic_end


def _traverse(edge: DirectedEdge, departure: float) -> float | None:
    """Integrate progress as obstruction speed factors change over time."""
    current = departure
    remaining = edge.base_duration
    boundaries = sorted(
        {
            boundary
            for obstruction in edge.obstructions
            for boundary in (obstruction.start, obstruction.end)
            if boundary > departure + EPSILON
        }
    )

    factor = _speed_factor(edge.obstructions, current)
    # A traversal cannot be initiated while its direction is blocked. If a
    # block starts later, progress pauses on the edge until the factor changes.
    if factor <= 0.0:
        return None
    if remaining <= EPSILON:
        return current

    boundary_index = 0
    while remaining > EPSILON:
        factor = _speed_factor(edge.obstructions, current)
        while (
            boundary_index < len(boundaries)
            and boundaries[boundary_index] <= current + EPSILON
        ):
            boundary_index += 1

        next_boundary = (
            boundaries[boundary_index]
            if boundary_index < len(boundaries)
            else None
        )

        if factor <= 0.0:
            if next_boundary is None:
                return None
            current = next_boundary
            continue

        if next_boundary is None:
            return current + remaining / factor

        progress = factor * (next_boundary - current)
        if progress + EPSILON >= remaining:
            return current + remaining / factor

        remaining -= progress
        current = next_boundary

    return current


def _speed_factor(
    obstructions: tuple[Obstruction, ...], timestamp: float
) -> float:
    active = [
        obstruction.speed_factor
        for obstruction in obstructions
        if obstruction.start <= timestamp < obstruction.end
    ]
    return min(active, default=1.0)


def _parse_time(value: str) -> float:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.timestamp()


def _coordinate(value: list[int]) -> Coordinate:
    return int(value[0]), int(value[1])


def _time_key(value: float) -> float:
    return round(value, 9)


def _encode_result(duration: float, arrival: float, path: list[str]) -> str:
    rounded_duration: int | float
    if abs(duration - round(duration)) <= EPSILON:
        rounded_duration = int(round(duration))
    else:
        rounded_duration = round(duration, 9)

    arrival_time = datetime.fromtimestamp(arrival, timezone.utc).isoformat(
        timespec="microseconds" if abs(arrival - round(arrival)) > EPSILON else "seconds"
    )
    arrival_time = arrival_time.replace("+00:00", "Z")
    return json.dumps(
        {
            "total_duration_sec": rounded_duration,
            "arrival_time": arrival_time,
            "path": path,
        },
        separators=(",", ":"),
    )


def _encode_unreachable() -> str:
    return json.dumps(
        {"total_duration_sec": None, "arrival_time": None, "path": []},
        separators=(",", ":"),
    )
