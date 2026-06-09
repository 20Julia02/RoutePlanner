import arcpy
import os
import heapq
import csv

def dijkstra(graph, start):
    distances = {node: float('inf') for node in graph}
    distances[start] = 0
    pq = [(0, start)]
    
    while pq:
        current_dist, current_node = heapq.heappop(pq)
        
        if current_dist > distances[current_node]:
            continue
            
        for neighbor, weight in graph[current_node].items():
            distance = current_dist + weight
            
            if distance < distances.get(neighbor, float('inf')):
                distances[neighbor] = distance
                heapq.heappush(pq, (distance, neighbor))
                
    return distances

def main():
    edges_fc = arcpy.GetParameterAsText(0) 
    monuments_fc = arcpy.GetParameterAsText(1)
    out_workspace = arcpy.GetParameterAsText(2)
    search_radius = arcpy.GetParameterAsText(3)

    fld_edge_walk = arcpy.GetParameterAsText(4)      # u mnie impedance_
    fld_edge_cat = arcpy.GetParameterAsText(5)       # c_tvniv2
    fld_edge_slope = arcpy.GetParameterAsText(6)     # pct_pente
    fld_monument_weight = arcpy.GetParameterAsText(7)# views
    fld_monument_time = arcpy.GetParameterAsText(8)  # time

    out_csv_file = arcpy.GetParameterAsText(9)       

    arcpy.env.overwriteOutput = True
    
    out_vertices_fc = os.path.join(out_workspace, "wierzcholki_grafu")
    out_edges_fc = os.path.join(out_workspace, "krawedzie_grafu")
    near_table = os.path.join(out_workspace, "Relacja_Zabytki_Wierzcholki")

    spatial_ref = arcpy.Describe(edges_fc).spatialReference
    
    arcpy.management.CreateFeatureclass(out_workspace, "wierzcholki_grafu", "POINT", spatial_reference=spatial_ref)
    arcpy.management.AddFields(
        out_vertices_fc,
        [
            ["vertex_id", "LONG"],
            ["weight", "DOUBLE"],
            ["time", "DOUBLE"],
        ],
    )
    
    arcpy.management.CreateFeatureclass(out_workspace, "krawedzie_grafu", "POLYLINE", spatial_reference=spatial_ref)
    arcpy.management.AddFields(
        out_edges_fc,
        [
            ["orig_oid", "LONG"],
            ["walk_time", "DOUBLE"],
            ["category", "TEXT"],
            ["slope", "DOUBLE"],
            ["start_vertex", "LONG"],
            ["end_vertex", "LONG"],
        ],
    )

    vertex_dict = {}
    vertex_geoms = {}
    current_vertex_id = 1
    
    in_edge_fields = ["OID@", fld_edge_walk, fld_edge_cat, fld_edge_slope, "SHAPE@"]
    out_edge_fields = ["orig_oid", "walk_time", "category", "slope", "start_vertex", "end_vertex", "SHAPE@"]
    
    edges_to_insert = []
    
    with arcpy.da.SearchCursor(edges_fc, in_edge_fields) as cursor:
        for row in cursor:
            oid, walk_time, category, slope, shape = row
            if not shape: 
                continue
            
            start_pt = shape.firstPoint
            end_pt = shape.lastPoint
            
            start_coords = (round(start_pt.X, 3), round(start_pt.Y, 3))
            end_coords = (round(end_pt.X, 3), round(end_pt.Y, 3))
            
            if start_coords not in vertex_dict:
                vertex_dict[start_coords] = current_vertex_id
                vertex_geoms[current_vertex_id] = arcpy.PointGeometry(start_pt, spatial_ref)
                current_vertex_id += 1
                
            if end_coords not in vertex_dict:
                vertex_dict[end_coords] = current_vertex_id
                vertex_geoms[current_vertex_id] = arcpy.PointGeometry(end_pt, spatial_ref)
                current_vertex_id += 1
            
            start_v_id = vertex_dict[start_coords]
            end_v_id = vertex_dict[end_coords]
            
            safe_walk_time = walk_time if walk_time is not None else 0.0
            edges_to_insert.append((oid, safe_walk_time, category, slope, start_v_id, end_v_id, shape))

    with arcpy.da.InsertCursor(out_vertices_fc, ["vertex_id", "SHAPE@"]) as v_cursor:
        for v_id, geom in vertex_geoms.items():
            v_cursor.insertRow((v_id, geom))
            
    with arcpy.da.InsertCursor(out_edges_fc, out_edge_fields) as e_cursor:
        for edge in edges_to_insert:
            e_cursor.insertRow(edge)
    
    arcpy.analysis.GenerateNearTable(
        in_features=monuments_fc, 
        near_features=out_vertices_fc, 
        out_table=near_table, 
        search_radius=search_radius, 
        location="NO_LOCATION", 
        angle="NO_ANGLE", 
        closest="CLOSEST", 
        closest_count=1
    )
    
    arcpy.management.AlterField(near_table, "IN_FID", "id_zabytku", "ID Zabytku")
    arcpy.management.AlterField(near_table, "NEAR_FID", "id_wierzcholka", "ID Wierzchołka")
    
    monument_data = {}
    with arcpy.da.SearchCursor(monuments_fc, ["OID@", fld_monument_weight, fld_monument_time]) as m_cursor:
        for row in m_cursor:
             monument_data[row[0]] = {"weight": row[1] or 0, "time": row[2] or 0}
             
    vertex_agg = {}
    with arcpy.da.SearchCursor(near_table, ["id_zabytku", "id_wierzcholka"]) as n_cursor:
        for row in n_cursor:
            m_oid, v_oid = row[0], row[1]
            
            if v_oid not in vertex_agg:
                vertex_agg[v_oid] = {"weight": 0, "time": 0}
                
            if m_oid in monument_data:
                vertex_agg[v_oid]["weight"] += monument_data[m_oid]["weight"]
                vertex_agg[v_oid]["time"] += monument_data[m_oid]["time"]

    poi_vertices = []
    
    with arcpy.da.UpdateCursor(out_vertices_fc, ["OID@", "weight", "time", "vertex_id"]) as uv_cursor:
        for row in uv_cursor:
            v_oid = row[0]
            
            if v_oid in vertex_agg:
                row[1] = vertex_agg[v_oid]["weight"]
                row[2] = vertex_agg[v_oid]["time"]
            else:
                row[1] = 0
                row[2] = 0
            
            if row[1] > 0:
                poi_vertices.append(row[3])
                
            uv_cursor.updateRow(row)

    if not out_csv_file or not poi_vertices:
        arcpy.AddWarning("Brak podanej ścieżki do CSV lub brak wierzchołków z wagą > 0. Pomijam generowanie macierzy kosztów.")
    else:   
        graph = {}
        with arcpy.da.SearchCursor(out_edges_fc, ["start_vertex", "end_vertex", "walk_time"]) as cursor:
            for u, v, cost in cursor:
                if u not in graph: 
                    graph[u] = {}
                if v not in graph: 
                    graph[v] = {}
                
                graph[u][v] = cost
                graph[v][u] = cost
        
        poi_vertices.sort()
        
        csv_header = ["atrakcja_start", "atrakcja_koniec", "koszt"]
        csv_rows = []
        
        for source in poi_vertices:
            if source in graph:
                shortest_paths = dijkstra(graph, source)
            else:
                shortest_paths = {}
            
            for target in poi_vertices:
                if source == target:
                    continue
                    
                dist = shortest_paths.get(target, float('inf'))
                final_cost = dist if dist != float('inf') else -1
                
                csv_rows.append([source, target, final_cost])
        
        try:
            with open(out_csv_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(csv_header)
                writer.writerows(csv_rows)
        except Exception as e:
            arcpy.AddError(f"Błąd podczas zapisu pliku CSV: {str(e)}")

if __name__ == '__main__':
    main()
