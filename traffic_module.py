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

    data_rate_bps: float
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

    packet_type: PacketType.DATA
    
    delivered: bool = False
    delivery_time: Optional[float] = None

# Traffic Module

class TrafficModule:
    
    def __init__(self, network_module):
        self.network = network_module
        self.flows: Dict[int, Flow] = {}
        self.active_flows: set[int] = set()
        self._packet_counter: int = 0

    def on_flow_start(self, time: float, flow_id: int):
        flow = self.flows(flow_id)
        if flow is None:
            return
        
        flow.state = FlowState.ACTIVE
        self.active_flows.discard(flow_id)

    def on_packet_generation(self, time: float, interval: float):
        for flow_id in list(self.active_flows):
            flow = self.flows.get(flow_id)
            if flow is None or flow.is_finished(time):
                continue

            bits_to_send = flow.data_rate_bps * interval
            bytes_to_send = int (bits_to_send / 8)

            num_packets = max(1, bytes_to_send // flow.packet_size_bytes)

            for _ in range(num_packets):
                packet = Packet(
                    packet_id = self._packet_counter,
                    flow_id = flow.flow_id,
                    src = flow.src,
                    dst = flow.dst,
                    size_bytes = flow.packet_size_bytes,
                    creation_time = time
                )

            self._packet_counter += 1
            flow.packets_generated += 1
            flow.bytes_generated += packet.size_bytes

            self.network.inject_packet(packet)

    def on_packet_delivered(self, packet: Packet, time: float):
        packet.delivered = True
        packet.delivery_time = time

        flow = self.flows.get(packet.flow_id)
        if flow:
            flow.bytes_delivered += packet.size_bytes