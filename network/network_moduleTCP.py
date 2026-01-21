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
from topology.logic_module import GROUND_STATIONS, LinkType
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
    
    def __init__(self, topology):
        self.topology = topology

    def get_path(self, src: str, dst: str, sim_time) -> List[str]:
        """
        Stateless path query for a single topology snapshot.
        """
        if src == dst:
            return []

        # Compute topology snapshot
        pos, _, _, _ = self.topology.compute(sim_time)

        gs_positions = {
            name: self.topology.get_gs_position(name, sim_time)
            for name, *_ in GROUND_STATIONS
        }

        graph = self.topology.construct_unified_graph(pos, gs_positions)

        # Try primary routing
        path = self._find_shortest_path(graph, src, dst)
        if path:
            return path

        # Fallbacks (pure)
        return self._fallback_path(graph, src, dst)
    
    def _find_shortest_path(self, graph, src, dst) -> List[str]:
        try:
            s = graph.vs.find(name=src).index
            d = graph.vs.find(name=dst).index
            paths = graph.get_all_shortest_paths(s, to=d)
            if not paths:
                return []
            return [graph.vs[i]["name"] for i in min(paths, key=len)]
        except:
            return []

    def _fallback_path(self, graph, src, dst) -> List[str]:
        # single satellite
        for sat in graph.vs.select(type="satellite"):
            name = sat["name"]
            if graph.are_connected(src, name) and graph.are_connected(name, dst):
                return [src, name, dst]
        return []

    def _find_path_via_single_satellite(self, graph, src: str, dst: str) -> List[str]:
        """Find path via one or two satellites (pure logic)"""

        try:
            sat_vertices = [v for v in graph.vs if v["type"] == "satellite"]

            # One satellite
            for sat in sat_vertices:
                name = sat["name"]
                if graph.are_connected(src, name) and graph.are_connected(name, dst):
                    return [src, name, dst]

            # Two satellites
            for sat1 in sat_vertices:
                for sat2 in sat_vertices:
                 if sat1 == sat2:
                        continue
                n1, n2 = sat1["name"], sat2["name"]
                if (
                    graph.are_connected(src, n1)
                    and graph.are_connected(n1, n2)
                    and graph.are_connected(n2, dst)
                ):
                    return [src, n1, n2, dst]

            return []

        except Exception:
            return []

    
    def _emergency_three_hop_path(self, graph, src: str, dst: str) -> List[str]:
        """Emergency GS → SAT1 → SAT2 → GS path"""

        try:
            sat_vertices = [v for v in graph.vs if v["type"] == "satellite"]

            for sat1 in sat_vertices:
                for sat2 in sat_vertices:
                    if sat1 == sat2:
                        continue
                    n1, n2 = sat1["name"], sat2["name"]

                    if (
                        graph.are_connected(src, n1)
                        and graph.are_connected(n1, n2)
                        and graph.are_connected(n2, dst)
                    ):
                        return [src, n1, n2, dst]

            return []

        except Exception:
            return []

    
    def _check_direct_connection(self, graph, node1: str, node2: str) -> bool:
        try:
            return graph.are_connected(node1, node2)
        except Exception:
            return False

    def _simple_fallback_path(self, graph, src: str, dst: str) -> List[str]:
        """Simple fallback routing without state"""

        try:
            # Direct connection
            if graph.are_connected(src, dst):
                return [src, dst]

            # Ground-station fallback via satellites
            if src.startswith("GS_") and dst.startswith("GS_"):
                sat_vertices = [v for v in graph.vs if v["type"] == "satellite"]

                for sat in sat_vertices:
                    name = sat["name"]
                    if graph.are_connected(src, name):
                        try:
                            s = graph.vs.find(name=name).index
                            d = graph.vs.find(name=dst).index
                            path = graph.get_shortest_paths(s, to=d, output="vpath")[0]
                            if path:
                                return [src] + [graph.vs[i]["name"] for i in path]
                        except Exception:
                            continue

            return []

        except Exception:
            return []
        
    def get_path(self, graph, src: str, dst: str) -> List[str]:
        if src == dst:
            return []

        path = self._find_shortest_path(graph, src, dst)
        if path:
            return path

        path = self._find_path_via_single_satellite(graph, src, dst)
        if path:
            return path

        path = self._emergency_three_hop_path(graph, src, dst)
        if path:
            return path

        return self._simple_fallback_path(graph, src, dst)

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

