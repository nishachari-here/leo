import glfw
from OpenGL.GL import *
from OpenGL.GLU import *
import numpy as np
import csv
import time
import math
from skyfield.api import load, EarthSatellite, wgs84
from datetime import timedelta
from collections import defaultdict

# ==========================================================
# CONFIGURATION
# ==========================================================
CSV_FILENAME = 'starlink.csv'
EARTH_RADIUS_KM = 6371.0
SCALE = 1.0 / EARTH_RADIUS_KM
MAX_SATS = 100

TIME_MULTIPLIER = 100.0

ISL_MAX_KM = 2500
IOL_CONE_DEG = 30
MIN_ELEV_DEG = 15

GROUND_STATIONS = [
    ("GS_INDIA", 28.6139, 77.2090, 0),
]

# ==========================================================
# PHYSICAL HELPER: EARTH OCCLUSION CHECK
# ==========================================================
def link_clear_of_earth(p1, p2):
    d = p2 - p1
    f = p1
    a = np.dot(d, d)
    b = 2 * np.dot(f, d)
    c = np.dot(f, f) - EARTH_RADIUS_KM**2
    disc = b*b - 4*a*c
    if disc < 0:
        return True
    s = math.sqrt(disc)
    t1 = (-b - s) / (2*a)
    t2 = (-b + s) / (2*a)
    return not ((0 <= t1 <= 1) or (0 <= t2 <= 1))

# ==========================================================
# DATA STRUCTURE
# ==========================================================
class SatelliteNode:
    def __init__(self, sat, orbit_id, index):
        self.sat = sat
        self.orbit_id = orbit_id
        self.index = index

# ==========================================================
############################
# LOGICAL MODULE START
############################
# ==========================================================
class LogicalTopology:
    """
    Maintains ISL, IOL, and UDL relationships
    Dynamically recomputed every timestep
    """

    def __init__(self, sats):
        self.nodes = self._group_orbits(sats)

    def _group_orbits(self, sats):
        groups = defaultdict(list)
        for sat in sats:
            inc = round(sat.model.inclo, 1)
            raan = round(sat.model.nodeo, 1)
            groups[(inc, raan)].append(sat)

        nodes = []
        orbit_id = 0
        for group in groups.values():
            group.sort(key=lambda s: s.model.mo)
            for i, sat in enumerate(group):
                nodes.append(SatelliteNode(sat, orbit_id, i))
            orbit_id += 1
        return nodes

    def compute(self, t):
        pos = {n: n.sat.at(t).position.km for n in self.nodes}
        orbits = defaultdict(list)
        for n in self.nodes:
            orbits[n.orbit_id].append(n)

        isl_same, isl_adj, iol, udl = [], [], [], []

        # ---- ISL: SAME ORBIT ----
        for orbit_nodes in orbits.values():
            N = len(orbit_nodes)
            for i, n in enumerate(orbit_nodes):
                for nb in [orbit_nodes[(i-1)%N], orbit_nodes[(i+1)%N]]:
                    if link_clear_of_earth(pos[n], pos[nb]):
                        isl_same.append((n, nb))

        # ---- ISL: ADJACENT ORBIT ----
        for oid in orbits:
            for adj in [oid-1, oid+1]:
                if adj in orbits:
                    for n in orbits[oid]:
                        nearest = min(
                            orbits[adj],
                            key=lambda m: np.linalg.norm(pos[n] - pos[m])
                        )
                        if (np.linalg.norm(pos[n] - pos[nearest]) <= ISL_MAX_KM and
                            link_clear_of_earth(pos[n], pos[nearest])):
                            isl_adj.append((n, nearest))

        # ---- IOL: CONE MODEL ----
        for a in self.nodes:
            for b in self.nodes:
                if a == b:
                    continue
                v = pos[b] - pos[a]
                nadir = -pos[a]
                angle = math.degrees(
                    math.acos(
                        np.dot(v, nadir) /
                        (np.linalg.norm(v) * np.linalg.norm(nadir))
                    )
                )
                if angle <= IOL_CONE_DEG and link_clear_of_earth(pos[a], pos[b]):
                    iol.append((a, b))

        # ---- UDL: SAT ↔ GROUND ----
        for n in self.nodes:
            for name, lat, lon, h in GROUND_STATIONS:
                gs = wgs84.latlon(lat, lon, elevation_m=h)
                alt, _, _ = (n.sat - gs).at(t).altaz()
                if alt.degrees >= MIN_ELEV_DEG:
                    gs_pos = gs.at(t).position.km
                    if link_clear_of_earth(pos[n], gs_pos):
                        udl.append((n, name))

        return pos, isl_same, isl_adj, iol, udl

