import math
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum, auto
import heapq

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

    flow_type = FlowType.BULK
    flow_state = FlowState.WAITING

    bytes_generated: int = 0
    bytes_delivered: int = 0
    packets_generated: int = 0

    def is_active(self, t: float) -> bool:
        return self.start_time <= t < self.end_time
    
    def is_finished(self, t: float) -> bool:
        return t >= self.end_time

# Packet definition

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
        # Automatically calculate bits for transmission delay math
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

    def on_packet_generation(self, time: float, interval: float):
        for flow_id in list(self.active_flows):
            flow = self.flows.get(flow_id)
            if flow is None or flow.is_finished(time):
                continue

            # Calculate how many packets to burst this interval
            bits_to_send = flow.data_rate_bps * interval
            num_packets = max(1, int(bits_to_send // (flow.packet_size_bytes * 8)))

            for _ in range(num_packets):
                # Determine priority based on flow type
                priority_map = {FlowType.CONTROL: 1, FlowType.STREAMING: 2, FlowType.BULK: 3}
                
                packet = Packet(
                    packet_id=self._packet_counter,
                    flow_id=flow.flow_id,
                    src=flow.src,
                    dst=flow.dst,
                    size_bytes=flow.packet_size_bytes,
                    creation_time=time,
                    priority=priority_map.get(flow.flow_type, 3),
                    ttl=64 # Default TTL
                )

                self._packet_counter += 1
                flow.packets_generated += 1
                flow.bytes_generated += packet.size_bytes

                # --- THE QUEUE UPDATE ---
                # Find the source node in the topology
                src_node = self.network.topology.get_node_by_id(packet.src)
                
                if src_node and hasattr(src_node, 'queue'):
                    # Attempt to add to the node's dequeue
                    if len(src_node.queue) < src_node.queue.maxlen:
                        src_node.queue.append(packet)
                    else:
                        # Task: Handle Packet Drop (Congestion)
                        self.on_packet_dropped(packet, "Source Buffer Overflow")
                else:
                    # If it's a Ground Station without a queue, inject directly
                    gs_queue = self.network.topology.gs_queues.get(packet.src)
                    if gs_queue is not None:
                        if len(gs_queue) < gs_queue.maxlen:
                            gs_queue.append(packet)
                        else:
                            self.on_packet_dropped(packet, "GS Source Buffer Overflow")

    def on_packet_delivered(self, packet: Packet, time: float):
        """
        Success Signal: The packet reached its destination GS.
        """
        packet.delivered = True
        packet.delivery_time = time
        
        # Calculate Latency: How long did the trip take?
        latency = packet.delivery_time - packet.creation_time

        flow = self.flows.get(packet.flow_id)
        if flow:
            flow.bytes_delivered += packet.size_bytes
            # Optional: You could track average latency per flow here
            
        # Log to the SimulationManager's dataset for RL 'Positive Reward'
        self.network.delivery_logs.append({
    "type": "DELIVERY",
    "packet_id": packet.packet_id,
    "latency": latency,
    "hops": packet.hops,
    "status": "SUCCESS"
})

    def on_packet_dropped(self, packet: Packet, reason: str):
        """
        Failure Signal: Packet was lost due to TTL or Overflow.
        """
        # Log to dataset for RL 'Negative Reward'
        self.network.delivery_logs.append({
        "type": "DROP",
        "packet_id": packet.packet_id,
        "reason": reason,
        "status": "FAILURE"
    })