import json
import random

random.seed(42)  # fixed seed per Step 11's reproducibility protocol

with open("/home/rit/Documents/Shrilaxmi/Data_Engg/datasets/bird/dev_20240627/dev.json") as f:
    data = json.load(f)

sample = random.sample(data, 25)

with open("pilot_sample.json", "w") as f:
    json.dump(sample, f, indent=2)

print(f"Sampled {len(sample)} examples from {len(data)} total.")
for i, ex in enumerate(sample):
    print(f"{i+1}. db_id={ex.get('db_id')} question={ex.get('question')[:80]}...")
