import numpy as np
import csv
import matplotlib.pyplot as plt
from skyfield.api import load, wgs84, EarthSatellite
from skyfield.iokit import parse_tle_file
import plotly.graph_objects as go
import csv
import math
from datetime import datetime, timezone
max_days = 7  # Maximum age of TLE data in days

def get_satellite_data(num_sats=None, url=None, csv_file=None):
    ts = load.timescale()

    if csv_file is not None:

        if not load.exists(csv_file): #or load.days_old(csv_file) >= max_days:
            load.download(url, filename=csv_file)
        # Load from a CSV file
        with load.open(csv_file, mode='r') as f:
            data = list(csv.DictReader(f))
        satellites = [EarthSatellite.from_omm(ts, fields) for fields in data]
    if  num_sats is None or num_sats > len(satellites) or num_sats <= 0:
        print(f"Requested {num_sats} satellites, but only {len(satellites)} available. Using all available satellites.")
        num_sats = len(satellites)
    subset = satellites[:num_sats]
    print(f"Total satellites loaded: {len(satellites)} | Using subset: {len(subset)}")
    return subset


def generate_omm_csv(filename, n_planes, n_sats=None, sats_per_plane=None, 
                     inclination=53.05, altitude_km=550, phasing_f=1):
    
    # Handle satellite count logic
    if sats_per_plane is not None:
        total_sats = n_planes * sats_per_plane
    elif n_sats is not None:
        total_sats = n_sats
        sats_per_plane = n_sats // n_planes
    else:
        raise ValueError("Provide either n_sats or sats_per_plane.")

    # Orbital Physics Constants
    mu = 398600.4418  # Earth's gravitational parameter
    radius = 6378.137 + altitude_km 
    period_seconds = 2 * math.pi * math.sqrt(math.pow(radius, 3) / mu)
    mean_motion = 86400 / period_seconds
    
    # Use current time for the Epoch
    epoch_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')

    # Headers based on your sample
    headers = [
        "OBJECT_NAME", "OBJECT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY", 
        "INCLINATION", "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY", 
        "EPHEMERIS_TYPE", "CLASSIFICATION_TYPE", "NORAD_CAT_ID", "ELEMENT_SET_NO", 
        "REV_AT_EPOCH", "BSTAR", "MEAN_MOTION_DOT", "MEAN_MOTION_DDOT"
    ]

    with open(filename, mode='w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        
        for p in range(n_planes):
            raan = (360.0 / n_planes) * p
            for s in range(sats_per_plane):
                sat_id = p * sats_per_plane + s
                mean_anomaly = (360.0 / sats_per_plane) * s + (360.0 * phasing_f / total_sats) * p
                
                writer.writerow({
                    "OBJECT_NAME": f"SAT-{p:02d}-{s:02d}",
                    "OBJECT_ID": f"2025-{sat_id:03d}A",
                    "EPOCH": epoch_str,
                    "MEAN_MOTION": f"{mean_motion:.8f}",
                    "ECCENTRICITY": "0.0001", # Near-circular
                    "INCLINATION": f"{inclination:.4f}",
                    "RA_OF_ASC_NODE": f"{raan:.4f}",
                    "ARG_OF_PERICENTER": "0.0000",
                    "MEAN_ANOMALY": f"{mean_anomaly % 360.0:.4f}",
                    "EPHEMERIS_TYPE": "0",
                    "CLASSIFICATION_TYPE": "U",
                    "NORAD_CAT_ID": 50000 + sat_id,
                    "ELEMENT_SET_NO": "999",
                    "REV_AT_EPOCH": "0",
                    "BSTAR": "0.0001",
                    "MEAN_MOTION_DOT": "0",
                    "MEAN_MOTION_DDOT": "0"
                })

    print(f"Generated OMM CSV: {filename} ({total_sats} satellites)")
