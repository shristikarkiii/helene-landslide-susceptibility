"""
Publishes the result to ArcGIS Online as a public web map, using the account
ArcGIS Pro is signed in to (no credentials in code).

The MOOC account can publish hosted *feature* layers but not tiles or imagery,
so the 30 m raster is summarized into ~0.1 km² hexagons first. Each hexagon
carries its mean and max susceptibility and a class based on the same top
5/10/20% cut-offs used in the evaluation.

  step "prep"    -> data/web.gdb (hexagons, landslides, counties)
  step "publish" -> hosted feature layer + public web map
"""
import json
import os
import shutil
import sys

import arcpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GDB = os.path.join(ROOT, "data", "helene.gdb")
WEB = os.path.join(ROOT, "data", "web.gdb")
TITLE = "Hurricane Helene Landslide Susceptibility (Western NC)"
step = sys.argv[1] if len(sys.argv) > 1 else "all"
arcpy.env.overwriteOutput = True

CLASSES = [  # (label, lower percentile of hexagon mean), most severe first
    ("Very high (top 5%)", 95), ("High (top 5-10%)", 90),
    ("Elevated (top 10-20%)", 80), ("Moderate (top 20-50%)", 50),
    ("Low (bottom 50%)", 0)]
COLORS = [[45, 0, 75, 230], [130, 30, 110, 220], [214, 80, 80, 200],
          [250, 170, 110, 170], [252, 240, 200, 110]]

if step in ("all", "prep"):
    arcpy.CheckOutExtension("Spatial")
    if arcpy.Exists(WEB):
        arcpy.management.Delete(WEB)
    arcpy.management.CreateFileGDB(os.path.dirname(WEB), "web.gdb")
    aoi = os.path.join(WEB, "counties")
    arcpy.management.CopyFeatures(os.path.join(GDB, "aoi"), aoi)
    arcpy.management.AddField(aoi, "role", "TEXT", field_length=40)
    with arcpy.da.UpdateCursor(aoi, ["NAME", "role"]) as cur:
        for name, _ in cur:
            role = ("Test (held out)" if name in ("Watauga", "Avery")
                    else "Training")
            cur.updateRow([name, role])

    hexes = os.path.join(arcpy.env.scratchGDB, "hex_all")
    arcpy.management.GenerateTessellation(
        hexes, arcpy.Describe(aoi).extent, "HEXAGON", "0.1 SquareKilometers",
        arcpy.SpatialReference(26917))
    lyr = arcpy.management.MakeFeatureLayer(hexes, "hex_lyr")
    arcpy.management.SelectLayerByLocation(lyr, "HAVE_THEIR_CENTER_IN", aoi)
    hex_fc = os.path.join(WEB, "susceptibility_hex")
    arcpy.management.CopyFeatures(lyr, hex_fc)

    zt = os.path.join(arcpy.env.scratchGDB, "hex_zonal")
    arcpy.sa.ZonalStatisticsAsTable(hex_fc, "GRID_ID",
                                    os.path.join(GDB, "suscep_full"), zt,
                                    "DATA", "MEAN")
    arcpy.sa.ZonalStatisticsAsTable(hex_fc, "GRID_ID",
                                    os.path.join(GDB, "suscep_full"),
                                    zt + "_max", "DATA", "MAXIMUM")
    arcpy.management.JoinField(hex_fc, "GRID_ID", zt, "GRID_ID", ["MEAN"])
    arcpy.management.JoinField(hex_fc, "GRID_ID", zt + "_max", "GRID_ID", ["MAX"])
    arcpy.management.AlterField(hex_fc, "MEAN", "suscep_mean", "Mean susceptibility")
    arcpy.management.AlterField(hex_fc, "MAX", "suscep_max", "Max susceptibility")

    vals = sorted(v for (v,) in arcpy.da.SearchCursor(hex_fc, ["suscep_mean"])
                  if v is not None)
    cuts = [(lab, vals[int(len(vals) * p / 100)] if p else -1) for lab, p in CLASSES]
    arcpy.management.AddField(hex_fc, "suscep_class", "TEXT", field_length=40)
    arcpy.management.AddField(hex_fc, "class_rank", "SHORT")
    with arcpy.da.UpdateCursor(hex_fc, ["suscep_mean", "suscep_class",
                                        "class_rank"]) as cur:
        for mean, _, _ in cur:
            if mean is None:
                cur.deleteRow()
                continue
            rank, lab = next((i, lab) for i, (lab, c) in enumerate(cuts)
                             if mean >= c)
            cur.updateRow([round(mean, 4), lab, rank + 1])

    pts = os.path.join(WEB, "landslides")
    arcpy.analysis.PairwiseClip(os.path.join(GDB, "ls_all"), aoi, pts)
    print("hexagons:", arcpy.management.GetCount(hex_fc)[0],
          "| landslides:", arcpy.management.GetCount(pts)[0])
    print("class cut-offs (hex mean):", [(lab, round(c, 4)) for lab, c in cuts])


def class_renderer():
    return {"type": "uniqueValue", "field1": "suscep_class",
            "uniqueValueInfos": [
                {"value": lab, "label": lab,
                 "symbol": {"type": "esriSFS", "style": "esriSFSSolid",
                            "color": col,
                            "outline": {"type": "esriSLS", "style": "esriSLSNull",
                                        "color": [0, 0, 0, 0], "width": 0}}}
                for (lab, _), col in zip(CLASSES, COLORS)]}


