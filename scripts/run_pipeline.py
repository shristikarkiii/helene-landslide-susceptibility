"""
End-to-end run of the HeleneLandslide toolbox.

  train counties : Buncombe, McDowell, Yancey (NC)
  test counties  : Watauga, Avery (NC)  -- never seen by the model

Run with ArcGIS Pro's Python:
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\Scripts\\propy.bat" scripts\\run_pipeline.py
"""
import ctypes
import glob
import os
import sys

import arcpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
GDB = os.path.join(ROOT, "data", "helene.gdb")
OUT = os.path.join(ROOT, "outputs")

TRAIN = ["Buncombe", "McDowell", "Yancey"]
TEST = ["Watauga", "Avery"]

# Keep Windows awake while this process runs (released automatically on exit);
# the factor build takes long enough for the laptop to go to sleep mid-run.
ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)

arcpy.env.overwriteOutput = True
os.makedirs(OUT, exist_ok=True)
if not arcpy.Exists(GDB):
    arcpy.management.CreateFileGDB(os.path.dirname(GDB), "helene.gdb")
arcpy.env.scratchWorkspace = os.path.join(ROOT, "data")   # scratch.gdb lives here
arcpy.ImportToolbox(os.path.join(ROOT, "HeleneLandslide.pyt"), "helene")
step = sys.argv[1] if len(sys.argv) > 1 else "all"


def counties(names, out):
    shp = os.path.join(RAW, "counties", "cb_2023_us_county_500k.shp")
    where = "STATEFP = '37' AND NAME IN ({})".format(
        ", ".join(f"'{n}'" for n in names))
    lyr = arcpy.management.MakeFeatureLayer(shp, "cty", where)
    arcpy.management.Project(lyr, out, arcpy.SpatialReference(26917))
    arcpy.management.Delete("cty")
    return out


if step in ("all", "prep"):
    train_area = counties(TRAIN, os.path.join(GDB, "train_counties"))
    test_area = counties(TEST, os.path.join(GDB, "test_counties"))
    arcpy.management.Merge([train_area, test_area], os.path.join(GDB, "aoi"))

    # Landslide points. Only points mapped from Sentinel-2 were moved from
    # the impact location (road, river, house) to the headscarp during the
    # USGS review, so only those describe where the slope actually failed.
    arcpy.conversion.JSONToFeatures(
        os.path.join(RAW, "HurricaneHelene_LS_Inventory.geojson"),
        os.path.join(GDB, "ls_all_wgs"), "POINT")
    arcpy.management.Project(os.path.join(GDB, "ls_all_wgs"),
                             os.path.join(GDB, "ls_all"),
                             arcpy.SpatialReference(26917))
    arcpy.analysis.Select(os.path.join(GDB, "ls_all"),
                          os.path.join(GDB, "ls_headscarp"),
                          "Source LIKE '%S2%'")
    for fc in ("ls_all", "ls_headscarp"):
        print(fc, arcpy.management.GetCount(os.path.join(GDB, fc))[0])

if step in ("all", "factors"):
    tiles = sorted(glob.glob(os.path.join(RAW, "dem", "*.tif")))
    arcpy.helene.BuildTerrainFactors(
        ";".join(tiles), os.path.join(GDB, "aoi"),
        os.path.join(RAW, "nlcd2021.tif"),
        os.path.join(RAW, "precip",
                     "NWS_finalPrecip_inches_20240924T1200Z_0928T1200Z.tif"),
        30, 0.5, GDB)

TERRAIN = ["elev", "slope", "relief", "northness", "eastness", "plan_curv",
           "prof_curv", "twi", "dist_stream", "landcover"]
if step in ("all", "train"):
    arcpy.env.randomGenerator = "1 ACM599"
    pts = os.path.join(GDB, "ls_headscarp")
    train_area = os.path.join(GDB, "train_counties")
    arcpy.helene.TrainSusceptibility(
        pts, train_area, GDB, ";".join(TERRAIN + ["rain_4day"]), 90, 10000,
        os.path.join(GDB, "suscep_full"), OUT)
    # Same model without rainfall: how much does the storm itself explain?
    os.makedirs(os.path.join(OUT, "terrain_only"), exist_ok=True)
    arcpy.helene.TrainSusceptibility(
        pts, train_area, GDB, ";".join(TERRAIN), 90, 10000,
        os.path.join(GDB, "suscep_terrain"),
        os.path.join(OUT, "terrain_only"))

if step in ("all", "evaluate"):
    arcpy.env.randomGenerator = "2024 ACM599"     # reproducible background
    candidates = [os.path.join(GDB, "suscep_full"),
                  os.path.join(GDB, "suscep_terrain"),
                  os.path.join(GDB, "slope"),
                  os.path.join(RAW, "USGS_NWS_LS_hazard.tif")]
    arcpy.helene.EvaluateSusceptibility(
        ";".join(candidates), os.path.join(GDB, "ls_headscarp"),
        os.path.join(GDB, "test_counties"), 20000,
        os.path.join(OUT, "evaluation_test_counties.csv"))
    print(open(os.path.join(OUT, "evaluation_test_counties.csv")).read())

# arcpy occasionally hangs during interpreter shutdown after Presence-only
# Prediction; everything is already on disk, so exit hard.
sys.stdout.flush()
os._exit(0)
