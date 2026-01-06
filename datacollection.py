from setup import generate_omm_csv, get_satellite_data
from sim_man import SimulationManager
import pickle
from traffic_module import Flow
from traffic_module import FlowState
CONSTELLATION_FILE = 'customconstellation.csv'
PLANES = 22
SATS_PER_PLANE = 20 



my_satellites = get_satellite_data(
    num_sats=None,
    url=None,
    csv_file=CONSTELLATION_FILE
)
sim = SimulationManager(my_satellites, headless=True)
# Schedule the first topology update and traffic heartbeat

test_flow = Flow(
    flow_id=1, 
    src="GS_INDIA", 
    dst="GS_AUSTRALIA", 
    start_time=0, 
    end_time=5400, 
    data_rate_bps=10000000 # 100kbps
)

test_flow.state = FlowState.ACTIVE

sim.traffic.flows[1] = test_flow
sim.traffic.active_flows.add(1)
sim.run_headless(until_seconds=5.0)



if len(sim.dataset) > 0:
    with open("test_data.pkl", "wb") as f:
        pickle.dump(sim.dataset, f)
    
    # Check one sample for the new 'queue_load' key
    sample = sim.dataset[0]
    print(f"Success! States captured: {len(sim.dataset)}")
    print(f"Sample Keys: {sample.keys()}")
    if 'queue_states' in sample:
        print("Queue data detected in dataset.")
else:
    print("Warning: No data captured. Check your GS visibility or Flow start_time.")