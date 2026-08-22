# LEO Constellation Simulation and Benchmark

This repository contains tools to simulate, visualize, and benchmark LEO satellite constellations.

## Features
- Visual 3D simulation of LEO constellation (`test.py` -> `SatelliteSim`).
- Logical topology computation (ISL, IOL, UDL) and unified graph construction.
- Pathfinding implementations and comparative benchmark (Dijkstra, A*, Bellman-Ford).
- Plotting and isolation analysis for network connectivity.

## Requirements
- Python 3.9+ (recommend 3.10 or 3.11)
- GPU/OpenGL drivers for the real-time visualizer (GLFW + PyOpenGL)
- Create and activate a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1    # PowerShell
pip install -r requirements.txt
```

## Quick Start

1) Generate a test constellation and run the benchmark (non-interactive):

```powershell
python test.py
```

By default `test.py` will generate OMM CSV files, run the comparative benchmark across several satellite counts, save `comparative_analysis_results.csv`, and produce `performance_plot.png`.

2) Run the visual simulator interactively (recommended if you have OpenGL/GLFW support):

Open a Python shell or create a small runner script and run:

```python
from test import parse_csv, SatelliteSim, generate_omm_csv
generate_omm_csv('cc.csv', n_planes=25, sats_per_plane=3)
sats = parse_csv('cc.csv')
sim = SatelliteSim(sats)
sim.run()
```

Note: `test.py` contains an optional call to `SatelliteSim(sats_data).run()` which is commented—uncomment it if you prefer running the sim directly from `python test.py`.

## Important Files
- [test.py](test.py): Main simulation, logical topology, visualization, and benchmarking.
- [setup.py](setup.py): Project setup utilities.
- [requirements.txt](requirements.txt): Python dependencies.
- [generate_omm_csv / setup helpers] in `setup.py`: helpers used by `test.py` to prepare OMM CSVs.

## Troubleshooting
- If the OpenGL window fails to create, ensure `glfw` and system GPU drivers are installed and up to date.
- On Windows, run PowerShell as Administrator to create the virtual environment if you hit permission issues.
- If Skyfield or satellite OMM parsing fails, ensure network time and timezone settings are correct; check that OMM CSVs are valid.

## Next steps / Recommendations
- If you want, I can: run the benchmarks locally, adjust `test.py` to expose CLI options, or add a pared-down runner script for the visualizer.
