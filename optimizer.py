import random
import math
import heapq
import arcpy
from itertools import pairwise
from typing import List, Tuple, Optional
from models import Graph, CostMatrix, RouteResult
from shortest_path_alg import PathAlgorithm

class RouteOptimizer:
    def __init__(self, graph: Graph, cost_matrix: CostMatrix, alg: PathAlgorithm, start_id: int, duration_h: float):
        self.graph = graph
        self.cost_matrix = cost_matrix
        self.alg = alg
        self.start_id = start_id
        self.duration_sec = duration_h * 3600
        self.min_duration_sec = duration_h * 3600 * 0.85

    def calculate_score(self, nodes_list: List[int], exact_walk_time: Optional[float] = None) -> Tuple[float, float]:
        weight_sum = self.graph.get_attr_weight(nodes_list)
        attr_time = self.graph.get_attr_time(nodes_list)
        
        if exact_walk_time is not None:
            # Tryb dokładny: używamy czasu z algorytmu A* (wykorzystywane na końcu programu)
            walk_time = exact_walk_time
        else:
            # Tryb przybliżony: liczymy z macierzy (wykorzystywane przez Symulowane Wyżarzanie)
            costs = []
            for f, s in pairwise(nodes_list):
                c = self.cost_matrix.get_cost(f, s)
                if c == -1: # TODO
                    return -float('inf'), float('inf')
                costs.append(c)

            cost_start = self.alg.heuristic(self.start_id, nodes_list[0])
            cost_end = self.alg.heuristic(nodes_list[-1], self.start_id)
            walk_time = sum(costs) + cost_start + cost_end
            
        route_time = walk_time + attr_time
        
        coeff = weight_sum / walk_time
            
        if route_time > self.duration_sec:
            overtime = route_time - self.duration_sec
            penalty = 1 / (1 + 0.001 * overtime)
            coeff = coeff * penalty

        # TODO poprawić to, żeby dodawało następne atrakcje zamiast sztucznie pilnować czasu
        if route_time < self.min_duration_sec:
            undertime = self.min_duration_sec - route_time
            penalty_under = 1 / (1 + 0.001 * undertime)
            coeff = coeff * penalty_under
            
        return coeff, route_time
    
    def _get_random_combination(self) -> Tuple[Optional[float], Optional[float], Optional[List[int]]]:
        duration = 0
        attr_prev = None
        attr_list = []
        available_attrs = list(self.graph.get_attractions_id())
        
        while available_attrs:
            candidates = random.sample(available_attrs, min(3, len(available_attrs)))
            chosen_attr = None
            
            for attr_id in candidates:
                added_duration = self.graph.get_attraction_duration(attr_id)
                if attr_prev:
                    route_time = self.cost_matrix.get_cost(attr_prev, attr_id)
                    if route_time is None:
                        available_attrs.remove(attr_id)
                        continue
                    added_duration += route_time
                
                if duration + added_duration <= self.duration_sec:
                    chosen_attr = attr_id
                    duration += added_duration
                    break
                else:
                    available_attrs.remove(attr_id)

            if not chosen_attr: break
            attr_prev = chosen_attr
            attr_list.append(chosen_attr)
            available_attrs.remove(chosen_attr)
            
        if not attr_list: return None, None, None
        coeff, route_time = self.calculate_score(attr_list)
        return coeff, route_time, attr_list

    def _mutate_route(self, route: List[int]) -> List[int]:
        new_route = list(route)
        r = random.random()
        if r < 0.3 and len(new_route) >= 2:
            i, j = random.sample(range(len(new_route)), 2)
            new_route[i], new_route[j] = new_route[j], new_route[i]
        elif r < 0.6 and len(new_route) >= 2:
            i, j = sorted(random.sample(range(len(new_route)), 2))
            new_route[i:j] = reversed(new_route[i:j])
        else:
            i = random.randrange(len(new_route))
            available = list(set(self.graph.attractions.keys()) - set(new_route))
            if available:
                new_route[i] = random.choice(available)
        return new_route

    def simulated_annealing(self, initial_nodes: List[int], current_score: float, T_start=1.0, T_end=0.001, alpha=0.999) -> Tuple[float, List[int]]:
        current_attr = list(initial_nodes)
        best_attr = list(current_attr)
        best_score = current_score
        T = T_start
        
        while T > T_end:
            new_attr = self._mutate_route(current_attr)
            new_score, _ = self.calculate_score(new_attr)
            
            if new_score > current_score or math.exp((new_score - current_score) / T) > random.random():
                current_attr = new_attr
                current_score = new_score

            if current_score > best_score:
                best_score = current_score
                best_attr = list(current_attr)
                
            T *= alpha
            
        return best_score, best_attr

    def find_best_route(self, choices_nmb=1000000, best_routes_nmb=6) -> Tuple:
        sorted_best_attr = []
        for _ in range(choices_nmb):
            coeff, _, attr_list = self._get_random_combination()
            if coeff is None: 
                continue
            
            if len(sorted_best_attr) < best_routes_nmb:
                heapq.heappush(sorted_best_attr, (coeff, attr_list))
            elif coeff > sorted_best_attr[0][0]:
                heapq.heapreplace(sorted_best_attr, (coeff, attr_list))
                
        sorted_results = []
        for score, attr_list in sorted_best_attr:
            sa_score, sa_vertices = self.simulated_annealing(attr_list, score)
            
            edges_path, verts_path = [], []
            cost_path = 0
            full_vertices = [self.start_id] + sa_vertices + [self.start_id]
        
            for i in range(len(full_vertices) - 1):
                res = self.alg.solve_a_star(full_vertices[i], full_vertices[i+1], verts_path)
                if res:
                    cost_path += res.time
                    edges_path += res.edges
                    verts_path += res.nodes
                else:
                    arcpy.AddError("Punkty:", full_vertices[i], full_vertices[i+1], "Rozwiązanie", res, "\n")
                    
            new_score, total_route_time = self.calculate_score(sa_vertices, exact_walk_time=cost_path)
            route = RouteResult(
                score=new_score,
                total_time=total_route_time,
                attractions=sa_vertices,
                nodes_path=verts_path,
                edges_path=edges_path
            )
            sorted_results.append(route)
            
        sorted_results.sort(key=lambda x: x.score, reverse=True)
        return sorted_results[0]