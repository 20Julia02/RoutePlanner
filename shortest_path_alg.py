import bisect
from typing import Tuple, List, Optional
from models import Graph, PathResult

class _Priority:
    """Kolejka priorytetowa używana wewnętrznie przez algorytmy."""
    def __init__(self, f=lambda x: x):
        self.L = []
        self.f = f
    def append(self, item):
        bisect.insort(self.L, (self.f(item), item))
    def __len__(self):
        return len(self.L)
    def smallest(self):
        return self.L.pop(0)[1]

def _reconstruct_path(end_id: int, prev: dict) -> Tuple[float, List[int], List[int]]:
    """Prywatna funkcja pomocnicza do odtwarzania ścieżki po wykonaniu A*."""
    path_nodes = [end_id]
    path_edges = []
    current = end_id

    while current in prev:
        current, edge = prev[current]
        path_edges.append(edge)
        path_nodes.append(current)

    path_nodes.reverse()
    path_edges.reverse()
    total_length = sum(e.length for e in path_edges)
    return total_length, path_nodes, [e.id for e in path_edges]

class PathAlgorithm:
    def __init__(self, graph: Graph):
        self._graph = graph

    def heuristic(self, node_id: int, goal_id: int) -> float:
        n1 = self._graph.nodes[node_id]
        n2 = self._graph.nodes[goal_id]
        dist = ((n2.x - n1.x)**2 + (n2.y - n1.y)**2)**0.5
        return dist / 1.1

    def solve_a_star(self, start_id: int, end_id: int, penalties: List[int]) -> Optional[Tuple]:
        pq = _Priority(lambda x: x[0])
        pq.append((self.heuristic(start_id, end_id), start_id))
        
        visited = set()
        cost = {start_id: 0}
        real_time = {start_id: 0}
        prev = {}
        neighbors_checked = 0

        while len(pq) > 0:
            u = pq.smallest()
            if u in visited: continue
            visited.add(u)

            if u == end_id: break

            for v, edge in self._graph.neighbors(u):
                neighbors_checked += 1
                new_cost = cost[u] + edge.time
                new_real_time = real_time[u] + edge.time
                
                # Kara za wierzchołki, przez które już przeszliśmy
                if v in penalties:
                    new_cost += edge.time * 10

                if v not in cost or new_cost < cost[v]:
                    cost[v] = new_cost
                    real_time[v] = new_real_time
                    prev[v] = (u, edge)
                    f_cost = new_cost + self.heuristic(v, end_id)
                    pq.append((f_cost, v))

        if end_id not in cost: return None

        total_len, verts, edges = _reconstruct_path(end_id, prev)
        return PathResult(
            time=real_time[end_id],
            length=total_len,
            nodes=verts,
            edges=edges
        )