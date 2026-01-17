import numpy as np
import psutil
from schedule_core import ScheduleCore
from traffic_module import TrafficModule, Flow
from logic_module import LogicalTopology, GROUND_STATIONS
from datetime import datetime, timezone
from skyfield.api import load
from config import LogicalConfig
from network_moduleTCP import EnhancedNetworkModule
from datetime import timedelta

class SimulationManager:
    def __init__(self, satellites, headless=True):
        self.core = ScheduleCore()
        self.topology = LogicalTopology(satellites)
        self.ts = load.timescale()
        self.start_epoch = self.ts.from_datetime(datetime.now(timezone.utc))
        self.headless = headless
        
        # Create EnhancedNetworkModule (REPLACES old network module)
        self.network = EnhancedNetworkModule(self)
        
        # Share the schedule core between simulation and network
        self.network.core = self.core
        
        # Pass network module to TrafficModule
        self.traffic = TrafficModule(self.network)
        
        # Data Collection Log
        self.dataset = []
        self.delivery_logs = []
        self.cfg = LogicalConfig()
        self.route_cache = {}
        
        # Add GROUND_STATIONS to topology for compatibility
        self.topology.GROUND_STATIONS = GROUND_STATIONS
    
    def get_sim_time(self, seconds_from_start):
        """Converts simulation seconds to skyfield time"""
        return self.ts.tt_jd(self.start_epoch.tt + (seconds_from_start / 86400.0))
    
    def process_events(self, until_time):
        """Process scheduled events up to until_time"""
        if hasattr(self.core, 'run'):
            self.core.run(until=until_time)
    
    def collect_data(self, g, packet, optimal_path):
        """Collect data for RL training"""
        # 1. Capture the 'pressure' in the network
        queue_states = {n.sat.name: len(n.queue) for n in self.topology.nodes}
        for gs_name, q in self.topology.gs_queues.items():
            queue_states[gs_name] = len(q)
        
        # 2. Get adjacency matrix
        try:
            adj_data = g.get_adjacency().data
            if hasattr(adj_data, 'tolist'):
                matrix_to_save = adj_data.tolist()
            else:
                matrix_to_save = adj_data
        except:
            matrix_to_save = []
        
        # 3. Build the entry
        entry = {
            "time": self.core.current_time,
            "matrix": matrix_to_save,
            "src": packet.src,
            "dst": packet.dst,
            "target_path": optimal_path,
            "queue_states": queue_states
        }
        
        # 4. Save to dataset
        self.dataset.append(entry)
    
    def run_step(self, time_secs, interval):
        """Run one simulation step with protocol emulation"""
        # Clear route cache each step
        self.route_cache = {}
        
        if int(time_secs) % 1 == 0:
            print(f"--- Sim Time: {time_secs}s | Dataset: {len(self.dataset)} | Active Flows: {len(self.traffic.active_flows)} ---", flush=True)
        
        # 1. Generate new packets using EnhancedNetworkModule
        self.traffic.on_packet_generation(time_secs, interval)
        
        # 2. PROCESS SCHEDULED EVENTS - CRITICAL!
        self.process_events(time_secs + interval)
        
        # 3. Check for retransmissions in the network module
        retransmit_packets = self.network.check_retransmissions(time_secs)
        for packet in retransmit_packets:
            # Resend packets that timed out
            self.network.send_packet(packet, time_secs)
        
        # 4. Process events again (for retransmissions)
        self.process_events(time_secs + interval)
        
        # 5. Update network statistics
        stats = self.network.update_statistics()
        
        # 6. Collect data for RL training (optional)
        if self.traffic.active_flows:
            flow = self.traffic.flows[list(self.traffic.active_flows)[0]]
            sim_time = self.get_sim_time(time_secs)
            gs_pos = {}
            for name, lat, lon, alt in self.topology.GROUND_STATIONS:
                gs_pos[name] = self.topology.get_gs_position(name, sim_time)
            
            pos, _, _, _ = self.topology.compute(sim_time)
            g = self.topology.construct_unified_graph(pos, gs_pos)
            path = self.topology.get_path(g, flow.src, flow.dst)
            
            # Create a dummy packet for data collection
            class DummyPacket:
                def __init__(self, src, dst, creation_time):
                    self.src = src
                    self.dst = dst
                    self.creation_time = creation_time
            
            dummy_packet = DummyPacket(flow.src, flow.dst, time_secs)
            self.collect_data(g, dummy_packet, path)
    
    def run_headless(self, until_seconds, shared_counter=None, stop_event=None):
        """Run simulation without visualization"""
        interval = 0.5  # Smaller steps for better event processing
        current_time = 0
        
        while current_time < until_seconds:
            # OS Synchronization & Watchdog
            if stop_event and stop_event.is_set():
                break
            
            if psutil.virtual_memory().percent > 70:
                break
            
            # Drive the simulation step
            self.run_step(current_time, interval)
            current_time += interval
            
            # Update Shared Odometer
            if shared_counter:
                with shared_counter.get_lock():
                    shared_counter.value += 1
    
    def collect_single_sample(self):
        """Collect a single sample for RL"""
        interval = 1.0  # 1 second of sim time
        
        # 1. Advance the clock
        self.core.current_time += interval
        
        # 2. Run the logic
        self.run_step(self.core.current_time, interval)
        
        # 3. Return the last entry added to the dataset
        if self.dataset:
            return self.dataset[-1]
        return None
    
    def calculate_delays(self, node_a_name, node_b_name, packet_size_bits, pos):
        """Calculate propagation and transmission delays"""
        # 1. Propagation Delay (Light speed in km/s is ~300,000)
        dist = self.topology.get_distance(node_a_name, node_b_name, pos)
        prop_delay = dist / 300000.0
        
        # 2. Transmission Delay (Assuming 1 Gbps link = 1e9 bits/sec)
        bandwidth_bps = 1_000_000_000
        trans_delay = packet_size_bits / bandwidth_bps
        
        return prop_delay + trans_delay
    
    def calculate_reward(self, packet):
        """Calculate reward for RL based on packet delivery"""
        # Check if it's a ProtocolPacket or basic Packet
        if hasattr(packet, 'priority'):
            priority = packet.priority
        else:
            priority = 3  # Default to bulk
            
        if packet.delivered:
            base_reward = {1: 20.0, 2: 10.0, 3: 5.0}.get(priority, 5.0)
            latency_penalty = (packet.delivery_time - packet.creation_time) * 2.0
            return base_reward - latency_penalty
        else:
            return {1: -100.0, 2: -40.0, 3: -10.0}.get(priority, -10.0)
    
    def apply_action_to_packet(self, packet, current_node_name, next_hop_name):
        """
        Execute RL agent's decision
        Returns: (reward, done, info)
        """
        # 1. Get Physical Distance and Link Status
        sim_time = self.get_sim_time(self.core.current_time)
        pos, links, _, _ = self.topology.compute(sim_time)
        
        # 2. Calculate delay
        delay = self.calculate_delays(current_node_name, next_hop_name, 
                                      getattr(packet, 'size_bits', 12000), pos)
        
        # 3. Apply Latency to Packet
        packet.available_at = self.core.current_time + delay
        self.core.current_time += delay
        
        # 4. Handle "Hop"
        if next_hop_name == packet.dst:
            # SUCCESS: Packet reached destination
            reward = 50.0 - (self.core.current_time - packet.creation_time)
            return reward, True, {"status": "DELIVERED"}
        
        elif self.topology.get_node_by_id(next_hop_name):
            # PROGRESS: Packet moved to next satellite
            # Note: The network module handles actual queuing now
            reward = -0.1  # Small penalty for every hop
            return reward, False, {"status": "IN_TRANSIT"}
        
        else:
            # FAILURE: Invalid Node
            reward = -10.0
            return reward, True, {"status": "DROPPED"}