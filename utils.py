import arcpy

def get_nearest_vertex_id(input_point, target_layer):
    arcpy.analysis.Near(input_point, target_layer, "5000 METERS")
    with arcpy.da.SearchCursor(input_point, ["NEAR_FID"]) as cursor:
        near_fid = next(cursor)[0]
    if near_fid !=-1:
        where = f"OBJECTID = {near_fid}"
        with arcpy.da.SearchCursor(target_layer, ["vertex_id"], where_clause=where) as cursor:
            vertex_id = next(cursor)[0]
        return vertex_id
    else:
        return None