class NetworkModule:
    """Complete network module with TCP/IP, routing, and congestion control"""
    
    def __init__(self, sim_manager):
        #External References
        self.sim = sim_manager
        self.core = sim_manager.core
        self.topology = sim_manager.topology

        #Routing
        self.router = TopologyRouter(self)

        #TCP stacks per node
        self.protocol_stacks = {}
        
        # Statistics
        self.total_packets_sent = 0
        self.total_packets_received = 0
        self.total_bytes_transferred = 0
        self.tcp_retransmission = 0
    
    def send_tcp_packet(
        self,
        src: str,
        dst: str,
        src_port: int,
        dst_port: int,
        data: bytes,
        current_time: float,
    ) -> bool:
        """
        TrafficModule → TCP → Network entry point
        """
        if src == dst:
            return False

        stack = self._get_protocol_stack(src)

        conn = stack.establish_connection(src, dst, src_port, dst_port)

        # Handshake
        if conn.state == ConnectionState.CLOSED:
            syn = stack.send_syn(conn, current_time)
            if syn:
                self._schedule_forward(syn, current_time)
            return True

        # Data transfer
        if conn.state == ConnectionState.ESTABLISHED:
            packets = stack.send_data(conn, data, current_time)
            for pkt in packets:
                self._schedule_forward(pkt, current_time)
            return bool(packets)

        return False
        
    #Event Scheduling

    def _schedule_forward(self, packet: ProtocolPacket, current_time: float):
        """
        Schedule next-hop arrival via scheduler
        """
        next_hop, delay = self._compute_next_hop(packet, current_time)

        if not next_hop:
            return

        arrival_time = current_time + delay

        self.total_packets_sent += 1
        self.total_bytes_transferred += packet.data_length

        self.core.schedule(
            arrival_time,
            1,
            self._packet_arrival_event,
            packet,
            next_hop,
        )

    def _packet_arrival_event(
        self,
        current_time: float,
        packet: ProtocolPacket,
        node: str,
    ):
        """
        Scheduler callback when packet reaches a node
        """
        packet.move_to_node(node)

        if not packet.decrement_ttl():
            return

        if node == packet.dst:
            self._deliver_packet(packet, current_time)
            return

        self._schedule_forward(packet, current_time)

    #Pure Routing
    def _compute_next_hop(self, packet: ProtocolPacket, current_time: float):
        sim_time = self.sim.get_sim_time(current_time)

        path = self.router.find_path_with_fallback(
            packet.current_node,
            packet.dst,
            current_time,
        )

        if not path or len(path) < 2:
            return None, None

        next_hop = path[1]
        delay = self.topology.get_link_delay(
            path[0],
            next_hop,
            sim_time,
        )

        return next_hop, delay

    #Delivery and TCP Handoff
    def _deliver_packet(self, packet: ProtocolPacket, current_time: float):
        self.total_packets_received += 1

        if packet.protocol_type == ProtocolType.TCP:
            self._handle_tcp_delivery(packet, current_time)

    def _handle_tcp_delivery(self, packet: ProtocolPacket, current_time: float):
        stack = self._get_protocol_stack(packet.dst)

        response = stack.receive_packet(packet, current_time)

        # Notify traffic module ONLY for data packets
        if packet.data and hasattr(self.sim, "traffic"):
            self.sim.traffic.on_packet_delivered(packet, current_time)

        if response:
            self._schedule_forward(response, current_time)

    #Network Maintenance
    def process_events(self, current_time: float):
        """
        Called periodically by SimulationManager
        Handles TCP retransmissions & timeouts
        """
        for stack in self.protocol_stacks.values():
            retransmits = stack.check_timeouts(current_time)
            for pkt in retransmits:
                self.tcp_retransmissions += 1
                self._schedule_forward(pkt, current_time)

    #Helpers
    def _get_protocol_stack(self, node: str) -> TCPProtocolStack:
        if node not in self.protocol_stacks:
            self.protocol_stacks[node] = TCPProtocolStack(node)
        return self.protocol_stacks[node]

    def get_statistics(self):
        return {
            "total_packets_sent": self.total_packets_sent,
            "total_packets_received": self.total_packets_received,
            "throughput_bps": (
                self.total_bytes_transferred * 8
                / max(self.core.current_time, 1e-6)
            ),
            "tcp_retransmissions": self.tcp_retransmissions,
        }

    #Statistics
    def get_statistics(self):
        return {
            "total_packets_sent": self.total_packets_sent,
            "total_packets_received": self.total_packets_received,
            "throughput_bps": (
                self.total_bytes_transferred * 8 / max(self.core.current_time, 1e-6)
            ),
        }