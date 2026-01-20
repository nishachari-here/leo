# network_moduleTCP.py - COMPLETELY FIXED VERSION
import time
import math
import random
from collections import deque, defaultdict
from enum import Enum, auto
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Set
import heapq
import numpy as np
from logic_module import GROUND_STATIONS, LinkType
import warnings
warnings.filterwarnings('ignore', message='Couldn\'t reach some vertices')

# ==================== Protocol Definitions ====================

class ProtocolType(Enum):
    TCP = auto()
    UDP = auto()
    QUIC = auto()
    SCTP = auto()

class ConnectionState(Enum):
    CLOSED = auto()
    LISTEN = auto()
    SYN_SENT = auto()
    SYN_RECEIVED = auto()
    ESTABLISHED = auto()
    FIN_WAIT_1 = auto()
    FIN_WAIT_2 = auto()
    CLOSING = auto()
    TIME_WAIT = auto()
    CLOSE_WAIT = auto()
    LAST_ACK = auto()

@dataclass
class Connection:
    """Represents a TCP connection with full state machine"""
    src: str
    dst: str
    src_port: int
    dst_port: int
    state: ConnectionState = ConnectionState.CLOSED
    
    # Sequence numbers
    seq_num: int = 0
    ack_num: int = 0
    
    # Flow control
    window_size: int = 65535
    advertised_window: int = 65535
    
    # Congestion control (TCP Reno)
    cwnd: float = 1.0
    ssthresh: float = 65535.0
    dup_acks: int = 0
    in_fast_recovery: bool = False
    
    # RTT estimation
    rtt_samples: List[float] = None
    srtt: float = 0.1  # Start with small RTT
    rttvar: float = 0.0
    rto: float = 1.0  # Reasonable starting RTO
    
    # Statistics
    bytes_sent: int = 0
    bytes_received: int = 0
    packets_sent: int = 0
    packets_received: int = 0
    packets_retransmitted: int = 0
    
    # Buffers
    send_buffer: deque = None
    recv_buffer: deque = None
    retransmit_queue: List = None
    
    # Timing
    last_ack_time: float = 0.0
    last_send_time: float = 0.0
    syn_sent_time: float = 0.0  # Track when SYN was sent
    
    def __post_init__(self):
        if self.rtt_samples is None:
            self.rtt_samples = []
        if self.send_buffer is None:
            self.send_buffer = deque(maxlen=1000)
        if self.recv_buffer is None:
            self.recv_buffer = deque(maxlen=1000)
        if self.retransmit_queue is None:
            self.retransmit_queue = []
        
        # Start with more reasonable values for satellite networks
        self.rto = 3.0  # Increase from 2.0 to 3.0 seconds for satellite networks
        self.srtt = 1.0  # Start with 1 second RTT
        self.max_syn_retries = 5  # Add this field
    
    def update_rtt(self, sample: float):
        """Update RTT estimates (RFC 6298) - adjusted for satellite networks"""
        if len(self.rtt_samples) == 0:
            self.srtt = sample
            self.rttvar = sample / 2
        else:
            alpha = 0.125
            beta = 0.25
            self.rttvar = (1 - beta) * self.rttvar + beta * abs(self.srtt - sample)
            self.srtt = (1 - alpha) * self.srtt + alpha * sample
        
        # Satellite networks need longer RTOs
        self.rto = max(1.0, min(10.0, self.srtt + max(0.5, 4 * self.rttvar)))
        
        self.rtt_samples.append(sample)
        if len(self.rtt_samples) > 10:
            self.rtt_samples.pop(0)
    
    def on_packet_loss(self):
        """TCP Reno fast retransmit/fast recovery"""
        self.ssthresh = max(2, self.cwnd / 2)
        self.cwnd = self.ssthresh
        self.dup_acks = 0
        self.in_fast_recovery = True
    
    def on_timeout(self):
        """TCP Reno timeout - LESS AGGRESSIVE for satellite networks"""
        self.ssthresh = max(8, self.cwnd / 2)  # Higher minimum ssthresh
        self.cwnd = 4.0  # Start with 4 instead of 2
        self.dup_acks = 0
        self.in_fast_recovery = False
    
    def on_ack_received(self, bytes_acked: int):
        """Update congestion window on ACK - FASTER GROWTH"""
        if self.cwnd < self.ssthresh:
            # Slow start - grow faster
            self.cwnd += 2.0  # Increase from 1.0 to 2.0
        else:
            # Congestion avoidance - grow faster
            self.cwnd += 2.0 / self.cwnd  # Increase from 1.0/cwnd to 2.0/cwnd
        
        self.dup_acks = 0
        if self.in_fast_recovery and self.cwnd >= self.ssthresh:
            self.in_fast_recovery = False

# ==================== Protocol Packet ====================

@dataclass
class ProtocolPacket:
    """TCP/IP packet with full headers"""
    src: str
    dst: str
    src_port: int = 0
    dst_port: int = 0
    seq_num: int = 0
    ack_num: int = 0
    flags: Dict[str, bool] = None
    data: bytes = b""
    protocol_type: ProtocolType = ProtocolType.TCP
    ttl: int = 64
    window_size: int = 65535
    
    # Additional fields
    priority: int = 3
    creation_time: float = 0.0
    delivered: bool = False
    delivery_time: Optional[float] = None
    hops: int = 0
    
    # Journey tracking
    path: List[str] = None
    current_node: str = None
    
    def __post_init__(self):
        if self.flags is None:
            self.flags = {'SYN': False, 'ACK': False, 'FIN': False, 'RST': False, 'PSH': True}
        if self.path is None:
            self.path = []
        
        if self.src not in self.path:
            self.path.append(self.src)
        self.current_node = self.src
    
    @property
    def data_length(self):
        return len(self.data)
    
    def decrement_ttl(self):
        self.ttl -= 1
        return self.ttl > 0
    
    def move_to_node(self, node: str):
        if node != self.current_node:
            self.hops += 1
            if node not in self.path:
                self.path.append(node)
            self.current_node = node

# ==================== Topology-Aware Router ====================

