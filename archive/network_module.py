import math
from collections import deque
from typing import Dict
from traffic_module import Packet
from schedule_core import ScheduleCore

C = 299792.458  # km/s

# ----------------------------
# Channel Model
# ----------------------------

class ChannelModel:
    def __init__(self, bandwidth_bps=1e9):
        self.bandwidth_bps = bandwidth_bps

    def tx_delay(self, packet: Packet) -> float:
        return (packet.size_bytes * 8) / self.bandwidth_bps

    def propagation_delay(self, dist_km: float) -> float:
        return dist_km / C


# ----------------------------
# Interface Cache
# ----------------------------

class InterfaceCache:
    def __init__(self, max_queue=100):
        self.queue = deque()
        self.max_queue = max_queue
        self.busy = False

    def enqueue(self, packet: Packet) -> bool:
        if len(self.queue) >= self.max_queue:
            return False
        self.queue.append(packet)
        return True

    def dequeue(self):
        return self.queue.popleft() if self.queue else None


# ----------------------------
# Protocol Stack
# ----------------------------

class ProtocolStack:
    def __init__(self, node_name: str):
        self.node_name = node_name

    def process_packet(self, packet: Packet):
        return 0.001  # 1 ms


# ----------------------------
# Network Module
# ----------------------------

class NetworkModule:
    def __init__(self, sim_manager):
        self.sim = sim_manager
        self.core: ScheduleCore = sim_manager.core
        self.topology = sim_manager.topology

        self.channel = ChannelModel()
        self.stacks: Dict[str, ProtocolStack] = {}
        self.caches: Dict[tuple, InterfaceCache] = {}

    def send_packet(self, packet: Packet):
        delay = self.get_stack(packet.src).process_packet(packet)
        self.core.schedule(
            self.core.current_time + delay,
            1,
            self.route_packet,
            packet
        )

    def route_packet(self, time, packet: Packet):
        t = self.sim.get_sim_time(time)

        gs_pos = {
            name: self.topology.get_gs_position(name, t)
            for name, _, _, _ in self.topology.GROUND_STATIONS
        }

        pos, _, _, _ = self.topology.compute(t)
        graph = self.topology.construct_unified_graph(pos, gs_pos)

        path = self.topology.get_path(graph, packet.src, packet.dst)
        if len(path) < 2:
            return

        src, next_hop = path[0], path[1]
        eid = graph.get_eid(
            graph.vs.find(name=src).index,
            graph.vs.find(name=next_hop).index
        )

        dist = graph.es[eid]["weight"]
        packet.hop_distance_km = dist

        cache = self.get_cache(src, next_hop)
        if not cache.enqueue(packet):
            return

        if not cache.busy:
            self.schedule_transmission(src, next_hop)

    def schedule_transmission(self, src, dst):
        cache = self.get_cache(src, dst)
        pkt = cache.dequeue()
        if not pkt:
            cache.busy = False
            return

        cache.busy = True

        tx = self.channel.tx_delay(pkt)
        prop = self.channel.propagation_delay(pkt.hop_distance_km)

        self.core.schedule(
            self.core.current_time + tx + prop,
            2,
            self.receive_packet,
            pkt,
            src,
            dst
        )

    def receive_packet(self, time, packet, src, dst):
        cache = self.get_cache(src, dst)
        cache.busy = False

        if dst == packet.dst:
            self.sim.traffic.on_packet_delivered(packet, time)
        else:
            packet.src = dst
            self.send_packet(packet)

        self.schedule_transmission(src, dst)

    def get_stack(self, node):
        if node not in self.stacks:
            self.stacks[node] = ProtocolStack(node)
        return self.stacks[node]

    def get_cache(self, src, dst):
        key = (src, dst)
        if key not in self.caches:
            self.caches[key] = InterfaceCache()
        return self.caches[key]

    def get_queue_length(self, src, dst):
        cache = self.caches.get((src, dst))
        return len(cache.queue) if cache else 0
