from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Iterable


LOOKBACK = timedelta(hours=24)

# Phase 1 deliberately uses stable bands. Later phases can add signals within
# these bands without changing the structural ordering.
ISOLATED_SCORE = 0.0
EXTENSION_SCORE = 0.15
CONVERGENCE_SCORE = 0.45
RETURN_SCORE = 0.70
MULTI_LOOP_SCORE = 0.95

IDENTITY_DIVERGENCE_BONUS = 0.03
DISCONNECTED_IDENTITY_BONUS = 0.06
STRUCTURAL_IDENTITY_BONUS = 0.08
MAX_DISCONNECTED_IDENTITY_BONUS = 0.18

CONSISTENT_DECAY_BONUS = 0.02
BRANCH_RETENTION_BONUS = 0.06
VALUE_REVERSAL_BONUS = 0.50
LOCAL_VALUE_INCREASE_BONUS = 0.12
MIN_RETAINED_RATIO = 0.85


@dataclass(frozen=True)
class ActiveTransaction:
    transaction: dict[str, Any]
    created_at: datetime


class GhostChainsProcessor:
    """Maintain a 24-hour directed transaction graph and score new edges."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._active: list[ActiveTransaction] = []
        self._scores_by_tx_id: dict[str, float] = {}
        self._watermark: datetime | None = None

    def reset(self) -> None:
        """Restore all graph, cache, and derived state to startup state."""
        with self._lock:
            self._active.clear()
            self._scores_by_tx_id.clear()
            self._watermark = None

    def process_batch(
        self, transactions: Iterable[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Atomically process a request's transactions in input order."""
        results: list[dict[str, Any]] = []
        with self._lock:
            for transaction in transactions:
                tx_id = transaction["txId"]
                if tx_id in self._scores_by_tx_id:
                    results.append(
                        {"txId": tx_id, "riskScore": self._scores_by_tx_id[tx_id]}
                    )
                    continue

                created_at = parse_timestamp(transaction["createdAt"])
                self._advance_window(created_at)
                adjacency = self._build_adjacency()
                structural_score = self._structural_score(transaction, adjacency)
                identity_score = self._identity_score(
                    transaction, adjacency, structural_score
                )
                value_score = self._value_score(transaction, adjacency)
                score = min(
                    1.0,
                    round(structural_score + identity_score + value_score, 6),
                )

                self._scores_by_tx_id[tx_id] = score
                # Transactions older than the current active window are scored
                # and remembered for idempotency, but cannot alter current state.
                if self._watermark is not None and created_at >= self._watermark - LOOKBACK:
                    self._active.append(
                        ActiveTransaction(deepcopy(transaction), created_at)
                    )
                results.append({"txId": tx_id, "riskScore": score})

        return results

    def _advance_window(self, created_at: datetime) -> None:
        if self._watermark is None or created_at > self._watermark:
            self._watermark = created_at

        cutoff = self._watermark - LOOKBACK
        self._active = [item for item in self._active if item.created_at >= cutoff]

    def _build_adjacency(self) -> dict[str, set[str]]:
        adjacency: dict[str, set[str]] = defaultdict(set)
        for item in self._active:
            transaction = item.transaction
            adjacency[transaction["fromUserId"]].add(transaction["toUserId"])
        return adjacency

    def _structural_score(
        self, transaction: dict[str, Any], adjacency: dict[str, set[str]]
    ) -> float:
        source = transaction["fromUserId"]
        target = transaction["toUserId"]
        nodes = set(adjacency)
        nodes.update(node for neighbours in adjacency.values() for node in neighbours)

        # Adding source -> target returns to an upstream node and closes a loop.
        if source == target or _is_reachable(adjacency, target, source):
            # Closing another loop onto a node already participating in a cycle
            # is the Phase 1 multi-loop signal.
            if _node_is_in_cycle(adjacency, target):
                return MULTI_LOOP_SCORE
            return RETURN_SCORE

        # Convergence exists when an ancestor of the source already has a
        # separate route to the proposed target.
        reverse = _reverse_graph(adjacency)
        for ancestor in _reachable_nodes(reverse, source):
            if ancestor != source and _is_reachable(adjacency, ancestor, target):
                return CONVERGENCE_SCORE

        # A new flow entering a node that already receives another flow is also
        # convergence when the two upstream components were previously separate.
        if reverse.get(target):
            return CONVERGENCE_SCORE

        if source in nodes or target in nodes:
            return EXTENSION_SCORE
        return ISOLATED_SCORE

    def _identity_score(
        self,
        transaction: dict[str, Any],
        adjacency: dict[str, set[str]],
        structural_score: float,
    ) -> float:
        """Return independent IP and device evidence for the proposed edge.

        Shared identity on a routine linear flow is neutral. It becomes useful
        evidence when it aligns with convergence/return structure, crosses
        disconnected graph components, or changes at an entity handoff.
        """
        bonus = 0.0
        source = transaction["fromUserId"]
        target = transaction["toUserId"]
        undirected = _undirected_graph(adjacency)
        current_component = _weak_component(undirected, {source, target})

        for field in ("ipAddress", "deviceId"):
            value = transaction.get(field)
            if value is None:
                continue

            matching_edges: list[tuple[str, str]] = []
            handoff_values: set[str] = set()
            for item in self._active:
                previous = item.transaction
                previous_value = previous.get(field)
                previous_source = previous["fromUserId"]
                previous_target = previous["toUserId"]

                if previous_value == value:
                    matching_edges.append((previous_source, previous_target))

                # Identity at edges touching the sender describes the handoff
                # into/out of this transaction's position in the graph.
                if (
                    previous_value is not None
                    and source in (previous_source, previous_target)
                ):
                    handoff_values.add(previous_value)

            if any(previous_value != value for previous_value in handoff_values):
                bonus += IDENTITY_DIVERGENCE_BONUS

            connected_match = any(
                edge_source in current_component or edge_target in current_component
                for edge_source, edge_target in matching_edges
            )
            if connected_match and structural_score >= CONVERGENCE_SCORE:
                bonus += STRUCTURAL_IDENTITY_BONUS

            disconnected_components = _matching_disconnected_components(
                matching_edges, current_component, undirected
            )
            bonus += min(
                MAX_DISCONNECTED_IDENTITY_BONUS,
                DISCONNECTED_IDENTITY_BONUS * len(disconnected_components),
            )

        return bonus

    def _value_score(
        self,
        transaction: dict[str, Any],
        adjacency: dict[str, set[str]],
    ) -> float:
        """Score amount progression inside the transaction's flow segment."""
        previous_amounts, boundary = self._infer_value_segment(
            transaction["fromUserId"], adjacency
        )
        if not previous_amounts:
            return 0.0

        amounts = previous_amounts + [float(transaction["amount"])]

        # A reversal is meaningful only when at least two earlier hops already
        # established a decreasing trajectory. This avoids comparing unrelated
        # branch allocations as though they were one global ratio.
        if len(previous_amounts) >= 3:
            established_decay = all(
                earlier > later
                for earlier, later in zip(previous_amounts, previous_amounts[1:])
            )
            if established_decay and amounts[-1] > amounts[-2]:
                return VALUE_REVERSAL_BONUS

        if amounts[-1] > amounts[-2]:
            return LOCAL_VALUE_INCREASE_BONUS

        ratios = [
            later / earlier
            for earlier, later in zip(amounts, amounts[1:])
            if earlier > 0
        ]
        retains_most_value = len(ratios) == len(amounts) - 1 and all(
            MIN_RETAINED_RATIO <= ratio < 1.0 for ratio in ratios
        )
        if not retains_most_value:
            return 0.0

        if boundary == "branch":
            return BRANCH_RETENTION_BONUS
        if len(amounts) >= 4:
            return CONSISTENT_DECAY_BONUS
        return 0.0

    def _infer_value_segment(
        self,
        source: str,
        adjacency: dict[str, set[str]],
    ) -> tuple[list[float], str | None]:
        """Walk backward until flow becomes structurally ambiguous."""
        amounts_reversed: list[float] = []
        current = source
        visited: set[str] = set()
        boundary: str | None = None

        while current not in visited:
            visited.add(current)
            incoming = [
                item
                for item in self._active
                if item.transaction["toUserId"] == current
            ]
            if len(incoming) != 1:
                if len(incoming) > 1:
                    boundary = "convergence"
                break

            edge = incoming[0].transaction
            amounts_reversed.append(float(edge["amount"]))
            predecessor = edge["fromUserId"]
            if len(adjacency.get(predecessor, ())) > 1:
                boundary = "branch"
                break
            current = predecessor

        amounts_reversed.reverse()
        return amounts_reversed, boundary


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO 8601 timestamp and normalize it to UTC."""
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("createdAt must include a timezone")
    return parsed.astimezone(timezone.utc)


def _is_reachable(
    adjacency: dict[str, set[str]], start: str, destination: str
) -> bool:
    return destination in _reachable_nodes(adjacency, start)


def _reachable_nodes(adjacency: dict[str, set[str]], start: str) -> set[str]:
    visited: set[str] = set()
    pending = list(adjacency.get(start, ()))
    while pending:
        node = pending.pop()
        if node in visited:
            continue
        visited.add(node)
        pending.extend(adjacency.get(node, ()))
    return visited


def _reverse_graph(adjacency: dict[str, set[str]]) -> dict[str, set[str]]:
    reverse: dict[str, set[str]] = defaultdict(set)
    for source, targets in adjacency.items():
        for target in targets:
            reverse[target].add(source)
    return reverse


def _undirected_graph(
    adjacency: dict[str, set[str]],
) -> dict[str, set[str]]:
    undirected: dict[str, set[str]] = defaultdict(set)
    for source, targets in adjacency.items():
        for target in targets:
            undirected[source].add(target)
            undirected[target].add(source)
    return undirected


def _weak_component(
    undirected: dict[str, set[str]], starts: set[str]
) -> set[str]:
    component = set(starts)
    pending = list(starts)
    while pending:
        node = pending.pop()
        for neighbour in undirected.get(node, ()):
            if neighbour not in component:
                component.add(neighbour)
                pending.append(neighbour)
    return component


def _matching_disconnected_components(
    matching_edges: list[tuple[str, str]],
    current_component: set[str],
    undirected: dict[str, set[str]],
) -> set[frozenset[str]]:
    components: set[frozenset[str]] = set()
    for source, target in matching_edges:
        if source in current_component or target in current_component:
            continue
        components.add(frozenset(_weak_component(undirected, {source, target})))
    return components


def _node_is_in_cycle(adjacency: dict[str, set[str]], node: str) -> bool:
    return node in _reachable_nodes(adjacency, node)


processor = GhostChainsProcessor()
