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
from config import LogicalConfig
#Configuration Section

CSV_FILENAME = 'customconstellation.csv'
MAX_SATS = 100
TIME_MULTIPLIER = 100.0
MAX_QUEUE_SIZE = 100
EARTH_RADIUS = 6371.0
SCALE = 1.0/6371.0

LOGICAL_CONFIG = LogicalConfig()

GROUND_STATIONS = [
    ("GS_INDIA", 28.6139, 77.2090, 0),
    ("GS_USA", 37.7749, -122.4194, 0),
    ("GS_BRAZIL", -23.5505, -46.6333, 0),
    ("GS_AUSTRALIA", -33.8688, 151.2093, 0),
    ("NP",90.0,0.0,0),
    ("SP",-90.0,0.0,0),
]

# Earth Occlusion Check

def link_clear_of_earth(p1, p2):
    d = p2 - p1
    f = p1
    a = np.dot(d, d)
    b = 2 * np.dot(f, d)
    c = np.dot(f, f) - EARTH_RADIUS**2
    disc = b*b - 4*a*c
    if disc < 0:
        return True
    s = math.sqrt(disc)
    t1 = (-b - s) / (2*a)
    t2 = (-b + s) / (2*a)
    return not ((0 <= t1 <= 1) or (0 <= t2 <= 1))

# Data Structure

@dataclass(eq=False)
class SatelliteNode:
    sat: EarthSatellite
    orbit_id: int
    index: int

# Link Type

class LinkType(Enum):
    ISL_INTRA = auto()
    ISL_INTER = auto()
    IOL = auto()
    UDL = auto()
    
# Logical Module

