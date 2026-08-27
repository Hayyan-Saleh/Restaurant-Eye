import json
from pathlib import Path
from collections import Counter, defaultdict
import random

filepath = Path(__file__).parent / '../../data/debug/classification/samples/raw_poses.json'
filepath = filepath.resolve()

with open(filepath) as f:
    data = json.load(f)
    
data = [item for item in data if item['label'] != '']

with open(filepath, 'w') as f:
    json.dump(data, f, indent=2)

label_counts = Counter(item['label'] for item in data)
total = len(data)

for label, count in label_counts.items():
    print(f"{label}: {count} ({count/total*100:.1f}%)")
print(f"Total: {total}")
