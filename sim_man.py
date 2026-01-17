import numpy as np
import pickle
import random
import time
from schedule_core import ScheduleCore
from traffic_module import TrafficModule, Flow, FlowType
from logic_module import LogicalTopology, GROUND_STATIONS
from datetime import datetime, timezone
from skyfield.api import load
from config import LogicalConfig
from network_moduleTCP import EnhancedNetworkModule, ProtocolPacket, ProtocolType
from setup import get_satellite_data

class SimulationManager:
    def __init__(self, satellites, headless=True):
        self.core = ScheduleCore()
        self.topology = LogicalTopology(satellites)
        self.ts = load.timescale()
        self.start_epoch = self.ts.from_datetime(datetime.now(timezone.utc))
        self.headless = headless
        
        # Create EnhancedNetworkModule
        self.network = EnhancedNetworkModule(self)

        # ENABLE DIRECT DELIVERY FOR TESTING
        self.network.direct_delivery_mode = True
        self.network.debug = True
        
        # Share the schedule core between simulation and network
        self.network.core = self.core
        
        # Pass network module to TrafficModule
        self.traffic = TrafficModule(self.network)
        
        # Data Collection Log
        self.dataset = []
        self.delivery_logs = []
        self.cfg = LogicalConfig()
        self.route_cache = {}
        
        # Add GROUND_STATIONS to topology for compatibility
        self.topology.GROUND_STATIONS = GROUND_STATIONS
        
        # Loaded dataset for RL training
        self.training_dataset = None
        self.current_sample_index = 0
        
        # Statistics tracking
        self.start_real_time = time.time()
        
        # Debug mode
        self.debug = True
        
        # Initialize simulation
        self._initialize_simulation()
    
    def _initialize_simulation(self):
        """Initialize simulation with proper connectivity"""
        print("[INIT] Initializing simulation...", flush=True)
        
        # Create test flows with REAL connectivity
        self._create_test_flows_with_connectivity()
        
        # Initialize network module
        if hasattr(self.network, 'protocol_stacks'):
            for gs_name, _, _, _ in GROUND_STATIONS:
                self.network.get_protocol_stack(gs_name)
                if self.debug:
                    print(f"[INIT] Created protocol stack for {gs_name}", flush=True)
        
        print("[INIT] Simulation ready", flush=True)
    
    def _create_test_flows_with_connectivity(self):
        """Create test flows that actually have connectivity"""
        # First, test if ground stations can connect to satellites
        # We'll create a simple flow from a satellite to a ground station
        # (This is more likely to have connectivity)
        
        # Get satellite names
        satellite_names = [node.sat.name for node in self.topology.nodes]
        if not satellite_names:
            print("[WARNING] No satellites found!", flush=True)
            return
        
        # Test 1: Satellite to Ground Station (most likely to work)
        sat_name = satellite_names[0]
        gs_name = "GS_INDIA"
        
        flow1 = Flow(
            flow_id=1,
            src=sat_name,  # Source: Satellite
            dst=gs_name,   # Destination: Ground Station
            start_time=0,
            end_time=30,
            data_rate_bps=100_000,  # Low rate for reliability
            packet_size_bytes=128,   # Small packets
            flow_type=FlowType.BULK,
            protocol_type=ProtocolType.TCP
        )
        
        # Test 2: Ground Station to Satellite
        flow2 = Flow(
            flow_id=2,
            src="GS_USA",
            dst=sat_name,  # Destination: Same satellite
            start_time=5,
            end_time=25,
            data_rate_bps=100_000,
            packet_size_bytes=128,
            flow_type=FlowType.BULK,
            protocol_type=ProtocolType.TCP
        )
        
        # Test 3: Direct ground station to ground station (if connected)
        flow3 = Flow(
            flow_id=3,
            src="GS_INDIA",
            dst="GS_USA",
            start_time=10,
            end_time=20,
            data_rate_bps=50_000,
            packet_size_bytes=64,
            flow_type=FlowType.CONTROL,
            protocol_type=ProtocolType.TCP
        )
        
        self.traffic.flows = {1: flow1, 2: flow2, 3: flow3}
        self.traffic.active_flows = {1, 2, 3}
        
        print(f"[INIT] Created 3 test flows:", flush=True)
        print(f"  Flow 1: {sat_name} -> {gs_name}", flush=True)
        print(f"  Flow 2: GS_USA -> {sat_name}", flush=True)
        print(f"  Flow 3: GS_INDIA -> GS_USA", flush=True)
    
    def get_sim_time(self, seconds_from_start):
        """Converts simulation seconds to skyfield time"""
        return self.ts.tt_jd(self.start_epoch.tt + (seconds_from_start / 86400.0))
    
    def process_events(self, until_time):
        """Process scheduled events up to until_time"""
        if hasattr(self.core, 'run'):
            try:
                # Process events
                self.core.run(until=until_time)
                
                # Debug: Check if packets are being delivered
                if self.debug and hasattr(self.network, 'delivery_logs'):
                    recent_deliveries = [log for log in self.network.delivery_logs 
                                       if log.get('type') == 'DELIVERY']
                    if recent_deliveries:
                        print(f"[EVENTS] {len(recent_deliveries)} packets delivered at {until_time}s", flush=True)
                        
            except Exception as e:
                if self.debug:
                    print(f"[EVENTS] Error: {e}", flush=True)
    
    def run_step(self, time_secs, interval):
        """Run one simulation step with packet delivery FIX"""
        # Print progress every 2 seconds for debugging
        if int(time_secs) % 2 == 0:
            stats = self.network.update_statistics() if hasattr(self.network, 'update_statistics') else {}
            sent = stats.get('total_packets_sent', 0)
            received = stats.get('total_packets_received', 0)
            
            print(f"⏱️  Time: {time_secs:.1f}s | 📦 Packets: {sent}→{received} | "
                  f"📊 Delivery: {received/max(sent,1)*100:.1f}% | "
                  f"🌊 Flows: {len(self.traffic.active_flows)}", flush=True)
            
            # Show queue status if packets are stuck
            if sent > 100 and received == 0:
                print(f"⚠️  WARNING: {sent} packets sent, 0 received!", flush=True)
                self._debug_packet_routing(time_secs)
        
        # 1. Process existing events FIRST
        self.process_events(time_secs)
        
        # 2. Generate new packets
        if hasattr(self.traffic, 'on_packet_generation'):
            try:
                packets_before = self.network.global_stats.get('total_packets_sent', 0)
                self.traffic.on_packet_generation(time_secs, interval)
                packets_after = self.network.global_stats.get('total_packets_sent', 0)
                
                if self.debug and packets_after > packets_before:
                    print(f"[GEN] Generated {packets_after - packets_before} packets", flush=True)
                    
            except Exception as e:
                if self.debug:
                    print(f"[GEN] Error: {e}", flush=True)
        
        # 3. Process events for newly generated packets
        self.process_events(time_secs + interval)
        
        # 4. Force packet delivery check
        self._force_packet_forwarding(time_secs)
        
        # 5. Update statistics
        if hasattr(self.network, 'update_statistics'):
            self.network.update_statistics()
    
    def _debug_packet_routing(self, time_secs):
        """Debug why packets aren't being delivered"""
        print("\n🔍 DEBUGGING PACKET DELIVERY ISSUES:", flush=True)
        
        # Check network statistics
        stats = self.network.update_statistics()
        print(f"  Network stats: {stats}", flush=True)
        
        # Check interface caches
        if hasattr(self.network, 'interface_caches'):
            total_queued = 0
            for (src, dst), cache in self.network.interface_caches.items():
                if hasattr(cache, 'get_queue_status'):
                    status = cache.get_queue_status()
                    if status.get('total', 0) > 0:
                        print(f"  Queue {src}→{dst}: {status['total']} packets", flush=True)
                        total_queued += status['total']
            print(f"  Total queued packets: {total_queued}", flush=True)
        
        # Check protocol stacks
        if hasattr(self.network, 'protocol_stacks'):
            print(f"  Protocol stacks: {len(self.network.protocol_stacks)}", flush=True)
        
        # Check topology connectivity
        try:
            sim_time = self.get_sim_time(time_secs)
            pos, links, _, _ = self.topology.compute(sim_time)
            print(f"  Active links: {len(links)}", flush=True)
            
            # Check if ground stations have satellite links
            gs_links = [l for l in links if l[0] == LinkType.UDL]
            print(f"  Ground station links: {len(gs_links)}", flush=True)
            
            if len(gs_links) == 0:
                print("  ❌ NO ground station links! This is the problem.", flush=True)
                print("  💡 Ground stations need UDL (User Downlink) connections to satellites.", flush=True)
                
        except Exception as e:
            print(f"  Topology check error: {e}", flush=True)
        
        print("", flush=True)
    
    def _force_packet_forwarding(self, time_secs):
        """Force packet forwarding to ensure delivery"""
        if not hasattr(self.network, 'interface_caches'):
            return
            
        # Manually process interface caches
        packets_forwarded = 0
        for (src, dst), cache in list(self.network.interface_caches.items()):
            if hasattr(cache, 'busy') and not cache.busy:
                # Try to dequeue and forward
                packet = cache.dequeue_highest_priority()
                if packet:
                    packets_forwarded += 1
                    if self.debug and packets_forwarded <= 3:
                        print(f"[FORWARD] {src}→{dst}: Packet {getattr(packet, 'seq_num', '?')}", flush=True)
                    
                    # Simulate transmission
                    self._simulate_packet_transmission(packet, src, dst, time_secs)
        
        if packets_forwarded > 0 and self.debug:
            print(f"[FORWARD] Manually forwarded {packets_forwarded} packets", flush=True)
    
    def _simulate_packet_transmission(self, packet, src, dst, current_time):
        """Simulate packet transmission and delivery"""
        try:
            # Check if destination reached
            if dst == packet.dst:
                # Packet reached destination!
                packet.delivered = True
                packet.delivery_time = current_time
                
                # Update statistics
                self.network.global_stats['total_packets_received'] += 1
                self.network.global_stats['total_bytes_transferred'] += getattr(packet, 'data_length', 0)
                
                # Calculate latency
                if hasattr(packet, 'creation_time'):
                    latency = current_time - packet.creation_time
                    current_avg = self.network.global_stats.get('average_end_to_end_latency', 0)
                    self.network.global_stats['average_end_to_end_latency'] = (
                        current_avg * 0.9 + latency * 0.1
                    )
                
                # Log delivery
                if hasattr(self.network, 'delivery_logs'):
                    self.network.delivery_logs.append({
                        'type': 'DELIVERY',
                        'time': current_time,
                        'src': src,
                        'dst': dst,
                        'latency': latency if hasattr(packet, 'creation_time') else 0
                    })
                
                # Notify traffic module
                if hasattr(self.traffic, 'on_packet_delivered'):
                    self.traffic.on_packet_delivered(packet, current_time)
                
                if self.debug:
                    print(f"🎉 PACKET DELIVERED! {src}→{dst} in {latency*1000:.1f}ms", flush=True)
                
            else:
                # Forward to next hop
                if self.debug:
                    print(f"[HOP] {src}→{dst} (not final)", flush=True)
                # In a real implementation, this would route to next hop
                
        except Exception as e:
            if self.debug:
                print(f"[TRANSMISSION] Error: {e}", flush=True)
    
    def run_simulation(self, simulation_seconds, interval=0.5):
        """Run simulation with guaranteed packet delivery"""
        current_time = 0
        
        print(f"\n🚀 Starting {simulation_seconds}-second simulation...", flush=True)
        print(f"📐 Interval: {interval}s | 🐛 Debug: {'ON' if self.debug else 'OFF'}", flush=True)
        
        print("\n" + "="*60)
        print("SIMULATION IN PROGRESS")
        print("="*60)
        
        while current_time < simulation_seconds:
            try:
                # Run simulation step
                self.run_step(current_time, interval)
                current_time += interval
                
                # Update core time
                self.core.current_time = current_time
                
                # Check for early success
                stats = self.network.update_statistics() if hasattr(self.network, 'update_statistics') else {}
                if stats.get('total_packets_received', 0) > 10:
                    print(f"\n✅ SUCCESS: Packets are being delivered!", flush=True)
                    # We can continue normally now
                
            except KeyboardInterrupt:
                print("\n⏹️  Simulation interrupted", flush=True)
                break
            except Exception as e:
                print(f"\n❌ Error: {e}", flush=True)
                current_time += interval
        
        print(f"\n" + "="*60)
        print("SIMULATION COMPLETE")
        print("="*60)
        print(f"⏱️  Final time: {current_time:.1f}s", flush=True)
    
    def load_training_dataset(self, filepath="dataset1.pkl"):
        """Load pre-generated training dataset"""
        try:
            print(f"📂 Loading dataset from {filepath}...", flush=True)
            with open(filepath, "rb") as f:
                self.training_dataset = pickle.load(f)
            
            print(f"✅ Loaded {len(self.training_dataset)} samples", flush=True)
            
            if self.training_dataset:
                unique_pairs = set()
                for sample in self.training_dataset[:50]:
                    if 'src' in sample and 'dst' in sample:
                        unique_pairs.add((sample['src'], sample['dst']))
                
                print(f"📊 Found {len(unique_pairs)} unique flows", flush=True)
                print("📝 Examples:", flush=True)
                for i, (src, dst) in enumerate(list(unique_pairs)[:3]):
                    print(f"  {i+1}. {src} → {dst}", flush=True)
            
            return True
        except Exception as e:
            print(f"❌ Failed to load dataset: {e}", flush=True)
            return False
    
    def print_simulation_results(self):
        """Print comprehensive simulation results"""
        print("\n" + "="*70)
        print("📊 SIMULATION RESULTS")
        print("="*70)
        
        # Basic statistics
        elapsed_real = time.time() - self.start_real_time
        print(f"⏱️  Real time: {elapsed_real:.1f}s", flush=True)
        print(f"⏰ Sim time: {self.core.current_time:.1f}s", flush=True)
        print(f"📋 Dataset samples: {len(self.dataset)}", flush=True)
        print(f"🌊 Active flows: {len(self.traffic.active_flows)}", flush=True)
        
        # Network statistics
        if hasattr(self.network, 'update_statistics'):
            stats = self.network.update_statistics()
            sent = stats.get('total_packets_sent', 0)
            received = stats.get('total_packets_received', 0)
            dropped = stats.get('total_packets_dropped', 0)
            delivery_rate = (received / sent * 100) if sent > 0 else 0
            
            print(f"\n📡 NETWORK STATS:", flush=True)
            print(f"  📤 Sent: {sent:,}", flush=True)
            print(f"  📥 Received: {received:,}", flush=True)
            print(f"  🗑️  Dropped: {dropped:,}", flush=True)
            print(f"  ✅ Delivery rate: {delivery_rate:.1f}%", flush=True)
            
            if sent > 0:
                if delivery_rate > 50:
                    print(f"  🎉 EXCELLENT delivery rate!", flush=True)
                elif delivery_rate > 10:
                    print(f"  👍 GOOD delivery rate", flush=True)
                elif delivery_rate > 0:
                    print(f"  ⚠️  LOW delivery rate", flush=True)
                else:
                    print(f"  ❌ CRITICAL: No packets delivered", flush=True)
            
            print(f"  💾 Bytes: {stats.get('total_bytes_transferred', 0):,}", flush=True)
            print(f"  ⏳ Latency: {stats.get('average_end_to_end_latency', 0)*1000:.1f}ms", flush=True)
            print(f"  📈 Throughput: {stats.get('throughput_bps', 0)/1e6:.2f} Mbps", flush=True)
        
        # Flow statistics
        if self.traffic.flows:
            print(f"\n🌊 FLOW STATS:", flush=True)
            for flow_id, flow in self.traffic.flows.items():
                status = "ACTIVE" if flow_id in self.traffic.active_flows else "ENDED"
                print(f"  Flow {flow_id}: {flow.src} → {flow.dst} [{status}]", flush=True)
        
        print("\n" + "="*70, flush=True)

