# Kubernetes Environment Guide: How This Lab Works

This is a guided tour of the live OptiPlex Kubernetes lab. It explains what each backend service does, how traffic and storage move through the platform, and which commands answer the most useful operational questions.

This is a five-node kubeadm Kubernetes `v1.35.8` cluster. The current control planes are `o-3090-01` (`192.168.1.3`), `g3` (`192.168.1.20`), and `o-3060-02` (`192.168.1.21`). The workers are `o-3090-02` (`192.168.1.4`) and `o-3060-01` (`192.168.1.5`).

## 1. The mental model

Kubernetes is a desired-state control system:

1. You declare an object such as a Deployment, Job, Service, PersistentVolumeClaim, or NetworkPolicy in Git.
2. Argo CD compares Git with the Kubernetes API and applies the desired objects.
3. Controllers observe those objects and reconcile the actual state. A Deployment controller creates Pods; a StatefulSet controller creates stable Pods and volumes; a Job controller runs work until it succeeds; a storage provisioner creates a PersistentVolume for a PVC.
4. The kubelet on the selected node asks containerd to start the containers, mounts the volumes, configures networking through CNI, and reports Pod status back to the API server.
5. Prometheus, Grafana, node-exporter, DCGM exporter, and kube-state-metrics observe the resulting state.

The API server is the control-plane entry point. It does not run every workload itself. It stores desired state in etcd, exposes the Kubernetes API, and coordinates the controllers and scheduler.

## 2. Control plane and high availability

Normal clients use:

```text
https://k8s.lab.home.arpa:6443
              ↓
  kube-vip virtual IP: 192.168.1.19
              ↓
one elected control-plane API server
```

`kube-vip` uses ARP leader election on the management LAN. Whichever control-plane node holds the `kube-system/plndr-cp-lock` Lease advertises `192.168.1.19`. If that node fails, another control plane becomes leader and advertises the same address.

The three control-plane hosts each run these static Pods:

- `kube-apiserver`: authenticates and serves Kubernetes API requests.
- `kube-controller-manager`: keeps desired state and actual state aligned.
- `kube-scheduler`: selects nodes for Pods that do not have a fixed node.
- `etcd`: stores cluster configuration, object state, credentials, leases, and controller data.
- `kube-vip`: owns or releases the API virtual IP.

### Why three etcd members?

etcd uses Raft consensus. Three voting members tolerate one unavailable member because two members still form a majority. The members are local to `.3`, `.20`, and `.21`; live etcd data is never hosted on the NAS.

### Verify the control plane

Run this from `o-3090-01` using its protected administrative kubeconfig:

```bash
sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf kubectl get nodes -o wide
sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf kubectl get --raw=/readyz
sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf \
  kubectl -n kube-system get lease plndr-cp-lock
```

A healthy result has five Ready nodes, an `ok` ready check, and a recently renewed `plndr-cp-lock` Lease.

## 3. What runs on each host

| Host | Kubernetes role | Backend responsibility | Storage role |
|---|---|---|---|
| `o-3090-01` | Control plane + etcd | API, scheduler/controller, backups, platform state | NVMe-backed local platform/state paths at `/srv/lab` |
| `g3` | Control plane + etcd + GPU | HA control plane and explicitly scheduled CUDA work | 100 GiB local training filesystem at `/srv/training`; 20 GiB LVM reserve |
| `o-3060-02` | Control plane + etcd | HA control plane, etcd, audit/backup support | 469 GiB NVMe at `/srv/lab`; do not place generic training cache beside etcd paths |
| `o-3090-02` | Application worker | media, AI services, monitoring, general applications | 469 GiB NVMe at `/srv/lab` |
| `o-3060-01` | CPU worker | preprocessing, data staging, evaluation, cache | 469 GiB NVMe at `/srv/lab` |

Control-plane taints keep ordinary workloads away from the control plane. `g3` is a deliberate exception for GPU jobs: a GPU job must select `lab.home.arpa/gpu-node=true`, request `nvidia.com/gpu: 1`, and tolerate the control-plane `NoSchedule` taint.

