# sim_man.py - FIXED VERSION
import time
import random
from datetime import datetime, timezone
from skyfield.api import load
from network_moduleTCP import EnhancedNetworkModule, ProtocolType, ProtocolPacket
from logic_module import LogicalTopology, GROUND_STATIONS
from config import LogicalConfig
from setup import get_satellite_data
from traffic_module import TrafficModule, Flow, FlowType, FlowState

# ScheduleCore class
from dataclasses import dataclass, field
from typing import Callable, Any
import heapq

@dataclass(order=True)
class Event:
    time: float
    priority: int
    action: Callable[..., None] = field(compare=False)
    args: tuple = field(compare=False, default=())

class ScheduleCore:
    def __init__(self):
        self.current_time: float = 0.0
        self._event_queue: list[Event] = []
    
    def schedule(self, time: float, priority: int, action: Callable, *args):
        event = Event(time=time, priority=priority, action=action, args=args)
        heapq.heappush(self._event_queue, event)

    def run(self, until: float):
        while self._event_queue:
            event = heapq.heappop(self._event_queue)
            if event.time > until:
                break
            self.current_time = event.time
            event.action(self.current_time, *event.args)

class SimpleSimulationManager:
    """Simple simulation manager that sends TCP packets between ground stations"""
    
    def __init__(self, satellites):
        # Core components
        self.core = ScheduleCore()
        
        # Create config with OPTIMIZED settings
        self.cfg = LogicalConfig()
        self.cfg.debug = False  # Reduce debug spam
        self.cfg.min_elev_deg = 15.0  # INCREASED for more stable connections
        self.cfg.isl_max_km = 5500.0  # Reasonable ISL range
        
        # Pass config to topology
        self.topology = LogicalTopology(satellites, config=self.cfg)
        
        self.ts = load.timescale()
        self.start_epoch = self.ts.from_datetime(datetime.now(timezone.utc))
        
        # Network module with all TCP/IP functionality
        self.network = EnhancedNetworkModule(self)
        self.network.core = self.core
        self.network.topology = self.topology
        self.network.sim = self
        self.network.debug = False  # Reduce debug spam
        
        # IMPORTANT: Add send_packet method to network module for compatibility
        self._add_send_packet_method()
        
        # Traffic module
        self.traffic = TrafficModule(self.network)
        
        self.debug = True
        self.running = False
        
        # Create flows
        self._create_all_flows()
        
        print("[SIM] Simulation manager initialized")
        print(f"[SIM] {len(satellites)} satellites, {len(GROUND_STATIONS)} ground stations")
        print(f"[SIM] Using optimized topology with elevation > {self.cfg.min_elev_deg}°")
        
        # Test connectivity before starting
        self._test_basic_connectivity()
    
    def _add_send_packet_method(self):
        """Add send_packet method to network module for compatibility with traffic module"""
        def send_packet(packet, current_time):
            """Wrapper method to send packets via TCP"""
            try:
                # Extract flow ID from packet
                flow_id = getattr(packet, 'flow_id', 0)
                src_port = 5000 + flow_id if flow_id > 0 else 5000
                
                # Send via TCP
                return self.network.send_tcp_packet(
                    src=packet.src,
                    dst=packet.dst,
                    src_port=src_port,
                    dst_port=80,
                    data=bytes(1024),  # 1KB data
                    current_time=current_time
                )
            except Exception as e:
                if self.debug:
                    print(f"[SIM] Error in send_packet: {e}")
                return False
        
        # Add method to network module
        self.network.send_packet = lambda packet, current_time: send_packet(packet, current_time)
    
    def _create_all_flows(self):
        """Create 4 TCP flows with realistic parameters"""
        # All 4 flows between DIFFERENT ground stations
        flow_pairs = [
            ("GS_USA", "GS_BRAZIL"),
            ("GS_AUSTRALIA", "GS_INDIA"),
            ("GS_INDIA", "GS_USA"),
            ("GS_BRAZIL", "GS_AUSTRALIA"),
        ]
        
        for i, (src, dst) in enumerate(flow_pairs):
            flow_id = i + 1
            
            # Stagger start times
            start_time = 2.0 + (i * 3.0)  # 2s, 5s, 8s, 11s
            
            flow = Flow(
                flow_id=flow_id,
                src=src,
                dst=dst,
                start_time=start_time,
                end_time=30,
                data_rate_bps=4000,  # 4 Kbps for better stability
                packet_size_bytes=512,  # Smaller packets
                flow_type=FlowType.BULK,
                flow_state=FlowState.WAITING,
                protocol_type=ProtocolType.TCP
            )
            
            # Use the traffic module's create_flow method
            if self.traffic.create_flow(flow):
                print(f"[SIM] Created flow {flow_id}: {src} → {dst} (4 Kbps, starts at {start_time}s)")
            else:
                print(f"[SIM] FAILED to create flow {flow_id}")
        
        print(f"[SIM] Created {len(flow_pairs)} TCP flows")
    
    def _test_basic_connectivity(self):
        """Test if ground stations can reach each other"""
        print("\n[SIM] Testing basic connectivity...")
        print("="*50)
        
        # Test connectivity for all flow pairs
        test_pairs = [
            ("GS_USA", "GS_BRAZIL"),
            ("GS_AUSTRALIA", "GS_INDIA"),
            ("GS_INDIA", "GS_USA"),
            ("GS_BRAZIL", "GS_AUSTRALIA"),
        ]
        
        successful_paths = 0
        
        for src, dst in test_pairs:
            # Skip self-to-self
            if src == dst:
                print(f"❌ {src:15} → {dst:15}: SELF-TO-SELF (NOT ALLOWED)")
                continue
            
            try:
                # Get current time
                t = self.get_sim_time(0)
                
                # Get topology state
                pos, links, _, _ = self.topology.compute(t)
                
                # Get ground station positions
                gs_positions = {}
                for name, lat, lon, alt in GROUND_STATIONS:
                    gs_positions[name] = self.topology.get_gs_position(name, t)
                
                # Build graph
                g = self.topology.construct_unified_graph(pos, gs_positions)
                
                # Find path
                path = self.topology.get_path(g, src, dst)
                
                if path:
                    successful_paths += 1
                    print(f"✓ {src:15} → {dst:15}: {len(path)-1:2} hops")
                    if len(path) <= 6:  # Only show short paths
                        print(f"    Path: {' → '.join(path)}")
                else:
                    print(f"✗ {src:15} → {dst:15}: NO PATH FOUND")
                    
            except Exception as e:
                print(f"✗ {src:15} → {dst:15}: ERROR: {e}")
        
        print(f"\n✓ {successful_paths}/{len(test_pairs)} paths found")
        print("="*50)
        
        if successful_paths < len(test_pairs):
            print("[SIM] ⚠️ Some flows may have connectivity issues")
    
    def get_sim_time(self, seconds_from_start):
        """Convert simulation seconds to skyfield time"""
        return self.ts.tt_jd(self.start_epoch.tt + (seconds_from_start / 86400.0))
    
    def run_simulation(self, duration_seconds: float, time_step: float = 0.1):
        """Run simulation for specified duration - IMPROVED"""
        print(f"\n[SIM] Starting simulation for {duration_seconds} seconds")
        print("="*60)
        print("TCP packets will be sent between ground stations via satellites")
        print("ALL 4 TCP flows:")
        print("  1. USA → Brazil (starts at 2s)")
        print("  2. Australia → India (starts at 5s)")
        print("  3. India → USA (starts at 8s)")
        print("  4. Brazil → Australia (starts at 11s)")
        print(f"Time step: {time_step}s")
        print("="*60)
        
        self.running = True
        current_time = 0.0
        
        # Statistics tracking
        last_stats_time = 0.0
        stats_interval = 2.0  # Print stats every 2 seconds
        last_flow_check = 0.0
        
        print("[SIM] Starting simulation...")
        
        while current_time < duration_seconds and self.running:
            try:
                # Generate traffic using traffic module
                packets_generated = self.traffic.on_packet_generation(current_time, time_step)
                
                # Process network events
                self.network.process_events(current_time)
                
                # Process scheduled events
                self.core.run(until=current_time + time_step)
                
                # Update simulation time
                current_time += time_step
                self.core.current_time = current_time
                
                # Check flow status periodically
                if current_time - last_flow_check >= 3.0:
                    self._check_flow_status(current_time)
                    last_flow_check = current_time
                
                # Print statistics periodically
                if current_time - last_stats_time >= stats_interval:
                    self._print_statistics(current_time)
                    last_stats_time = current_time
                
            except KeyboardInterrupt:
                print("\n[SIM] Simulation interrupted")
                self.running = False
                break
            except Exception as e:
                print(f"[SIM] Error at time {current_time:.1f}s: {e}")
                # Continue despite errors
                continue
        
        # Final statistics
        self._print_final_statistics(current_time)
        print(f"\n[SIM] Simulation completed at {current_time:.1f}s")
    
    def _check_flow_status(self, current_time: float):
        """Check and log flow status"""
        print(f"\n[FLOW STATUS] Time: {current_time:.1f}s")
        active_count = 0
        
        for flow_id in sorted(self.traffic.flows.keys()):
            flow = self.traffic.flows[flow_id]
            
            if flow.is_active(current_time):
                active_count += 1
                
                # Get status from traffic module
                status = self.traffic.get_flow_status(flow_id)
                if status:
                    rate = status['delivery_rate']
                    
                    # Format status line
                    status_line = f"Flow {flow_id}: {flow.src} → {flow.dst}"
                    status_line += f" | Sent: {status['packets_generated']}"
                    status_line += f" | Delivered: {status['packets_delivered']}"
                    status_line += f" | Rate: {rate:.1f}%"
                    status_line += f" | State: {status['state']}"
                    
                    # Add TCP info if available
                    try:
                        src_stack = self.network._get_protocol_stack(flow.src)
                        key = (flow.src, 5000 + flow_id, flow.dst, 80)
                        if key in src_stack.connections:
                            conn = src_stack.connections[key]
                            status_line += f" | cwnd: {conn.cwnd:.1f}"
                    except:
                        pass
                    
                    print(status_line)
        
        if active_count == 0:
            print("No active flows")
    
    def _print_statistics(self, current_time: float):
        """Print current statistics"""
        try:
            stats = self.network.get_statistics()
            traffic_stats = self.traffic.get_statistics()
            
            print(f"\n⏱️  Time: {current_time:.1f}s")
            
            # Network stats
            if stats['total_packets_sent'] > 0:
                net_delivery = (stats['total_packets_received'] / stats['total_packets_sent']) * 100
                print(f"📦 Network: {stats['total_packets_sent']:,} sent, "
                      f"{stats['total_packets_received']:,} received ({net_delivery:.1f}%)")
            
            # Traffic stats
            if traffic_stats['total_packets_generated'] > 0:
                traffic_rate = traffic_stats['delivery_rate']
                print(f"🚀 Traffic: {traffic_stats['total_packets_generated']} generated, "
                      f"{traffic_stats['total_packets_delivered']} delivered ({traffic_rate:.1f}%)")
            
            # TCP stats
            print(f"🔗 TCP: {stats['tcp_connections']} connections, "
                  f"{stats['tcp_retransmissions']} retransmissions")
            
            # Show throughput
            throughput_mbps = stats['throughput_bps'] / 1e6
            print(f"📊 Throughput: {throughput_mbps:.3f} Mbps")
            
            # Show individual flows
            print(f"🔢 Active flows: {traffic_stats['active_flows']}/{traffic_stats['total_flows']}")
            
            # Show any duplicate delivery issues
            if traffic_stats.get('duplicate_deliveries_blocked', 0) > 0:
                print(f"⚠️  Duplicates blocked: {traffic_stats['duplicate_deliveries_blocked']}")
            
        except Exception as e:
            print(f"[STATS] Error getting statistics: {e}")
    
    def _print_final_statistics(self, total_time: float):
        """Print final comprehensive statistics"""
        print("\n" + "="*60)
        print("📊 FINAL SIMULATION STATISTICS")
        print("="*60)
        
        try:
            # Get network statistics
            net_stats = self.network.get_statistics()
            traffic_stats = self.traffic.get_statistics()
            
            print(f"\n📡 NETWORK PERFORMANCE:")
            print(f"  • Simulation duration: {total_time:.1f} seconds")
            print(f"  • Total packets sent: {net_stats['total_packets_sent']:,}")
            print(f"  • Total packets received: {net_stats['total_packets_received']:,}")
            
            if net_stats['total_packets_sent'] > 0:
                net_delivery_rate = (net_stats['total_packets_received'] / net_stats['total_packets_sent']) * 100
                print(f"  • Packet delivery rate: {net_delivery_rate:.1f}%")
            
            print(f"  • Total data transferred: {net_stats['total_bytes_transferred']/1e6:.3f} MB")
            print(f"  • Average throughput: {net_stats['throughput_bps']/1e6:.3f} Mbps")
            
            print(f"\n⏱️  LATENCY AND ROUTING:")
            print(f"  • Average latency: {net_stats['avg_latency_ms']:.1f} ms")
            print(f"  • Average hops per packet: {net_stats['avg_hops']:.2f}")
            print(f"  • Packet loss rate: {net_stats['packet_loss_rate']*100:.1f}%")
            print(f"  • Routing failures: {net_stats['routing_failures']}")
            
            print(f"\n🔗 TCP PERFORMANCE:")
            print(f"  • TCP connections: {net_stats['tcp_connections']}")
            print(f"  • TCP retransmissions: {net_stats['tcp_retransmissions']}")
            print(f"  • Queue drops: {net_stats['queue_drops']}")
            
            # Flow statistics
            print(f"\n🚀 TRAFFIC STATISTICS:")
            print(f"  • Total flows created: {traffic_stats['total_flows']}")
            print(f"  • Active flows: {traffic_stats['active_flows']}")
            print(f"  • Traffic delivery rate: {traffic_stats['delivery_rate']:.1f}%")
            
            # Individual flow stats
            if self.traffic.flows:
                print(f"\n  Individual Flow Performance:")
                for flow_id, flow in sorted(self.traffic.flows.items()):
                    status = self.traffic.get_flow_status(flow_id)
                    if status:
                        rate = status['delivery_rate']
                        symbol = "✓" if rate > 70 else "⚠️" if rate > 30 else "✗"
                        print(f"    {symbol} Flow {flow_id}: {flow.src} → {flow.dst}: "
                            f"{rate:.1f}% ({status['packets_delivered']}/{status['packets_generated']})")
            
            # Show TCP connection details
            print(f"\n🔗 TCP CONNECTION DETAILS:")
            for flow_id, flow in sorted(self.traffic.flows.items()):
                try:
                    src_stack = self.network._get_protocol_stack(flow.src)
                    key = (flow.src, 5000 + flow_id, flow.dst, 80)
                    if key in src_stack.connections:
                        conn = src_stack.connections[key]
                        print(f"    Flow {flow_id}: {flow.src} → {flow.dst}")
                        print(f"      • State: {conn.state.name}")
                        print(f"      • cwnd: {conn.cwnd:.1f}")
                        print(f"      • RTO: {conn.rto:.2f}s")
                        print(f"      • Packets sent: {conn.packets_sent}")
                        print(f"      • Retransmissions: {conn.packets_retransmitted}")
                except:
                    pass
            
            # Show traffic module internal stats
            if 'duplicate_deliveries_blocked' in traffic_stats:
                print(f"\n🛡️  TRAFFIC MODULE PROTECTION:")
                print(f"  • Duplicate deliveries blocked: {traffic_stats['duplicate_deliveries_blocked']}")
                print(f"  • Data packets generated: {traffic_stats.get('total_data_packets_generated', 0)}")
                print(f"  • Data packets delivered: {traffic_stats.get('total_data_packets_delivered', 0)}")
                print(f"  • SYN packets: {traffic_stats.get('total_syn_packets', 0)}")
                print(f"  • ACK packets: {traffic_stats.get('total_ack_packets', 0)}")
                    
        except Exception as e:
            print(f"Error generating final statistics: {e}")
            import traceback
            traceback.print_exc()
        
        print("\n" + "="*60)

