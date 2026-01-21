import multiprocessing as mp
import logging
import psutil
import time
import random
import pickle
from sim_man import SimulationManager
from setup import get_satellite_data, generate_omm_csv
from traffic_module import Flow
from logic_module import GROUND_STATIONS # Ensure this is imported

# --- MOVE THESE TO GLOBAL SCOPE ---
CONSTELLATION_FILE = 'customconstellation.csv'
GS_NAMES = [gs[0] for gs in GROUND_STATIONS]

def get_random_scenario(names):
    src, dst = random.sample(names, 2)
    load = random.uniform(1_000_000, 20_000_000)
    return src, dst, load

def simulation_worker(worker_id):
    global shared_counter, stop_event
    # Give worker a unique name and seed
    mp.current_process().name = f"Worker-{worker_id}"
    random.seed(time.time() + worker_id)
    
    local_data = []
    try:
        # Initialize Sim
        sats_data = get_satellite_data(num_sats=100, csv_file=CONSTELLATION_FILE)
        sim = SimulationManager(sats_data, headless=True)
        sim.core.current_time += (worker_id * 500)

        while not stop_event.is_set() and len(local_data) < 50: # Start small to test
            if psutil.virtual_memory().percent > 70: break
            src, dst, load = get_random_scenario(GS_NAMES)
            
            # Setup a flow
            fid = random.randint(1000, 9999)
            f = Flow(fid, src, dst, sim.core.current_time, sim.core.current_time + 10, load)
            sim.traffic.flows = {fid: f}
            sim.traffic.active_flows = {fid}

            # Run 5 steps
            for _ in range(5):
                sim.run_step(sim.core.current_time, 1.0)
                sim.core.current_time += 1.0
                
                sample = sim.collect_single_sample() 
            
                if sample:
                    local_data.append(sample)
                    with shared_counter.get_lock():
                        shared_counter.value += 1
            
            # Reset
            for node in sim.topology.nodes: node.queue.clear()
            
    except Exception as e:
        # This will now print if a worker fails!
        print(f"CRITICAL ERROR in Worker-{worker_id}: {e}")
        
    return local_data
def init_worker(counter, event):
    # This makes these variables global inside each worker process
    global shared_counter, stop_event
    shared_counter = counter
    stop_event = event

if __name__ == "__main__":
    # 1. FORCE 440 SATS (Fixes the '100 available' issue)
    #generate_omm_csv(CONSTELLATION_FILE, 22, 20)
    SATS = get_satellite_data(num_sats=440, csv_file=CONSTELLATION_FILE)

    num_cores = max(1, mp.cpu_count() - 2)
    total_samples_captured = mp.Value('i', 0)
    stop_event = mp.Event()

    logging.info(f"Starting Data Farm with {num_cores} workers...")

    with mp.Pool(processes=num_cores, 
                 initializer=init_worker, 
                 initargs=(total_samples_captured, stop_event)) as pool:
        # Pass SATS directly to workers
        async_results = [pool.apply_async(simulation_worker, 
                         (i,)) 
                         for i in range(num_cores)]

        try:
            while not all(r.ready() for r in async_results):
                print(f"Progress: {total_samples_captured.value} samples collected...", end='\r')
                time.sleep(1)
        except KeyboardInterrupt:
            logging.info("Interrupt caught. Cleaning up...")
            stop_event.set()

        # Collect results
        results = [r.get() for r in async_results]
        final_dataset = [s for sublist in results if sublist for s in sublist]

        if final_dataset:
            with open("farm_data.pkl", "wb") as f:
                pickle.dump(final_dataset, f)
            logging.info(f"Success! Saved {len(final_dataset)} samples.")
        else:
            logging.error("Final dataset is still empty.")