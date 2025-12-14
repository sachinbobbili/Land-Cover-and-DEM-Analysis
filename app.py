# app.py
import os
import ee
from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta

# Setup map HTML path (Not used for dynamic maps, but kept for consistency)
MAPS_TEMPLATE_DIR = os.path.join(os.getcwd(), 'templates', 'maps')
MAP_HTML_PATH = os.path.join(MAPS_TEMPLATE_DIR, 'map.html')
os.makedirs(MAPS_TEMPLATE_DIR, exist_ok=True)

# Initialize Earth Engine
try:
    ee.Initialize(project='ee-sachinbobbili')
    print("Earth Engine initialized successfully.")

    # Load Earth Engine assets
    INDIA_DIST_FC = ee.FeatureCollection("users/sachinbobbili/India_Dist")

    # New Layers as per request:
    # Google Dynamic World V1 (ImageCollection)
    # SRTM DEM 90m (Image)
    # HydroSHEDS Free Flowing Rivers (FeatureCollection)
    # Global Surface Water Layer (Image)
    DYNAMIC_WORLD = ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
    SRTM_DEM_90M = ee.Image("CGIAR/SRTM90_V4")
    HYDROSHEDS_RIVERS_FC = ee.FeatureCollection("WWF/HydroSHEDS/v1/FreeFlowingRivers")
    JRC_GSW = ee.Image("JRC/GSW1_4/GlobalSurfaceWater")

    # Pre-fetch the list of states for the dropdown menu
    STATES_LIST = INDIA_DIST_FC.aggregate_array('stname').distinct().sort().getInfo()
except Exception as e:
    print(f"EE init or asset load failed: {e}")
    # Set assets to None and states list to empty if initialization fails
    INDIA_DIST_FC = None
    DYNAMIC_WORLD = None
    SRTM_DEM_90M = None
    HYDROSHEDS_RIVERS_FC = None
    JRC_GSW = None
    STATES_LIST = []

# Define visualization parameters for ALL layers globally, including names for the chart/legends
# Google Dynamic World V1 LULC Visualization Parameters & Names
# Values for 'label' band are 0-8
LANDCOVER_VIS = {
    'min': 0,
    'max': 8,
    'palette': [
        '#419BDF',  # 0: Water
        '#397D49',  # 1: Trees
        '#88B053',  # 2: Grass
        '#7A87C6',  # 3: Flooded vegetation
        '#E49635',  # 4: Crops
        '#DFC35A',  # 5: Shrub and scrub
        '#C4281B',  # 6: Built
        '#A59B8F',  # 7: Bare
        '#B39FE1'   # 8: Snow and ice
    ],
    'names': [
        'Water', 'Trees', 'Grass', 'Flooded vegetation ', 'Crops',
        'Shrub and scrub', 'Built', 'Bare', 'Snow and ice'
    ],
    'title': 'Dynamic World LULC'
}

# SRTM DEM 90m Visualization Parameters

DEM_VIS = {
    "min": 0,
    "max": 3000,
    "palette": [
        "#006400",  # low elevation
        "#7FFF00",
        "#FFFF00",
        "#FFA500",
        "#FF0000",
        "#800000"   # high elevation,
    ],
    'title': 'SRTM DEM Elevation (m)'
}


# Slope Visualization Parameters (derived from SRTM DEM)
SLOPE_VIS =  {
    "min": 0,
    "max": 45,
    "palette": [
        "#f7fbff",
        "#c6dbef",
        "#6baed6",
        "#2171b5",
        "#08306b"
    ],
    'title': 'Slope (Degrees)'
}

# River Networks (HydroSHEDS Free Flowing Rivers - FeatureCollection)
RIVERS_VIS = {
    'palette': ['#0000FF'], # A single color for river lines (Blue)
    'title': 'HydroSHEDS River Networks'
}

# Global Surface Water (JRC GSW)
# We will visualize 'occurrence' band.
JRC_GSW_VIS = {
    'bands': ['occurrence'], # Select the 'occurrence' band
    'min': 0,
    'max': 100, # Percentage
    'palette': ['#FFFFFF', '#0000FF'], # White (0%) to Blue (100%)
    'names': ['0-20%', '20-40%', '40-60%', '60-80%', '80-100%'], # Custom labels for legend
    'title': 'Global Surface Water Occurrence (%)'
}


# Initialize Flask
app = Flask(__name__)
app.config['STATES'] = STATES_LIST

# ==============================================================================
# Flask Routes
# ==============================================================================

@app.route('/')
def welcome():
    return render_template('welcome.html')

@app.route('/app')
def index():
    return render_template('index.html')

@app.route('/get_states')
def get_states():
    """API endpoint to get the list of states."""
    if not STATES_LIST:
        return jsonify({'error': 'States data not available. Check EE connection or asset path.'}), 500
    return jsonify({'states': STATES_LIST})

