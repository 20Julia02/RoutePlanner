import arcpy
from data_io import load_graph_data, export_route_to_arcgis
from shortest_path_alg import PathAlgorithm
from optimizer import RouteOptimizer
from utils import get_nearest_vertex_id

def main():
    nodes_lyr_path = arcpy.GetParameterAsText(0) # r"C:\Users\DELL\Documents\ArcGIS\Projects\Dane_rzym\wierzcholki_wt.csv"
    edges_lyr_path = arcpy.GetParameterAsText(1) # r"C:\Julia\praca_dyplomowa\krawedzie.csv"
    cost_matrix_path = arcpy.GetParameterAsText(2) # r"C:\Julia\praca_dyplomowa\cost_matrix.csv"
    
    hotel = arcpy.GetParameter(3)
    days = int(arcpy.GetParameterAsText(4))
    max_hours_per_day = int(arcpy.GetParameterAsText(5))

    graph, cost_matrix = load_graph_data(nodes_lyr_path, edges_lyr_path, cost_matrix_path)
    alg = PathAlgorithm(graph)

    start_id = get_nearest_vertex_id(hotel, nodes_lyr_path)

    for day in range(1, days + 1):
        optimizer = RouteOptimizer(graph, cost_matrix, alg, start_id, duration_h=max_hours_per_day)

        best_route = optimizer.find_best_route()
        
        arcpy.AddMessage(f"Dzień {day} - Wybrane atrakcje: {best_route.attractions}")
        arcpy.AddMessage(f"Czas (h): {best_route.total_time / 3600:.2f}")
        arcpy.AddMessage(f"Waga: {best_route.score:.4f}\n")

        export_route_to_arcgis(edges_lyr_path, best_route.edges_path, day)

        graph.del_available_attractions(set(best_route.attractions))

if __name__ == "__main__":
    main()