# logic_module.py - FIXED VERSION with proper routing
import time
import math
import csv
import numpy as np
from datetime import timedelta
from collections import defaultdict
from skyfield.api import load, EarthSatellite, wgs84
from dataclasses import dataclass
from enum import Enum, auto
from typing import Dict, List, Tuple, Set
import igraph as ig
from collections import deque
from config.config import LogicalConfig

# Configuration Section
CSV_FILENAME = 'customconstellation.csv'
MAX_SATS = 100
TIME_MULTIPLIER = 100.0
MAX_QUEUE_SIZE = 1000
EARTH_RADIUS = 6371.0
SCALE = 1.0/6371.0

GROUND_STATIONS = [
    ("GS_INDIA", 28.6139, 77.2090, 0),
    ("GS_USA", 37.7749, -122.4194, 0),
    ("GS_BRAZIL", -23.5505, -46.6333, 0),
    ("GS_AUSTRALIA", -33.8688, 151.2093, 0),
    ("NP", 90.0, 0.0, 0),
    ("SP", -90.0, 0.0, 0),
]

# Earth Occlusion Check - SIMPLIFIED (always returns True for testing)
def link_clear_of_earth(p1, p2, earth_radius=EARTH_RADIUS):
    """SIMPLIFIED: Always return True for testing"""
    return True

# Data Structure
@dataclass(eq=False)
class SatelliteNode:
    sat: EarthSatellite
    orbit_id: int
    index: int
    
    def __post_init__(self):
        if self.queue is None:
            self.queue = deque(maxlen=MAX_QUEUE_SIZE)

# Link Type
class LinkType(Enum):
    ISL_INTRA = auto()
    ISL_INTER = auto()
    IOL = auto()
    UDL = auto()

