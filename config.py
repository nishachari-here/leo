class LogicalConfig:
    def __init__(self):
        # --- Network Constraints ---
        self.MAX_QUEUE_SIZE = 100        # Packets per satellite
        self.LINK_BANDWIDTH_GBPS = 10.0  # Speed of ISLs
        self.ISL_MAX_DISTANCE_KM = 5000  # Max range for a laser link
        self.isl_max_km = 5000.0         # Max ISL distance (km)
        self.iol_cone_deg = 60.0         # Inter-orbit link cone angle
        self.min_elev_deg = 5.0          # Minimum elevation for ground links
        
        # --- Packet Physics ---
        self.DEFAULT_TTL = 64
        self.PACKET_SIZE_BYTES = 1500
        
        # --- RL Environment Settings ---
        self.DATA_COLLECTION_INTERVAL = 1.0  # Seconds between samples
        
        # --- Debug & Routing ---
        self.debug = True
        self.routing_update_interval = 1.0
        self.max_path_length = 20
        self.max_link_distance_km = 5000.0
        self.max_velocity_km_s = 7.8
        
        # For compatibility with code expecting these attributes
        self.max_queue_size = self.MAX_QUEUE_SIZE