class TopologyRouter:
    """Router that uses the logic module's topology for routing"""
    
    def __init__(self, network_module):
        self.network = network_module
        self.route_cache: Dict[Tuple[str, str], List[str]] = {}
        self.cache_ttl = 5.0
        self.last_cache_clean = 0.0
        
        # Current topology state
        self.current_graph = None
        self.current_positions = None
        self.current_gs_positions = None
        self.last_topology_update = 0.0
    
    def update_topology(self, current_time: float) -> bool:
        """Update topology information"""
        if not hasattr(self.network, 'topology') or not self.network.topology:
            return False
        
        try:
            # Update every 0.1 seconds or when needed
            if current_time - self.last_topology_update < 0.1 and self.current_graph is not None:
                return True
            
            sim_time = self.network.sim.get_sim_time(current_time)
            
            # Get positions and links
            pos, links, new_links, removed_links = self.network.topology.compute(sim_time)
            
            # Get ground station positions
            gs_positions = {}
            for name, lat, lon, alt in GROUND_STATIONS:
                try:
                    gs_positions[name] = self.network.topology.get_gs_position(name, sim_time)
                except:
                    # Fallback to approximate position
                    gs_positions[name] = np.array([0, 0, 0])
            
            # Build unified graph
            self.current_graph = self.network.topology.construct_unified_graph(pos, gs_positions)
            self.current_positions = pos
            self.current_gs_positions = gs_positions
            self.last_topology_update = current_time
            
            return True
            
        except Exception as e:
            if self.network.debug:
                print(f"[ROUTER] Update error: {e}")
            return False
    
    def find_path_with_fallback(self, src: str, dst: str, current_time: float) -> List[str]:
        """Find path with aggressive fallback strategies"""
        # Check cache first
        cache_key = (src, dst)
        if cache_key in self.route_cache:
            cached_time, path = self.route_cache[cache_key]
            if current_time - cached_time < self.cache_ttl and len(path) <= 10:
                return path.copy()
        
        # Update topology if needed
        self.update_topology(current_time)
        
        if self.current_graph is None:
            return self._simple_fallback_path(src, dst)
        
        try:
            # Try normal path first
            path = self.find_path(src, dst, current_time)
            
            if path:
                return path
            
            # If no path found, try alternative strategies
            print(f"[ROUTER] No direct path from {src} to {dst}, trying fallback...")
            
            # Strategy 1: Try via a common intermediate ground station
            intermediate_stations = ["GS_USA", "GS_AUSTRALIA", "GS_BRAZIL", "GS_INDIA"]
            
            for intermediate in intermediate_stations:
                if intermediate != src and intermediate != dst:
                    path1 = self.find_path(src, intermediate, current_time)
                    path2 = self.find_path(intermediate, dst, current_time)
                    
                    if path1 and path2:
                        # Combine paths (remove duplicate intermediate)
                        combined_path = path1[:-1] + path2
                        print(f"[ROUTER] Found 2-hop path via {intermediate}")
                        cache_key = (src, dst)
                        self.route_cache[cache_key] = (current_time, combined_path.copy())
                        return combined_path
            
            # Strategy 2: Find ANY satellite path
            try:
                src_idx = self.current_graph.vs.find(name=src).index
                dst_idx = self.current_graph.vs.find(name=dst).index
                
                # Get ALL simple paths (not just shortest) with cutoff
                try:
                    paths = self.current_graph.get_all_simple_paths(src_idx, to=dst_idx, cutoff=8)
                    if paths:
                        # Pick the shortest path
                        shortest = min(paths, key=len)
                        path_names = [self.current_graph.vs[i]["name"] for i in shortest]
                        print(f"[ROUTER] Found alternative path ({len(path_names)-1} hops)")
                        cache_key = (src, dst)
                        self.route_cache[cache_key] = (current_time, path_names.copy())
                        return path_names
                except:
                    pass
            except:
                pass
            
            # Strategy 3: Emergency 3-hop path
            emergency_path = self._emergency_three_hop_path(src, dst, current_time)
            if emergency_path:
                cache_key = (src, dst)
                self.route_cache[cache_key] = (current_time, emergency_path.copy())
                return emergency_path
            
            return []
                
        except Exception as e:
            return self._simple_fallback_path(src, dst)
    
    def find_path(self, src: str, dst: str, current_time: float) -> List[str]:
        """Find path from src to dst - FIXED TO FIND SHORTER PATHS"""
        # Check cache first
        cache_key = (src, dst)
        if cache_key in self.route_cache:
            cached_time, path = self.route_cache[cache_key]
            if current_time - cached_time < self.cache_ttl and len(path) <= 10:
                return path.copy()
        
        # Update topology if needed
        self.update_topology(current_time)
        
        if self.current_graph is None:
            return self._simple_fallback_path(src, dst)
        
        try:
            # IMPORTANT: Get SHORTEST path with hop limit
            start_idx = self.current_graph.vs.find(name=src).index
            end_idx = self.current_graph.vs.find(name=dst).index
            
            # Get shortest path with max 10 hops
            try:
                # First try to find a direct path through at most 2 satellites
                path_indices = None
                
                # Try BFS with depth limit
                paths = self.current_graph.get_all_shortest_paths(start_idx, to=end_idx)
                if paths:
                    # Find shortest path
                    shortest_path = min(paths, key=len)
                    if len(shortest_path) <= 7:  # Accept up to 7 hops total
                        path_indices = shortest_path
                
                if path_indices:
                    path_names = [self.current_graph.vs[i]["name"] for i in path_indices]
                    if len(path_names) > 10:  # Path too long
                        # Try to find a better path via a single satellite
                        return self._find_path_via_single_satellite(src, dst, current_time)
                    
                    self.route_cache[cache_key] = (current_time, path_names.copy())
                    return path_names
                else:
                    # Fallback to single satellite path
                    return self._find_path_via_single_satellite(src, dst, current_time)
                    
            except Exception as e:
                return self._find_path_via_single_satellite(src, dst, current_time)
                
        except Exception as e:
            return self._simple_fallback_path(src, dst)

    def _find_path_via_single_satellite(self, src: str, dst: str, current_time: float) -> List[str]:
        """Find path via a single satellite (shortest possible)"""
        if not self.current_graph:
            return []
        
        try:
            # Get all satellites
            sat_vertices = [v for v in self.current_graph.vs if v["type"] == "satellite"]
            
            # Try each satellite as intermediate
            for sat_v in sat_vertices:
                sat_name = sat_v["name"]
                
                # Check if src can connect to satellite AND satellite to dst
                if self._check_direct_connection(src, sat_name) and self._check_direct_connection(sat_name, dst):
                    path = [src, sat_name, dst]
                    cache_key = (src, dst)
                    self.route_cache[cache_key] = (current_time, path.copy())
                    return path
            
            # Try 2 satellites if needed
            for sat1 in sat_vertices:
                for sat2 in sat_vertices:
                    if sat1 == sat2:
                        continue
                        
                    sat1_name = sat1["name"]
                    sat2_name = sat2["name"]
                    
                    if (self._check_direct_connection(src, sat1_name) and 
                        self._check_direct_connection(sat1_name, sat2_name) and 
                        self._check_direct_connection(sat2_name, dst)):
                        path = [src, sat1_name, sat2_name, dst]
                        if len(path) <= 6:  # Accept up to 6 hops
                            cache_key = (src, dst)
                            self.route_cache[cache_key] = (current_time, path.copy())
                            return path
            
            return []
            
        except:
            return []
    
    def _emergency_three_hop_path(self, src: str, dst: str, current_time: float) -> List[str]:
        """Emergency 3-hop path: GS → SAT1 → SAT2 → GS"""
        if not self.current_graph:
            return []
        
        try:
            # Get all satellites
            sat_vertices = [v for v in self.current_graph.vs if v["type"] == "satellite"]
            
            # Try every combination of 2 satellites
            for sat1 in sat_vertices:
                for sat2 in sat_vertices:
                    if sat1 == sat2:
                        continue
                        
                    sat1_name = sat1["name"]
                    sat2_name = sat2["name"]
                    
                    # Check: src → sat1, sat1 → sat2, sat2 → dst
                    if (self._check_direct_connection(src, sat1_name) and 
                        self._check_direct_connection(sat1_name, sat2_name) and 
                        self._check_direct_connection(sat2_name, dst)):
                        
                        path = [src, sat1_name, sat2_name, dst]
                        print(f"[ROUTER] Emergency 3-hop path: {src} → {sat1_name} → {sat2_name} → {dst}")
                        return path
            
            return []
        except:
            return []
    
    def _check_direct_connection(self, node1: str, node2: str) -> bool:
        """Check if two nodes are directly connected in current graph"""
        if not self.current_graph:
            return False
        
        try:
            v1 = self.current_graph.vs.find(name=node1)
            v2 = self.current_graph.vs.find(name=node2)
            
            # Check if edge exists
            return self.current_graph.are_connected(v1, v2)
        except:
            return False
    
    def _simple_fallback_path(self, src: str, dst: str) -> List[str]:
        """Simple fallback path - improved with better searching"""
        if not self.current_graph:
            return []
        
        try:
            # Try direct connection
            if self._check_direct_connection(src, dst):
                return [src, dst]
            
            # For ground stations, try to find ANY path through satellites
            if src.startswith("GS_") and dst.startswith("GS_"):
                # Get all satellites
                sat_vertices = [v for v in self.current_graph.vs if v["type"] == "satellite"]
                
                # Try each satellite
                for sat_v in sat_vertices:
                    sat_name = sat_v["name"]
                    if self._check_direct_connection(src, sat_name):
                        # This satellite connects to source, try to find path to destination
                        try:
                            sat_idx = self.current_graph.vs.find(name=sat_name).index
                            dst_idx = self.current_graph.vs.find(name=dst).index
                            
                            # Try to find path from satellite to destination
                            path = self.current_graph.get_shortest_paths(
                                sat_idx, to=dst_idx, output="vpath"
                            )[0]
                            if path:
                                full_path = [src] + [self.current_graph.vs[i]["name"] for i in path]
                                return full_path
                        except:
                            continue
            
            return []
        except:
            return []
    
    def get_next_hop(self, src: str, dst: str, current_time: float) -> Optional[str]:
        """Get next hop from src to dst - WITH SELF-TO-SELF BLOCK"""
        
        # BLOCK self-to-self routing requests
        if src == dst:
            if self.network.debug:
                print(f"[ROUTER BLOCKED] Self-to-self routing request: {src} → {dst}")
            return None
        
        path = self.find_path_with_fallback(src, dst, current_time)
        if len(path) > 1:
            return path[1]
        
        # If still no path, check if this is a critical flow
        if src.startswith("GS_") and dst.startswith("GS_"):
            print(f"[ROUTER CRITICAL] No route found from {src} to {dst}")
            
            # Try one more emergency attempt
            if not self._check_direct_connection(src, dst):
                # Look for ANY satellite that can reach both
                if self.current_graph:
                    sat_vertices = [v for v in self.current_graph.vs if v["type"] == "satellite"]
                    for sat in sat_vertices:
                        sat_name = sat["name"]
                        if self._check_direct_connection(src, sat_name):
                            print(f"[ROUTER] {src} can reach satellite {sat_name}")
        
        return None
