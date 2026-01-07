import pickle
import matplotlib.pyplot as plt
import numpy as np

def visualize_cumulative(file_path):
    with open(file_path, 'rb') as f:
        data = pickle.load(f)
    
    total_samples = len(data)
    time_steps = np.arange(total_samples)
    
    # 1. Extract Metrics
    total_packets_in_network = []
    max_queue_depth = []
    node_stress_counts = {} # How many times each node was congested

    for sample in data:
        queues = sample.get('queue_states', {})
        q_values = list(queues.values())
        
        total_packets_in_network.append(sum(q_values))
        max_queue_depth.append(max(q_values) if q_values else 0)
        
        # Track "Stress" (Nodes with > 50% capacity)
        for node, val in queues.items():
            if val > 50:
                node_stress_counts[node] = node_stress_counts.get(node, 0) + 1

    # --- Plotting ---
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10))

    # Plot 1: Network Load Over Time
    ax1.plot(time_steps, total_packets_in_network, label='Total Packets', color='blue', alpha=0.7)
    ax1.plot(time_steps, max_queue_depth, label='Peak Queue Depth', color='red', linestyle='--')
    ax1.set_title("Cumulative Network Load Trend")
    ax1.set_xlabel("Simulation Step (Sequential)")
    ax1.set_ylabel("Number of Packets")
    ax1.legend()
    ax1.grid(True, which='both', linestyle='--', alpha=0.5)

    # Plot 2: Top 10 Stressed Nodes (Cumulative)
    if node_stress_counts:
        # Sort and take top 10
        sorted_nodes = sorted(node_stress_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        nodes, frequencies = zip(*sorted_nodes)
        
        ax2.bar(nodes, frequencies, color='salmon')
        ax2.set_title("Top 10 Most Frequently Congested Nodes")
        ax2.set_ylabel("Steps Spent in Congestion (>50 pkts)")
        ax2.tick_params(axis='x', rotation=45)
    else:
        ax2.text(0.5, 0.5, "No nodes exceeded congestion threshold.", ha='center')

    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    # Ensure this matches your saved file name from the generation script
    visualize_cumulative("dataset1.pkl")