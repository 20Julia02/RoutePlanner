from data_io import load_graph_data, export_route_to_arcgis
from shortest_path_alg import PathAlgorithm
from optimizer import RouteOptimizer

def main():
    nodes_path = r"C:\Users\DELL\Documents\ArcGIS\Projects\Dane_rzym\wierzcholki_wt.csv"
    edges_path = r"C:\Julia\praca_dyplomowa\krawedzie.csv"
    cost_matrix_path = r"C:\Julia\praca_dyplomowa\krawedzie.csv"
    
    start_id = 20653
    days = 4
    max_hours_per_day = 12

    graph, cost_matrix = load_graph_data(nodes_path, edges_path, cost_matrix_path)
    alg = PathAlgorithm(graph)

    for day in range(1, days + 1):
        optimizer = RouteOptimizer(graph, cost_matrix, alg, start_id, duration_h=max_hours_per_day)

        best_route = optimizer.find_best_route()
        
        print(f"Wybrane atrakcje: {best_route.attractions}")
        print(f"Czas (h): {best_route.total_time / 3600:.2f}")
        print(f"Waga: {best_route.score:.4f}\n")

        export_route_to_arcgis(best_route.edges_path, day)

        graph.del_available_attractions(set(best_route.attractions))

if __name__ == "__main__":
    main()