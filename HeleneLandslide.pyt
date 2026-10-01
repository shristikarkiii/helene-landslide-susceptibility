# -*- coding: utf-8 -*-
"""
Helene Landslide Susceptibility toolbox (ArcGIS Pro 3.x, Spatial Analyst).

Three tools, meant to be run in order:

  1. Build Terrain Factors   DEM tiles + NLCD + rainfall  ->  30 m factor rasters
  2. Train Susceptibility    landslide points + factors   ->  MaxEnt model + map
  3. Evaluate Susceptibility map + held-out landslides    ->  AUC, success-rate table

Every tool can also be called from Python:

    arcpy.ImportToolbox(r"...\\HeleneLandslide.pyt", "helene")
    arcpy.helene.BuildTerrainFactors(...)
"""
import csv
import math
import os
import time

import sys

import arcpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helene_metrics import auc, bootstrap_auc, capture  # noqa: E402
from arcpy.sa import (Aggregate, Con, Curvature, DistanceAccumulation, Fill,
                      FlowAccumulation, FlowDirection, FocalStatistics,
                      NbrRectangle, Raster, Slope, Aspect, Ln, Tan, Cos, Sin,
                      ExtractByMask)

# NAD83 / UTM zone 17N: metric, covers the whole southern Appalachian AOI.
UTM17N = arcpy.SpatialReference(26917)

# NLCD classes grouped into what matters for slope stability.
NLCD_GROUPS = {
    1: [41, 42, 43],            # forest
    2: [21, 22, 23, 24],        # developed (road cuts, fill, drainage)
    3: [71, 81, 82],            # grass / pasture / crops
    4: [52, 90, 95],            # shrub / wetland
    5: [11, 12, 31],            # water / ice / barren
}

FACTORS = ["elev", "slope", "relief", "northness", "eastness",
           "plan_curv", "prof_curv", "twi", "dist_stream", "rain_4day",
           "landcover"]
CATEGORICAL = {"landcover"}


def _msg(text):
    arcpy.AddMessage(text)


def _check_sa():
    if arcpy.CheckExtension("Spatial") != "Available":
        raise arcpy.ExecuteError("Spatial Analyst license is not available.")
    arcpy.CheckOutExtension("Spatial")


class Toolbox:
    def __init__(self):
        self.label = "Helene Landslide Susceptibility"
        self.alias = "helene"
        self.tools = [BuildTerrainFactors, TrainSusceptibility,
                      EvaluateSusceptibility]


