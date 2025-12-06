import numpy as np
import csv
import matplotlib.pyplot as plt
from skyfield.api import load, wgs84, EarthSatellite
from skyfield.iokit import parse_tle_file
max_days = 7  # Maximum age of TLE data in days

def get_satellite_data(num_sats=None, url=None, csv_file=None):
    ts = load.timescale()

    # Load the Starlink TLEs directly from CelesTrak
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

def plot_map(lats, lons, time_obj):
    plt.figure(figsize=(12, 6))
    
    # 1. Plot the satellites
    # 'zorder=2' ensures they are drawn on top of grid lines
    plt.scatter(lons, lats, color='red', s=10, label='Satellites', zorder=2)
    
    # 2. Add Background & Formatting (Simulating a World Map)
    # Load a stock background image if you have one, otherwise use a grid
    # To add a real map image, uncomment the next two lines and ensure 'map.png' exists:
    # img = plt.imread("map.png")
    # plt.imshow(img, extent=[-180, 180, -90, 90])

    plt.title(f'Starlink Constellation Sample (100 Nodes)\n{time_obj.utc_strftime("%Y-%m-%d %H:%M:%S UTC")}')
    plt.xlabel('Longitude (Degrees)')
    plt.ylabel('Latitude (Degrees)')
    
    # Set limits to represent the whole Earth
    plt.xlim(-180, 180)
    plt.ylim(-90, 90)
    
    # Add a grid to represent lat/lon lines
    plt.grid(True, linestyle='--', alpha=0.5, zorder=1)
    plt.axhline(0, color='black', linewidth=0.8) # Equator
    plt.axvline(0, color='black', linewidth=0.8) # Prime Meridian
    
    plt.legend(loc='upper right')
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    # 1. Get Data
    my_sats = get_satellite_data(100, url="https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=csv", csv_file="starlink.csv")
    
    # 2. Process Physics
    ts = load.timescale()
    current_time = ts.now()
    latitudes, longitudes, current_time = calculate_positions(my_sats, current_time)
    
    # 3. Visualize
    plot_map(latitudes, longitudes, current_time)