# ==================== TCP Protocol Stack ====================

class TCPProtocolStack:
    """Full TCP protocol implementation"""
    
    def __init__(self, node_name: str):
        self.node_name = node_name
        self.connections: Dict[Tuple[str, int, str, int], Connection] = {}
    
    def establish_connection(self, src: str, dst: str, 
                        src_port: int, dst_port: int) -> Connection:
        """Create and return a new TCP connection - ULTRA STRICT VALIDATION"""
        
        # CRITICAL: Validate source and destination BEFORE creating connection
        if src == dst:
            print(f"[TCP CRITICAL BLOCKED] Self-to-self connection attempt: {src} → {dst}")
            print(f"  Source port: {src_port}, Dest port: {dst_port}")
            
            # Create a DUMMY connection that will NEVER send anything
            dummy_conn = Connection(
                src=src,
                dst=dst,
                src_port=src_port,
                dst_port=dst_port,
                state=ConnectionState.CLOSED,
                seq_num=0
            )
            dummy_conn.state = ConnectionState.CLOSED
            
            # Add to connections to prevent retries
            key = (src, src_port, dst, dst_port)
            self.connections[key] = dummy_conn
            
            return dummy_conn
        
        key = (src, src_port, dst, dst_port)
        
        if key not in self.connections:
            conn = Connection(
                src=src,
                dst=dst,
                src_port=src_port,
                dst_port=dst_port,
                state=ConnectionState.CLOSED,
                seq_num=random.randint(1000, 2**31 - 1)
            )
            self.connections[key] = conn
            print(f"[TCP] Created connection: {src}:{src_port} → {dst}:{dst_port}")
        
        return self.connections[key]
    
    def send_syn(self, conn: Connection, current_time: float) -> Optional[ProtocolPacket]:
        """Send SYN packet to initiate connection - WITH DEBUG"""
        
        # CRITICAL DEBUG: Log connection details
        print(f"[TCP DEBUG] Creating SYN packet:")
        print(f"  SRC: {conn.src}, DST: {conn.dst}")
        print(f"  SRC_PORT: {conn.src_port}, DST_PORT: {conn.dst_port}")
        print(f"  SRC == DST: {conn.src == conn.dst}")
        
        if conn.src == conn.dst:
            print(f"[TCP CRITICAL] Self-to-self connection detected in send_syn!")
            print(f"  Connection: {conn.src}:{conn.src_port} → {conn.dst}:{conn.dst_port}")
            # Don't create the packet at all
            return None
        
        conn.state = ConnectionState.SYN_SENT
        conn.syn_sent_time = current_time
        
        syn_packet = ProtocolPacket(
            src=conn.src,
            dst=conn.dst,
            src_port=conn.src_port,
            dst_port=conn.dst_port,
            seq_num=conn.seq_num,
            flags={'SYN': True},
            protocol_type=ProtocolType.TCP,
            creation_time=current_time
        )
        
        conn.seq_num += 1  # SYN consumes one sequence number
        
        # Add to retransmission queue
        conn.retransmit_queue.append({
            'packet': syn_packet,
            'send_time': current_time,
            'retransmit_count': 0
        })
        
        conn.packets_sent += 1
        
        print(f"[TCP] Created SYN packet: {conn.src}:{conn.src_port} → {conn.dst}:{conn.dst_port}")
        return syn_packet
    
    def send_data(self, conn: Connection, data: bytes, 
                current_time: float) -> List[ProtocolPacket]:
        """Send data over established connection - FIXED PACKET CREATION"""
        
        if conn.src == conn.dst:
            print(f"[TCP CRITICAL] Self-to-self connection in send_data!")
            return []
        
        if conn.state != ConnectionState.ESTABLISHED:
            return []
        
        packets = []
        mss = 1460
        
        for i in range(0, len(data), mss):
            segment = data[i:i + mss]
            
            if len(conn.retransmit_queue) >= conn.cwnd:
                break
            
            seq_num = conn.seq_num
            conn.seq_num += len(segment)
            
            # Create packet with explicit destination
            packet = ProtocolPacket(
                src=conn.src,
                dst=conn.dst,  # EXPLICIT destination
                src_port=conn.src_port,
                dst_port=conn.dst_port,
                seq_num=seq_num,
                ack_num=conn.ack_num,
                flags={'ACK': True, 'PSH': True},
                data=segment,
                protocol_type=ProtocolType.TCP,
                creation_time=current_time
            )
            
            # IMMEDIATE VALIDATION
            if packet.src == packet.dst:
                print(f"[TCP CRITICAL] Created self-to-self data packet!")
                print(f"  Connection: {conn.src}:{conn.src_port} → {conn.dst}:{conn.dst_port}")
                print(f"  Packet: {packet.src} → {packet.dst}")
                continue
            
            packets.append(packet)
            
            conn.bytes_sent += len(segment)
            conn.packets_sent += 1
            conn.last_send_time = current_time
            
            # Add to retransmission queue
            conn.retransmit_queue.append({
                'packet': packet,
                'send_time': current_time,
                'retransmit_count': 0
            })
        
        return packets
    
    def receive_packet(self, packet: ProtocolPacket, 
                      current_time: float) -> Optional[ProtocolPacket]:
        """Process received TCP packet"""
        # Handle SYN packet (connection initiation)
        if packet.flags.get('SYN', False) and not packet.flags.get('ACK', False):
            return self._handle_syn(packet, current_time)
        
        # Handle SYN-ACK packet
        if packet.flags.get('SYN', False) and packet.flags.get('ACK', False):
            return self._handle_syn_ack(packet, current_time)
        
        # Handle ACK packet
        if packet.flags.get('ACK', False) and not packet.flags.get('SYN', False):
            return self._handle_ack(packet, current_time)
        
        # Handle data packet
        if packet.data:
            return self._handle_data(packet, current_time)
        
        return None
    
    def _handle_syn(self, packet: ProtocolPacket, current_time: float) -> ProtocolPacket:
        """Handle incoming SYN (server side)"""
        # Create connection for this request
        conn = self.establish_connection(
            packet.dst,  # We are the server
            packet.src,  # Client is the source
            packet.dst_port,
            packet.src_port
        )
        
        conn.state = ConnectionState.SYN_RECEIVED
        conn.ack_num = packet.seq_num + 1
        conn.seq_num = random.randint(1000, 2**31 - 1)
        
        # Create SYN-ACK response
        syn_ack = ProtocolPacket(
            src=conn.src,
            dst=conn.dst,
            src_port=conn.src_port,
            dst_port=conn.dst_port,
            seq_num=conn.seq_num,
            ack_num=conn.ack_num,
            flags={'SYN': True, 'ACK': True},
            protocol_type=ProtocolType.TCP,
            creation_time=current_time
        )
        
        conn.seq_num += 1  # SYN consumes sequence number
        
        # Log connection attempt
        print(f"[TCP] {self.node_name}: Received SYN from {packet.src}:{packet.src_port}, sending SYN-ACK")
        
        return syn_ack
    
    def _handle_syn_ack(self, packet: ProtocolPacket, current_time: float) -> ProtocolPacket:
        """Handle SYN-ACK (client side)"""
        key = (packet.dst, packet.dst_port, packet.src, packet.src_port)
        
        if key in self.connections:
            conn = self.connections[key]
            
            if conn.state == ConnectionState.SYN_SENT:
                # Update connection state
                conn.state = ConnectionState.ESTABLISHED
                conn.ack_num = packet.seq_num + 1
                
                # Remove SYN from retransmission queue
                for i, entry in enumerate(conn.retransmit_queue):
                    if entry['packet'].flags.get('SYN', False):
                        # Calculate RTT for SYN-ACK
                        rtt = current_time - entry['send_time']
                        conn.update_rtt(rtt)
                        conn.retransmit_queue.pop(i)
                        break
                
                # Send ACK to complete handshake
                ack_packet = ProtocolPacket(
                    src=conn.src,
                    dst=conn.dst,
                    src_port=conn.src_port,
                    dst_port=conn.dst_port,
                    seq_num=conn.seq_num,
                    ack_num=conn.ack_num,
                    flags={'ACK': True},
                    protocol_type=ProtocolType.TCP,
                    creation_time=current_time
                )
                
                print(f"[TCP] {self.node_name}: Connection established with {conn.dst}:{conn.dst_port}")
                
                return ack_packet
        
        return None
    
    def _handle_ack(self, packet: ProtocolPacket, current_time: float) -> Optional[ProtocolPacket]:
        """Handle ACK packet"""
        # Look for connection in both directions
        key1 = (packet.dst, packet.dst_port, packet.src, packet.src_port)
        key2 = (packet.src, packet.src_port, packet.dst, packet.dst_port)
        
        conn = None
        if key1 in self.connections:
            conn = self.connections[key1]
        elif key2 in self.connections:
            conn = self.connections[key2]
        
        if conn:
            # Update ACK number
            if packet.ack_num > conn.seq_num:
                conn.seq_num = packet.ack_num
            
            # Check retransmission queue for ACKed packets
            acked_indices = []
            for i, entry in enumerate(conn.retransmit_queue):
                entry_packet = entry['packet']
                # Packet is ACKed if ack_num > packet's seq_num + data_length
                expected_ack = entry_packet.seq_num + entry_packet.data_length
                if packet.ack_num >= expected_ack:
                    # Calculate RTT
                    if entry['retransmit_count'] == 0:  # Only for first transmission
                        rtt = current_time - entry['send_time']
                        conn.update_rtt(rtt)
                    acked_indices.append(i)
            
            # Remove ACKed packets
            for i in sorted(acked_indices, reverse=True):
                conn.retransmit_queue.pop(i)
            
            # Update congestion window
            if acked_indices:
                conn.on_ack_received(len(packet.data) if hasattr(packet, 'data') else 0)
        
        return None
    
    def _handle_data(self, packet: ProtocolPacket, current_time: float) -> ProtocolPacket:
        """Handle data packet"""
        key = (packet.dst, packet.dst_port, packet.src, packet.src_port)
        
        if key in self.connections:
            conn = self.connections[key]
            
            if conn.state == ConnectionState.ESTABLISHED:
                # Update received bytes
                conn.bytes_received += len(packet.data)
                conn.packets_received += 1
                conn.ack_num = packet.seq_num + len(packet.data)
                
                # Send ACK for received data
                ack_packet = ProtocolPacket(
                    src=conn.src,
                    dst=conn.dst,
                    src_port=conn.src_port,
                    dst_port=conn.dst_port,
                    seq_num=conn.seq_num,
                    ack_num=conn.ack_num,
                    flags={'ACK': True},
                    protocol_type=ProtocolType.TCP,
                    creation_time=current_time
                )
                
                return ack_packet
        
        return None
    
    def check_timeouts(self, current_time: float) -> List[ProtocolPacket]:
        """Check for timed-out packets - COMPLETELY REWRITTEN"""
        retransmit_packets = []
        
        for conn in self.connections.values():
            # Skip self-to-self connections
            if conn.src == conn.dst:
                continue
            
            # Check for SYN timeout
            if conn.state == ConnectionState.SYN_SENT:
                if current_time - conn.syn_sent_time > conn.rto:
                    print(f"[TCP] {self.node_name}: SYN timeout to {conn.dst}, retransmitting")
                    syn_packet = self.send_syn(conn, current_time)
                    if syn_packet:
                        retransmit_packets.append(syn_packet)
                continue
            
            # Check data packet timeouts - CRITICAL FIX HERE
            to_remove = []
            for i, entry in enumerate(conn.retransmit_queue):
                original_packet = entry['packet']
                
                # CRITICAL: Create a NEW packet for retransmission, don't reuse the old one
                if current_time - entry['send_time'] > conn.rto:
                    # Create a fresh copy of the packet
                    new_packet = ProtocolPacket(
                        src=conn.src,  # Use connection source
                        dst=conn.dst,  # Use connection destination
                        src_port=conn.src_port,
                        dst_port=conn.dst_port,
                        seq_num=original_packet.seq_num,
                        ack_num=original_packet.ack_num,
                        flags=original_packet.flags.copy(),
                        data=original_packet.data,
                        protocol_type=original_packet.protocol_type,
                        ttl=original_packet.ttl,
                        window_size=original_packet.window_size,
                        priority=original_packet.priority,
                        creation_time=current_time,  # NEW creation time
                        delivered=False,
                        delivery_time=None,
                        hops=original_packet.hops,
                        path=original_packet.path.copy() if original_packet.path else [],
                        current_node=conn.src  # Reset to source
                    )
                    
                    # CRITICAL: Validate the new packet
                    if new_packet.src == new_packet.dst:
                        print(f"[TCP ERROR] Created self-to-self retransmit packet!")
                        print(f"  Connection: {conn.src} → {conn.dst}")
                        print(f"  Packet: {new_packet.src} → {new_packet.dst}")
                        continue
                    
                    entry['send_time'] = current_time
                    entry['retransmit_count'] += 1
                    conn.packets_retransmitted += 1
                    
                    if entry['retransmit_count'] == 1:
                        conn.on_timeout()
                        print(f"[TCP] {self.node_name}: Timeout for packet to {conn.dst}")
                    
                    retransmit_packets.append(new_packet)
                    
                    # Drop after too many retransmissions
                    if entry['retransmit_count'] > 3:
                        to_remove.append(i)
            
            # Remove bad packets
            for i in sorted(to_remove, reverse=True):
                conn.retransmit_queue.pop(i)
        
        return retransmit_packets
    
    def reset_stuck_connections(self, current_time: float):
        """Reset connections stuck in SYN_SENT for too long"""
        for conn in self.connections.values():
            if (conn.state == ConnectionState.SYN_SENT and 
                current_time - conn.syn_sent_time > 15.0):
                
                print(f"[TCP] {self.node_name}: Resetting stuck connection to {conn.dst}")
                print(f"  Stuck for: {current_time - conn.syn_sent_time:.1f}s")
                
                # Reset connection
                conn.state = ConnectionState.CLOSED
                conn.retransmit_queue.clear()

