"""
Builds HeleneLandslide.aprx: one map with the model, counties and landslides,
plus a letter-size layout (title, legend, scale bar, north arrow, credits)
exported to outputs/figures/layout.pdf and layout.png.
Open the .aprx in ArcGIS Pro to restyle or extend it.
"""
import os
import shutil
import sys

import arcpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GDB = os.path.join(ROOT, "data", "helene.gdb")
FIG = os.path.join(ROOT, "outputs", "figures")
APRX = os.path.join(ROOT, "HeleneLandslide.aprx")
BLANK = r"C:\Program Files\ArcGIS\Pro\Resources\ArcToolBox\Services\routingservices\data\Blank.aprx"

shutil.copy(BLANK, APRX)
aprx = arcpy.mp.ArcGISProject(APRX)
aprx.defaultGeodatabase = GDB
aprx.homeFolder = ROOT
for m in aprx.listMaps():
    aprx.deleteItem(m)
m = aprx.createMap("Helene susceptibility")
m.spatialReference = arcpy.SpatialReference(26917)
try:
    m.addBasemap("Light Gray Canvas")
except Exception:          # offline: the layout still works without one
    pass

sus = m.addDataFromPath(os.path.join(GDB, "suscep_full"))
sus.name = "Landslide susceptibility (MaxEnt)"
sym = sus.symbology
sym.updateColorizer("RasterStretchColorizer")
sym.colorizer.colorRamp = aprx.listColorRamps("Magma")[0]
sym.colorizer.invertColorRamp = True        # dark = most susceptible
sym.colorizer.stretchType = "MinimumMaximum"
sym.colorizer.minLabel = "Low"
sym.colorizer.maxLabel = "High"
sus.symbology = sym
sus.transparency = 15


def style_outline(lyr, rgb, name, width=1.8):
    lyr.name = name
    s = lyr.symbology
    s.renderer.symbol.color = {"RGB": [0, 0, 0, 0]}
    s.renderer.symbol.outlineColor = {"RGB": rgb + [100]}
    s.renderer.symbol.outlineWidth = width
    lyr.symbology = s


style_outline(m.addDataFromPath(os.path.join(GDB, "test_counties")),
              [179, 89, 0], "Test counties (held out)")
style_outline(m.addDataFromPath(os.path.join(GDB, "train_counties")),
              [11, 110, 153], "Training counties")
pts = m.addDataFromPath(os.path.join(GDB, "ls_headscarp"))
pts.name = "Mapped landslides (USGS)"
s = pts.symbology
s.renderer.symbol.applySymbolFromGallery("Circle 1")
s.renderer.symbol.color = {"RGB": [57, 211, 83, 100]}
s.renderer.symbol.outlineColor = {"RGB": [20, 60, 30, 100]}
s.renderer.symbol.size = 3
pts.symbology = s

# Layout -------------------------------------------------------------------
lyt = aprx.createLayout(11, 8.5, "INCH", "Helene landslide layout")
frame = lyt.createMapFrame(arcpy.Extent(0.3, 0.6, 8.0, 7.6), m, "Main map")
frame.camera.setExtent(arcpy.Describe(os.path.join(GDB, "aoi")).extent)
frame.camera.scale *= 1.05


def text(s, x, y, size):
    return aprx.createTextElement(lyt, arcpy.Point(x, y), "POINT", s, size)


text("Hurricane Helene Landslide Susceptibility", 0.3, 8.05, 20)
text("Western North Carolina  |  trained on Buncombe, McDowell, Yancey; "
     "tested on Watauga, Avery", 0.3, 7.75, 10)
leg_style = aprx.listStyleItems("ArcGIS 2D", "LEGEND", "Title and Medium Text Legend")[0]
legend = lyt.createMapSurroundElement(arcpy.Point(8.2, 7.6), "LEGEND",
                                      frame, leg_style, "Legend")
legend.showTitle = False
legend.fittingStrategy = "AdjustFontSize"
legend.elementWidth, legend.elementHeight = 2.6, 2.6
legend.elementPositionX, legend.elementPositionY = 8.2, 7.6
na = aprx.listStyleItems("ArcGIS 2D", "NORTH_ARROW", "ArcGIS North 1")[0]
arrow = lyt.createMapSurroundElement(arcpy.Point(8.3, 3.4), "NORTH_ARROW", frame, na)
arrow.elementHeight = 0.7
sb = aprx.listStyleItems("ArcGIS 2D", "SCALE_BAR", "Alternating Scale Bar 1 Metric")[0]
bar = lyt.createMapSurroundElement(arcpy.Point(8.2, 0.75), "SCALE_BAR",
                                   frame, sb)
bar.elementWidth = 2.4
cim = bar.getDefinition("V3")          # 2 clean divisions, no crowded ticks
cim.divisions, cim.subdivisions, cim.divisionsBeforeZero = 2, 0, 0
bar.setDefinition(cim)
text("Held-out test (245 landslides):\nAUC 0.82 vs 0.77 for slope alone\n"
     "Top 10% of land captures 57%\nof landslides", 8.2, 1.6, 10)
text("Data: USGS Helene landslide inventory (Burgi et al. 2025); USGS 3DEP 10 m; "
     "NLCD 2021; NWS 4-day rainfall (Martinez et al. 2024). NAD83 / UTM 17N. "
     "Author: Shristi Karki", 0.3, 0.3, 7)

aprx.save()
lyt.exportToPDF(os.path.join(FIG, "layout.pdf"), resolution=200)
lyt.exportToPNG(os.path.join(FIG, "layout.png"), resolution=150)
print("saved", APRX)
sys.stdout.flush()
os._exit(0)
