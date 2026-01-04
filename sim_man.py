import numpy as np
from schedule_core import ScheduleCore
from traffic_module import TrafficModule, Flow
from logic_module import LogicalTopology, GROUND_STATIONS
from datetime import datetime, timezone
from skyfield.api import load

class SimulationManager:
    def __init__(self, satellites):
        self.core = ScheduleCore()
        self.topology = LogicalTopology(satellites)
        self.traffic = TrafficModule(self) # Passing 'self' as the network_module
        self.ts=load.timescale()
        self.start_epoch = self.ts.from_datetime(datetime.now(timezone.utc))
        # Data Collection Log
        self.dataset = []
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
            self.collect_data(g, packet, path)
            self.traffic.on_packet_delivered(packet, self.core.current_time)

    def collect_data(self, g, packet, optimal_path):
        """
        Saves the Graph state and the best path to a list for RL training.
        """
        entry = {
            "time": self.core.current_time,
            "matrix": g.get_adjacency().data,
            "src": packet.src,
            "dst": packet.dst,
            "target_path": optimal_path
        }
        self.dataset.append(entry)

    def run_step(self, time, interval):
        """Periodic event to drive the simulation"""
        # Update traffic
        
        self.traffic.on_packet_generation(time, interval)
        
        # Schedule the next heartbeat
        self.core.schedule(time + interval, 2, self.run_step, interval)