if step in ("all", "publish"):
    from arcgis.gis import GIS
    gis = GIS("pro")
    zip_base = os.path.join(ROOT, "data", "helene_web")
    shutil.make_archive(zip_base, "zip", os.path.dirname(WEB), "web.gdb")

    for old in gis.content.search(f'title:"{TITLE}" AND owner:{gis.users.me.username}'):
        old.delete()                                     # idempotent re-runs
    props = {"title": TITLE, "type": "File Geodatabase",
             "tags": "landslide,Hurricane Helene,North Carolina,MaxEnt,ArcGIS Pro",
             "snippet": "Landslide susceptibility after Hurricane Helene, "
                        "summarized to 0.1 km² hexagons. Source data: USGS."}
    folder = gis.content.folders.get()
    src = folder.add(props, file=zip_base + ".zip").result()
    fl_item = src.publish({"name": "helene_landslide_susceptibility"})
    fl_item.update({"description": open(os.path.join(ROOT, "README.md"),
                                        encoding="utf-8").read()[:4000]})
    url = fl_item.url
    names = {lyr.properties.name: i for i, lyr in enumerate(fl_item.layers)}
    print("layers:", names)

    def op(name, title, renderer, popup, visible=True, opacity=1):
        return {"id": name, "title": title, "url": f"{url}/{names[name]}",
                "layerType": "ArcGISFeatureLayer", "itemId": fl_item.id,
                "visibility": visible, "opacity": opacity,
                "layerDefinition": {"drawingInfo": {"renderer": renderer}},
                "popupInfo": popup}

    hex_pop = {"title": "{suscep_class}", "fieldInfos": [
        {"fieldName": "suscep_mean", "label": "Mean susceptibility", "visible": True,
         "format": {"places": 3}},
        {"fieldName": "suscep_max", "label": "Max susceptibility", "visible": True,
         "format": {"places": 3}}]}
    pt_pop = {"title": "Mapped landslide", "fieldInfos": [
        {"fieldName": "Impact", "label": "Primary impact", "visible": True},
        {"fieldName": "Source", "label": "Mapped from", "visible": True}]}
    cty_pop = {"title": "{NAME} County", "fieldInfos": [
        {"fieldName": "role", "label": "Role in model", "visible": True}]}
    county_r = {"type": "uniqueValue", "field1": "role", "uniqueValueInfos": [
        {"value": v, "label": v, "symbol": {
            "type": "esriSFS", "style": "esriSFSNull", "color": [0, 0, 0, 0],
            "outline": {"type": "esriSLS", "style": "esriSLSSolid",
                        "color": c, "width": 2}}}
        for v, c in (("Training", [11, 110, 153, 255]),
                     ("Test (held out)", [179, 89, 0, 255]))]}
    pt_r = {"type": "simple", "symbol": {
        "type": "esriSMS", "style": "esriSMSCircle", "size": 4.5,
        "color": [57, 211, 83, 255],
        "outline": {"type": "esriSLS", "style": "esriSLSSolid",
                    "color": [20, 60, 30, 255], "width": 0.5}}}

    e = arcpy.Describe(os.path.join(WEB, "counties")).extent.projectAs(
        arcpy.SpatialReference(4326))
    webmap = {
        "operationalLayers": [
            op("susceptibility_hex", "Landslide susceptibility (MaxEnt)",
               class_renderer(), hex_pop, opacity=0.9),
            op("counties", "Training vs test counties", county_r, cty_pop),
            op("landslides", "Mapped landslides (USGS, Sept-Oct 2024)", pt_r, pt_pop)],
        "baseMap": {"title": "Light Gray Canvas", "baseMapLayers": [
            {"id": "base", "title": "World Light Gray Base", "layerType": "ArcGISTiledMapServiceLayer",
             "url": "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer",
             "visibility": True, "opacity": 1},
            {"id": "ref", "title": "World Light Gray Reference", "layerType": "ArcGISTiledMapServiceLayer",
             "url": "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer",
             "visibility": True, "opacity": 1, "isReference": True}]},
        "spatialReference": {"wkid": 102100, "latestWkid": 3857},
        "version": "2.31", "authoringApp": "ArcGIS API for Python",
        "authoringAppVersion": "2.4.3"}
    wm = folder.add({
        "title": TITLE, "type": "Web Map", "text": json.dumps(webmap),
        "extent": f"{e.XMin},{e.YMin},{e.XMax},{e.YMax}",
        "tags": props["tags"],
        "snippet": "Where Hurricane Helene was most likely to trigger "
                   "landslides. Model trained on 3 counties, tested on 2 it never saw.",
        "description": "Built with a custom ArcGIS Pro Python toolbox. Code, "
                       "method and validation: https://github.com/shristikarkiii/"
                       "helene-landslide-susceptibility"}).result()
    for item in (fl_item, wm):
        item.sharing.sharing_level = "EVERYONE"
    src.delete()                                         # keep only the layer
    print("feature layer:", fl_item.homepage)
    print("web map:", wm.homepage)
    print("viewer: https://www.arcgis.com/apps/mapviewer/index.html?webmap=" + wm.id)

sys.stdout.flush()
os._exit(0)
