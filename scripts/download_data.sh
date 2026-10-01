#!/usr/bin/env bash
# Fetches every input into data/raw. All sources are public US federal data.
set -euo pipefail
cd "$(dirname "$0")/../data" && mkdir -p raw/dem && cd raw
SB=https://www.sciencebase.gov/catalog/file/get

# USGS preliminary Helene landslide inventory (Burgi et al., 2025, doi:10.5066/P14CHGKS)
curl -sfL -o HurricaneHelene_LS_Inventory.geojson "$SB/674634a1d34e6d1dac3abddc?name=HurricaneHelene_LS_Inventory.geojson"
curl -sfL -o ReadMe.txt "$SB/674634a1d34e6d1dac3abddc?name=ReadMe.txt"

# NWS rainfall + USGS emergency hazard map (Martinez et al., 2024, doi:10.5066/P134ERB9)
curl -sfL -o NWS_Precip_Inches.zip "$SB/66feded8d34e80be174ab8d4?name=NWS_Precip_Inches.zip"
unzip -oq NWS_Precip_Inches.zip -d precip
curl -sfL -o USGS_NWS_LS_hazard.tif "$SB/66feded8d34e80be174ab8d4?name=USGS_NWS_LS_hazard_20240924T1200Z_0928T1200Z.tif"

# USGS 3DEP 1/3 arc-second (10 m) DEM
for t in n36w083 n36w082 n37w083 n37w082; do
  curl -sfL -o dem/USGS_13_$t.tif "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/$t/USGS_13_$t.tif"
done

# NLCD 2021 land cover, clipped server-side (EPSG:5070 metres)
curl -sfL -o nlcd2021.tif "https://www.mrlc.gov/geoserver/mrlc_download/wcs?service=WCS&version=2.0.1&request=GetCoverage&coverageid=mrlc_download__NLCD_2021_Land_Cover_L48&subset=X(1150000,1315000)&subset=Y(1440000,1592000)&format=image/geotiff"

# Census 2023 county boundaries
curl -sfL -o counties.zip https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_500k.zip
unzip -oq counties.zip -d counties
