import math
import random
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum, auto
import heapq

# Import protocol emulation classes
from network_moduleTCP import ProtocolPacket, ProtocolType

# Flow definition
class FlowType(Enum):
    BULK = auto()
    STREAMING = auto()
    CONTROL = auto()

class FlowState(Enum):
    WAITING = auto()
    ACTIVE = auto()
    COMPLETED = auto()

@dataclass
class Flow:
    flow_id: int
    src: str
    dst: str
    start_time: float
    end_time: float
    data_rate_bps: float = 5000000
    packet_size_bytes: int = 1500
    flow_type: FlowType = FlowType.BULK
    flow_state: FlowState = FlowState.WAITING
    protocol_type: ProtocolType = ProtocolType.TCP  # ADDED: Protocol type
    
    bytes_generated: int = 0
    bytes_delivered: int = 0
    packets_generated: int = 0
    
    def is_active(self, t: float) -> bool:
        return self.start_time <= t < self.end_time
    
    def is_finished(self, t: float) -> bool:
        return t >= self.end_time

# Packet definition (kept for compatibility)
class PacketType(Enum):
    DATA = auto()
    CONTROL = auto()

@dataclass
class Packet:
    packet_id: int
    flow_id: int
    src: str
    dst: str
    size_bytes: int
    creation_time: float
    
    # New Fields for RL and Network Physics
    size_bits: int = field(init=False)
    priority: int = 1  # 1: High (Control), 2: Medium (Streaming), 3: Low (Bulk)
    ttl: int = 64      # Time-to-Live (standard networking)
    hops: int = 0      # Counter for how many satellites it has hit
    
    packet_type: PacketType = PacketType.DATA
    delivered: bool = False
    delivery_time: Optional[float] = None
    
    def __post_init__(self):
        self.size_bits = self.size_bytes * 8
    
    def decrement_ttl(self) -> bool:
        """Returns False if packet should be dropped (TTL expired)."""
        self.ttl -= 1
        self.hops += 1
        return self.ttl > 0

class TrafficModule:
    def __init__(self, network_module):
        self.network = network_module
        self.flows: Dict[int, Flow] = {}
        self.active_flows: set[int] = set()
        self._packet_counter: int = 0
        self._seq_counter: int = random.randint(0, 10000)  # Random starting sequence
    
    def on_packet_generation(self, time: float, interval: float):
        """Generate packets from active flows using protocol emulation"""
        for flow_id in list(self.active_flows):
            flow = self.flows.get(flow_id)
            if flow is None or flow.is_finished(time):
                continue

            # Calculate how many packets to send this interval
            bits_to_send = flow.data_rate_bps * interval
            num_packets = max(1, int(bits_to_send // (flow.packet_size_bytes * 8)))

            for _ in range(num_packets):
                # Determine priority based on flow type
                priority_map = {FlowType.CONTROL: 1, FlowType.STREAMING: 2, FlowType.BULK: 3}
                priority = priority_map.get(flow.flow_type, 3)
                
                # Create ProtocolPacket with TCP headers
                protocol_packet = ProtocolPacket(
                    src=flow.src,
                    dst=flow.dst,
                    src_port=5000 + flow.flow_id,  # Unique source port per flow
                    dst_port=80,  # HTTP port (example)
                    seq_num=self._seq_counter,
                    ack_num=0,
                    data=bytes(flow.packet_size_bytes),  # Dummy data of correct size
                    priority=priority,
                    protocol_type=flow.protocol_type,
                    creation_time=time
                )
                
                # Update sequence counter
                self._seq_counter += flow.packet_size_bytes
                
                # Use the enhanced network module to send packet
                self.network.send_packet(protocol_packet, time)
                
                # Update counters
                self._packet_counter += 1
                flow.packets_generated += 1
                flow.bytes_generated += flow.packet_size_bytes
    
    def on_packet_delivered(self, packet, time: float):
        """
        Handle packet delivery for both Packet and ProtocolPacket types
        Called by network module when packet reaches destination
        """
        packet.delivered = True
        packet.delivery_time = time
        
        # Calculate latency
        latency = packet.delivery_time - packet.creation_time
        
        # Find the flow
        flow_id = self._get_flow_id_from_packet(packet)
        if flow_id is not None:
            flow = self.flows.get(flow_id)
            if flow:
                # Get packet size
                packet_size = self._get_packet_size(packet)
                flow.bytes_delivered += packet_size
        
        # Log for RL
        if hasattr(self.network, 'delivery_logs'):
            self.network.delivery_logs.append({
                "type": "DELIVERY",
                "packet_id": self._get_packet_id(packet),
                "latency": latency,
                "hops": getattr(packet, 'hops', 0),
                "status": "SUCCESS"
            })
    
    def on_packet_dropped(self, packet, reason: str):
        """Handle packet drop for both packet types"""
        if hasattr(self.network, 'delivery_logs'):
            self.network.delivery_logs.append({
                "type": "DROP",
                "packet_id": self._get_packet_id(packet),
                "reason": reason,
                "status": "FAILURE"
            })
    
    def on_ack_received(self, packet, time: float):
        """Handle ACK packets (for TCP congestion control)"""
        # This would be called by the network module when ACKs are received
        if hasattr(self.network, 'delivery_logs'):
            self.network.delivery_logs.append({
                "type": "ACK",
                "packet_id": getattr(packet, 'seq_num', 0),
                "time": time,
                "status": "ACK_RECEIVED"
            })
    
    # Helper methods for packet type compatibility
    def _get_flow_id_from_packet(self, packet):
        """Extract flow ID from different packet types"""
        if hasattr(packet, 'flow_id'):
            return packet.flow_id
        elif hasattr(packet, 'src_port'):
            # ProtocolPacket: derive flow_id from src_port
            return (packet.src_port - 5000) if packet.src_port >= 5000 else 0
        return None
    
    def _get_packet_size(self, packet):
        """Get packet size from different packet types"""
        if hasattr(packet, 'data'):
            return len(packet.data)
        elif hasattr(packet, 'size_bytes'):
            return packet.size_bytes
        return 1500  # Default
    
    def _get_packet_id(self, packet):
        """Get packet ID from different packet types"""
        if hasattr(packet, 'packet_id'):
            return packet.packet_id
        elif hasattr(packet, 'seq_num'):
            return packet.seq_num
        return 0