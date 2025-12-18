import numpy as np
import csv
import matplotlib.pyplot as plt
from skyfield.api import load, wgs84, EarthSatellite
from skyfield.iokit import parse_tle_file
import plotly.graph_objects as go
max_days = 7  # Maximum age of TLE data in days

def get_satellite_data(num_sats=None, url=None, csv_file=None):
    ts = load.timescale()

    if csv_file is not None:

        if not load.exists(csv_file) or load.days_old(csv_file) >= max_days:
            load.download(url, filename=csv_file)
        # Load from a CSV file
        with load.open(csv_file, mode='r') as f:
            data = list(csv.DictReader(f))
        satellites = [EarthSatellite.from_omm(ts, fields) for fields in data]
    if num_sats > len(satellites) or num_sats <= 0 or num_sats is None:
        print(f"Requested {num_sats} satellites, but only {len(satellites)} available. Using all available satellites.")
        num_sats = len(satellites)
    subset = satellites[:num_sats]
    print(f"Total satellites loaded: {len(satellites)} | Using subset: {len(subset)}")
    return subset

def calculate_positions(satellites, t):
    ts = load.timescale()
    
    lats = []
    lons = []
    
    print(f"Calculating positions for {t.utc_strftime('%Y-%m-%d %H:%M:%S UTC')}")
    
    for sat in satellites:
        # Propagate satellite position
        geocentric = sat.at(t)
        
        # Convert to geometric latitude and longitude
        lat, lon = wgs84.latlon_of(geocentric)
        
        # Append degrees to list
        lats.append(lat.degrees)
        lons.append(lon.degrees)
        
    return lats, lons, t


def vis_sat_path(satellites, dur, data_points):
    t=load.timescale().now()
   
  
    scatter_traces = []
    for i, sat in enumerate(satellites):
                sat_positions = []
                for ti in np.linspace(0, dur, data_points):
                    geocentric = sat.at(t + ti)
                    sat_positions.append(geocentric)
                xyz_coords = [pos.xyz.km for pos in sat_positions]
                x_coords = xyz_coords[0][:]
                y_coords = xyz_coords[1][:]
                z_coords = xyz_coords[2][:]
                
                scatter_trace = go.Scatter3d(
                    x=x_coords,
                    y=y_coords,
                    z=z_coords,
                    mode='lines+markers',
                    marker=dict(
                    size=3, # Reduced size for smoother lines
                    color=i,
                    colorscale='Viridis',
                    opacity=0.8
                    ),
                    line=dict(
                        width=3,
                        color=i,
                        colorscale='Viridis',
                    ),
                    name=f'Satellite {i}'
                )
                scatter_traces.append(scatter_trace)

    fig = go.Figure(data=scatter_traces)
    fig.show()
   
            
if __name__ == "__main__":
    # 1. Get Data
    sats = get_satellite_data(100, url="https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=csv", csv_file="starlink.csv")
    my_sats = sats[:10]
    
    vis_sat_path(my_sats, dur=3600*24, data_points=15000)