## 4. The storage backend in detail

Storage is not one thing in Kubernetes. This lab intentionally uses three different storage patterns because they have different availability and performance properties.

### 4.1 Storage vocabulary

- **StorageClass**: a policy for creating storage. The default class here is `local-path`.
- **PersistentVolumeClaim (PVC)**: an application's request for storage, such as “I need 5 GiB, ReadWriteOnce.”
- **PersistentVolume (PV)**: the actual provisioned or predeclared storage backing a claim.
- **CSI/provisioner**: a controller that turns a PVC into a PV. This lab's dynamic local storage is managed by Rancher's `local-path-provisioner`.
- **Access mode**: `RWO` means one node can mount the volume read/write. `RWX` means multiple nodes can mount it read/write.
- **Reclaim policy**: `Retain` means deleting a PVC does not automatically erase the PV's underlying data.

### 4.2 Dynamic local-path storage

The default StorageClass is:

```text
name:                local-path
provisioner:         rancher.io/local-path
volumeBindingMode:   WaitForFirstConsumer
reclaimPolicy:       Retain
```

`WaitForFirstConsumer` is important. Kubernetes first schedules the Pod, then the provisioner creates the local PV on that Pod's node. That prevents a volume being created on one node while the Pod is scheduled to a different node.

The local-path provisioner maps these nodes to local directories:

```text
o-3090-01  → /srv/lab/volumes
o-3090-02  → /srv/lab/volumes
o-3060-01  → /srv/lab/volumes
```

For a dynamic claim, the lifecycle is:

```text
Deployment/Job creates PVC
        ↓
Scheduler selects a node
        ↓
local-path-provisioner creates a host directory under /srv/lab/volumes
        ↓
Provisioner creates PV with node affinity
        ↓
PVC binds to PV
        ↓
kubelet mount-binds the local directory into the Pod
```

The `ai/cpu-preprocess-workspace` claim is a current example. It is a 5 GiB RWO `local-path` PVC selected for `o-3060-01`. Its completed preprocessing Job wrote a checksum manifest to that worker's NVMe-backed local volume.

### 4.3 Static node-affine GPU storage

GPU training uses a deliberately explicit PV rather than dynamic local-path storage:

```text
PV:           g3-gpu-training
Capacity:     80 GiB
Host path:    /srv/training/volumes/gpu-training
Node affinity:g3 only
PVC:          ai/gpu-training-workspace
```

This teaches an important infrastructure rule: local disks offer fast data locality but are not highly available. A Pod using `g3-gpu-training` must run on `g3`. If `g3` is unavailable, the workload cannot fail over to another node because its data is physically local.

Use this tier for regenerable datasets, checkpoints, container workspaces, and cache. Promote successful artifacts to the NAS or an object-storage tier; do not treat the GPU-local disk as the only copy of a valuable model.

### 4.4 NAS-backed shared storage

The `media/downloads` and `media/media-library` claims use predeclared 1 TiB `RWX` PVs backed by the Synology NAS. RWX is appropriate when several Pods or nodes need shared access, such as media services. It trades local-disk performance for shareability and off-host durability.

The NAS also receives verified etcd snapshots. That is backup storage, not a live etcd data directory.

### 4.5 What Retain means operationally

`local-path` uses `Retain`. If an application PVC is removed accidentally, Kubernetes preserves its PV rather than immediately deleting the host data. This helps recover stateful application data, but it creates an administrator task:

1. Inspect the retained PV and its host path.
2. Decide whether the data should be preserved, migrated, or securely erased.
3. Remove the PV and host directory only after that decision.

Do not assume “PVC deleted” means “data deleted.”

### Inspect storage yourself

```bash
sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf \
  kubectl get storageclass,pv,pvc -A -o wide

sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf \
  kubectl -n ai describe pvc cpu-preprocess-workspace

sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf \
  kubectl describe pv g3-gpu-training
```

