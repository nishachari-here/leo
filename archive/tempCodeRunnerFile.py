    distance_km = self.get_distance(src_name, dst_name, pos)
        # Light speed in vacuum is ~299,792 km/s
        prop_delay = distance_km / 299792.458 
        
        # 2. Bandwidth-based Transmission Delay
        # bandwidth_bps = 1e9 (1 Gbps)
        trans_delay = packet_size_bits / 1_000_000_000.0
        
        return prop_delay + trans_delay