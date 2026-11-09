import json

with open("config.json", encoding="utf-8") as f:
    cfg = json.load(f)
targets = cfg.get("targets", cfg.get("searches", []))
print("Targets OK:", len(targets))