@app.route('/get_districts/<state_name>')
def get_districts(state_name):
    """API endpoint to get the list of districts for a selected state."""
    if not INDIA_DIST_FC:
        return jsonify({'error': 'EE asset for districts is not loaded.'}), 500
    try:
        districts = INDIA_DIST_FC.filter(ee.Filter.eq('stname', state_name)) \
                                 .aggregate_array('dtname').distinct().sort().getInfo()
        return jsonify({'districts': districts})
    except Exception as e:
        print(f"Error fetching districts: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/generate_map_data', methods=['POST'])
def generate_map_data():
    """
    API endpoint to generate Earth Engine tile URLs, map properties, and chart data
    for the frontend.
    """
    data = request.get_json()
    state = data.get('state')
    district = data.get('district')

    if not state or not district:
        return jsonify({'error': 'Missing state or district'}), 400
    
    # Check if all necessary Earth Engine assets are loaded
    if not all([INDIA_DIST_FC, DYNAMIC_WORLD, SRTM_DEM_90M, HYDROSHEDS_RIVERS_FC, JRC_GSW]):
        return jsonify({'error': 'Earth Engine assets not loaded. Check server logs for EE initialization errors.'}), 500

    try:
        # Get the feature as a FeatureCollection (even if it contains only one feature)
        district_fc = INDIA_DIST_FC.filter(ee.Filter.And(
            ee.Filter.eq('stname', state),
            ee.Filter.eq('dtname', district)
        ))
        
        # Check if the FeatureCollection is empty
        if district_fc.size().getInfo() == 0:
            return jsonify({'error': 'District not found in the dataset.'}), 404
        
        # Get the single feature and its geometry from the collection
        district_feature = district_fc.first()
        geom = district_feature.geometry()
        
        # --- Dynamic World LULC (last two months) ---
        end_date = datetime.now()
        start_date = end_date - timedelta(days=60) # Last 60 days
        
        # Filter Dynamic World by date and take the latest mosaic
        dynamic_world_filtered = DYNAMIC_WORLD.filterDate(start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')) \
                                              .filterBounds(geom) \
                                              .mosaic() \
                                              .select('label') # Select the 'label' band for LULC
        landcover_clipped = dynamic_world_filtered.clip(geom)

        # --- SRTM DEM 90m and Slope ---
        dem_clipped = SRTM_DEM_90M.clip(geom)
        slope_clipped = ee.Terrain.slope(dem_clipped).clip(geom) # Calculate slope from DEM

        # --- River Networks (FeatureCollection) ---
        rivers_filtered = HYDROSHEDS_RIVERS_FC.filterBounds(geom)
        rivers_painted = ee.Image().paint(
            featureCollection=rivers_filtered,
            color=0,        # A single value to paint the features
            width=2         # Width of the line in pixels
        ).visualize(
            palette=RIVERS_VIS['palette'],
            opacity=1
        )

        # --- Global Surface Water (JRC GSW) ---
        gsw_clipped = JRC_GSW.clip(geom)

        # ======================================================================
        # Map Data Generation (Tile URLs)
        # ======================================================================
        
        landcover_map_id = landcover_clipped.getMapId(LANDCOVER_VIS)
        landcover_url = landcover_map_id['tile_fetcher'].url_format

        dem_map_id = dem_clipped.getMapId(DEM_VIS)
        dem_url = dem_map_id['tile_fetcher'].url_format

        slope_map_id = slope_clipped.getMapId(SLOPE_VIS)
        slope_url = slope_map_id['tile_fetcher'].url_format

        rivers_map_id = rivers_painted.getMapId() 
        rivers_url = rivers_map_id['tile_fetcher'].url_format
        
        gsw_map_id = gsw_clipped.getMapId(JRC_GSW_VIS)
        gsw_url = gsw_map_id['tile_fetcher'].url_format

        # Get tile information for the district boundary layer
        boundary_image = ee.Image().paint(
            featureCollection=district_fc,
            color=0,        # The value to paint (e.g., 0 for a solid color)
            width=1         # The width of the line in pixels
        ).visualize(
            palette=['red'], # Color of the boundary line
            opacity=1
        )
        boundary_map_id = boundary_image.getMapId()
        boundary_url = boundary_map_id['tile_fetcher'].url_format

        # Get the centroid of the geometry to center the map
        centroid = geom.centroid().coordinates().getInfo()

        # ======================================================================
        # Chart Data Generation (Land Cover only)
        # ======================================================================

        # Calculate area for each land cover class using a frequency histogram
        # Dynamic World 'label' band values are 0-8 directly.
        histogram = landcover_clipped.reduceRegion(
            reducer=ee.Reducer.frequencyHistogram(),
            geometry=geom,
            scale=10, # Dynamic World is 10m resolution
            maxPixels=1e13
        ).get('label').getInfo() # Get the result for the 'label' band

        chart_labels = []
        chart_values = []

        # Create a mapping from Dynamic World label value to name
        dw_label_to_name = {
            0: 'Water', 1: 'Trees', 2: 'Grass', 3: 'Flooded vegetation',
            4: 'Crops', 5: 'Shrub and scrub', 6: 'Built', 7: 'Bare', 8: 'Snow and ice'
        }
        
        # Sort keys numerically to ensure consistent chart order
        sorted_keys = sorted(histogram.keys(), key=lambda k: int(float(k)))

        for key in sorted_keys:
            class_value = int(float(key))
            pixel_count = histogram[key]
            area_sqkm = round((pixel_count * 100) / 1e6, 2) # 10m pixel = 100 sq m

            label_name = dw_label_to_name.get(class_value, f'Class {class_value}')

            chart_labels.append(label_name)
            chart_values.append(area_sqkm)

        # Return all data to the frontend
        return jsonify({
            'landcover_url': landcover_url,
            'dem_url': dem_url, 
            'slope_url': slope_url,         # New
            'rivers_url': rivers_url,       
            'gsw_url': gsw_url,             
            'boundary_url': boundary_url,
            'center': [centroid[1], centroid[0]], 
            'zoom': 9, 
            'chart_data': {
                'labels': chart_labels,
                'values': chart_values
            },
            'status': 'success'
        })

    except Exception as e:
        print(f"Error in generate_map_data for {state}, {district}: {e}")
        return jsonify({'error': f'Map data generation failed: {e}. Check server logs.'}), 500

# ==============================================================================
# Run Flask app
# ==============================================================================
if __name__ == '__main__':
    app.run(debug=True)