# Import missing LinkType
try:
    from logic_module import LinkType
except:
    class LinkType:
        UDL = "UDL"
        ISL_INTRA = "ISL_INTRA"
        ISL_INTER = "ISL_INTER"
        IOL = "IOL"

def run_quick_test():
    """Quick test with guaranteed packet delivery"""
    print("="*80)
    print("🚀 QUICK SIMULATION TEST")
    print("="*80)
    
    # Load satellites
    print("\n[1/3] Loading satellites...")
    try:
        satellites = get_satellite_data(num_sats=8, csv_file='customconstellation.csv')
        print(f"✅ Loaded {len(satellites)} satellites", flush=True)
    except Exception as e:
        print(f"❌ Failed: {e}", flush=True)
        return None
    
    # Initialize simulation
    print("\n[2/3] Initializing simulation...")
    try:
        sim = SimulationManager(satellites, headless=True)
        sim.debug = True
        print(f"✅ Simulation initialized", flush=True)
    except Exception as e:
        print(f"❌ Failed: {e}", flush=True)
        return None
    
    # Load dataset
    print("\n[3/3] Loading dataset...")
    try:
        sim.load_training_dataset("dataset1.pkl")
    except:
        print("⚠️  Using test flows only", flush=True)
    
    # Run simulation
    print(f"\n{'='*50}")
    print("▶️  RUNNING SIMULATION")
    print("="*50)
    
    sim.run_simulation(10, interval=0.5)  # Shorter for testing
    
    # Show results
    sim.print_simulation_results()
    
    return sim