# --------------------------------------------------------------------------
class BuildTerrainFactors:
    def __init__(self):
        self.label = "1. Build Terrain Factors"
        self.description = ("Mosaics 3DEP DEM tiles, projects to UTM 17N and "
                            "derives the 30 m landslide conditioning factors.")

    def getParameterInfo(self):
        dem = arcpy.Parameter(displayName="DEM tiles (3DEP 1/3 arc-second)",
                              name="dem_tiles", datatype="DERasterDataset",
                              parameterType="Required", direction="Input",
                              multiValue=True)
        aoi = arcpy.Parameter(displayName="Analysis area polygon",
                              name="aoi", datatype="GPFeatureLayer",
                              parameterType="Required", direction="Input")
        aoi.filter.list = ["Polygon"]
        nlcd = arcpy.Parameter(displayName="NLCD land cover raster",
                               name="nlcd", datatype="DERasterDataset",
                               parameterType="Required", direction="Input")
        rain = arcpy.Parameter(displayName="Event rainfall raster (inches)",
                               name="rain", datatype="DERasterDataset",
                               parameterType="Required", direction="Input")
        cell = arcpy.Parameter(displayName="Output cell size (m)",
                               name="cell_size", datatype="GPLong",
                               parameterType="Required", direction="Input")
        cell.value = 30
        stream = arcpy.Parameter(displayName="Stream initiation area (km²)",
                                 name="stream_km2", datatype="GPDouble",
                                 parameterType="Required", direction="Input")
        stream.value = 0.5
        gdb = arcpy.Parameter(displayName="Output geodatabase",
                              name="out_gdb", datatype="DEWorkspace",
                              parameterType="Required", direction="Input")
        gdb.filter.list = ["Local Database"]
        return [dem, aoi, nlcd, rain, cell, stream, gdb]

    def updateMessages(self, parameters):
        cell = parameters[4]
        if cell.value is not None and not 10 <= cell.value <= 100:
            cell.setErrorMessage("Use a cell size between 10 and 100 m; the "
                                 "DEM is 10 m and the rainfall grid is ~1 km.")

    def execute(self, parameters, messages):
        _check_sa()
        tiles = [t.strip("'") for t in parameters[0].valueAsText.split(";")]
        aoi = parameters[1].valueAsText
        nlcd = parameters[2].valueAsText
        rain = parameters[3].valueAsText
        cell = int(parameters[4].value)
        stream_km2 = float(parameters[5].value)
        gdb = parameters[6].valueAsText

        arcpy.env.overwriteOutput = True
        scratch = arcpy.env.scratchGDB
        t0 = time.time()

        def done(path):
            return arcpy.Exists(path)

        def step(label):
            _msg(f"[{(time.time() - t0) / 60:5.1f} min] {label}")

        # Every intermediate is written to disk and skipped when it already
        # exists, so a run interrupted by a sleeping laptop resumes where it
        # stopped instead of re-mosaicking 2 GB of DEM.
        aoi_utm = os.path.join(scratch, "aoi_utm")
        if not done(aoi_utm):
            # 2 km buffer so edge cells get full neighbourhoods.
            arcpy.management.Project(aoi, os.path.join(scratch, "aoi_proj"),
                                     UTM17N)
            arcpy.analysis.PairwiseBuffer(os.path.join(scratch, "aoi_proj"),
                                          aoi_utm, "2 Kilometers",
                                          dissolve_option="ALL")

        dem10 = os.path.join(scratch, "dem10")
        if not done(dem10):
            step("Mosaicking, clipping and projecting DEM")
            # Mosaic in the tiles' own geographic CRS, clip to the AOI's
            # geographic envelope, and only then project.
            tile_sr = arcpy.Describe(tiles[0]).spatialReference
            aoi_geo = os.path.join(scratch, "aoi_geo")
            arcpy.management.Project(aoi_utm, aoi_geo, tile_sr)
            e = arcpy.Describe(aoi_geo).extent
            arcpy.management.MosaicToNewRaster(tiles, scratch, "dem_mosaic",
                                               tile_sr, "32_BIT_FLOAT",
                                               number_of_bands=1)
            dem_geo = os.path.join(scratch, "dem_geo")
            arcpy.management.Clip(os.path.join(scratch, "dem_mosaic"),
                                  f"{e.XMin} {e.YMin} {e.XMax} {e.YMax}",
                                  dem_geo)
            arcpy.management.ProjectRaster(dem_geo, dem10, UTM17N,
                                           "BILINEAR", 10)

        arcpy.env.outputCoordinateSystem = UTM17N
        arcpy.env.extent = arcpy.Describe(aoi_utm).extent
        arcpy.env.snapRaster = dem10

        # Slope is computed on the 10 m DEM and averaged to the output cell,
        # which keeps steep short slopes that a 30 m DEM would smooth away.
        factor = max(1, cell // 10)
        out = lambda name: os.path.join(gdb, name)       # noqa: E731
        if not done(out("slope")):
            step(f"Slope (10 m, averaged to {cell} m)")
            slope10 = Slope(dem10, "DEGREE", method="GEODESIC")
            Aggregate(slope10, factor, "MEAN", "TRUNCATE",
                      "DATA").save(out("slope"))
        if not done(out("elev")):
            step(f"Elevation ({cell} m)")
            Aggregate(dem10, factor, "MEAN", "TRUNCATE",
                      "DATA").save(out("elev"))
        dem, slope = Raster(out("elev")), Raster(out("slope"))
        arcpy.env.snapRaster = dem
        arcpy.env.cellSize = dem

        if not done(out("relief")):
            step("Relief, aspect, curvature")
            # Relief over a 90 m window, echoing the slope-relief threshold of
            # the USGS national landslide susceptibility model (Mirus 2024).
            FocalStatistics(dem, NbrRectangle(3, 3, "CELL"),
                            "RANGE").save(out("relief"))
        if not done(out("eastness")):
            aspect = Aspect(dem, method="GEODESIC")
            rad = aspect * (math.pi / 180.0)
            Con(aspect < 0, 0, Cos(rad)).save(out("northness"))  # flat -> 0
            Con(aspect < 0, 0, Sin(rad)).save(out("eastness"))
        if not done(out("plan_curv")):
            Curvature(dem, 1, out("prof_curv"), out("plan_curv"))

        facc_path = os.path.join(scratch, "facc")
        if not done(facc_path):
            step("Hydrology: fill, flow direction, flow accumulation")
            fdir = FlowDirection(Fill(dem), "NORMAL", None, "D8")
            FlowAccumulation(fdir, None, "FLOAT", "D8").save(facc_path)
        facc = Raster(facc_path)
        if not done(out("twi")):
            step("Topographic wetness index")
            slope_rad = Con(slope < 0.1, 0.1, slope) * (math.pi / 180.0)
            Ln(((facc + 1) * cell) / Tan(slope_rad)).save(out("twi"))
        if not done(out("dist_stream")):
            step("Distance to streams")
            stream_cells = stream_km2 * 1e6 / (cell * cell)
            DistanceAccumulation(Con(facc >= stream_cells, 1)).save(
                out("dist_stream"))

        if not done(out("landcover")):
            step("Land cover")
            nlcd_utm = os.path.join(scratch, "nlcd_utm")
            arcpy.management.ProjectRaster(nlcd, nlcd_utm, UTM17N,
                                           "NEAREST", cell)
            remap = arcpy.sa.RemapValue(
                [[v, k] for k, vals in NLCD_GROUPS.items() for v in vals])
            arcpy.sa.Reclassify(nlcd_utm, "Value", remap,
                                "NODATA").save(out("landcover"))
        if not done(out("rain_4day")):
            step("Rainfall")
            rain_utm = os.path.join(scratch, "rain_utm")
            arcpy.management.ProjectRaster(rain, rain_utm, UTM17N,
                                           "BILINEAR", cell)
            # Resample onto the factor grid so every raster aligns cell-for-cell.
            (Raster(rain_utm) + 0).save(out("rain_4day"))
        step("All factors built")
        arcpy.env.extent = None


# --------------------------------------------------------------------------
class TrainSusceptibility:
    def __init__(self):
        self.label = "2. Train Susceptibility Model"
        self.description = ("Fits Presence-only Prediction (MaxEnt) to mapped "
                            "landslide points inside a training area and "
                            "predicts susceptibility over the full factor "
                            "extent.")

    def getParameterInfo(self):
        pts = arcpy.Parameter(displayName="Landslide points",
                              name="points", datatype="GPFeatureLayer",
                              parameterType="Required", direction="Input")
        pts.filter.list = ["Point"]
        train = arcpy.Parameter(displayName="Training area polygon",
                                name="train_area", datatype="GPFeatureLayer",
                                parameterType="Required", direction="Input")
        train.filter.list = ["Polygon"]
        gdb = arcpy.Parameter(displayName="Factor geodatabase (from tool 1)",
                              name="factor_gdb", datatype="DEWorkspace",
                              parameterType="Required", direction="Input")
        factors = arcpy.Parameter(displayName="Factors to use",
                                  name="factors", datatype="GPString",
                                  parameterType="Required", direction="Input",
                                  multiValue=True)
        factors.filter.type = "ValueList"
        factors.filter.list = FACTORS
        factors.value = FACTORS
        thin = arcpy.Parameter(displayName="Spatial thinning distance (m)",
                               name="thin_m", datatype="GPLong",
                               parameterType="Required", direction="Input")
        thin.value = 90
        nbg = arcpy.Parameter(displayName="Random background points",
                              name="n_background", datatype="GPLong",
                              parameterType="Required", direction="Input")
        nbg.value = 10000
        out_ras = arcpy.Parameter(displayName="Output susceptibility raster",
                                  name="out_raster", datatype="DERasterDataset",
                                  parameterType="Required", direction="Output")
        out_tbl = arcpy.Parameter(displayName="Output folder for model tables",
                                  name="out_folder", datatype="DEFolder",
                                  parameterType="Required", direction="Input")
        return [pts, train, gdb, factors, thin, nbg, out_ras, out_tbl]

    def execute(self, parameters, messages):
        _check_sa()
        arcpy.env.overwriteOutput = True
        pts = parameters[0].valueAsText
        train = parameters[1].valueAsText
        gdb = parameters[2].valueAsText
        names = parameters[3].valueAsText.split(";")
        thin = int(parameters[4].value)
        n_bg = int(parameters[5].value)
        out_ras = parameters[6].valueAsText
        folder = parameters[7].valueAsText
        work = arcpy.env.scratchGDB

        arcpy.env.snapRaster = os.path.join(gdb, "slope")
        rasters = [[os.path.join(gdb, n), "true" if n in CATEGORICAL else "false"]
                   for n in names]

        train_pts = os.path.join(work, "train_pts")
        arcpy.analysis.PairwiseClip(pts, train, train_pts)
        n = int(arcpy.management.GetCount(train_pts)[0])
        _msg(f"{n} landslides inside the training area")

        # Left to itself the tool uses every raster cell in the study area as
        # background (~4 million here), which takes hours. A random sample of
        # 10,000 is the usual MaxEnt practice (Phillips & Dudik 2008).
        bg = _random_points(train, n_bg, "train_bg")
        for fc, flag in ((train_pts, 1), (bg, 0)):
            arcpy.management.CalculateField(fc, "presence", flag, "PYTHON3",
                                            field_type="SHORT")
        both = os.path.join(work, "train_presence_bg")
        arcpy.management.Merge([train_pts, bg], both)

        # The prediction rasters are the same full-extent factor rasters, so
        # the model trained on the training counties is mapped everywhere.
        arcpy.stats.PresenceOnlyPrediction(
            input_point_features=both,
            contains_background="PRESENCE_AND_BACKGROUND_POINTS",
            presence_indicator_field="presence",
            explanatory_rasters=rasters,
            basis_expansion_functions="LINEAR;QUADRATIC;HINGE",
            number_knots=10,
            study_area_type="STUDY_POLYGON",
            study_area_polygon=train,
            spatial_thinning="THINNING",
            thinning_distance_band=f"{thin} Meters",
            number_of_iterations=10,
            relative_weight=100,
            link_function="CLOGLOG",
            presence_probability_cutoff=0.5,
            output_trained_features=os.path.join(work, "trained_features"),
            output_response_curve_table=os.path.join(work, "response_curves"),
            output_sensitivity_table=os.path.join(work, "sensitivity"),
            output_pred_raster=out_ras,
            explanatory_rasters_matching=rasters,
            allow_predictions_outside_of_data_ranges="ALLOWED",
            resampling_scheme="RANDOM",
            number_of_groups=3,
        )
        for tbl in ("response_curves", "sensitivity"):
            arcpy.conversion.ExportTable(os.path.join(work, tbl),
                                         os.path.join(folder, tbl + ".csv"))
        _msg(f"Susceptibility raster written to {out_ras}")


# --------------------------------------------------------------------------
def _random_points(area, n, name):
    """`n` random points across the whole of `area`. CreateRandomPoints puts
    `n` in *each* polygon, so the counties are dissolved first."""
    work = arcpy.env.scratchGDB
    one = os.path.join(work, name + "_area")
    arcpy.analysis.PairwiseDissolve(area, one)
    arcpy.management.CreateRandomPoints(work, name, one, None, n, "30 Meters")
    return os.path.join(work, name)


def _sample(raster, points, field="v"):
    """Values of `raster` at `points` (NoData dropped)."""
    tmp = os.path.join(arcpy.env.scratchGDB, "samp")
    arcpy.sa.ExtractValuesToPoints(points, raster, tmp, "NONE", "VALUE_ONLY")
    vals = [r[0] for r in arcpy.da.SearchCursor(tmp, ["RASTERVALU"])]
    return [v for v in vals if v is not None and v > -9999]


class EvaluateSusceptibility:
    def __init__(self):
        self.label = "3. Evaluate Susceptibility"
        self.description = ("Scores one or more susceptibility rasters on "
                            "held-out landslides: AUC and the share of "
                            "landslides captured by the top 5/10/20% of land.")

    def getParameterInfo(self):
        rasters = arcpy.Parameter(displayName="Rasters to evaluate",
                                  name="rasters", datatype="DERasterDataset",
                                  parameterType="Required", direction="Input",
                                  multiValue=True)
        pts = arcpy.Parameter(displayName="Held-out landslide points",
                              name="points", datatype="GPFeatureLayer",
                              parameterType="Required", direction="Input")
        area = arcpy.Parameter(displayName="Test area polygon",
                               name="test_area", datatype="GPFeatureLayer",
                               parameterType="Required", direction="Input")
        nbg = arcpy.Parameter(displayName="Random background cells",
                              name="n_background", datatype="GPLong",
                              parameterType="Required", direction="Input")
        nbg.value = 20000
        out = arcpy.Parameter(displayName="Output CSV",
                              name="out_csv", datatype="DEFile",
                              parameterType="Required", direction="Output")
        out.filter.list = ["csv"]
        return [rasters, pts, area, nbg, out]

    def execute(self, parameters, messages):
        _check_sa()
        arcpy.env.overwriteOutput = True
        rasters = [r.strip("'") for r in parameters[0].valueAsText.split(";")]
        pts = parameters[1].valueAsText
        area = parameters[2].valueAsText
        nbg = int(parameters[3].value)
        out_csv = parameters[4].valueAsText
        work = arcpy.env.scratchGDB

        test_pts = os.path.join(work, "test_pts")
        arcpy.analysis.PairwiseClip(pts, area, test_pts)
        bg = _random_points(area, nbg, "background")

        rows = []
        for r in rasters:
            pos, neg = _sample(r, test_pts), _sample(r, bg)
            row = {"raster": os.path.basename(r), "n_landslides": len(pos),
                   "n_background": len(neg), "auc": round(auc(pos, neg), 3)}
            lo, hi = bootstrap_auc(pos, neg)
            row["auc_95ci"] = f"{lo:.3f}-{hi:.3f}"
            for f in (0.05, 0.10, 0.20):
                row[f"top{int(f * 100)}pct_capture"] = round(capture(pos, neg, f), 3)
            rows.append(row)
            _msg(str(row))

        with open(out_csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
