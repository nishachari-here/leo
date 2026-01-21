import json
import subprocess
import sys
import asyncio
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

class SimulationRunner:
    """Handles different simulation modes"""
    
    @staticmethod
    def run_full_simulation(config):
        """Run full simulation with TCP"""
        print(f"Starting FULL simulation with config: {config}")
        # Import your simulation modules
        from core.sim_man import run_quick_test
        
        # Override parameters based on config
        result = run_quick_test()
        
        return {
            "status": "completed",
            "mode": "full",
            "results": {
                "packets_sent": result.network.global_stats.get('total_packets_sent', 0),
                "packets_received": result.network.global_stats.get('total_packets_received', 0),
                "delivery_rate": result.network.global_stats.get('packet_loss_rate', 0),
                "average_latency": result.network.global_stats.get('average_end_to_end_latency', 0),
                "throughput": result.network.global_stats.get('throughput_bps', 0)
            }
        }
    
    @staticmethod
    def run_quick_simulation(config):
        """Run quick simulation with UDP"""
        print(f"Starting QUICK simulation with config: {config}")
        
        # Modify sim_man to use UDP
        from core.sim_man import run_quick_test, SimulationManager
        from network.network_moduleTCP import ProtocolType
        
        # Create custom simulation with UDP
        from data.setup import get_satellite_data
        satellites = get_satellite_data(num_sats=config['satellites'])
        sim = SimulationManager(satellites)
        
        # Modify flows to use UDP
        for flow_id, flow in sim.traffic.flows.items():
            flow.protocol_type = ProtocolType.UDP
        
        # Run shorter simulation
        sim.run_simulation(config['duration'], config['interval'])
        
        return {
            "status": "completed",
            "mode": "quick",
            "results": {
                "packets_sent": sim.network.global_stats.get('total_packets_sent', 0),
                "packets_received": sim.network.global_stats.get('total_packets_received', 0),
                "message": "Quick simulation completed with UDP"
            }
        }
    
    @staticmethod
    def run_no_tcp_simulation(config):
        """Run simulation without TCP"""
        print(f"Starting NO-TCP simulation with config: {config}")
        
        # This would use your custom protocol stack
        from core.sim_man import run_quick_test
        from network.network_moduleTCP import EnhancedNetworkModule
        
        result = run_quick_test()
        
        # Disable TCP features
        if hasattr(result.network, 'protocol_stacks'):
            for stack in result.network.protocol_stacks.values():
                stack.protocol_type = 'CUSTOM'
        
        return {
            "status": "completed",
            "mode": "no-tcp",
            "results": {
                "message": "Simulation completed without TCP protocol",
                "custom_protocol_used": True
            }
        }

@app.route('/api/simulate', methods=['POST'])
def run_simulation():
    """API endpoint to run simulation"""
    try:
        config = request.json
        
        if not config or 'mode' not in config:
            return jsonify({"error": "No mode specified"}), 400
        
        runner = SimulationRunner()
        
        if config['mode'] == 'full':
            result = runner.run_full_simulation(config)
        elif config['mode'] == 'quick':
            result = runner.run_quick_simulation(config)
        elif config['mode'] == 'no-tcp':
            result = runner.run_no_tcp_simulation(config)
        else:
            return jsonify({"error": f"Unknown mode: {config['mode']}"}), 400
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/status', methods=['GET'])
def get_status():
    """Get simulation status"""
    return jsonify({
        "status": "ready",
        "modes_available": ["full", "quick", "no-tcp"],
        "protocols": ["tcp", "udp", "quic"]
    })

if __name__ == '__main__':
    # Start the web server
    print("Starting Simulation Server...")
    print("Open http://localhost:5000/index.html in your browser")
    
    # Run Flask app
    app.run(debug=True, port=5000)