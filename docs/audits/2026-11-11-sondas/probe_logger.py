# Sonda: pérdida de líneas de metrics.jsonl cuando varios procesos comparten el RotatingFileHandler.
# Misma capacidad de rotación en ambos casos (4 ficheros x 150 KB > 2.000 líneas), así que con 1 proceso no debe perderse nada.
import os, sys, shutil, multiprocessing as mp
sys.path.insert(0, "/src")

BASE = os.path.join(os.environ.get("LOG_DIR", "/work/logs"), "probe_logger")


def worker(d, tag, n):
    import logger
    m = logger.JSONMetricsLogger(os.path.join(d, "metrics.jsonl"), max_bytes=150000, backup_count=3)
    for i in range(n):
        m.emit("evt", service=tag, i=i, pad="x" * 80)


def run(nproc, per):
    d = os.path.join(BASE, f"n{nproc}")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    ps = [mp.Process(target=worker, args=(d, f"p{k}", per)) for k in range(nproc)]
    [p.start() for p in ps]
    [p.join() for p in ps]
    kept = sum(sum(1 for _ in open(os.path.join(d, f), encoding="utf-8")) for f in os.listdir(d))
    total = nproc * per
    print(f"  {nproc} proceso(s): escritas={total} conservadas={kept} perdidas={total - kept} ({(total - kept) / total:.1%})")


if __name__ == "__main__":
    run(1, 2000)   # referencia
    run(5, 400)    # 5 procesos, como los 5 contenedores que comparten data/logs/metrics.jsonl
