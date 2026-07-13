# GPU Alert Runbook

What each alert means and exactly what to do when it fires. Linked from every
rule's `runbook_url` so on-call has the response one click away.

Severity key: **critical** = page now · **warning** = look today · **info** = FinOps review.

---

## GPUXidError
**Critical.** An NVIDIA XID hardware/driver error was reported (`DCGM_FI_DEV_XID_ERRORS`).
XIDs frequently precede a dying GPU or a crashed job.
**Do:**
1. `nvidia-smi -q | grep -i xid` on the node to read the XID code.
2. Look up the code (e.g. 79 = GPU fell off the bus, 48/63/64 = ECC/row-remap, 13 = app fault).
3. Hardware XIDs (79, 48, 62, 63, 64, 74, 92) → **cordon + drain the node**, open a vendor/RMA ticket.
4. App XIDs (13, 31, 43, 45) → usually the workload; check the job logs, not the GPU.

## GPUDoubleBitECCError
**Critical.** Uncorrectable (double-bit) ECC error — corrupted memory, almost always failing hardware.
**Do:** cordon + drain the node, `nvidia-smi -q -d ROW_REMAPPER,ECC`, RMA the card. Do **not** keep training on it — results are silently corrupt.

## GPUUnavailable
**Critical.** dcgm-exporter stopped reporting for a node for 3m — the node, driver, or exporter died. Job placement will silently fail here.
**Do:**
1. `kubectl -n monitoring get pods -l app=dcgm-exporter -o wide` — is the pod on that node Running?
2. `nvidia-smi` on the node — driver alive? If it hangs → driver/GPU crash, reboot the node.
3. If the exporter pod is crashlooping, `kubectl logs` it (usually a driver/toolkit mismatch).

## GPUThermalThrottle
**Warning.** Sustained temp > 87°C → the GPU is throttling; you're paying for compute you aren't getting.
**Do:** check datacenter/chassis airflow and fan curves; verify the card isn't dust-blocked; consider a power/clock cap (`nvidia-smi -pl`) as a stopgap. Persistent throttling on one card in a healthy rack → suspect that card's cooling.

## GPUSingleBitECCSpike
**Warning.** Correctable ECC errors climbing fast (>500/30m) — often precedes an uncorrectable failure.
**Do:** schedule a maintenance window to run `nvidia-smi -q -d ROW_REMAPPER`; if the remap resources are depleting, plan to RMA before it hard-fails mid-run.

## GPUMemoryPressure
**Warning.** VRAM > 95% for 10m — risks OOM-killed jobs.
**Do:** identify the workload (`DCGM_FI_DEV_FB_USED` by pod/job), right-size batch/model-parallel settings, or rebalance placement. Recurrent pressure on the same GPUs → your scheduler is over-packing them.

## GPUIdleButAllocated
**Info / FinOps.** A GPU sat under 5% for 2h while allocated — burning ~$2–4/hr (H100) for nothing. **This is where 60–70% of GPU spend leaks.**
**Do:**
1. Find the owner: which pod/job holds the GPU (`kube-state-metrics` + the node/gpu labels)?
2. Idle notebooks/dev pods are the usual culprits — set idle-culling / TTLs.
3. If it's a stuck job, reclaim it. Track reclaimed idle-GPU-hours as hard $ saved — that number is how this pack pays for itself.

---
_Part of the GPU Monitoring Starter Pack. Tune thresholds in `prometheus-gpu-alerts.yaml` to your fleet._
