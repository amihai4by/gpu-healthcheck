#!/usr/bin/env python3
"""
gpu_healthcheck.py — a free, dependency-light GPU cluster health-check.

Reads NVIDIA GPU health from either:
  (A) nvidia-smi on the local box, or
  (B) a Prometheus/DCGM-exporter endpoint (whole fleet),
then prints a clean per-GPU report + a fleet summary that estimates how much
money you're burning every month on idle-but-allocated GPUs.

Stdlib only. No pip install. Works on Python 3.7+.

  # Local box (uses nvidia-smi)
  python3 gpu_healthcheck.py

  # Whole fleet via Prometheus scraping dcgm-exporter
  python3 gpu_healthcheck.py --prometheus http://localhost:9090

  # Set your real GPU hourly price for accurate $ waste
  python3 gpu_healthcheck.py --cost-per-hour 2.50

  # Machine-readable output for cron/CI
  python3 gpu_healthcheck.py --json

MIT-licensed. Not affiliated with NVIDIA. DCGM/Grafana are trademarks of their owners.
"""

import argparse
import json
import subprocess
import sys
import urllib.parse
import urllib.request

PACK_URL = "https://probizgen.gumroad.com/l/xxnqbt"

# Rough on-demand hourly prices (USD) as a default hint. Override with --cost-per-hour.
# Idle time is billed the same as busy time — that's the whole problem.
PRICE_HINTS = {
    "h100": 3.00, "h200": 3.50, "a100": 1.80, "l40": 1.10, "l4": 0.60,
    "a10": 0.75, "v100": 0.90, "t4": 0.35, "a6000": 0.80, "rtx": 0.50,
}
DEFAULT_COST_PER_HOUR = 2.00  # sane default if we can't guess the SKU
HOURS_PER_MONTH = 730

# A GPU below this utilization (%) is treated as effectively idle.
IDLE_UTIL_THRESHOLD = 5.0
# Sustained temps above this (°C) mean you're likely thermal-throttling.
HOT_TEMP_THRESHOLD = 87.0
# Framebuffer above this fraction risks OOM-killed jobs.
VRAM_PRESSURE_THRESHOLD = 0.95


