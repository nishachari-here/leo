from setup import generate_omm_csv, get_satellite_data
from sim_man import SimulationManager
import pickle
from traffic_module import Flow, FlowState
import random

GROUND_STATIONS = [
    ("GS_INDIA", 28.6139, 77.2090, 0),
    ("GS_USA", 37.7749, -122.4194, 0),
    ("GS_BRAZIL", -23.5505, -46.6333, 0),
    ("GS_AUSTRALIA", -33.8688, 151.2093, 0),
    ("NP",90.0,0.0,0),
    ("SP",-90.0,0.0,0),
]

CONSTELLATION_FILE = 'customconstellation.csv'

# --- Scenario Logic ---

def get_random_scenario(gs_list):
    hotspots = ["GS_USA", "GS_INDIA"]
    src_gs, dst_gs = random.sample(gs_list, 2)
    
    if src_gs in hotspots:
        base_load = random.uniform(10_000_000, 25_000_000) 
    else:
        base_load = random.uniform(500_000, 5_000_000)
        
    return src_gs, dst_gs, base_load

def run_training_generation(sim_manager, gs_list, iterations=10):
    all_training_samples = []
    
    for i in range(iterations):
        # Use the randomized scenario logic
        src_gs, dst_gs, load = get_random_scenario(gs_list)
        
        print(f"--- Iteration {i+1}/{iterations}: {src_gs} -> {dst_gs} @ {load/1e6:.2f} Mbps ---")
        start_delay = random.uniform(0, 5.0)  # Staggered start
        # Setup the flow
        test_flow = Flow(
            flow_id=i, 
            src=src_gs, 
            dst=dst_gs, 
            start_time=start_delay, 
            end_time=start_delay + 10.0,
            data_rate_bps=load
        )
        test_flow.state = FlowState.ACTIVE
        
        # Reset simulator state for fresh iteration
        sim_manager.traffic.flows = {i: test_flow}
        sim_manager.traffic.active_flows = {i}
        
        # Clear queues to prevent data carry-over
        for node in sim_manager.topology.nodes:
            node.queue.clear()
        for q in sim_manager.topology.gs_queues.values():
            q.clear()

        # Run simulation (dataset accumulates inside sim_manager)
        sim_manager.run_headless(until_seconds=10.0)
        
        # Move samples to our main list and clear the manager's buffer
        all_training_samples.extend(sim_manager.dataset)
        sim_manager.dataset = [] 

    return all_training_samples

# --- Main Execution ---

if __name__ == "__main__":
    # 1. Load Satellites
    my_satellites = get_satellite_data(
        num_sats=None,
        csv_file=CONSTELLATION_FILE
    )
    
    # 2. Initialize Manager
    sim = SimulationManager(my_satellites, headless=True)
    gs_list = [gs[0] for gs in GROUND_STATIONS]

    # 3. Generate Training Data (Running 10 different random scenarios)
    print("Starting Data Generation Sidequest...")
    training_data = run_training_generation(sim, gs_list, iterations=10)

    # 4. Save and Report
    if len(training_data) > 0:
        output_file = "dataset1.pkl"
        with open(output_file, "wb") as f:
            pickle.dump(training_data, f)
        
        print("\n" + "="*30)
        print(f"DATA GENERATION COMPLETE")
        print(f"Total Samples Saved: {len(training_data)}")
        print(f"File Saved to: {output_file}")
        
        # Peek at the first sample
        sample = training_data[0]
        if 'queue_states' in sample:
            max_q = max(sample['queue_states'].values())
            print(f"Max Congestion Observed in Sample 1: {max_q} packets")
        print("="*30)
    else:
        print("Warning: No data captured. Check visibility or connectivity.")