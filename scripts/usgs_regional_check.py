"""
Is the USGS emergency hazard map (Martinez et al. 2024) any good at the
scale it was made for? Scores it on all 2,217 inventory landslides against
20,000 random points across the inventory's bounding box (5 states).

Context for the README: inside Watauga + Avery it scores AUC ~0.50, because
its ~7 km cells give only a few dozen distinct values across both counties.
"""
import os
import sys

import arcpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from helene_metrics import auc, bootstrap_auc, capture  # noqa: E402

arcpy.CheckOutExtension("Spatial")
arcpy.env.overwriteOutput = True
GDB = os.path.join(ROOT, "data", "helene.gdb")
HAZ = os.path.join(ROOT, "data", "raw", "USGS_NWS_LS_hazard.tif")
work = arcpy.env.scratchGDB


def sample(points):
    arcpy.sa.ExtractValuesToPoints(points, HAZ, "memory/s", "NONE", "VALUE_ONLY")
    return [v for (v,) in arcpy.da.SearchCursor("memory/s", ["RASTERVALU"])
            if v is not None and v > -1e30]


ext = arcpy.Describe(os.path.join(GDB, "ls_all_wgs")).extent
arcpy.env.randomGenerator = "7 ACM599"
arcpy.management.CreateRandomPoints(
    work, "reg_bg", None, f"{ext.XMin} {ext.YMin} {ext.XMax} {ext.YMax}", 20000)
arcpy.management.DefineProjection(os.path.join(work, "reg_bg"),
                                  arcpy.SpatialReference(4326))
pos, neg = sample(os.path.join(GDB, "ls_all")), sample(os.path.join(work, "reg_bg"))
lo, hi = bootstrap_auc(pos, neg)

test = os.path.join(work, "reg_test_pts")
arcpy.analysis.PairwiseClip(os.path.join(GDB, "ls_headscarp"),
                            os.path.join(GDB, "test_counties"), test)
distinct = len(set(sample(test)))

lines = [f"landslides={len(pos)} background={len(neg)}",
         f"regional AUC={auc(pos, neg):.3f} (95% CI {lo:.3f}-{hi:.3f})",
         f"top10pct_capture={capture(pos, neg, 0.10):.3f}",
         f"distinct hazard values at test-county landslides={distinct}"]
print("\n".join(lines))
with open(os.path.join(ROOT, "outputs", "usgs_map_regional_check.txt"), "w") as fh:
    fh.write("\n".join(lines) + "\n")
sys.stdout.flush()
os._exit(0)
