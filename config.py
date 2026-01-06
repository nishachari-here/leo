class LogicalConfig:
    def __init__(self):
        # --- Network Constraints ---
        self.MAX_QUEUE_SIZE = 100        # Packets per satellite
        self.LINK_BANDWIDTH_GBPS = 10.0  # Speed of ISLs
        self.ISL_MAX_DISTANCE_KM = 5000  # Max range for a laser link
        self.isl_max_km: float = 5000
        self.iol_cone_deg: float = 60
        self.min_elev_deg: float = 5
        # --- Packet Physics ---
        self.DEFAULT_TTL = 64
        self.PACKET_SIZE_BYTES = 1500
        
        # --- RL Environment Settings ---
        self.DATA_COLLECTION_INTERVAL = 1.0 # Seconds between samples