import numpy as np
import psutil
from schedule_core import ScheduleCore
from traffic_module import TrafficModule, Flow
from logic_module import LogicalTopology, GROUND_STATIONS
from datetime import datetime, timezone
from skyfield.api import load
from config import LogicalConfig



class SimulationManager:
    def __init__(self, satellites, headless=True):
        self.core = ScheduleCore()
        self.topology = LogicalTopology(satellites)
        self.traffic = TrafficModule(self) # Passing 'self' as the network_module
        self.ts=load.timescale()
        self.start_epoch = self.ts.from_datetime(datetime.now(timezone.utc))
        self.headless = headless
        # Data Collection Log
        self.dataset = []
        self.delivery_logs = []  # New log for packet delivery events
        self.cfg = LogicalConfig()
        self.route_cache = {}
    def get_sim_time(self, seconds_from_start):
    # Adds simulation seconds to the starting epoch
        return self.ts.tt_jd(self.start_epoch.tt + (seconds_from_start / 86400.0))
    
    def inject_packet(self, packet):
        """
        Called by TrafficModule. 
        This is where we use igraph to route and log data.
        """
        time=self.get_sim_time(self.core.current_time)
        # 1. Get current ground station positions
        gs_pos = {name: self.topology.get_gs_position(name, time) 
                  for name, _, _, _ in GROUND_STATIONS}
        
        # 2. Compute current Graph
        pos, links, _, _ = self.topology.compute(time)
        g = self.topology.construct_unified_graph(pos, gs_pos)
        
        # 3. Find Dijkstra Path (The 'Ground Truth' for RL)
        path = self.topology.get_path(g, packet.src, packet.dst)
        
        if path:
            # 4. Log the state and the optimal action for RL training
            self.traffic.on_packet_delivered(packet, self.core.current_time)

    def collect_data(self, g, packet, optimal_path):
    # 1. Capture the 'pressure' in the network
        queue_states = {n.sat.name: len(n.queue) for n in self.topology.nodes}
        for gs_name, q in self.topology.gs_queues.items():
            queue_states[gs_name] = len(q)
        adj_data = g.get_adjacency().data
        if hasattr(adj_data, 'tolist'):
         matrix_to_save = adj_data.tolist()
        else:
            matrix_to_save = adj_data  # It is already a list
        # 2. Build the entry
        entry = {
            "time": self.core.current_time,
            "matrix": matrix_to_save, # .tolist() is vital for Pickling!
            "src": packet.src,
            "dst": packet.dst,
            "target_path": optimal_path,
            "queue_states": queue_states
        }
        
        # 3. Save to the internal list (Fixing the typo)
        self.dataset.append(entry)

    def run_step(self, time_secs, interval):
        self.route_cache={}
        if int(time_secs) % 1 == 0:
            print(f"--- Sim Time: {time_secs}s | Packets in Dataset: {len(self.dataset)} ---", flush=True)
    # 1. Generate new packets from Ground Stations/Flows
        self.traffic.on_packet_generation(time_secs, interval)

        # 2. Get current topology state for routing decisions
        sim_time = self.get_sim_time(time_secs)
        pos, links, _, _ = self.topology.compute(sim_time)
        
        # Get ground station positions for the graph
        gs_pos = {name: self.topology.get_gs_position(name, sim_time) 
                for name, _, _, _ in GROUND_STATIONS}
        g = self.topology.construct_unified_graph(pos, gs_pos)

        if self.traffic.active_flows:
            flow = self.traffic.flows[list(self.traffic.active_flows)[0]]
            path = self.topology.get_path(g,flow.src, flow.dst)
            self.collect_data(g, flow, path)
        limit = 1
        # 3. FORWARDING LOOP: Process one packet from every node's queue
        for node in self.topology.nodes:
            if not node.queue:
                continue
            # Pull the oldest packet (First-In-First-Out)
            packets_processed = 0
            not_ready = []
            sent_this_tick = 0
             # Max packets per satellite per second
            sent = 0
            while node.queue and sent < limit:
                
                packet = node.queue.popleft()
                packets_processed += 1
               
                if packet.available_at > time_secs:
                    not_ready.append(packet)
                    continue
                
            # Check TTL (Loop/Congestion Prevention)
                if not packet.decrement_ttl():
                    self.traffic.on_packet_dropped(packet, "TTL_EXPIRED")
                    continue
                packet.hops += 1
            # 4. ROUTING DECISION (The RL Actor's job)
            # For now, we still use Dijkstra to generate training labels
                cache_key = (node.sat.name, packet.dst)
            
                if cache_key in self.route_cache:
                    path = self.route_cache[cache_key]
                else:
                # Calculate once and store
                    path = self.topology.get_path(g, node.sat.name, packet.dst)
                    self.route_cache[cache_key] = path
                if path and len(path) > 1:
                    next_hop_name = path[1] # Path[0] is the current node
                    delay = self.calculate_delays(node.sat.name, next_hop_name, packet.size_bits,pos)
                    packet.available_at = time_secs + delay
                
                    # Check if next hop is a satellite or Ground Station
                    if next_hop_name == packet.dst:
                        self.traffic.on_packet_delivered(packet, time_secs)
                    else:
                        # Move to next satellite's queue
                        next_node = self.topology.get_node_by_id(next_hop_name)
                        if next_node and len(next_node.queue) < next_node.queue.maxlen:
                            next_node.queue.append(packet)
                        else:
                            self.traffic.on_packet_dropped(packet, "BUFFER_OVERFLOW")
                    sent += 1               
                else:
                    self.traffic.on_packet_dropped(packet, "NO_ROUTE_FOUND")
            node.queue.extendleft(reversed(not_ready)) # Re-add not ready packets at the front
                
        
    def run_headless(self, until_seconds, shared_counter=None, stop_event=None):
        interval = 1.0 # 1 second steps
        current_time = 0
        
        while current_time < until_seconds:
            # Task 5 & 6: OS Synchronization & Watchdog
            if stop_event and stop_event.is_set():
                break
            
            if psutil.virtual_memory().percent > 70:
                break
                
            # Drive the simulation step
            self.run_step(current_time, interval)
            current_time += interval
            
            # Task 1: Update Shared Odometer
            if shared_counter:
                with shared_counter.get_lock():
                    shared_counter.value += 1

    def collect_single_sample(self):
        
        interval = 1.0 # 1 second of sim time
        
        # 1. Advance the clock
        self.core.current_time += interval
        
        # 2. Run the logic (Traffic generation + One-hop forwarding)
        # This is the run_step we wrote earlier
        self.run_step(self.core.current_time, interval)
        
        # 3. Return the last entry added to the dataset by run_step
        if self.dataset:
            return self.dataset[-1] 
        return None
    def calculate_delays(self, node_a_name, node_b_name, packet_size_bits,pos):
    # 1. Propagation Delay (Light speed in km/s is ~300,000)
        dist = self.topology.get_distance(node_a_name, node_b_name,pos) # Ensure this returns km
        prop_delay = dist / 300000.0
        
        # 2. Transmission Delay (Assuming 1 Gbps link = 1e9 bits/sec)
        bandwidth_bps = 1_000_000_000 
        trans_delay = packet_size_bits / bandwidth_bps
        
        return prop_delay + trans_delay