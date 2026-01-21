import glfw
from OpenGL.GL import *
from OpenGL.GLU import *

import numpy as np
import csv
import time
from datetime import timedelta
from collections import deque

from skyfield.api import load, EarthSatellite, wgs84
import networkx as nx

# ==========================================================
# CONFIG
# ==========================================================
CSV_FILENAME = "starlink.csv"
MAX_SATS = 80

EARTH_RADIUS_KM = 6371.0
SCALE = 1.0 / EARTH_RADIUS_KM

TIME_MULTIPLIER = 60.0

ISL_MAX_KM = 2500
PACKET_SPEED = 0.6          # visual units / second
PACKET_GEN_RATE = 0.2      # packets per second

GROUND_STATIONS = [
    ("GS_INDIA", 28.6139, 77.2090, 0),
    ("GS_AUSTRALIA", -33.8688, 151.2093, 0),
]

# ==========================================================
# PACKET
# ==========================================================
class Packet:
    def __init__(self, path):
        self.path = path
        self.seg = 0
        self.progress = 0.0
        self.done = False

# ==========================================================
# NODE
# ==========================================================
class Node:
    def __init__(self, name, sat=None, is_gs=False):
        self.name = name
        self.sat = sat
        self.is_gs = is_gs
        self.queue = deque()

# ==========================================================
# LOGICAL TOPOLOGY
# ==========================================================
class LogicalTopology:
    def __init__(self, sats):
        self.nodes = []
        self.node_map = {}

        for s in sats:
            n = Node(s.name, sat=s)
            self.nodes.append(n)
            self.node_map[s.name] = n

        for name, *_ in GROUND_STATIONS:
            n = Node(name, is_gs=True)
            self.nodes.append(n)
            self.node_map[name] = n

    def compute_positions(self, t):
        pos = {}
        for n in self.nodes:
            if n.is_gs:
                lat, lon, h = next(gs[1:] for gs in GROUND_STATIONS if gs[0] == n.name)
                pos[n.name] = wgs84.latlon(lat, lon, elevation_m=h).at(t).position.km
            else:
                pos[n.name] = n.sat.at(t).position.km
        return pos

    def build_graph(self, pos):
        g = nx.Graph()
        for n in self.nodes:
            g.add_node(n.name)

        names = list(pos.keys())
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i], names[j]
                d = np.linalg.norm(pos[a] - pos[b])
                if d < ISL_MAX_KM:
                    g.add_edge(a, b, weight=d)
        return g

