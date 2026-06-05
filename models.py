from dataclasses import dataclass
from typing import Dict, List, Tuple, Set, Optional

@dataclass
class Node:
    id: int
    x: float
    y: float
    weight: int
    time: float

@dataclass
class Edge:
    id: int
    start: Node
    end: Node
    length: float
    time: float
    category: int
    slope: float

class CostMatrix:
    def __init__(self):
        self._matrix: Dict[Tuple[int, int], float] = {}

    def add_cost(self, source: int, target: int, cost: float) -> None:
        self._matrix[(source, target)] = cost

    def get_cost(self, source: int, target: int) -> Optional[float]:
        return self._matrix.get((source, target))
        
        
class Graph:
    def __init__(self):
        self._nodes: Dict[int, Node] = {}
        self._attractions: Dict[int, Node] = {}
        self._edges: Dict[int, Edge] = {}
        self._adj: Dict[int, List[Tuple[int, Edge]]] = {}

    # Read-only properties
    @property
    def nodes(self) -> Dict[int, Node]:
        return self._nodes
    
    @property
    def attractions(self) -> Dict[int, Node]:
        return self._attractions

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

    def neighbors(self, node_id: int) -> List[Tuple[int, Edge]]:
        return self._adj.get(node_id, [])

    def get_attractions_id(self) -> List[int]:
        return list(self._attractions.keys())

    def get_attraction_duration(self, attr_id: int) -> Optional[float]:
        attr = self._attractions.get(attr_id)
        return attr.time if attr else None

    def del_available_attractions(self, attrs_to_del: Set[int]) -> None:
        for attr in attrs_to_del:
            self._attractions.pop(attr, None)

    def get_attr_weight(self, attractions_in_route: List[int]) -> Tuple[int, float]:
        weight_sum = 0
        for node_id in attractions_in_route:
            attr = self._attractions.get(node_id)
            if attr:
                weight_sum += attr.weight
        return weight_sum
    
    def get_attr_time(self, attractions_in_route: List[int]) -> Tuple[int, float]:
        time_sum = 0.0
        for node_id in attractions_in_route:
            attr = self._attractions.get(node_id)
            if attr:
                time_sum += attr.time
        return time_sum

    def get_attractions_from_nodes(self, nodes_ids: List[int]) -> Set[int]:
        return set(nodes_ids) & set(self._attractions.keys())
    
@dataclass
class PathResult:
    time: float
    length: float
    nodes: List[int]
    edges: List[int]

@dataclass
class RouteResult:
    score: float
    total_time: float
    attractions: List[int]
    nodes_path: List[int]
    edges_path: List[int]