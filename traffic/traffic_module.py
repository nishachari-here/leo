# traffic_module.py - COMPLETELY FIXED VERSION
import math
import random
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set, Tuple
from enum import Enum, auto
import time

# Import protocol emulation classes - REMOVE
from network.network_moduleTCP import ProtocolPacket, ProtocolType, ConnectionState

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
    data_rate_bps: float = 5000
    packet_size_bytes: int = 1208
    flow_type: FlowType = FlowType.BULK
    flow_state: FlowState = FlowState.WAITING

    # Statistics
    bytes_generated: int = 0
    bytes_delivered: int = 0
    packets_generated: int = 0
    packets_delivered: int = 0

    # Internal timing (pure bookkeeping, not scheduling)
    _last_gen_time: float = 0.0
    _initialized: bool = False

    def is_active(self, t: float) -> bool:
        return self.start_time <= t < self.end_time

    def is_finished(self, t: float) -> bool:
        return t >= self.end_time

class TrafficModule:

    def __init__(self, network):
        self.network = network

        self.flows: Dict[int, Flow] = {}
        self.active_flows: Set[int] = set()

        # Delivery de-duplication (opaque packet IDs)
        self._delivered_packet_ids: Set[int] = set()

        # Statistics
        self.stats = {
            "data_packets_generated": 0,
            "data_packets_delivered": 0,
            "duplicate_deliveries_blocked": 0,
        }

        print("[TRAFFIC] Phase-4 TrafficModule initialized")
    
    #Flow Management
    def create_flow(self, flow: Flow) -> bool:
        if flow.src == flow.dst:
            print(f"[TRAFFIC ERROR] Self-to-self flow blocked: {flow.src}")
            return False

        self.flows[flow.flow_id] = flow
        self.active_flows.add(flow.flow_id)

        print(
            f"[TRAFFIC] Flow {flow.flow_id}: {flow.src} → {flow.dst} "
            f"({flow.data_rate_bps / 1000:.1f} Kbps)"
        )
        return True
    
    #Packet Generation
    def create_flow(self, flow: Flow) -> bool:
        if flow.src == flow.dst:
            print(f"[TRAFFIC ERROR] Self-to-self flow blocked: {flow.src}")
            return False

        self.flows[flow.flow_id] = flow
        self.active_flows.add(flow.flow_id)

        print(
            f"[TRAFFIC] Flow {flow.flow_id}: {flow.src} → {flow.dst} "
            f"({flow.data_rate_bps / 1000:.1f} Kbps)"
        )
        return True
    
    #Network Callbacks
    def on_packet_delivered(self, packet, current_time: float):
        """
        Network callback.
        Packet is treated as OPAQUE.
        """

        packet_uid = id(packet)
        if packet_uid in self._delivered_packet_ids:
            self.stats["duplicate_deliveries_blocked"] += 1
            return

        self._delivered_packet_ids.add(packet_uid)

        flow_id = getattr(packet, "flow_id", None)
        if flow_id is None:
            return

        flow = self.flows.get(flow_id)
        if not flow:
            return

        payload_size = getattr(packet, "payload_size", None)
        if payload_size is None:
            return

        flow.packets_delivered += 1
        flow.bytes_delivered += payload_size
        flow.flow_state = FlowState.ACTIVE

        self.stats["data_packets_delivered"] += 1

    #Statistics
    def get_statistics(self):
        total_generated = sum(f.packets_generated for f in self.flows.values())
        total_delivered = sum(f.packets_delivered for f in self.flows.values())

        delivery_rate = (
            (total_delivered / total_generated) * 100
            if total_generated > 0
            else 0
        )

        return {
            "total_flows": len(self.flows),
            "active_flows": len(self.active_flows),
            "packets_generated": total_generated,
            "packets_delivered": total_delivered,
            "delivery_rate": delivery_rate,
            **self.stats,
        }
    
    def get_flow_status(self, flow_id: int):
        """Get detailed status for a specific flow"""
        flow = self.flows.get(flow_id)
        if not flow:
            return None
        
        # Ensure valid counts
        packets_delivered = min(flow.packets_delivered, flow.packets_generated)
        delivery_rate = (packets_delivered / flow.packets_generated * 100) if flow.packets_generated > 0 else 0
        
        return {
            'flow_id': flow_id,
            'src': flow.src,
            'dst': flow.dst,
            'state': flow.flow_state.name,
            'packets_generated': flow.packets_generated,
            'packets_delivered': packets_delivered,
            'delivery_rate': delivery_rate,
            'syn_attempts': flow._syn_attempts,
            'last_syn_time': flow._last_syn_time
        }
    
    def get_flow_status(self, flow_id: int):
        flow = self.flows.get(flow_id)
        if not flow:
            return None

        packets_generated = flow.packets_generated
        packets_delivered = min(flow.packets_delivered, packets_generated)

        delivery_rate = (
            (packets_delivered / packets_generated) * 100
            if packets_generated > 0 else 0
        )

        return {
            'flow_id': flow.flow_id,
            'src': flow.src,
            'dst': flow.dst,
            'state': flow.flow_state.name,
            'packets_generated': packets_generated,
            'packets_delivered': packets_delivered,
            'delivery_rate': delivery_rate,
            'bytes_generated': flow.bytes_generated,
            'bytes_delivered': flow.bytes_delivered,
        }
    
    def reset(self):
        self._delivered_packet_ids.clear()
        self.stats = {k: 0 for k in self.stats}

        for flow in self.flows.values():
            flow.packets_generated = 0
            flow.packets_delivered = 0
            flow.bytes_generated = 0
            flow.bytes_delivered = 0
            flow.flow_state = FlowState.WAITING
            flow._last_gen_time = 0.0
    
    def cleanup(self):
        """Clean up old delivered packet records"""
        # For now, keep all records
        # In a longer simulation, we might want to periodically clear old records
        pass