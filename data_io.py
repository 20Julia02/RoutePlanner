import arcpy
from models import Graph, Node, Edge, CostMatrix
from typing import List, Tuple

def load_graph_data(nodes_path: str, edges_path: str, cost_matrix_path: str) -> Tuple[Graph, CostMatrix]:
    graph = Graph()
    cost_matrix = CostMatrix()
    
    with open(nodes_path, "r") as file:
        next(file)
        for line in file:
            row = line.split(",")
            node = Node(int(row[0]), float(row[1]), float(row[2]), int(row[3]), int(row[4]) * 60)
            graph.add_node(node)

    with open(edges_path, "r") as file:
        next(file)
        for line in file:
            row = line.split(",")
            start, end = graph.nodes[int(row[1])], graph.nodes[int(row[2])]
            edge = Edge(int(row[0]), start, end, float(row[3]), float(row[4]), int(row[5]), float(row[6]))
            graph.add_edge(edge)

    with open(cost_matrix_path, "r") as file:
        for line in file:
            row = line.split(";")
            cost_matrix.add_cost(int(row[0]), int(row[1]), float(row[2]))
            
    return graph, cost_matrix

def export_route_to_arcgis(edges_list: List[int], day: int) -> None:
    edges_str = str(edges_list)[1:-1]
    edges_expr = f"OBJECTID_1 IN ({edges_str})"
    arcpy.conversion.ExportFeatures("trasy_piesze", f"trasa_zwiedzania_{day}", edges_expr)