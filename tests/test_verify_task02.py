import time
import core_engine as ce

t0 = time.perf_counter()
v1 = ce.generate_text_embedding('Nintendo Switch OLED Blanca impecable con caja', '')
v2 = ce.generate_text_embedding('Consola Nintendo Switch OLED blanca en su caja original', '')
v3 = ce.generate_text_embedding('PlayStation 5 Lector de discos con mando DualSense', '')
dt = (time.perf_counter() - t0) * 1000

assert v1 is not None, "v1 is None"
assert v2 is not None, "v2 is None"
assert v3 is not None, "v3 is None"

sim_same = ce.cosine_similarity(v1, v2)
sim_diff = ce.cosine_similarity(v1, v3)

print(f"len={len(v1)} dt={dt:.3f}ms sim_same={sim_same:.3f} sim_diff={sim_diff:.3f}")
assert len(v1) == 64, f"len is {len(v1)}, expected 64"
assert dt < 5.0, f"dt is {dt}ms, expected < 5.0ms"
assert sim_same > 0.65, f"sim_same is {sim_same}, expected > 0.65"
assert sim_diff < 0.45, f"sim_diff is {sim_diff}, expected < 0.45"
print("TASK_02_SPECIFIC_OK")