class LogicalTopology:

    def __init__(self, sats):
        self.nodes = self._group_orbits(sats)
        self.cfg = LOGICAL_CONFIG
        self.links = set()
        self.prev_links = set()
        self.last_update_time = None
        self.node_map = {node.sat.name: node for node in self.nodes}
        max_q = getattr(self.cfg, 'MAX_QUEUE_SIZE', 100)
        for node in self.nodes:
            node.queue = deque(maxlen=max_q)
        self.gs_queues = {gs[0]: deque(maxlen=max_q) for gs in GROUND_STATIONS}

    def get_node_by_id(self, node_id: str):
        return self.node_map.get(node_id)
    def get_queue_status(self):
        return {node.sat.name: len(node.queue) for node in self.nodes}

    def _group_orbits(self, sats):
        groups = defaultdict(list)
        for sat in sats:
            inc = round(sat.model.inclo, 1)
            raan = round(sat.model.nodeo, 1)
            groups[(inc, raan)].append(sat)
        nodes = []
        orbit_id = 0

        for key in sorted(groups.keys()):
            group = groups[key]
            group.sort(key = lambda s: s.model.mo)
            for i, sat in enumerate(group):
                nodes.append(SatelliteNode(sat, orbit_id, i))
            
            orbit_id += 1
        
        return nodes
    
    def compute(self, t):
        pos = {n: n.sat.at(t).position.km for n in self.nodes}
        orbits = defaultdict(list)
        for n in self.nodes:
            orbits[n.orbit_id].append(n)
        
        candidate_links = set()

        # same-orbit ISLs
        for orbit_nodes in orbits.values():
            N = len(orbit_nodes)
            for i, n in enumerate(orbit_nodes):
                for nb in (orbit_nodes[(i - 1) % N], orbit_nodes[(i + 1) % N]):
                    if link_clear_of_earth(pos[n], pos[nb]):
                        a, b = sorted((n, nb), key = id)
                        candidate_links.add(
                            (LinkType.ISL_INTRA, a, b)
                        )
        
        # adjacent orbit ISLs
        for oid in orbits:
            for adj in (oid-1, oid+1):
                if adj not in orbits:
                    continue

                for n in orbits[oid]:
                    nearest = min(
                        orbits[adj],
                        key = lambda m: np.linalg.norm(pos[n] - pos[m])
                    )

                    dist = np.linalg.norm(pos[n] - pos[nearest])

                    if dist <= self.cfg.isl_max_km and link_clear_of_earth(pos[n], pos[nearest]):
                        a, b = sorted((n, nearest), key = id)
                        candidate_links.add(
                            (LinkType.ISL_INTER, a, b)
                        )

        # IOL cone models
        for a in self.nodes:
            for b in self.nodes:
                if a is b:
                    continue

                v = pos[a] - pos[b]
                nadir = -pos[a]

                cos_angle = np.dot(v, nadir) / (
                    np.linalg.norm(v) * np.linalg.norm(nadir)
                )

                cos_angle = np.clip(cos_angle, -1.0, 1.0)
                angle = math.degrees(math.acos(cos_angle))

                if angle <= self.cfg.iol_cone_deg and link_clear_of_earth(pos[a], pos[b]):
                    a_norm, b_norm = sorted((a, b), key = id)
                    candidate_links.add(
                        (LinkType.IOL, a_norm, b_norm)
                    )

        # UDL satellite ground links
        for n in self.nodes:
            for name, lat, lon, h in GROUND_STATIONS:
                gs = wgs84.latlon(lat, lon, elevation_m = h)

                alt, _, _ = (n.sat - gs).at(t).altaz()
                if alt.degrees < self.cfg.min_elev_deg:
                    continue

                gs_pos = gs.at(t).position.km

                if link_clear_of_earth(pos[n], gs_pos):
                    candidate_links.add(
                        (LinkType.UDL, n, name)
                    )

        new_links = candidate_links - self.links
        removed_links = self.links - candidate_links
        self.prev_links = self.links
        self.links = candidate_links
        self.last_update_time = t

        return pos, self.links, new_links, removed_links
    
    def construct_unified_graph(self, pos, gs_positions):
        num_sats = len(self.nodes)
        num_gs = len(GROUND_STATIONS)

        g = ig.Graph(n= num_sats + num_gs, directed=False)

        gs_names = [gs[0] for gs in GROUND_STATIONS]
        g.vs["name"] = [n.sat.name for n in self.nodes] + gs_names
        g.vs["type"] = ["satellite"] * num_sats + ["ground_station"] * num_gs

        sat_to_id = {node: i for i, node in enumerate(self.nodes)}
        gs_to_id = {name: i + num_sats for i, name in enumerate(gs_names)}

        edges = []
        weights = []

        for link_type, a, b, in self.links:
            if link_type in (LinkType.ISL_INTRA, LinkType.ISL_INTER, LinkType.IOL):
                u = sat_to_id[a]
                v = sat_to_id[b]
                dist = np.linalg.norm(pos[a] - pos[b])
            
            elif link_type == LinkType.UDL:
                u = sat_to_id[a]
                v = gs_to_id[b]
                dist = np.linalg.norm(pos[a] - gs_positions[b])

            else:
                continue

            if np.isnan(dist):
                continue

            edges.append((u, v))
            weights.append(dist)

        g.add_edges(edges)
        g.es["weight"] = weights

        return g
    
    @staticmethod
    def get_path(g, start_name, end_name):
        
        try:
            id_a = g.vs.find(name = start_name).index
            id_b = g.vs.find(name = end_name).index
        except ValueError:
            return []
        
        nan_edges = [e.index for e in g.es if np.isnan(e["weight"])]
        if nan_edges:
            g.delete_edges(nan_edges)

        try:
            path_indices = g.get_shortest_paths(
                id_a,
                to = id_b,
                weights = g.es["weight"],
                output = "vpath"
            )[0]
            return [g.vs[i]["name"] for i in path_indices]
        
        except Exception as e:
            print(f"Djikstra failed: {e}")
            return []
    def get_gs_position(self, name, t):
    
    # 1. Find the GS metadata from your GROUND_STATIONS list
        gs_metadata = next((gs for gs in GROUND_STATIONS if gs[0] == name), None)
        if not gs_metadata:
            raise ValueError(f"Ground Station {name} not found in configuration.")

        _, lat, lon, alt = gs_metadata

        # 2. Create the wgs84 geodetic object
        gs_geodetic = wgs84.latlon(lat, lon, elevation_m=alt)

        # 3. Compute the position at time 't'
        # This converts Geodetic (Lat/Lon) -> ECI (X, Y, Z) based on Earth's rotation
        pos_eci = gs_geodetic.at(t).position.km
        
        return pos_eci
    def get_distance(self, node_a_id, node_b_id, pos, gs_pos=None):
        """
        Calculates distance between any two nodes (Sat-Sat, Sat-GS, or GS-GS).
        """
        def resolve_pos(node_id):
            # 1. Check if it's a Satellite by name
            if node_id in self.node_map:
                sat_node = self.node_map[node_id]
                return pos.get(sat_node)
            
            # 2. Check if it's a Ground Station
            if gs_pos and node_id in gs_pos:
                return gs_pos[node_id]
            
            # 3. Fallback: Try to compute GS position manually
            try:
                return self.get_gs_position(node_id, self.last_update_time)
            except ValueError:
                return None

        p1 = resolve_pos(node_a_id)
        p2 = resolve_pos(node_b_id)

        if p1 is None or p2 is None:
            # This prevents the crash; if a node isn't found, distance is infinite
            return float('inf')

        return np.linalg.norm(np.array(p1) - np.array(p2))