# ==================== Network Queue ====================

class NetworkQueue:
    """Queue for packet buffering"""
    
    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self.queue = deque(maxlen=max_size)
        self.stats = {
            'enqueued': 0,
            'dequeued': 0,
            'dropped': 0
        }
    
    def enqueue(self, packet: ProtocolPacket) -> bool:
        if len(self.queue) >= self.max_size:
            self.stats['dropped'] += 1
            return False
        
        self.queue.append(packet)
        self.stats['enqueued'] += 1
        return True
    
    def dequeue(self) -> Optional[ProtocolPacket]:
        if self.queue:
            self.stats['dequeued'] += 1
            return self.queue.popleft()
        return None
    
    def size(self):
        return len(self.queue)
    
    def is_empty(self):
        return len(self.queue) == 0

# ==================== Channel Model ====================

class ChannelModel:
    """Models link characteristics for satellite networks"""
    
    def __init__(self, bandwidth_bps: float = 100e6):  # 100 Mbps for satellite links
        self.bandwidth_bps = bandwidth_bps
        self.propagation_delay_per_km = 1 / 299792.458  # speed of light
    
    def calculate_delay(self, packet: ProtocolPacket, distance_km: float) -> float:
        """Calculate total delay for packet transmission"""
        # Transmission delay (packet size / bandwidth)
        tx_delay = (packet.data_length * 8) / self.bandwidth_bps
        
        # Propagation delay (distance / speed of light)
        prop_delay = distance_km * self.propagation_delay_per_km
        
        # Processing and queuing delay (more realistic for satellites)
        processing_delay = 0.0005  # 500 microseconds
        
        # Total delay
        total_delay = tx_delay + prop_delay + processing_delay
        
        # Add some jitter for realism
        jitter = random.uniform(-0.0001, 0.0001)
        
        return max(0.001, total_delay + jitter)  # Minimum 1ms delay