# --------------------------------------------------------------------------- #
# Collectors
# --------------------------------------------------------------------------- #
def collect_local():
    """Return a list of GPU dicts by shelling out to nvidia-smi."""
    query = "index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw"
    cmd = [
        "nvidia-smi",
        f"--query-gpu={query}",
        "--format=csv,noheader,nounits",
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
    except FileNotFoundError:
        sys.exit("error: nvidia-smi not found. Run this on a GPU box, or use "
                 "--prometheus to read a remote fleet.")
    except subprocess.CalledProcessError as e:
        sys.exit(f"error: nvidia-smi failed:\n{e.output}")

    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue
        idx, name, util, mem_used, mem_total, temp, power = parts

        def num(x):
            try:
                return float(x)
            except ValueError:
                return 0.0  # e.g. "[N/A]" on some MIG / vGPU setups

        gpus.append({
            "host": "localhost",
            "gpu": idx,
            "name": name,
            "util": num(util),
            "mem_used_mib": num(mem_used),
            "mem_total_mib": num(mem_total),
            "mem_frac": (num(mem_used) / num(mem_total)) if num(mem_total) else 0.0,
            "temp": num(temp),
            "power_w": num(power),
        })
    return gpus


def _prom_query(base_url, expr):
    """Run one instant PromQL query, return {(instance,gpu): value}."""
    url = base_url.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.load(resp)
    except Exception as e:  # noqa: BLE001 - surface any transport/parse error cleanly
        sys.exit(f"error: could not query Prometheus at {base_url}: {e}")
    if data.get("status") != "success":
        sys.exit(f"error: Prometheus returned: {data.get('error', data)}")

    out = {}
    for series in data["data"]["result"]:
        m = series["metric"]
        # dcgm-exporter labels: gpu, Hostname, instance. Keep it robust to either.
        key = (m.get("Hostname") or m.get("instance") or "?", m.get("gpu", "?"))
        try:
            out[key] = float(series["value"][1])
        except (KeyError, ValueError, IndexError):
            continue
    return out


def collect_prometheus(base_url):
    """Return a list of GPU dicts by querying dcgm-exporter metrics via Prometheus."""
    util = _prom_query(base_url, "DCGM_FI_DEV_GPU_UTIL")
    if not util:
        sys.exit("error: no DCGM_FI_DEV_GPU_UTIL series found. Is dcgm-exporter "
                 "deployed and scraped? (The $39 pack ships the exporter + scrape config.)")
    fb_used = _prom_query(base_url, "DCGM_FI_DEV_FB_USED")   # MiB
    fb_free = _prom_query(base_url, "DCGM_FI_DEV_FB_FREE")   # MiB
    temp = _prom_query(base_url, "DCGM_FI_DEV_GPU_TEMP")
    power = _prom_query(base_url, "DCGM_FI_DEV_POWER_USAGE")  # Watts
    # Prefer a 1h average for idle detection when available (snapshot lies on bursty jobs).
    util_avg = _prom_query(base_url, "avg_over_time(DCGM_FI_DEV_GPU_UTIL[1h])")

    gpus = []
    for key, u in sorted(util.items()):
        host, gpu = key
        used = fb_used.get(key, 0.0)
        free = fb_free.get(key, 0.0)
        total = used + free
        gpus.append({
            "host": host,
            "gpu": gpu,
            "name": "nvidia-gpu",
            "util": util_avg.get(key, u),  # 1h avg if we have it, else instant
            "util_instant": u,
            "mem_used_mib": used,
            "mem_total_mib": total,
            "mem_frac": (used / total) if total else 0.0,
            "temp": temp.get(key, 0.0),
            "power_w": power.get(key, 0.0),
        })
    return gpus


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
def guess_price(name):
    n = (name or "").lower()
    for token, price in PRICE_HINTS.items():
        if token in n:
            return price
    return None


def analyze(gpus, cost_per_hour):
    for g in gpus:
        price = cost_per_hour if cost_per_hour else (guess_price(g["name"]) or DEFAULT_COST_PER_HOUR)
        g["price_per_hour"] = price
        g["idle"] = g["util"] < IDLE_UTIL_THRESHOLD
        g["hot"] = g["temp"] >= HOT_TEMP_THRESHOLD
        g["vram_pressure"] = g["mem_frac"] >= VRAM_PRESSURE_THRESHOLD
        # Money left on the table each month if this GPU stays this idle.
        # Idle GPUs are billed in full: wasted = (1 - util) * price * hours.
        g["monthly_waste"] = round((1 - g["util"] / 100.0) * price * HOURS_PER_MONTH, 2) \
            if g["idle"] else 0.0

    fleet = {
        "gpu_count": len(gpus),
        "idle_count": sum(1 for g in gpus if g["idle"]),
        "hot_count": sum(1 for g in gpus if g["hot"]),
        "vram_pressure_count": sum(1 for g in gpus if g["vram_pressure"]),
        "avg_util": round(sum(g["util"] for g in gpus) / len(gpus), 1) if gpus else 0.0,
        "monthly_waste": round(sum(g["monthly_waste"] for g in gpus), 2),
    }
    fleet["annual_waste"] = round(fleet["monthly_waste"] * 12, 2)
    return fleet


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _flag(g):
    tags = []
    if g["idle"]:
        tags.append("IDLE")
    if g["hot"]:
        tags.append("HOT")
    if g["vram_pressure"]:
        tags.append("VRAM")
    return " ".join(tags) if tags else "ok"


def print_report(gpus, fleet):
    print()
    print("=" * 72)
    print("  GPU CLUSTER HEALTH-CHECK")
    print("=" * 72)
    if not gpus:
        print("  No GPUs found.")
        return

    header = f"  {'HOST':<16}{'GPU':<4}{'UTIL%':>7}{'VRAM%':>7}{'TEMP°C':>8}{'WATTS':>8}   STATUS"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for g in gpus:
        print(f"  {str(g['host'])[:15]:<16}{str(g['gpu']):<4}"
              f"{g['util']:>7.0f}{g['mem_frac'] * 100:>7.0f}"
              f"{g['temp']:>8.0f}{g['power_w']:>8.0f}   {_flag(g)}")

    print()
    print("  " + "-" * 70)
    print(f"  Fleet: {fleet['gpu_count']} GPU(s) | avg util {fleet['avg_util']}% | "
          f"{fleet['idle_count']} idle | {fleet['hot_count']} hot | "
          f"{fleet['vram_pressure_count']} VRAM-pressured")
    print()
    print(f"  💸 Estimated wasted spend on idle GPUs:")
    print(f"       ${fleet['monthly_waste']:,.0f} / month   (~${fleet['annual_waste']:,.0f} / year)")
    print(f"     Based on ${gpus[0]['price_per_hour']:.2f}/GPU-hr and {HOURS_PER_MONTH} hrs/mo. "
          f"Set --cost-per-hour for your real price.")
    print("  " + "-" * 70)

    # Contextual, honest verdict.
    if fleet["idle_count"]:
        print(f"\n  ⚠  {fleet['idle_count']} GPU(s) are allocated but sitting <{IDLE_UTIL_THRESHOLD:.0f}%% "
              f"utilized. That's money on fire.".replace("%%", "%"))
    if fleet["hot_count"]:
        print(f"  ⚠  {fleet['hot_count']} GPU(s) are ≥{HOT_TEMP_THRESHOLD:.0f}°C — likely "
              f"thermal-throttling (you pay for compute you don't get).")
    if not fleet["idle_count"] and not fleet["hot_count"] and not fleet["vram_pressure_count"]:
        print("\n  ✅ No idle, hot, or VRAM-pressured GPUs in this snapshot. Nice fleet.")

    # Soft, artifact-first CTA (reciprocity — you already got the useful bit above).
    print()
    print("  " + "─" * 70)
    print("  This is a one-shot snapshot. To CATCH these problems automatically —")
    print("  XID/ECC hardware failures, thermal throttling, VRAM pressure, and")
    print("  idle-spend — before they kill a training run, you want continuous")
    print("  alerting on the whole fleet.")
    print()
    print("  The GPU Monitoring Starter Pack does exactly that in ~15 min:")
    print("    • dcgm-exporter DaemonSet (30+ NVIDIA metrics)")
    print("    • 8 tuned Prometheus alert rules (health · perf · cost)")
    print("    • a clean Grafana fleet dashboard")
    print(f"    → {PACK_URL}")
    print("  " + "─" * 70)
    print()


def main():
    ap = argparse.ArgumentParser(
        description="Free GPU cluster health-check: idle-waste $, temp, VRAM, from "
                    "nvidia-smi or a Prometheus/DCGM endpoint.")
    ap.add_argument("--prometheus", metavar="URL",
                    help="Prometheus base URL scraping dcgm-exporter, e.g. http://localhost:9090. "
                         "Omit to read the local box via nvidia-smi.")
    ap.add_argument("--cost-per-hour", type=float, default=0.0,
                    help="Your GPU on-demand price in USD/hr (e.g. 2.50). "
                         "Default: guessed from GPU model, else $%.2f." % DEFAULT_COST_PER_HOUR)
    ap.add_argument("--json", action="store_true",
                    help="Emit machine-readable JSON instead of the report (for cron/CI).")
    args = ap.parse_args()

    gpus = collect_prometheus(args.prometheus) if args.prometheus else collect_local()
    fleet = analyze(gpus, args.cost_per_hour)

    if args.json:
        print(json.dumps({"fleet": fleet, "gpus": gpus, "pack": PACK_URL}, indent=2))
    else:
        print_report(gpus, fleet)


if __name__ == "__main__":
    main()
