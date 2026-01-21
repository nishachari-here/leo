from datetime import datetime, timezone
from skyfield.api import load
from core.schedule_core import ScheduleCore
from config.config import LogicalConfig
from data.setup import get_satellite_data
from topology.logic_module import LogicalTopology, GROUND_STATIONS
from network.network_moduleTCP import NetworkModule, ProtocolType
from traffic.traffic_module import TrafficModule, Flow, FlowType, FlowState

class SimpleSimulationManager:
    def __init__(self, satellites):
        #Core Infrastructure
        self.core = ScheduleCore()
        self.running = False

        #Configuration
        self.cfg = LogicalConfig()
        self.cfg.debug = False

        #Time Reference
        self.ts = load.timescale()
        self.start_epoch = self.ts.from_datetime(datetime.now(timezone.utc))
        self.simulation_end_time = 0.0

        #Topology
        self.topology = LogicalTopology(satellites, config=self.cfg)

        #Network
        self.network = NetworkModule(self)
        self.network.core = self.core
        self.network.topology = self.topology
        self.network.sim = self

        #Traffic
        self.traffic = TrafficModule(self.network)

        #Simulation Parameters
        self.traffic_interval = 0.1
        self.network_interval = 0.05
        self.stats_interval = 2.0

        #Flow Creation
        self._create_flows()
        print("[SIM] Event-driven simulation manager initialized")

    def get_sim_time(self, sim_seconds: float):
        return self.ts.tt_jd(
            self.start_epoch.tt + (sim_seconds / 86400)
        )
    
    def _create_flows(self):
        flow_pairs = [
            ("GS_USA", "GS_BRAZIL", 2.0),
            ("GS_AUSTRALIA", "GS_INDIA", 5.0),
            ("GS_INDIA", "GS_USA", 8.0),
            ("GS_BRAZIL", "GS_AUSTRALIA", 11.0),
        ]

        for i, (src, dst, start_time) in enumerate(flow_pairs):
            flow = Flow(
                flow_id=i + 1,
                src=src,
                dst=dst,
                start_time=start_time,
                end_time=30.0,
                data_rate_bps=4000,
                packet_size_bytes=512,
                flow_type=FlowType.BULK,
                flow_state=FlowState.WAITING,
                protocol_type=ProtocolType.TCP,
            )
            self.traffic.create_flow(flow)

        print(f"[SIM] Created {len(flow_pairs)} flows")

    #Events

    def _traffic_event(self, current_time: float):
        """Generate traffic and reschedule"""
        if not self.running:
            return

        self.traffic.on_packet_generation(
            current_time,
            self.traffic_interval
        )

        self.core.schedule(
            current_time + self.traffic_interval,
            priority = 3,
            action=self._traffic_event,
        )

    def _network_event(self, current_time: float):
        """Process network events and reschedule"""
        if not self.running:
            return
        
        self.network.process_events(current_time)

        self.core.schedule(
            current_time + self.network_interval,
            priority = 2,
            action=self._network_event
        )

    def _stats_event(self, current_time: float):
        """Periodic statistics logging"""
        if not self.running:
            return
        
        self._print_statistics(current_time)

        self.core.schedule(
            current_time + self.stats_interval,
            priority = 10,
            action=self._stats_event
        )

    def _stop_event(self, current_time: float):
        """Stop simulation cleanly"""
        print(f"\n[SIM] Simulation finished at t={current_time:.2f}s")
        self.running = False
        self._print_final_statistics(current_time)

    #Simulation control

    def initialize(self, duration_seconds: float):
        """Schedule initial events"""
        self.simulation_end_time = duration_seconds
        self.running = True

        self.core.schedule(0.0, 3, self._traffic_event)
        self.core.schedule(0.0, 2, self._network_event)
        self.core.schedule(0.0, 10, self._stats_event)
        self.core.schedule(duration_seconds, 0, self._stop_event)

    def run_simulation(self, duration_seconds: float):
        print("\n[SIM] Starting EVENT-DRIVEN simulation")
        print("=" * 60)

        self.initialize(duration_seconds)

        # This is the ONLY place time advances
        self.core.run_until(duration_seconds)

    #Statistics

    def _print_statistics(self, current_time: float):
        stats = self.network.get_statistics()
        traffic = self.traffic.get_statistics()

        print(f"\n⏱️  Time {current_time:.1f}s")
        print(
            f"📦 Network: {stats['total_packets_sent']} sent, "
            f"{stats['total_packets_received']} received"
        )
        print(
            f"🚀 Traffic: {traffic['total_packets_generated']} generated, "
            f"{traffic['total_packets_delivered']} delivered"
        )
        print(
            f"📊 Throughput: {stats['throughput_bps']/1e6:.3f} Mbps"
        )

    def _print_final_statistics(self, total_time: float):
        print("\n" + "=" * 60)
        print("📊 FINAL SIMULATION STATISTICS")
        print("=" * 60)

        stats = self.network.get_statistics()
        traffic = self.traffic.get_statistics()

        print(f"Duration: {total_time:.1f}s")
        print(f"Packets sent: {stats['total_packets_sent']}")
        print(f"Packets received: {stats['total_packets_received']}")
        print(f"Packet loss rate: {stats['packet_loss_rate']*100:.2f}%")
        print(f"Avg latency: {stats['avg_latency_ms']:.2f} ms")
        print(f"Throughput: {stats['throughput_bps']/1e6:.3f} Mbps")
        print(f"Traffic delivery rate: {traffic['delivery_rate']:.2f}%")

        print("=" * 60)

    #CLI Entry Point

    def run_simple_simulation():
        satellites = get_satellite_data(
        num_sats=100,
        csv_file="data/customconstellation.csv",
        )

        sim = SimpleSimulationManager(satellites)
        sim.run_simulation(duration_seconds=30.0)

    if __name__ == "__main__":
        run_simple_simulation()