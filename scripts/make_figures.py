"""
README figures (PNG) plus an ArcGIS Pro project with a print layout.

  outputs/figures/susceptibility_map.png   model map, train vs test counties
  outputs/figures/success_rate.png         % of landslides vs % of land flagged
  outputs/figures/response_curves.png      what the model learned per factor
  HeleneLandslide.aprx                     open in ArcGIS Pro to edit the layout
"""
import csv
import os
import sys

import arcpy
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from helene_metrics import auc  # noqa: E402

GDB = os.path.join(ROOT, "data", "helene.gdb")
OUT = os.path.join(ROOT, "outputs")
FIG = os.path.join(OUT, "figures")
os.makedirs(FIG, exist_ok=True)
arcpy.env.overwriteOutput = True

INK, MUTED, GRID = "#1f2328", "#59636e", "#d0d7de"
TRAIN_C, TEST_C = "#0b6e99", "#b35900"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                     "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                     "xtick.color": MUTED, "ytick.color": MUTED})


def raster_array(path):
    r = arcpy.Raster(path)
    a = arcpy.RasterToNumPyArray(r, nodata_to_value=np.nan).astype("float32")
    ext = r.extent
    return a, (ext.XMin, ext.XMax, ext.YMin, ext.YMax)


def outlines(fc):
    shapes = []
    for (geom,) in arcpy.da.SearchCursor(fc, ["SHAPE@"]):
        for part in geom:
            xy = [(p.X, p.Y) if p else (np.nan, np.nan) for p in part]
            shapes.append(np.array(xy))
    return shapes


def points(fc, clip=None):
    if clip:
        tmp = os.path.join(arcpy.env.scratchGDB, "fig_pts")
        arcpy.analysis.PairwiseClip(fc, clip, tmp)
        fc = tmp
    return np.array([xy for (xy,) in arcpy.da.SearchCursor(fc, ["SHAPE@XY"])])


# 1. Map ---------------------------------------------------------------------
sus, ext = raster_array(os.path.join(GDB, "suscep_full"))
fig, ax = plt.subplots(figsize=(9, 7.4), dpi=160)
im = ax.imshow(sus, extent=ext, cmap="magma_r", vmin=0, vmax=1,
               interpolation="nearest")
for fc, color, label in [("train_counties", TRAIN_C, "Training counties"),
                         ("test_counties", TEST_C, "Test counties (held out)")]:
    for i, s in enumerate(outlines(os.path.join(GDB, fc))):
        ax.plot(s[:, 0], s[:, 1], color=color, lw=1.6,
                label=label if i == 0 else None)
pts = points(os.path.join(GDB, "ls_headscarp"), os.path.join(GDB, "aoi"))
ax.scatter(pts[:, 0], pts[:, 1], s=3, c="#39d353", edgecolors="none",
           label="Mapped landslide (USGS)")
ax.set_xticks([]); ax.set_yticks([])
ax.set_title("Hurricane Helene landslide susceptibility, western North Carolina",
             loc="left", color=INK, fontsize=12)
cb = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.01)
cb.set_label("Relative susceptibility (MaxEnt, cloglog)")
ax.legend(loc="lower right", frameon=True, fontsize=8, markerscale=3)
x0, y0 = ext[0] + 6000, ext[3] - 8000          # empty Piedmont corner
ax.plot([x0, x0 + 20000], [y0, y0], color=INK, lw=3)
ax.text(x0 + 10000, y0 + 1800, "20 km", ha="center", color=INK, fontsize=8)
fig.text(0.01, 0.01, "Data: USGS landslide inventory (Burgi et al. 2025), "
         "USGS 3DEP, NLCD 2021, NWS rainfall. NAD83 / UTM 17N.",
         color=MUTED, fontsize=7)
fig.tight_layout()
fig.savefig(os.path.join(FIG, "susceptibility_map.png"))
plt.close(fig)


# 2. Success-rate curves on the test counties ---------------------------------
def sample(raster, fc):
    tmp = os.path.join(arcpy.env.scratchGDB, "fig_samp")
    arcpy.sa.ExtractValuesToPoints(fc, raster, tmp, "NONE", "VALUE_ONLY")
    return np.array([v for (v,) in arcpy.da.SearchCursor(tmp, ["RASTERVALU"])
                     if v is not None and v > -9999])


arcpy.CheckOutExtension("Spatial")
work = arcpy.env.scratchGDB
test_pts = os.path.join(work, "fig_test_pts")
arcpy.analysis.PairwiseClip(os.path.join(GDB, "ls_headscarp"),
                            os.path.join(GDB, "test_counties"), test_pts)
arcpy.env.randomGenerator = "2024 ACM599"
arcpy.analysis.PairwiseDissolve(os.path.join(GDB, "test_counties"),
                                os.path.join(work, "fig_test_area"))
arcpy.management.CreateRandomPoints(work, "fig_bg",
                                    os.path.join(work, "fig_test_area"),
                                    None, 20000, "30 Meters")
bg = os.path.join(work, "fig_bg")
curves = [("suscep_full", GDB, "This model (terrain + rainfall)", INK, "-"),
          ("suscep_terrain", GDB, "This model, terrain only", TRAIN_C, "-"),
          ("slope", GDB, "Slope alone", MUTED, "--"),
          ("USGS_NWS_LS_hazard.tif", os.path.join(ROOT, "data", "raw"),
           "USGS emergency hazard map", TEST_C, "-")]
fig, ax = plt.subplots(figsize=(6.4, 5), dpi=160)
frac = np.linspace(0, 1, 201)
for name, folder, label, color, ls in curves:
    path = os.path.join(folder, name)
    pos, neg = sample(path, test_pts), sample(path, bg)
    cuts = np.quantile(neg, 1 - frac)
    hit = [(pos >= c).mean() for c in cuts]
    ax.plot(frac * 100, np.array(hit) * 100, color=color, ls=ls, lw=1.8,
            label=f"{label}  (AUC {auc(list(pos), list(neg)):.2f})")
ax.plot([0, 100], [0, 100], color=GRID, lw=1, zorder=0)
ax.set_xlabel("% of test-county land flagged, highest susceptibility first")
ax.set_ylabel("% of held-out landslides captured")
ax.set_xlim(0, 100); ax.set_ylim(0, 100)
ax.grid(color=GRID, lw=0.5)
ax.set_title("Watauga + Avery counties: model never saw these landslides",
             loc="left", fontsize=10, color=INK)
ax.legend(fontsize=7.5, loc="lower right")
fig.tight_layout()
fig.savefig(os.path.join(FIG, "success_rate.png"))
plt.close(fig)

# 3. Response curves -----------------------------------------------------------
rc = os.path.join(OUT, "response_curves.csv")
if os.path.exists(rc):
    with open(rc, newline="") as fh:
        rows = list(csv.DictReader(fh))
    print("response curve columns:", list(rows[0]))
print("figures written to", FIG)