When reading a PV, focus on `Claim`, `StorageClass`, `Reclaim Policy`, `Node Affinity`, and `Source`. Those fields explain who owns the data, whether Kubernetes can schedule it elsewhere, and where the bytes actually live.

## 5. Container runtime and Pod lifecycle

Each host runs these layers:

```text
kubelet
  ↓ CRI
containerd
  ↓ OCI runtime
containers, namespaces, cgroups, mounts, and network interfaces
```

The kubelet is the node agent. It watches for Pods assigned to its node, prepares volumes and networking, asks containerd to run the containers, and sends status back to the API server. Containerd pulls images and manages container lifecycles.

Useful node-level checks:

```bash
sudo systemctl is-active kubelet containerd
sudo crictl ps
sudo crictl images
```

A Pod can be healthy in Kubernetes while a process inside the container is unhealthy only if the readiness/liveness probes are insufficient. Conversely, a running container is not necessarily a Ready Pod. Use `kubectl get pods` and inspect the `READY` column, then use `kubectl describe pod` for events.

## 6. Networking, DNS, and service exposure

There are three main traffic layers:

1. **Pod network**: Calico assigns Pod addresses in the `10.244.0.0/16` overlay.
2. **Cluster Service network**: Services receive virtual addresses in `10.96.0.0/12` and route to matching Pods.
3. **LAN ingress**: MetalLB advertises LoadBalancer addresses on the LAN through its speakers and FRR components.

Examples of LAN-facing LoadBalancer services include:

- Argo CD: `192.168.1.14`
- Prometheus: `192.168.1.13`
- Grafana: `192.168.1.12`
- VibeStudio: `192.168.1.17`
- Home Assistant: `192.168.1.15`

A request to VibeStudio follows this shape:

```text
client on LAN
  → MetalLB address 192.168.1.17
  → Service: ai/vibestudio
  → Endpoint selected by Kubernetes
  → VibeStudio Pod on an application worker
```

`kube-proxy` implements the Service-to-Pod routing rules on every node. Calico provides Pod networking and policy enforcement. CoreDNS (`kube-dns`) resolves names such as `ollama.ai.svc.cluster.local` inside the cluster.

The API endpoint `k8s.lab.home.arpa` is currently available to all nodes via local host mappings. Add an authoritative LAN DNS A record for `192.168.1.19` to remove that transitional dependency.

## 7. GitOps, CI/CD, and backend controllers

The desired configuration repository is on the Synology-backed Git remote. Argo CD runs in namespace `cicd` and tracks the `main` branch. The AI application includes the GPU workspace, GPU validation Job, and CPU preprocessing Job.

The typical delivery path is:

```text
Git commit
  → Synology Git remote / main
  → Argo CD repo-server reads desired manifests
  → Argo CD Application reports OutOfSync or Synced
  → sync applies resources to Kubernetes
  → Kubernetes controllers create workloads and PVCs
  → kubelet/containerd run them on selected nodes
  → Prometheus/DCGM/node-exporter observe the result
```

Check GitOps state:

```bash
sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf \
  kubectl -n cicd get applications.argoproj.io -o wide
```

`Synced` means the cluster objects match the repository revision Argo CD evaluated. `Healthy` means Argo CD's resource health checks consider the managed resources healthy. It does not replace application-level testing; that is why the GPU and preprocessing Jobs also write explicit evidence.

## 8. GPU backend and AI pipeline

`g3` has one NVIDIA GTX 1660 Ti Max-Q GPU with 6 GiB of VRAM. NVIDIA GPU Operator `v26.7.0` manages:

- NVIDIA driver lifecycle.
- NVIDIA Container Toolkit runtime integration.
- GPU Feature Discovery labels.
- Kubernetes NVIDIA device plugin, which advertises `nvidia.com/gpu: 1`.
- DCGM exporter, which exposes GPU metrics to Prometheus.