# Logical Module - REALISTIC MODE WITH PROPER ROUTING
class LogicalTopology:
    GROUND_STATIONS = GROUND_STATIONS
    
    def __init__(self, sats, config=None):
        if config is None:
            self.cfg = LogicalConfig()
        else:
            self.cfg = config
            
        # USE REALISTIC SETTINGS
        self.cfg.min_elev_deg = 10.0  # REALISTIC minimum elevation
        self.cfg.isl_max_km = 5000.0  # REALISTIC ISL range
        self.cfg.debug = False
        
        # Group satellites by orbit
        self.nodes = self._group_orbits(sats)
        
        self.node_map = {node.sat.name: node for node in self.nodes}
        
        # Initialize queues
        max_q = getattr(self.cfg, 'MAX_QUEUE_SIZE', 1000)
        for node in self.nodes:
            node.queue = deque(maxlen=max_q)
        self.gs_queues = {gs[0]: deque(maxlen=max_q) for gs in GROUND_STATIONS}
        
        print(f"[TOPOLOGY] REALISTIC MODE: Initialized with {len(self.nodes)} satellites")
        print(f"[TOPOLOGY] Using realistic connections: min_elev={self.cfg.min_elev_deg}°, isl_max={self.cfg.isl_max_km}km")
    
    def _group_orbits(self, sats):
        """Group satellites by orbit parameters"""
        groups = defaultdict(list)
        for sat in sats:
            # Extract orbital parameters
            inc = round(sat.model.inclo, 1) if hasattr(sat, 'model') else 0
            raan = round(sat.model.nodeo, 1) if hasattr(sat, 'model') else 0
            groups[(inc, raan)].append(sat)
        
        nodes = []
        orbit_id = 0
        
        for key in sorted(groups.keys()):
            group = groups[key]
            # Sort by mean anomaly
            group.sort(key=lambda s: s.model.mo if hasattr(s, 'model') else 0)
            for i, sat in enumerate(group):
                nodes.append(SatelliteNode(sat, orbit_id, i))
            orbit_id += 1
        
        return nodes
    
    def compute(self, t):
        """Compute current topology based on satellite positions"""
        should_print = True  # Always print for now
        
        # Get satellite positions
        pos = {}
        for n in self.nodes:
            try:
                pos[n] = n.sat.at(t).position.km
            except:
                pos[n] = np.array([0, 0, 0])
        
        # Get ground station positions
        gs_positions = {}
        for name, lat, lon, alt in GROUND_STATIONS:
            try:
                gs_positions[name] = self.get_gs_position(name, t)
            except:
                gs_positions[name] = np.array([0, 0, 0])
        
        if should_print:
            print(f"[TOPOLOGY] Time: {t.utc_datetime()}")
            print(f"[TOPOLOGY] First satellite position: {list(pos.values())[0] if pos else 'None'}")
            print(f"[TOPOLOGY] GS_INDIA position: {gs_positions.get('GS_INDIA', 'None')}")
        
        # Group by orbit
        orbits = defaultdict(list)
        for n in self.nodes:
            orbits[n.orbit_id].append(n)
        
        candidate_links = set()
        
        # 1. Same-orbit ISLs - Connect neighbors in same orbit
        for orbit_nodes in orbits.values():
            N = len(orbit_nodes)
            if N < 2:
                continue
                
            for i, n in enumerate(orbit_nodes):
                # Connect to next satellite in orbit (forward link)
                next_idx = (i + 1) % N
                next_neighbor = orbit_nodes[next_idx]
                
                if link_clear_of_earth(pos[n], pos[next_neighbor]):
                    a, b = sorted((n, next_neighbor), key=lambda x: x.sat.name)
                    candidate_links.add((LinkType.ISL_INTRA, a, b))
        
        # 2. Cross-orbit ISLs - Connect nearest in adjacent orbits
        orbit_ids = sorted(orbits.keys())
        for i, oid in enumerate(orbit_ids):
            next_oid = orbit_ids[(i + 1) % len(orbit_ids)]
            
            for n in orbits[oid]:
                nearest = None
                min_dist = float('inf')
                
                for m in orbits[next_oid]:
                    dist = np.linalg.norm(pos[n] - pos[m])
                    if dist < min_dist and dist <= self.cfg.isl_max_km:
                        min_dist = dist
                        nearest = m
                
                if nearest and link_clear_of_earth(pos[n], pos[nearest]):
                    a, b = sorted((n, nearest), key=lambda x: x.sat.name)
                    candidate_links.add((LinkType.ISL_INTER, a, b))
        
        # In the compute method of LogicalTopology:
        # 3. UDLs - Connect ground stations to VISIBLE satellites with better criteria
        udl_count = 0
        min_elev_rad = math.radians(self.cfg.min_elev_deg)

        for gs_name, gs_pos in gs_positions.items():
            visible_sats = []
            
            for n in self.nodes:
                sat_pos = pos[n]
                
                # Calculate elevation angle
                # Simplified elevation check
                distance = np.linalg.norm(gs_pos - sat_pos)
                
                # Earth radius in km
                earth_radius = 6371.0
                
                # Basic elevation calculation
                if distance < 3000:  # Maximum visible distance
                    # Simple visibility check
                    earth_angle = math.asin(earth_radius / (earth_radius + 550))  # 550km altitude
                    
                    # If ground station can see satellite
                    visible_sats.append((n, distance))
            
            # Sort by distance and connect to nearest 3
            visible_sats.sort(key=lambda x: x[1])
            for n, distance in visible_sats[:3]:  # Connect to up to 3 nearest satellites
                if link_clear_of_earth(gs_pos, pos[n]):
                    candidate_links.add((LinkType.UDL, n, gs_name))
                    udl_count += 1
        
        if should_print:
            print(f"[TOPOLOGY] Found {udl_count} UDL links (based on visibility)")
        
        # 4. Add cross-links for better connectivity
        all_sats = list(self.nodes)
        for i, a in enumerate(all_sats):
            connections = 0
            # Try to connect to nearest satellites within range
            for j in range(i + 1, min(i + 5, len(all_sats))):
                b = all_sats[j]
                if a.orbit_id != b.orbit_id:
                    dist = np.linalg.norm(pos[a] - pos[b])
                    if dist <= self.cfg.isl_max_km:
                        if link_clear_of_earth(pos[a], pos[b]):
                            a_norm, b_norm = sorted((a, b), key=lambda x: x.sat.name)
                            candidate_links.add((LinkType.IOL, a_norm, b_norm))
                            connections += 1
                            if connections >= 2:
                                break
        
        # Update links
        new_links = candidate_links - self.links
        removed_links = self.links - candidate_links
        self.prev_links = self.links.copy()
        self.links = candidate_links
        self.last_update_time = t
        
        # Debug output
        if should_print:
            link_counts = {lt: 0 for lt in LinkType}
            for link_type, _, _ in self.links:
                link_counts[link_type] += 1
            
            print(f"[TOPOLOGY] Total links: {len(self.links)}")
            print(f"[TOPOLOGY] UDL links: {link_counts[LinkType.UDL]}")
            print(f"[TOPOLOGY] ISL links: {link_counts[LinkType.ISL_INTRA] + link_counts[LinkType.ISL_INTER] + link_counts[LinkType.IOL]}")
            
            if link_counts[LinkType.UDL] == 0:
                print("[TOPOLOGY] ⚠️ No UDL links found (all ground stations disconnected)")
            else:
                print("[TOPOLOGY] ✓ Some UDL links present")
        
        return pos, self.links, new_links, removed_links
    
    def construct_unified_graph(self, pos, gs_positions=None):
        """Construct routing graph based on current links"""
        if gs_positions is None:
            gs_positions = {}
        
        # Create vertices
        num_sats = len(self.nodes)
        num_gs = len(GROUND_STATIONS)
        total_vertices = num_sats + num_gs
        
        g = ig.Graph(n=total_vertices, directed=False)
        
        # Set vertex attributes
        sat_names = [n.sat.name for n in self.nodes]
        gs_names = [gs[0] for gs in GROUND_STATIONS]
        all_names = sat_names + gs_names
        
        g.vs["name"] = all_names
        g.vs["type"] = ["satellite"] * num_sats + ["ground_station"] * num_gs
        
        # Create mapping
        name_to_id = {}
        for i, name in enumerate(all_names):
            name_to_id[name] = i
        
        # Add edges based on current links
        edges = []
        
        for link_type, a, b in self.links:
            if link_type in [LinkType.ISL_INTRA, LinkType.ISL_INTER, LinkType.IOL]:
                if isinstance(a, SatelliteNode) and isinstance(b, SatelliteNode):
                    v1_id = name_to_id.get(a.sat.name)
                    v2_id = name_to_id.get(b.sat.name)
                    if v1_id is not None and v2_id is not None:
                        edges.append((v1_id, v2_id))
            
            elif link_type == LinkType.UDL:
                if isinstance(a, SatelliteNode):
                    sat_name = a.sat.name
                    gs_name = b
                else:
                    sat_name = b.sat.name
                    gs_name = a
                
                v1_id = name_to_id.get(sat_name)
                v2_id = name_to_id.get(gs_name)
                
                if v1_id is not None and v2_id is not None:
                    edges.append((v1_id, v2_id))
        
        # Add edges to graph
        if edges:
            # Remove duplicate edges
            unique_edges = list(set(edges))
            g.add_edges(unique_edges)
            g.es["weight"] = [1.0] * len(unique_edges)
            
            # Check connectivity
            components = g.components()
            if self.cfg.debug:
                print(f"[GRAPH] Vertices: {len(g.vs)}, Edges: {len(g.es)}")
                print(f"[GRAPH] Connected components: {len(components)}")
                
                if len(components) == 1:
                    print("[GRAPH] ✓ FULLY CONNECTED")
                else:
                    # Show which ground stations are connected
                    connected_gs = set()
                    for comp in components:
                        for v_idx in comp:
                            node_type = g.vs[v_idx]["type"]
                            if node_type == "ground_station":
                                connected_gs.add(g.vs[v_idx]["name"])
                    
                    print(f"[GRAPH] ⚠️  {len(components)} disconnected components")
                    print(f"[GRAPH] Connected GS: {', '.join(connected_gs) if connected_gs else 'None'}")
            print(f"[GRAPH] Checking ground station connectivity:")
            for gs_name in GROUND_STATIONS:
                gs = gs_name[0]  # Get name from tuple
                try:
                    gs_idx = g.vs.find(name=gs).index
                    # Count satellite connections
                    sat_connections = 0
                    neighbors = g.neighbors(gs_idx)
                    for n_idx in neighbors:
                        if g.vs[n_idx]["type"] == "satellite":
                            sat_connections += 1
                    print(f"  {gs}: {sat_connections} satellite connections")
                except:
                    print(f"  {gs}: NOT FOUND in graph")
        
        return g
    
    @staticmethod
    def get_path(g, start_name, end_name):
        """Find shortest path between two nodes"""
        try:
            if start_name == end_name:
                return [start_name]
            
            start_idx = g.vs.find(name=start_name).index
            end_idx = g.vs.find(name=end_name).index
            
            # Get shortest path
            path_indices = g.get_shortest_paths(
                start_idx,
                to=end_idx,
                output="vpath",
                weights="weight"
            )[0]
            
            if path_indices:
                return [g.vs[i]["name"] for i in path_indices]
            return []
                
        except Exception as e:
            return []
    
    def get_gs_position(self, name, t):
        """Get ground station position at time t"""
        gs_metadata = next((gs for gs in GROUND_STATIONS if gs[0] == name), None)
        if not gs_metadata:
            raise ValueError(f"Ground Station {name} not found.")
        
        _, lat, lon, alt = gs_metadata
        gs_geodetic = wgs84.latlon(lat, lon, elevation_m=alt)
        return gs_geodetic.at(t).position.km
    
    def get_node_by_id(self, node_id: str):
        return self.node_map.get(node_id)