def run_simple_simulation():
    """Run a simple simulation with optimized settings"""
    print("="*60)
    print("🛰️  LEO SATELLITE NETWORK SIMULATOR - FIXED VERSION")
    print("="*60)
    print("Configuration: 100 satellites, 4 TCP flows")
    print("Flows:")
    print("  1. USA → Brazil (starts at 2s, 4 Kbps)")
    print("  2. Australia → India (starts at 5s, 4 Kbps)")
    print("  3. India → USA (starts at 8s, 4 Kbps)")
    print("  4. Brazil → Australia (starts at 11s, 4 Kbps)")
    print("Time: 30 seconds")
    print("="*60)
    
    # Load satellites
    print("\nLoading satellites...")
    try:
        satellites = get_satellite_data(num_sats=100, csv_file='customconstellation.csv')
        print(f"✅ Loaded {len(satellites)} satellites")
    except Exception as e:
        print(f"❌ Failed to load satellites: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Create simulation
    print("\nInitializing simulation...")
    try:
        sim = SimpleSimulationManager(satellites)
    except Exception as e:
        print(f"❌ Failed to initialize simulation: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Run simulation
    sim.run_simulation(duration_seconds=30, time_step=0.1)
    
    return sim

def main():
    """Main entry point"""
    run_simple_simulation()

if __name__ == "__main__":
    main()