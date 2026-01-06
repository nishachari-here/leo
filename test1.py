import pickle

with open("test_data.pkl", "rb") as f:
    data = pickle.load(f)

valid_samples = 0
missing_samples = 0
peak_load = 0

for i, sample in enumerate(data):
    if 'queue_states' in sample:
        valid_samples += 1
        current_load = sum(sample['queue_states'].values())
        peak_load = max(peak_load, current_load)
    else:
        missing_samples += 1

print(f"Analysis Results:")
print(f"Total entries in file: {len(data)}")
print(f"Samples with Queue Data: {valid_samples}")
print(f"Samples missing Queue Data: {missing_samples}")
print(f"Highest Congestion Point: {peak_load} packets")

for i, sample in enumerate(data[:5]):
    if 'queue_states' in sample:
        print(f"Sample {i}: {sample['queue_states']}")