# ==========================================================
# VISUAL + TRAFFIC SIM
# ==========================================================
class SatelliteSim:
    def __init__(self, sats):
        self.ts = load.timescale()
        self.start_real = time.time()
        self.start_sim = self.ts.now()

        self.time_multiplier = TIME_MULTIPLIER
        self.topology = LogicalTopology(sats[:MAX_SATS])

        self.graph = None
        self.route = None
        self.pos = {}

        self.packets = []
        self.last_packet_gen = 0.0

        # Camera
        self.dist = 3.0
        self.rx = 0.0
        self.ry = 0.0
        self.drag = False
        self.lx = 0
        self.ly = 0

    # ---------------- TIME ----------------
    def sim_time(self):
        dt = (time.time() - self.start_real) * self.time_multiplier
        return self.ts.from_datetime(
            self.start_sim.utc_datetime() + timedelta(seconds=dt)
        )

    # ---------------- PACKETS ----------------
    def generate_packets(self):
        now = time.time()
        if self.route and (now - self.last_packet_gen) > (1.0 / PACKET_GEN_RATE):
            self.packets.append(Packet(self.route))
            self.last_packet_gen = now

    def update_packets(self, dt):
        alive = []
        for p in self.packets:
            if p.done:
                continue

            if p.seg >= len(p.path) - 1:
                p.done = True
                continue

            a = self.pos[p.path[p.seg]]
            b = self.pos[p.path[p.seg + 1]]
            dist = np.linalg.norm(a - b)

            step = PACKET_SPEED * dt / max(dist * SCALE, 1e-6)
            p.progress += step

            if p.progress >= 1.0:
                p.progress = 0.0
                p.seg += 1
                if p.seg >= len(p.path) - 1:
                    p.done = True

            alive.append(p)

        self.packets = alive

    # ---------------- DRAW ----------------
    def draw_earth(self):
        glColor3f(0.2, 0.5, 1.0)
        q = gluNewQuadric()
        gluQuadricDrawStyle(q, GLU_LINE)
        gluSphere(q, 1.0, 32, 32)

    def draw_nodes(self):
        glPointSize(4)
        glBegin(GL_POINTS)
        glColor3f(1, 1, 1)
        for p in self.pos.values():
            p = p * SCALE
            glVertex3f(p[0], p[2], p[1])
        glEnd()

    def draw_links(self):
        if not self.graph:
            return
        glColor3f(0.4, 0.4, 0.4)
        glBegin(GL_LINES)
        for a, b in self.graph.edges:
            pa = self.pos[a] * SCALE
            pb = self.pos[b] * SCALE
            glVertex3f(pa[0], pa[2], pa[1])
            glVertex3f(pb[0], pb[2], pb[1])
        glEnd()

    def draw_packets(self):
        glPointSize(6)
        glBegin(GL_POINTS)
        glColor3f(1, 0, 0)
        for p in self.packets:
            if p.done or p.seg >= len(p.path) - 1:
                continue
            a = self.pos[p.path[p.seg]]
            b = self.pos[p.path[p.seg + 1]]
            x = a + (b - a) * p.progress
            x = x * SCALE
            glVertex3f(x[0], x[2], x[1])
        glEnd()

    # ---------------- INPUT ----------------
    def scroll(self, w, x, y):
        self.dist = max(1.2, self.dist - y * 0.2)

    def mouse_btn(self, w, b, a, m):
        self.drag = (b == glfw.MOUSE_BUTTON_LEFT and a == glfw.PRESS)

    def mouse_move(self, w, x, y):
        if self.drag:
            self.ry += (x - self.lx) * 0.4
            self.rx += (y - self.ly) * 0.4
        self.lx, self.ly = x, y

    # ---------------- MAIN LOOP ----------------
    def run(self):
        glfw.init()
        win = glfw.create_window(1200, 800, "LEO Packet-Level Simulation", None, None)
        glfw.make_context_current(win)
        glEnable(GL_DEPTH_TEST)

        glfw.set_scroll_callback(win, self.scroll)
        glfw.set_mouse_button_callback(win, self.mouse_btn)
        glfw.set_cursor_pos_callback(win, self.mouse_move)

        last = time.time()

        while not glfw.window_should_close(win):
            now = time.time()
            dt = now - last
            last = now

            t = self.sim_time()
            self.pos = self.topology.compute_positions(t)
            self.graph = self.topology.build_graph(self.pos)

            if self.route is None:
                try:
                    self.route = nx.shortest_path(
                        self.graph,
                        "GS_INDIA",
                        "GS_AUSTRALIA",
                        weight="weight",
                    )
                    print("Route locked:", self.route)
                except nx.NetworkXNoPath:
                    pass

            self.generate_packets()
            self.update_packets(dt)

            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            glLoadIdentity()
            gluPerspective(45, 1.5, 0.1, 100)

            glTranslatef(0, 0, -self.dist)
            glRotatef(self.rx, 1, 0, 0)
            glRotatef(self.ry, 0, 1, 0)

            self.draw_earth()
            self.draw_links()
            self.draw_nodes()
            self.draw_packets()

            glfw.swap_buffers(win)
            glfw.poll_events()

        glfw.terminate()

# ==========================================================
# CSV PARSER
# ==========================================================
def parse_csv(filename):
    ts = load.timescale()
    sats = []
    with open(filename) as f:
        for r in csv.DictReader(f):
            sats.append(EarthSatellite.from_omm(ts, r))
    return sats

# ==========================================================
# ENTRY POINT
# ==========================================================
if __name__ == "__main__":
    SatelliteSim(parse_csv(CSV_FILENAME)).run()