############################
# LOGICAL MODULE END
############################
# ==========================================================

# ==========================================================
# VISUAL SIMULATION
# ==========================================================
class SatelliteSim:
    def __init__(self, sats):
        self.ts = load.timescale()
        self.start_real = time.time()
        self.start_sim = self.ts.now()

        self.time_multiplier = TIME_MULTIPLIER
        self.topology = LogicalTopology(sats[:MAX_SATS])

        self.distance = 3.0
        self.rx = 0
        self.ry = 0
        self.last_x = 0
        self.last_y = 0
        self.drag = False

    def sim_time(self):
        dt = (time.time() - self.start_real) * self.time_multiplier
        return self.ts.from_datetime(
            self.start_sim.utc_datetime() + timedelta(seconds=dt)
        )

    def draw_earth(self):
        glColor3f(0.2, 0.5, 1.0)
        q = gluNewQuadric()
        gluQuadricDrawStyle(q, GLU_LINE)
        gluSphere(q, 1, 32, 32)

    def draw_satellites(self, pos):
        glPointSize(4)
        glBegin(GL_POINTS)
        glColor3f(1, 1, 1)
        for p in pos.values():
            p = p * SCALE
            glVertex3f(p[0], p[2], p[1])
        glEnd()

    def draw_links(self, links, color):
        glColor3f(*color)
        glBegin(GL_LINES)
        for a, b in links:
            pa = a.sat.at(self.t).position.km * SCALE
            pb = b.sat.at(self.t).position.km * SCALE
            glVertex3f(pa[0], pa[2], pa[1])
            glVertex3f(pb[0], pb[2], pb[1])
        glEnd()

    def draw_ground_links(self, udl_links, color):
        glColor3f(*color)
        glBegin(GL_LINES)
        gs_dict = {name: wgs84.latlon(lat, lon, elevation_m=h) 
                for name, lat, lon, h in GROUND_STATIONS}
        for sat_node, gs_name in udl_links:
            pa = sat_node.sat.at(self.t).position.km * SCALE
            gs_pos = gs_dict[gs_name].at(self.t).position.km * SCALE
            glVertex3f(pa[0], pa[2], pa[1])
            glVertex3f(gs_pos[0], gs_pos[2], gs_pos[1])
        glEnd()

    def draw_ground(self):
        glPointSize(8)
        glBegin(GL_POINTS)
        glColor3f(1, 1, 0)
        glVertex3f(0, 0, 0)
        glEnd()

    # ---------------- INPUT HANDLING ----------------
    def scroll(self, win, xoff, yoff):
        self.distance = max(1.2, self.distance - yoff * 0.2)

    def mouse_btn(self, win, button, action, mods):
        if button == glfw.MOUSE_BUTTON_LEFT:
            self.drag = action == glfw.PRESS

    def mouse_move(self, win, x, y):
        if self.drag:
            self.ry += (x - self.last_x) * 0.4
            self.rx += (y - self.last_y) * 0.4
        self.last_x, self.last_y = x, y

    def key(self, win, key, sc, action, mods):
        if action in (glfw.PRESS, glfw.REPEAT):
            if key == glfw.KEY_UP:
                self.time_multiplier *= 2
                print("Speed:", self.time_multiplier)
            elif key == glfw.KEY_DOWN:
                self.time_multiplier /= 2
                print("Speed:", self.time_multiplier)

    # ---------------- MAIN LOOP ----------------
    def run(self):
        glfw.init()
        win = glfw.create_window(1200, 800, "Earth-Safe Logical Topology", None, None)
        glfw.make_context_current(win)
        glEnable(GL_DEPTH_TEST)

        glfw.set_scroll_callback(win, self.scroll)
        glfw.set_mouse_button_callback(win, self.mouse_btn)
        glfw.set_cursor_pos_callback(win, self.mouse_move)
        glfw.set_key_callback(win, self.key)

        while not glfw.window_should_close(win):
            self.t = self.sim_time()
            pos, isl1, isl2, iol, udl = self.topology.compute(self.t)

            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            glLoadIdentity()
            gluPerspective(45, 1.5, 0.1, 100)

            glTranslatef(0, 0, -self.distance)
            glRotatef(self.rx, 1, 0, 0)
            glRotatef(self.ry, 0, 1, 0)

            self.draw_earth()
            self.draw_ground()
            self.draw_satellites(pos)

            self.draw_links(isl1, (1, 0, 0))       # Red
            self.draw_links(isl2, (1, 0.5, 0))     # Orange
            self.draw_links(iol, (0.7, 0, 1))      # Purple
            self.draw_ground_links(udl, (0, 1, 0)) # Green

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
