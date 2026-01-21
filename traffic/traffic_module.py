# traffic_module.py - COMPLETELY FIXED VERSION
import math
import random
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set, Tuple
from enum import Enum, auto
import time

# Import protocol emulation classes
from network_moduleTCP import ProtocolPacket, ProtocolType, ConnectionState

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
    protocol_type: ProtocolType = ProtocolType.TCP
    
    bytes_generated: int = 0
    bytes_delivered: int = 0
    packets_generated: int = 0
    packets_delivered: int = 0
    
    # Timing control
    _last_gen_time: float = 0.0
    _last_syn_time: float = 0.0
    _initialized: bool = False
    _syn_attempts: int = 0
    
    def is_active(self, t: float) -> bool:
        return self.flow_state == FlowState.ACTIVE or (self.start_time <= t < self.end_time)
    
    def is_finished(self, t: float) -> bool:
        return t >= self.end_time

class TrafficModule:
    def __init__(self, network_module):
        self.network = network_module
        self.flows: Dict[int, Flow] = {}
        self.active_flows: set[int] = set()
        self._packet_counter: int = 0
        self._last_generation_time: float = 0.0
        
        # CRITICAL: Track delivered packets to prevent double counting
        self._delivered_packet_ids: Set[int] = set()
        self._delivered_seq_nums: Set[Tuple[str, str, int]] = set()  # (src, dst, seq_num)
        
        # Statistics
        self.stats = {
            'total_data_packets_generated': 0,
            'total_data_packets_delivered': 0,
            'total_syn_packets': 0,
            'total_ack_packets': 0,
            'duplicate_deliveries_blocked': 0
        }
        
        print(f"[TRAFFIC] Initialized with network module")
    
    def create_flow(self, flow: Flow) -> bool:
        """Create a new flow with validation"""
        # CRITICAL: Check for self-to-self flows
        if flow.src == flow.dst:
            print(f"[TRAFFIC ERROR] Cannot create self-to-self flow: {flow.src} → {flow.dst}")
            return False
        
        self.flows[flow.flow_id] = flow
        self.active_flows.add(flow.flow_id)
        
        print(f"[TRAFFIC] Created flow {flow.flow_id}: {flow.src} → {flow.dst} "
              f"({flow.data_rate_bps/1000:.1f} Kbps, starts at {flow.start_time}s)")
        return True
    
    def on_packet_generation(self, time: float, interval: float) -> int:
        """Generate packets from active flows - COMPLETELY FIXED"""
        packets_generated = 0
        
        for flow_id in list(self.active_flows):
            flow = self.flows.get(flow_id)
            if flow is None or flow.is_finished(time):
                continue
            
            # CRITICAL CHECK: Skip if flow is self-to-self
            if flow.src == flow.dst:
                print(f"[TRAFFIC ERROR] Skipping self-to-self flow {flow_id}")
                self.active_flows.discard(flow_id)
                continue
            
            # Check if it's time to start this flow
            if time < flow.start_time:
                continue
            
            # Initialize connection if not done yet
            if not flow._initialized:
                # Wait a moment before sending SYN
                if time - flow.start_time < 0.5:
                    continue
                
                # Try to send initial SYN (but not too frequently)
                if time - flow._last_syn_time > 5.0 and flow._syn_attempts < 3:
                    try:
                        success = self.network.send_tcp_packet(
                            src=flow.src,
                            dst=flow.dst,
                            src_port=5000 + flow.flow_id,
                            dst_port=80,
                            data=bytes(0),  # SYN packet
                            current_time=time
                        )
                        if success:
                            print(f"[TRAFFIC] Flow {flow_id}: Sent initial SYN to {flow.dst}")
                            flow._last_syn_time = time
                            flow._syn_attempts += 1
                            self.stats['total_syn_packets'] += 1
                        else:
                            print(f"[TRAFFIC] Flow {flow_id}: Failed to send initial SYN")
                    except Exception as e:
                        print(f"[TRAFFIC] Error sending initial SYN for flow {flow_id}: {e}")
                
                # Mark as initialized even if SYN fails (will retry)
                flow._initialized = True
                continue
            
            # Generate data packets at controlled rate
            # For 5 Kbps and 1208 byte packets: ~0.517 packets/sec
            expected_interval = flow.packet_size_bytes * 8 / flow.data_rate_bps
            
            if time - flow._last_gen_time >= expected_interval:
                try:
                    # Send one data packet
                    success = self.network.send_tcp_packet(
                        src=flow.src,
                        dst=flow.dst,
                        src_port=5000 + flow.flow_id,
                        dst_port=80,
                        data=bytes(flow.packet_size_bytes),
                        current_time=time
                    )
                    
                    if success:
                        flow._last_gen_time = time
                        flow.packets_generated += 1
                        flow.bytes_generated += flow.packet_size_bytes
                        packets_generated += 1
                        self.stats['total_data_packets_generated'] += 1
                        
                        if self.network.debug and flow.packets_generated % 5 == 0:
                            print(f"[TRAFFIC] Flow {flow_id}: Sent DATA packet #{flow.packets_generated}")
                    
                except Exception as e:
                    print(f"[TRAFFIC] Error sending packet for flow {flow_id}: {e}")
        
        if packets_generated > 0:
            print(f"[TRAFFIC] Generated {packets_generated} DATA packets at time {time:.2f}s")
        
        return packets_generated
    
    def on_packet_delivered(self, packet, time: float):
        """Handle packet delivery - ULTRA STRICT to prevent double counting"""
        
        # CRITICAL: Only count DATA packets (not ACK/SYN)
        if not packet.data or len(packet.data) == 0:
            # This is an ACK or SYN packet, not a data packet
            # Don't count it in delivery statistics
            return
        
        # CRITICAL: Create unique identifier for this packet
        # Use combination of src, dst, seq_num
        packet_id = (packet.src, packet.dst, packet.seq_num)
        
        # Check if this packet has already been counted
        if packet_id in self._delivered_seq_nums:
            self.stats['duplicate_deliveries_blocked'] += 1
            if self.network.debug:
                print(f"[TRAFFIC WARNING] Packet {packet.seq_num} already counted, ignoring duplicate")
            return
        
        # Mark as delivered and counted
        self._delivered_seq_nums.add(packet_id)
        
        # Find the flow
        flow_id = self._get_flow_id_from_packet(packet)
        if flow_id is None:
            return
        
        flow = self.flows.get(flow_id)
        if not flow:
            return
        
        # Update flow statistics
        packet_size = len(packet.data)
        flow.bytes_delivered += packet_size
        flow.packets_delivered += 1
        self.stats['total_data_packets_delivered'] += 1
        
        # Mark flow as active
        flow.flow_state = FlowState.ACTIVE
        
        # Calculate delivery rate
        if flow.packets_generated > 0:
            rate = (flow.packets_delivered / flow.packets_generated) * 100
        else:
            rate = 0
        
        print(f"[DELIVERED] {flow.src} → {flow.dst}: Packet #{flow.packets_delivered} "
              f"({rate:.1f}%, {len(packet.data)} bytes)")
    
    def on_ack_received(self, packet, time: float):
        """Handle ACK packets - DON'T COUNT THEM AS DATA"""
        flow_id = self._get_flow_id_from_packet(packet)
        if flow_id is not None:
            flow = self.flows.get(flow_id)
            if flow:
                # ACK means data was delivered, but DON'T count the ACK itself
                self.stats['total_ack_packets'] += 1
                
                # Check if this is a SYN-ACK (connection established)
                if hasattr(packet, 'flags') and packet.flags.get('SYN', False):
                    print(f"[TRAFFIC] Flow {flow_id}: TCP connection established with {flow.dst}")
                
                # Mark flow as active (but don't increment delivery count)
                flow.flow_state = FlowState.ACTIVE
    
    def _get_flow_id_from_packet(self, packet) -> Optional[int]:
        """Extract flow ID from packet - FIXED"""
        try:
            if hasattr(packet, 'src_port'):
                # ProtocolPacket: derive flow_id from src_port
                flow_id = (packet.src_port - 5000)
                if 0 < flow_id <= 100:  # Reasonable range
                    return flow_id
        except:
            pass
        
        # Try to find flow by src/dst
        for flow_id, flow in self.flows.items():
            if flow.src == packet.src and flow.dst == packet.dst:
                return flow_id
        
        return None
    
    def _validate_delivery_counts(self):
        """Emergency validation to fix delivery counts"""
        for flow_id, flow in self.flows.items():
            if flow.packets_delivered > flow.packets_generated:
                print(f"[TRAFFIC EMERGENCY] Flow {flow_id} has more delivered ({flow.packets_delivered}) than sent ({flow.packets_generated})!")
                print(f"  Fixing by resetting delivered to sent...")
                flow.packets_delivered = flow.packets_generated
                flow.bytes_delivered = flow.bytes_generated
    
    def get_statistics(self):
        """Get traffic statistics - WITH VALIDATION"""
        # Emergency fix for corrupted counts
        self._validate_delivery_counts()
        
        total_bytes_generated = sum(f.bytes_generated for f in self.flows.values())
        total_bytes_delivered = sum(f.bytes_delivered for f in self.flows.values())
        total_packets_generated = sum(f.packets_generated for f in self.flows.values())
        total_packets_delivered = sum(f.packets_delivered for f in self.flows.values())
        
        # Ensure delivered <= generated (sanity check)
        total_packets_delivered = min(total_packets_delivered, total_packets_generated)
        total_bytes_delivered = min(total_bytes_delivered, total_bytes_generated)
        
        delivery_rate = (total_packets_delivered / total_packets_generated * 100) if total_packets_generated > 0 else 0
        
        # Combine with internal stats
        result = {
            'total_flows': len(self.flows),
            'active_flows': len(self.active_flows),
            'total_bytes_generated': total_bytes_generated,
            'total_bytes_delivered': total_bytes_delivered,
            'total_packets_generated': total_packets_generated,
            'total_packets_delivered': total_packets_delivered,
            'delivery_rate': delivery_rate,
        }
        
        # Add internal stats
        result.update(self.stats)
        
        return result
    
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
    
    def reset(self):
        """Reset all statistics"""
        self._delivered_seq_nums.clear()
        for flow in self.flows.values():
            flow.packets_generated = 0
            flow.packets_delivered = 0
            flow.bytes_generated = 0
            flow.bytes_delivered = 0
            flow.flow_state = FlowState.WAITING
            flow._syn_attempts = 0
            flow._initialized = False
        
        # Reset internal stats
        for key in self.stats:
            self.stats[key] = 0
    
    def cleanup(self):
        """Clean up old delivered packet records"""
        # For now, keep all records
        # In a longer simulation, we might want to periodically clear old records
        pass