The real PyTorch validation Job requested one GPU, 4 CPU cores, and 12 GiB of memory. It ran CUDA 12.4 and executed 25 training steps against the `g3-gpu-training` local PV. This proves the entire path: scheduler selection, GPU allocation, container runtime, CUDA libraries, volume attachment, and workload output.

Check the GPU backend:

```bash
sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf \
  kubectl get node g3 -o jsonpath='{.status.allocatable.nvidia\.com/gpu}{"\n"}'

sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf \
  kubectl -n gpu-operator get clusterpolicy cluster-policy

sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf \
  kubectl -n gpu-operator get pods -o wide
```

The GTX 1660 Ti is suited to CUDA validation, small-model inference, quantized models, LoRA/QLoRA fine-tuning, and training-pipeline engineering. It is not a full-finetuning platform for multi-billion-parameter models.

## 9. Observability and recovery

The monitoring namespace includes Prometheus, Grafana, Alertmanager, kube-state-metrics, and a node-exporter DaemonSet. Every node has node-exporter. The GPU Operator adds a DCGM exporter ServiceMonitor for GPU metrics.

Look at the backend controllers and DaemonSets:

```bash
sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf \
  kubectl get deploy,ds,sts -A -o wide
```

Important backup distinction:

- **etcd backups** restore Kubernetes control-plane state: objects, Secrets, ConfigMaps, RBAC, controller state, and leases.
- **PV/application backups** restore application data such as media, model artifacts, and configuration.
- **Git** restores declarative desired configuration.

A production recovery plan needs all three. Restoring etcd does not restore an erased local PV. Reapplying Git manifests does not restore application data.

## 10. A practical learning sequence

1. Run `kubectl get nodes`, `kubectl get pods -A`, and `kubectl get storageclass,pv,pvc -A`. Explain each row in your own words.
2. Pick one PVC, such as `ai/cpu-preprocess-workspace`, and trace it to its PV, node, host path, Job, and written artifact.
3. Pick one Service, such as `ai/vibestudio`, and trace client → MetalLB IP → Service → Endpoint → Pod → node.
4. Review the GPU Job manifest at `clusters/lab/ai/gpu-training.yaml`. Identify the scheduling controls, resource limits, local PV, and evidence file.
5. Inspect the Argo CD Application revision and compare it to the Git commit on the Synology remote.
6. Practice a read-only incident drill: identify the current kube-vip leader, etcd members, unavailable Pods, PVC bindings, and recent events without changing anything.

## 11. Guardrails and current limitations

- Local PVs are intentionally non-HA. Keep important data off-host as well.
- Do not put generic high-churn training storage on the etcd control-plane data paths.
- Do not schedule general workloads on g3 merely because it has a GPU; require explicit GPU scheduling controls.
- The LAN is 1 GbE. Data locality and staged artifacts matter more than pretending it is a distributed multi-GPU fabric.
- The authoritative DNS A record for `k8s.lab.home.arpa → 192.168.1.19` is still a follow-up task.
- The single 6 GiB GPU is a learning and platform-engineering resource, not a replacement for a production GPU fleet.

## 12. Quick-reference command set

```bash
# Cluster and API health
sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf kubectl get nodes -o wide
sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf kubectl get --raw=/readyz

# Workloads and failures
sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf kubectl get pods -A -o wide
sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf kubectl get events -A --sort-by=.lastTimestamp

# Storage lifecycle
sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf kubectl get storageclass,pv,pvc -A -o wide
sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf kubectl describe pvc -n ai cpu-preprocess-workspace

# Networking and services
sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf kubectl get svc -A -o wide
sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf kubectl get endpointslice -A

# HA and GPU
sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf kubectl -n kube-system get lease plndr-cp-lock
sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf kubectl -n gpu-operator get clusterpolicy cluster-policy

# GitOps status
sudo -n env KUBECONFIG=/etc/kubernetes/admin.conf kubectl -n cicd get applications.argoproj.io -o wide
```

Never paste kubeconfigs, private keys, join tokens, certificate keys, or backup SSH keys into chat, tickets, or source control.
