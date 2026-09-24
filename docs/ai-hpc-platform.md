# AI/HPC Platform Portfolio Runbook

This lab is operated as a small production-style platform rather than as a single-node AI demo. It demonstrates the operating areas that connect the existing infrastructure/SRE background to an AI infrastructure role: HA Kubernetes control planes, GPU lifecycle management, persistent local storage, batch-workload isolation, telemetry, GitOps, and disaster recovery.

## Resource allocation

| Host | Role | Compute allocation | Durable local storage | Scheduling posture |
|---|---|---|---|---|
| o-3090-01 (.3) | Control plane / etcd | 12 CPU threads, 31 GiB RAM | 458 GiB NVMe at `/srv/lab` | System services and existing stateful workloads; no AI batch jobs. |
| g3 (.20) | Control plane / GPU execution | 12 CPU threads, 31 GiB RAM, GTX 1660 Ti Mobile (6 GiB VRAM) | 100 GiB LVM at `/srv/training`, 20 GiB VG reserve | Explicitly selected GPU training/inference jobs only; preserves its control-plane taint. |
| o-3060-02 (.21) | Control plane / etcd | 6 CPU threads, 31 GiB RAM | 469 GiB NVMe at `/srv/lab` | etcd/audit durability and platform services; no generic AI scheduling. |
| o-3090-02 (.4) | Application worker | 12 CPU threads, 31 GiB RAM | 469 GiB NVMe at `/srv/lab` | Existing media, AI application, and user-facing service workloads. |
| o-3060-01 (.5) | CPU preprocessing worker | 6 CPU threads, 31 GiB RAM | 469 GiB NVMe at `/srv/lab` | Dataset preparation, feature extraction, evaluation, and artifact staging. |

The GPU is Turing-generation and has 6 GiB VRAM. It is appropriate for CUDA validation, observability, small-model inference, quantized models, LoRA/QLoRA fine-tuning, and training-pipeline engineering. It is not suitable for full fine-tuning of multi-billion-parameter models.

## Storage design

- `/srv/training` on g3 is the GPU-local checkpoint, working-set, and container-cache tier. GPU training pods use an explicit node-affine local PV; a local volume is never represented as highly available storage.
- `/srv/lab` on o-3060-01 is the CPU-worker NVMe tier for datasets, preprocessing cache, and build/test artifacts.
- NAS storage remains the replicated/off-host artifact, model, and backup tier. It is not used for live etcd data.
- Existing control-plane NVMe volumes remain reserved for their present durability responsibilities. Do not co-locate high-churn training cache with etcd paths.

## GPU operating model

1. NVIDIA GPU Operator manages the device plugin, runtime integration, GPU Feature Discovery, DCGM exporter, and driver lifecycle.
2. Only explicitly selected workloads run on g3: `nodeSelector: lab.home.arpa/gpu-node=true`, a control-plane NoSchedule toleration, and `limits.nvidia.com/gpu: 1` are mandatory.
3. DCGM exporter is scraped by the existing Prometheus stack. Alert and dashboard work should track GPU utilization, memory, temperature, ECC/Xid events, and pending GPU jobs.
4. Keep training jobs idempotent and checkpoint to the GPU-local tier; then promote successful artifacts to the NAS/object-storage tier.

## Portfolio sequence

1. Validate GPU Operator and run a reproducible CUDA/PyTorch smoke workload through Kubernetes.
2. Add a queued GPU training workflow with Kueue, resource quotas, priorities, admission checks, and a timeout/preemption story.
3. Build a Tekton pipeline that performs CPU preprocessing on o-3060-01, schedules GPU fine-tuning on g3, publishes metrics, and promotes artifacts only after evaluation gates.
4. Add a model-serving experiment with explicit GPU limits, canary rollout, request metrics, and rollback.
5. Publish architecture diagrams, benchmark reports, runbooks, post-incident examples, and GitOps change history. Emphasize tradeoffs: one GPU and 1 GbE LAN make queueing, locality, and observability more valuable than pretending this is a multi-GPU supercomputer.

## Evidence standards

For each experiment, retain the Git commit, rendered manifest, scheduler/admission decision, GPU and system metrics, raw benchmark result, checkpoint/artifact checksum, and rollback procedure. This produces interview-ready evidence of platform engineering judgment rather than only model demos.