# ==================== MAIN NETWORK MODULE ====================

class EnhancedNetworkModule:
    """Complete network module with TCP/IP, routing, and congestion control"""
    
    def __init__(self, sim_manager):
        self.sim = sim_manager
        self.core = sim_manager.core if hasattr(sim_manager, 'core') else None
        self.topology = getattr(sim_manager, 'topology', None)
        
        # Core components
        self.protocol_stacks: Dict[str, TCPProtocolStack] = {}
        self.router = TopologyRouter(self)
        self.queues: Dict[Tuple[str, str], NetworkQueue] = {}
        self.channels: Dict[Tuple[str, str], ChannelModel] = {}
        
        # Statistics
        self.stats = {
            'total_packets_sent': 0,
            'total_packets_received': 0,
            'total_packets_dropped': 0,
            'total_bytes_transferred': 0,
            'avg_latency_ms': 0.0,
            'avg_hops': 0.0,
            'packet_loss_rate': 0.0,
            'throughput_bps': 0.0,
            'tcp_connections': 0,
            'tcp_retransmissions': 0,
            'queue_drops': 0,
            'routing_failures': 0,
        }
        
        # Tracking
        self.packet_tracker: Dict[int, Dict] = {}
        self.start_time = time.time()
        self.debug = True
        
        print("[NETWORK] Enhanced TCP/IP network module initialized")
        
        # Initialize protocol stacks for all nodes
        self._initialize_protocol_stacks()
    
    def _initialize_protocol_stacks(self):
        """Initialize protocol stacks for all nodes"""
        # Ground stations
        for gs_name in [gs[0] for gs in GROUND_STATIONS]:
            self._get_protocol_stack(gs_name)
            print(f"[NETWORK] Initialized protocol stack for {gs_name}")
        
        # Satellites
        if self.topology and hasattr(self.topology, 'nodes'):
            for node in self.topology.nodes:
                sat_name = node.sat.name
                self._get_protocol_stack(sat_name)
    
    def send_tcp_packet(self, src: str, dst: str, 
                        src_port: int, dst_port: int,
                        data: bytes, current_time: float) -> bool:
        """Send TCP data from src to dst - WITH SELF-TO-SELF PREVENTION"""
        
        # CRITICAL FIX: Prevent self-to-self at the TCP layer
        if src == dst:
            if self.debug:
                print(f"[TCP ERROR] Self-to-self prevented: {src} → {dst}")
            return False  # DON'T EVEN TRY TO SEND
        
        try:
            src_stack = self._get_protocol_stack(src)
            
            # Get or create connection
            conn = src_stack.establish_connection(src, dst, src_port, dst_port)
            
            # DEBUG: Log connection state
            if self.debug:
                print(f"[TCP DEBUG] {src}: Connection state: {conn.state.name}, cwnd: {conn.cwnd}")
            
            # If connection is not yet established, initiate handshake
            if conn.state == ConnectionState.CLOSED:
                # Send SYN to initiate connection
                syn_packet = src_stack.send_syn(conn, current_time)
                if syn_packet:  # Only send if packet was created
                    self._send_packet_internal(syn_packet, current_time)
                    # Update statistics
                    self.stats['tcp_connections'] += 1
                    if self.debug:
                        print(f"[TCP] {src}: Sending SYN to {dst}:{dst_port}")
                    return True
                else:
                    return False
            
            # If SYN sent but not yet acknowledged
            elif conn.state == ConnectionState.SYN_SENT:
                # Check if SYN timed out
                if current_time - conn.syn_sent_time > conn.rto:
                    if conn.syn_sent_time > 0:  # Only retry if we've sent before
                        if self.debug:
                            print(f"[TCP] {src}: SYN timeout to {dst}, resending")
                        syn_packet = src_stack.send_syn(conn, current_time)
                        if syn_packet:
                            self._send_packet_internal(syn_packet, current_time)
                return False
            
            # If connection is established, send data
            elif conn.state == ConnectionState.ESTABLISHED:
                # Only send if congestion window allows
                if conn.cwnd > 0:
                    packets = src_stack.send_data(conn, data, current_time)
                    if packets:
                        for packet in packets:
                            self._send_packet_internal(packet, current_time)
                        if self.debug:
                            print(f"[TCP] {src}: Sent {len(packets)} data packets to {dst}")
                        return True
                    else:
                        if self.debug:
                            print(f"[TCP] {src}: No data packets generated (cwnd={conn.cwnd})")
                else:
                    if self.debug:
                        print(f"[TCP] {src}: Congestion window is zero")
                return False
            
            # If waiting for SYN-ACK or in other states
            else:
                if self.debug:
                    print(f"[TCP] {src}: Connection in state {conn.state.name}, not sending data")
                return False
                
        except Exception as e:
            if self.debug:
                print(f"[TCP] Send error: {e}")
            return False
        
    def _validate_packet(self, packet: ProtocolPacket) -> bool:
        """Validate packet before sending - ULTRA STRICT"""
        # 1. Prevent self-to-self AT ALL COSTS
        if packet.src == packet.dst:
            if self.debug:
                print(f"[VALIDATION CRITICAL] Self-to-self BLOCKED: {packet.src} → {packet.dst}")
                print(f"  Creation time: {packet.creation_time}")
                print(f"  Flags: {packet.flags}")
                print(f"  Path so far: {packet.path}")
                
                # Check if this is a SYN packet (connection initiation)
                if packet.flags.get('SYN', False):
                    print(f"  ⚠️  SYN packet trying to go to itself!")
            return False
        
        # 2. Ensure source and destination are valid nodes
        valid_nodes = ["GS_INDIA", "GS_USA", "GS_BRAZIL", "GS_AUSTRALIA", "NP", "SP"]
        
        # Add satellite names if topology exists
        if self.topology and hasattr(self.topology, 'nodes'):
            valid_nodes.extend([n.sat.name for n in self.topology.nodes])
        
        if packet.src not in valid_nodes:
            if self.debug:
                print(f"[VALIDATION FAILED] Invalid source: {packet.src}")
            return False
        
        if packet.dst not in valid_nodes:
            if self.debug:
                print(f"[VALIDATION FAILED] Invalid destination: {packet.dst}")
            return False
        
        # 3. Ensure destination is reachable (not same as current node unless it's the final destination)
        if packet.current_node == packet.dst and packet.current_node != packet.src:
            # This is OK - packet reached destination
            return True
        
        # 4. Validate ports
        if packet.src_port < 0 or packet.src_port > 65535:
            return False
        if packet.dst_port < 0 or packet.dst_port > 65535:
            return False
        
        return True
    
    def _send_packet_internal(self, packet: ProtocolPacket, current_time: float):
        """Internal method to send packet through network - ULTRA STRICT"""
        
        # ULTRA CRITICAL: Block self-to-self IMMEDIATELY
        if packet.src == packet.dst:
            if self.debug:
                print(f"[NETWORK NUKED] 💥 Self-to-self packet ANNIHILATED!")
                print(f"  Source/Dest: {packet.src}")
                print(f"  Creation: {packet.creation_time}")
                print(f"  Current: {current_time}")
                print(f"  Age: {current_time - packet.creation_time:.3f}s")
                print(f"  Flags: {packet.flags}")
                print(f"  Path: {' → '.join(packet.path) if packet.path else 'None'}")
                print(f"  TTL: {packet.ttl}")
                
                # Check what type of packet this is
                if packet.flags.get('SYN', False):
                    print(f"  🚨 This is a SYN packet!")
                elif packet.flags.get('ACK', False):
                    print(f"  🚨 This is an ACK packet!")
                elif packet.data:
                    print(f"  🚨 This is a DATA packet ({len(packet.data)} bytes)")
            
            # Don't even count it - just vaporize it
            return
        
        # Standard validation
        if not self._validate_packet(packet):
            self.stats['total_packets_dropped'] += 1
            self.stats['routing_failures'] += 1
            return
        
        self.stats['total_packets_sent'] += 1
        
        if self.debug and packet.flags.get('SYN', False):
            print(f"[SEND] {packet.src}:{packet.src_port} → {packet.dst}:{packet.dst_port} SYN")
        
        # Get next hop
        next_hop = self.router.get_next_hop(packet.current_node, packet.dst, current_time)
        
        if not next_hop:
            self.stats['total_packets_dropped'] += 1
            self.stats['routing_failures'] += 1
            
            # Log specifically for self-to-self routing attempts
            if packet.src == packet.dst:
                if self.debug:
                    print(f"[ROUTER IMPOSSIBLE] Attempted to route self-to-self: {packet.src}")
            else:
                if self.debug:
                    print(f"[NETWORK] No route from {packet.current_node} to {packet.dst}")
            return
        
        # Get queue for this link
        queue_key = (packet.current_node, next_hop)
        queue = self._get_queue(queue_key)
        
        # Enqueue packet
        if not queue.enqueue(packet):
            self.stats['total_packets_dropped'] += 1
            self.stats['queue_drops'] += 1
            if self.debug:
                print(f"[NETWORK] Queue full {packet.current_node}→{next_hop}")
            return
        
        # Process queue immediately
        self._process_queue(queue_key, current_time)
    
    def _process_queue(self, queue_key: Tuple[str, str], current_time: float):
        """Process packets in a queue"""
        queue = self.queues.get(queue_key)
        if not queue or queue.is_empty():
            return
        
        # Get next packet
        packet = queue.dequeue()
        if not packet:
            return
        
        src, dst = queue_key
        
        # Calculate transmission delay
        channel = self._get_channel(src, dst)
        distance_km = 1500  # Approximate LEO distance
        delay = channel.calculate_delay(packet, distance_km)
        
        # Schedule arrival
        arrival_time = current_time + delay
        
        if self.core and hasattr(self.core, 'schedule'):
            self.core.schedule(
                arrival_time,
                1,
                self._handle_packet_arrival,
                packet,
                src,
                dst,
                current_time
            )
        else:
            # Direct call if no scheduler
            self._handle_packet_arrival(arrival_time, packet, src, dst, current_time)
    
    def _handle_packet_arrival(self, arrival_time: float, packet: ProtocolPacket,
                              from_node: str, at_node: str, send_time: float):
        """Handle packet arrival at a node"""
        packet.move_to_node(at_node)
        
        # Check TTL
        if not packet.decrement_ttl():
            self.stats['total_packets_dropped'] += 1
            if self.debug:
                print(f"[NETWORK] TTL expired for packet from {packet.src}")
            return
        
        # Check if destination reached
        if at_node == packet.dst:
            self._deliver_packet(packet, arrival_time, send_time)
        else:
            # Continue forwarding
            self._send_packet_internal(packet, arrival_time)
    
    def _deliver_packet(self, packet: ProtocolPacket, delivery_time: float, 
                    send_time: float):
        """Deliver packet to final destination"""
        packet.delivered = True
        packet.delivery_time = delivery_time
        
        # Calculate latency
        latency = delivery_time - send_time
        
        # Update statistics
        self.stats['total_packets_received'] += 1
        self.stats['total_bytes_transferred'] += packet.data_length
        
        # Update averages
        if self.stats['avg_latency_ms'] == 0:
            self.stats['avg_latency_ms'] = latency * 1000
        else:
            self.stats['avg_latency_ms'] = 0.9 * self.stats['avg_latency_ms'] + 0.1 * (latency * 1000)
        
        if self.stats['avg_hops'] == 0:
            self.stats['avg_hops'] = packet.hops
        else:
            self.stats['avg_hops'] = 0.9 * self.stats['avg_hops'] + 0.1 * packet.hops
        
        # Handle TCP packets
        if packet.protocol_type == ProtocolType.TCP:
            self._handle_tcp_delivery(packet, delivery_time)
        
        # Record delivery
        self.packet_tracker[id(packet)] = {
            'src': packet.src,
            'dst': packet.dst,
            'latency_ms': latency * 1000,
            'hops': packet.hops,
            'path': packet.path.copy()
        }
        
        if self.debug and packet.data and len(packet.data) > 0:
            print(f"[DELIVERED] {packet.src} → {packet.dst} "
                  f"({latency*1000:.1f}ms, {packet.hops} hops, {len(packet.data)} bytes)")
    
    def _handle_tcp_delivery(self, packet: ProtocolPacket, delivery_time: float):
        """Handle TCP packet delivery - FIXED TO NOT DOUBLE COUNT"""
        dst_stack = self._get_protocol_stack(packet.dst)
        
        # Process packet through TCP stack
        response = dst_stack.receive_packet(packet, delivery_time)
        
        # IMPORTANT: Only notify for DATA packets (not ACK/SYN)
        # Check if this is a DATA packet (has data AND is not just an ACK)
        if packet.data and len(packet.data) > 0:
            # This is a DATA packet
            if hasattr(self.sim, 'traffic'):
                # Only count if not already counted
                if not getattr(packet, '_delivery_notified', False):
                    packet._delivery_notified = True
                    self.sim.traffic.on_packet_delivered(packet, delivery_time)
        
        # If this is an ACK packet, notify traffic module BUT DON'T COUNT AS DELIVERY
        elif packet.flags.get('ACK', False):
            if hasattr(self.sim, 'traffic'):
                self.sim.traffic.on_ack_received(packet, delivery_time)
        
        # Send any response generated by TCP stack
        if response:
            self._send_packet_internal(response, delivery_time)
    
    def process_events(self, current_time: float):
        """Process network events (retransmissions, etc.)"""
        # Reset stuck connections every 10 seconds
        if current_time % 10.0 < 0.1:  # Every ~10 seconds
            for stack in self.protocol_stacks.values():
                stack.reset_stuck_connections(current_time)
        
        # Check TCP timeouts
        for stack in self.protocol_stacks.values():
            retransmit_packets = stack.check_timeouts(current_time)
            for packet in retransmit_packets:
                if packet:  # Only send if packet is valid
                    self._send_packet_internal(packet, current_time)
                    self.stats['tcp_retransmissions'] += 1
        
        # Process all queues
        for queue_key in list(self.queues.keys()):
            self._process_queue(queue_key, current_time)
    
    def get_statistics(self) -> Dict:
        """Get current network statistics"""
        sent = self.stats['total_packets_sent']
        if sent > 0:
            self.stats['packet_loss_rate'] = self.stats['total_packets_dropped'] / sent
        
        elapsed = time.time() - self.start_time
        if elapsed > 0:
            self.stats['throughput_bps'] = self.stats['total_bytes_transferred'] * 8 / elapsed
        
        return self.stats.copy()
    
    def _get_protocol_stack(self, node_name: str) -> TCPProtocolStack:
        if node_name not in self.protocol_stacks:
            self.protocol_stacks[node_name] = TCPProtocolStack(node_name)
        return self.protocol_stacks[node_name]
    
    def _get_queue(self, key: Tuple[str, str]) -> NetworkQueue:
        if key not in self.queues:
            self.queues[key] = NetworkQueue(max_size=100)
        return self.queues[key]
    
    def _get_channel(self, src: str, dst: str) -> ChannelModel:
        key = (src, dst)
        if key not in self.channels:
            self.channels[key] = ChannelModel()
        return self.channels[key]