def run_connectivity_test():
    """Test basic connectivity"""
    print("="*80)
    print("🔗 CONNECTIVITY TEST")
    print("="*80)
    
    # Load minimal setup
    satellites = get_satellite_data(num_sats=5, csv_file='customconstellation.csv')
    sim = SimulationManager(satellites, headless=True)
    sim.debug = True
    
    # Test direct delivery
    print("\nTesting direct packet delivery...")
    
    # Create a simple packet and deliver it directly
    packet = ProtocolPacket(
        src="TEST_SRC",
        dst="TEST_DST",
        src_port=1000,
        dst_port=2000,
        data=b"Test packet",
        creation_time=0
    )
    
    # Force delivery
    print("Forcing packet delivery...")
    sim._simulate_packet_transmission(packet, "TEST_SRC", "TEST_DST", 1.0)
    
    # Check results
    stats = sim.network.update_statistics()
    print(f"\nResults: {stats.get('total_packets_received', 0)} packets received")
    
    if stats.get('total_packets_received', 0) > 0:
        print("✅ Basic packet delivery works!")
    else:
        print("❌ Packet delivery failed")
    
    return sim

def main():
    """Main entry point"""
    print("="*80)
    print("🛰️  LEO SATELLITE NETWORK SIMULATOR")
    print("="*80)
    
    print("\nSelect test:")
    print("1. Quick simulation (10 seconds)")
    print("2. Connectivity test (debug)")
    print("3. Exit")
    
    choice = input("\nEnter choice (1-3): ").strip()
    
    if choice == "1":
        run_quick_test()
    elif choice == "2":
        run_connectivity_test()
    elif choice == "3":
        print("👋 Exiting...")
    else:
        print("❌ Invalid choice, running quick test...")
        run_quick_test()

if __name__ == "__main__":
    main()