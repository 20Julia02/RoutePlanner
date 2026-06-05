import arcpy
from models import Graph, Node, Edge, CostMatrix
from typing import List, Tuple

def load_graph_data(nodes_lyr: str, edges_lyr: str, cost_matrix_path: str) -> Tuple[Graph, CostMatrix]:
    graph = Graph()
    cost_matrix = CostMatrix()
    
    with arcpy.da.SearchCursor(nodes_lyr, ["vertex_id", "SHAPE@X", "SHAPE@Y", "weight", "time"]) as cursor:
        for vertex_id, x, y, weight, time in cursor:
            node = Node(int(vertex_id), float(x), float(y), int(weight), int(time) * 60)
            graph.add_node(node)
    
    with arcpy.da.SearchCursor(edges_lyr, ["orig_oid", "start_vertex", "end_vertex", "SHAPE@LENGTH", "walk_time", "category", "slope"]) as cursor:
        for orig_oid, start_vertex, end_vertex, length, walk_time, category, slope in cursor:
            start, end = graph.nodes[int(start_vertex)], graph.nodes[int(end_vertex)]
            edge = Edge(int(orig_oid), start, end, float(length), float(walk_time), int(category), float(slope))
            graph.add_edge(edge)

    with open(cost_matrix_path, "r") as file:
        next(file)
        for line in file:
            row = line.strip().split(",")
            cost_matrix.add_cost(int(row[0]), int(row[1]), float(row[2]))
            
    return graph, cost_matrix

def export_route_to_arcgis(edges_lyr, edges_list: List[int], day: int) -> None:
    if not edges_list:
        arcpy.AddWarning(f"Dzień {day}: Brak krawędzi do wyeksportowania (ścieżka jest pusta).")
        return

    if len(edges_list) == 1:
        edges_expr = f"orig_oid = {edges_list[0]}"
    else:
        edges_str = str(edges_list)[1:-1]
        edges_expr = f"orig_oid IN ({edges_str})"

    try:
        arcpy.conversion.ExportFeatures(edges_lyr, f"trasa_zwiedzania_{day}", edges_expr)
    except arcpy.ExecuteError:
        arcpy.AddError(f"Błąd ArcGIS podczas eksportu dla dnia {day}. Zapytanie SQL: {edges_expr}")
        arcpy.AddError(arcpy.GetMessages(2))