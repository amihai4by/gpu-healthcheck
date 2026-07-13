# gpu-healthcheck — free NVIDIA GPU cluster health & idle-cost checker 🖥️💸

A tiny, **dependency-free** Python script that tells you — in one command — how healthy your
NVIDIA GPUs are and **how much money you're burning on idle GPUs every month.**

Point it at a **local box** (`nvidia-smi`) or your whole **fleet** (Prometheus + DCGM exporter).
No pip install. No agent. No signup. Just a script.

```bash
# local GPU box
python3 gpu_healthcheck.py

# whole fleet, via Prometheus scraping dcgm-exporter
python3 gpu_healthcheck.py --prometheus http://localhost:9090 --cost-per-hour 2.50
```

```
========================================================================
  GPU CLUSTER HEALTH-CHECK
========================================================================
  HOST            GPU   UTIL%  VRAM%  TEMP°C   WATTS   STATUS
  -----------------------------------------------------------
  gpu-node-1      0         2      5      45      70   IDLE
  gpu-node-1      1        88     95      89     650   HOT VRAM
  gpu-node-2      0         0      2      38      45   IDLE

  Fleet: 3 GPU(s) | avg util 30.0% | 2 idle | 1 hot | 1 VRAM-pressured

  💸 Estimated wasted spend on idle GPUs:
       $3,460 / month   (~$41,522 / year)
```

## Why this exists

Most GPU fleets run at **<30% average utilization** and quietly waste **60–70% of their GPU
budget on idle-but-allocated cards** — while a single H100 costs $25–40k and bills the same
whether it's training or sitting at 2%. `nvidia-smi` shows you *a* GPU *right now*. This shows
you the **fleet**, flags the three failures that actually cost you money, and puts a **dollar
figure** on the idle waste so you can go reclaim it.

It checks for:

- **Idle-but-allocated GPUs** — the #1 source of wasted GPU spend, with a monthly $ estimate.
- **Thermal throttling** — GPUs ≥87°C where you pay for compute you don't get.
- **VRAM pressure** — framebuffer ≥95%, the setup for OOM-killed training jobs.

## Install

Nothing to install. You need Python 3.7+ and either `nvidia-smi` (local) or a Prometheus
endpoint scraping [`dcgm-exporter`](https://github.com/NVIDIA/dcgm-exporter) (fleet).

```bash
curl -O https://raw.githubusercontent.com/amihai4by/gpu-healthcheck/main/gpu_healthcheck.py   # or just copy the file
python3 gpu_healthcheck.py
```

## Usage

| Command | What it does |
|---|---|
| `python3 gpu_healthcheck.py` | Health-check the local box via `nvidia-smi`. |
| `python3 gpu_healthcheck.py --prometheus http://PROM:9090` | Health-check the whole fleet via DCGM metrics. |
| `python3 gpu_healthcheck.py --cost-per-hour 2.50` | Use your real GPU hourly price for accurate $ waste. |
| `python3 gpu_healthcheck.py --json` | Machine-readable output for cron/CI/Slack. |

The fleet mode reads standard `dcgm-exporter` metrics: `DCGM_FI_DEV_GPU_UTIL`,
`DCGM_FI_DEV_FB_USED`/`FREE`, `DCGM_FI_DEV_GPU_TEMP`, `DCGM_FI_DEV_POWER_USAGE`. It prefers a
1-hour average for idle detection so bursty jobs don't look idle.

## From spot-check → always-on monitoring

This script is a **snapshot** — great for a one-off audit or a weekly cron. But an idle GPU at
2pm on Tuesday and a snapshot at 9am tell you nothing about the XID error that killed your
training run at 3am, or the GPU that's been quietly throttling for a week.

To **catch** these automatically across a Kubernetes GPU fleet — hardware failures (XID / ECC),
dead nodes, thermal throttling, VRAM pressure, and idle-spend — you want continuous DCGM →
Prometheus → Grafana alerting. Wiring that by hand is a 2–3 day yak-shave and everyone builds it
slightly wrong.

👉 **[GPU Monitoring Starter Pack for Kubernetes](https://probizgen.gumroad.com/l/xxnqbt)** —
the same DCGM → Prometheus → Grafana stack this script reads from, done right, in ~15 minutes:
a `dcgm-exporter` DaemonSet (30+ metrics), **8 tuned alert rules** across health / performance /
cost, and a clean Grafana fleet dashboard. Copy-paste ready. Free updates for life. **$39** — it
pays for itself the first time it catches a dying GPU before it kills a run, or flags a rack of
idle GPUs like the ones this script just found.

> Free companion tool → paid pack. Use the script forever; grab the pack when you want the
> alerting to run without you.

## Keywords

NVIDIA GPU monitoring · DCGM · dcgm-exporter · Prometheus · Grafana · Kubernetes GPU ·
MLOps · GPU observability · idle GPU cost · GPU utilization · GPU FinOps · thermal throttling ·
VRAM / framebuffer pressure · XID errors · ECC errors · H100 / A100 / L4 cost · GPU SRE.

## License & notes

MIT. Not affiliated with NVIDIA. DCGM and Grafana are trademarks of their respective owners.
Read-only — the script never changes anything on your cluster.
