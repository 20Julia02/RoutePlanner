from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass
class Node:
    """A walking-network vertex with optional aggregated attraction values."""

    id: int
    x: float
    y: float
    weight: int
    time: float


@dataclass
class Edge:
    """An undirected walking-network edge."""

    id: int
    start: Node
    end: Node
    length: float
    time: float


class CostMatrix:
    """Shortest travel times keyed by source and target vertex IDs."""

    def __init__(self):
        self._matrix: Dict[Tuple[int, int], float] = {}

    def add_cost(self, source: int, target: int, cost: float) -> None:
        self._matrix[(source, target)] = cost

    def get_cost(self, source: int, target: int) -> Optional[float]:
        return self._matrix.get((source, target))

    def items(self):
        return self._matrix.items()

    def __len__(self) -> int:
        return len(self._matrix)


class Graph:
    """Undirected graph with a separate index of attraction vertices."""

    def __init__(self):
        self._nodes: Dict[int, Node] = {}
        self._attractions: Dict[int, Node] = {}
        self._edges: Dict[int, Edge] = {}
        self._adj: Dict[int, List[Tuple[int, Edge]]] = {}

    @property
    def nodes(self) -> Dict[int, Node]:
        return self._nodes

    @property
    def attractions(self) -> Dict[int, Node]:
        return self._attractions

    @property
    def edges(self) -> Dict[int, Edge]:
        return self._edges

    def add_node(self, node: Node) -> None:
        self._nodes[node.id] = node
        if node.weight > 0:
            self._attractions[node.id] = node
        self._adj.setdefault(node.id, [])

    def add_edge(self, edge: Edge) -> None:
        self._edges[edge.id] = edge
        u, v = edge.start.id, edge.end.id
        self._adj[u].append((v, edge))
        self._adj[v].append((u, edge))

    def set_attraction_values(self, node_id: int, weight: int, duration: float) -> None:
        node = self._nodes[node_id]
        node.weight = weight
        node.time = duration
        if weight > 0:
            self._attractions[node_id] = node
        else:
            self._attractions.pop(node_id, None)

    def copy_for_attraction_edits(self) -> "Graph":
        """Copy mutable node values while sharing read-only routing topology."""

        graph = Graph()
        graph._nodes = {
            node_id: replace(node) for node_id, node in self._nodes.items()
        }
        graph._attractions = {
            node_id: graph._nodes[node_id] for node_id in self._attractions
        }
        graph._edges = self._edges
        graph._adj = self._adj
        return graph

    def neighbors(self, node_id: int) -> List[Tuple[int, Edge]]:
        return self._adj.get(node_id, [])

    def get_attraction_duration(self, attr_id: int) -> Optional[float]:
        attr = self._attractions.get(attr_id)
        return attr.time if attr else None

    def get_attr_weight(self, attraction_ids: Iterable[int]) -> int:
        """Sum attraction weights for IDs that still exist in the graph."""

        return sum(
            attraction.weight
            for node_id in attraction_ids
            if (attraction := self._attractions.get(node_id)) is not None
        )

    def get_attr_time(self, attraction_ids: Iterable[int]) -> float:
        """Sum visit durations for IDs that still exist in the graph."""

        return sum(
            attraction.time
            for node_id in attraction_ids
            if (attraction := self._attractions.get(node_id)) is not None
        )


@dataclass
class PathResult:
    """A path between two graph vertices."""

    time: float
    length: float
    nodes: List[int]
    edges: List[int]


@dataclass
class RouteResult:
    """A closed route through an ordered collection of attractions."""

    score: float
    total_time: float
    attractions: List[int]
    nodes_path: List[int]
    edges_path: List[int]
