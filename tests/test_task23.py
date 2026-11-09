import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core_engine
import sniper

# Test positional arguments & float behavior
c1 = sniper.estimate_landed_cost(200, False, "standard")
assert c1 == 213.49, f"Expected 213.49, got {c1}"

c2 = sniper.estimate_landed_cost(200, False, "heavy")
assert c2 == 216.95, f"Expected 216.95, got {c2}"

c3 = sniper.estimate_landed_cost(200, True, "heavy")
assert c3 == 219.95, f"Expected 219.95, got {c3}"

# Test unpack compatibility in sniper.py
cost, fees = sniper.estimate_landed_cost(200, False, False, shipping_tier="heavy")
assert cost == 216.95, f"Expected cost 216.95, got {cost}"
assert round(fees, 2) == 16.95, f"Expected fees 16.95, got {fees}"

# Test local
cost_loc, fees_loc = sniper.estimate_landed_cost(200, is_local=True)
assert cost_loc == 200.0, f"Expected 200.0, got {cost_loc}"
assert fees_loc == 0.0, f"Expected 0.0, got {fees_loc}"

# Test core_engine export
c_core = core_engine.estimate_landed_cost(200, False, "standard")
assert c_core == 213.49, f"Expected 213.49 from core, got {c_core}"

print("All Task 2.3 empirical tests PASSED!")

