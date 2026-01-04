from setup import generate_omm_csv, get_satellite_data
from sim_man import SimulationManager
import pickle
from traffic_module import Flow

CONSTELLATION_FILE = 'tiedtest.csv'
PLANES = 10
SATS_PER_PLANE = 20  

generate_omm_csv(
    filename=CONSTELLATION_FILE,
    n_planes=PLANES,
    sats_per_plane=SATS_PER_PLANE,
    inclination=53.0,
    altitude_km=550
)

my_satellites = get_satellite_data(
    num_sats=PLANES * SATS_PER_PLANE,
    csv_file=CONSTELLATION_FILE
)
sim = SimulationManager(my_satellites)
# Schedule the first topology update and traffic heartbeat

test_flow = Flow(
    flow_id=1, 
    src="GS_INDIA", 
    dst="GS_AUSTRALIA", 
    start_time=0, 
    end_time=5400, 
    data_rate_bps=100000 # 100kbps
)
sim.traffic.flows[1] = test_flow
sim.traffic.active_flows.add(1)
sim.core.schedule(0.0, 1, sim.run_step, 1.0) # Run every 1 second
sim.core.run(until=60.0)

with open("rl_training_data.pkl", "wb") as f:
    pickle.dump(sim.dataset, f)
print(f"Data collection complete. Total states captured: {len(sim.dataset)}")