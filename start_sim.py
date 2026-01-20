#!/usr/bin/env python3
import webbrowser
import threading
import time
from simulation_runner import app

def open_browser():
    time.sleep(2)  # Wait for server to start
    webbrowser.open('http://localhost:5000/index.html')

if __name__ == '__main__':
    # Start browser in background
    threading.Thread(target=open_browser).start()
    
    # Start Flask server
    app.run(debug=True, port=5000, use_reloader=False)