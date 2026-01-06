import multiprocessing as mp
import logging
import signal
import sys
import psutil
import time
from sim_man import SimulationManager
from setup import get_satellite_data, generate_omm_csv
import pickle

time_offset = 60

SATS=get_satellite_data(num_sats=200, csv_file='customcollection.csv')
# --- 2. LOGGING CONFIGURATION ---
# We configure logging to show the process name so we know which worker is talking
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] (%(processName)s) %(message)s',
    handlers=[logging.StreamHandler()]
)

# --- 1. SHARED MEMORY COUNTER ---
# This lives in global OS memory, accessible by all workers
total_samples_captured = mp.Value('i', 0) 
stop_event = mp.Event() # Used for Task 5: Graceful Shutdown

def signal_handler(sig, frame):
    logging.info("Interrupt received! Signaling workers to save and stop...")
    stop_event.set() # This tells all workers to wrap up their current loop

# Register the handler with the OS
signal.signal(signal.SIGINT, signal_handler)

def simulation_worker(worker_id, shared_counter, stop_event):
    # Give the process a name for the logger
    mp.current_process().name = f"Worker-{worker_id}"
    
    local_data = []
    sim = SimulationManager(SATS, headless=True) # Ensure Headless is True!
    sim.core.current_time += time_offset
    
    while not stop_event.is_set():
        # --- 6. RESOURCE WATCHDOG (Local Check) ---
        mem = psutil.virtual_memory().percent
        if mem > 70:
            logging.warning(f"Memory Critical ({mem}%). Stopping worker to save OS.")
            break
            
        # Run one step of simulation
        sample = sim.collect_single_sample()
        if sample and sim.core.current_time % 10 == 0:
            local_data.append(sample)
            # --- 1. UPDATE SHARED COUNTER ---
            with shared_counter.get_lock():
                shared_counter.value += 1
                
        # Optional: break after a certain amount of data
        if len(local_data) >= 100: 
            break
            
    return local_data

if __name__ == "__main__":
    num_cores = max(1, mp.cpu_count() - 3)
    logging.info(f"Starting Data Farm with {num_cores} workers...")

    with mp.Pool(processes=num_cores) as pool:
        # We use apply_async so we can monitor the shared counter while they work
        jobs = [pool.apply_async(simulation_worker, (i, total_samples_captured, stop_event)) 
                for i in range(num_cores)]

        try:
            while any(not j.ready() for j in jobs):
                # Monitor progress live
                print(f"Progress: {total_samples_captured.value} samples collected...", end='\r')
                time.sleep(1)
        except Exception as e:
            logging.error(f"Error in Orchestrator: {e}")
        finally:
            # Collect and Merge
            results = [j.get() for j in jobs if j.successful()]
            final_dataset = [s for sublist in results for s in sublist]
            
            with open("farm_data.pkl", "wb") as f:
                pickle.dump(final_dataset, f)
            logging.info(f"Side Quest Complete. Saved {len(final_dataset)} samples.")