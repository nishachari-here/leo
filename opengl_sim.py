import glfw
from OpenGL.GL import *
from OpenGL.GLU import *
import numpy as np
import csv
import time
from skyfield.api import load, EarthSatellite
from datetime import timedelta

# --- CONFIGURATION ---
CSV_FILENAME = 'starlink.csv' # Ensure your CSV file is named this or change here
EARTH_RADIUS_KM = 6371.0
SCALE_FACTOR = 1.0 / EARTH_RADIUS_KM # 1.0 OpenGL Unit = 1 Earth Radius
MAX_SATS = 100 # Limit to 100 satellites
TIME_MULTIPLIER = 100.0 # 100x faster than real-time

class SatelliteSim:
    def __init__(self, satellites):
        self.satellites = satellites[:MAX_SATS] # Take only the first 100
        self.ts = load.timescale()
        
        # Simulation Timing
        self.start_real_time = time.time()
        self.start_sim_time = self.ts.now()
        self.time_multiplier = TIME_MULTIPLIER

        # Camera State
        self.distance = 3.0
        self.rotation_x = 0.0
        self.rotation_y = 0.0
        self.last_mouse_x = 0
        self.last_mouse_y = 0
        self.mouse_pressed = False

    def get_current_sim_time(self):
        """Calculates the current simulation time based on the multiplier."""
        elapsed_real_seconds = time.time() - self.start_real_time
        elapsed_sim_seconds = elapsed_real_seconds * self.time_multiplier
        
        # Update skyfield time
        return self.ts.from_datetime(self.start_sim_time.utc_datetime() + timedelta(seconds=elapsed_sim_seconds))

    def load_data(self):
        """Calculates current positions for satellites at the accelerated sim time."""
        t = self.get_current_sim_time()
        positions = []
        for sat in self.satellites:
            # Propagate to current simulation time
            geocentric = sat.at(t)
            # Get km coordinates
            x, y, z = geocentric.position.km
            # Scale to Earth-relative units
            positions.append([x * SCALE_FACTOR, z * SCALE_FACTOR, y * SCALE_FACTOR])
        return positions

    def draw_earth(self):
        """Renders a wireframe sphere representing Earth."""
        glColor3f(0.2, 0.5, 1.0) # Earth Blue
        quad = gluNewQuadric()
        gluQuadricDrawStyle(quad, GLU_LINE)
        # Radius 1.0, 32 slices/stacks
        gluSphere(quad, 1.0, 32, 32)

    def draw_satellites(self, positions):
        """Renders satellites as bright points."""
        glPointSize(4.0)
        glBegin(GL_POINTS)
        glColor3f(1.0, 1.0, 1.0) # White
        for p in positions:
            glVertex3f(p[0], p[1], p[2])
        glEnd()

    def run(self):
        # Initialize GLFW
        if not glfw.init():
            return

        window = glfw.create_window(1200, 800, f"LEO Satellite Simulation ({self.time_multiplier}x Speed)", None, None)
        if not window:
            glfw.terminate()
            return

        glfw.make_context_current(window)
        glEnable(GL_DEPTH_TEST)

        # Register Callbacks
        glfw.set_scroll_callback(window, self.scroll_callback)
        glfw.set_cursor_pos_callback(window, self.mouse_callback)
        glfw.set_mouse_button_callback(window, self.mouse_button_callback)
        glfw.set_key_callback(window, self.key_callback)

        while not glfw.window_should_close(window):
            # Clear buffers
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            glLoadIdentity()

            # Set up perspective
            width, height = glfw.get_window_size(window)
            if height == 0: height = 1
            glViewport(0, 0, width, height)
            gluPerspective(45, (width / height), 0.1, 100.0)

            # Camera transformation
            glTranslatef(0, 0, -self.distance)
            glRotatef(self.rotation_x, 1, 0, 0)
            glRotatef(self.rotation_y, 0, 1, 0)

            # Update and Draw
            positions = self.load_data()
            self.draw_earth()
            self.draw_satellites(positions)

            glfw.swap_buffers(window)
            glfw.poll_events()

        glfw.terminate()

    # --- INPUT HANDLING ---
    def scroll_callback(self, window, xoffset, yoffset):
        self.distance -= yoffset * 0.2
        self.distance = max(1.1, min(self.distance, 20.0))

    def mouse_button_callback(self, window, button, action, mods):
        if button == glfw.MOUSE_BUTTON_LEFT:
            self.mouse_pressed = (action == glfw.PRESS)

    def mouse_callback(self, window, xpos, ypos):
        if self.mouse_pressed:
            dx = xpos - self.last_mouse_x
            dy = ypos - self.last_mouse_y
            self.rotation_y += dx * 0.5
            self.rotation_x += dy * 0.5
        self.last_mouse_x = xpos
        self.last_mouse_y = ypos

    def key_callback(self, window, key, scancode, action, mods):
        # Optional: Add keys to speed up or slow down
        if action == glfw.PRESS or action == glfw.REPEAT:
            if key == glfw.KEY_UP:
                self.time_multiplier *= 2.0
                print(f"Speed: {self.time_multiplier}x")
            elif key == glfw.KEY_DOWN:
                self.time_multiplier /= 2.0
                print(f"Speed: {self.time_multiplier}x")

def parse_omm_csv(filename):
    """Parses the specific OMM CSV format provided by the user."""
    ts = load.timescale()
    satellites = []
    try:
        with open(filename, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                sat = EarthSatellite.from_omm(ts, row)
                satellites.append(sat)
    except FileNotFoundError:
        print(f"Error: {filename} not found. Please create the file with your data.")
        return []
    return satellites

if __name__ == "__main__":
    # 1. Load data from your local CSV
    sats = parse_omm_csv(CSV_FILENAME)
    
    if sats:
        print(f"Loaded {len(sats)} satellites. Limiting to {MAX_SATS}.")
        # 2. Start the OpenGL App
        sim = SatelliteSim(sats)
        sim.run()
    else:
        print("No satellites to simulate.")