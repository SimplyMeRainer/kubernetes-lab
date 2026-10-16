# Kubernetes lab configuration

Public, sanitized configuration for **SimplyMeRainer's bare-metal Kubernetes training lab**.

This repository contains reviewed, non-secret desired-state manifests, runbooks, and version-pinned Helm release references. The live Argo CD instance reads a **private Synology mirror** over a repository-scoped read-only SSH deploy key; that key and the runtime-only operating material are not in this public repository. The applications use manual sync: automatic synchronization, pruning, and self-healing are disabled. No GitHub Actions workflow or cluster credentials are included.

## New services

- [Home Assistant](http://192.168.1.15) — owner onboarding required.
- [Overall lab dashboard](http://192.168.1.18) — Homepage.
- [VibeStudio](http://192.168.1.17) — local Gemma 4 12B coding suggestions.

See [service setup, restrictions and operations](docs/services.md). All lab UIs allow clients from the management subnet `192.168.1.0/24`. GitHub is the reviewable public source; the private Synology mirror at `/volume1/homes/rserrano/repos/kubernetes-lab.git` is the local Argo CD source.

## Layout

```text
clusters/lab/
  bootstrap/  # Namespaces, quotas/policies, and explicit MetalLB pool
  media/      # Plex, Radarr, Sonarr, SABnzbd, Lidarr, services and PVCs
  dns/        # External-service CoreDNS and gateway forwarding
  staging/    # Restricted two-replica training web application
  cicd/       # Tekton pipeline and scoped staging-deployer RBAC
helm/
  releases.yaml           # Chart repositories, versions and namespaces
  monitoring-values.yaml  # References an existing Grafana Secret
  argocd-values.yaml
  media-image-lock.json    # Observed immutable media image digests
```

The completed one-time PipelineRun was deliberately excluded. Applying declarative configuration should not replay a historical run. Destructive disk/reset scripts, host shell scripts, binary downloads, logs, generated reports, kubeconfigs and passwords remain outside this repository.

## Environment-specific configuration

These manifests are for the existing lab, not arbitrary machines:

- Control plane: `o-3090-01` at `192.168.1.3`; workers: `o-3090-02` at `192.168.1.4` and `o-3060-01` at `192.168.1.5`.
- The 3060 boots from SATA-0. Its NVMe remains unprovisioned and preserved; local-path provisioning is disabled on that node.
- MetalLB: `192.168.1.6–192.168.1.14` plus a separate explicit `192.168.1.15/32` and `192.168.1.18/32` home/dashboard pools and a `192.168.1.17/32` VibeStudio pool. All allocation is explicit.
- UI client source range: `192.168.1.0/24` (management VLAN).
- Synology NFS: `192.168.1.107`, existing `/volume1/media` and `/volume1/downloads` exports.
- Kubernetes pod CIDR: `10.244.0.0/16`; Service CIDR: `10.96.0.0/12`.
- Local storage class: `local-path`; volumes are node-bound and not replicated.
- Service DNS: `<service>.<namespace>.lab.home.arpa`; resolver: `192.168.1.6`.

The private repository includes this non-secret network configuration intentionally. Review it before sharing or changing visibility.

## Validate without deploying

From the repository root, using your separately stored lab kubeconfig:

```sh
export KUBECONFIG="$HOME/.kube/optiplex-lab.yaml"

kubectl kustomize clusters/lab/staging
kubectl apply --dry-run=server -k clusters/lab/staging
kubectl apply --dry-run=server -k clusters/lab/media
kubectl apply --dry-run=server -k clusters/lab/dns
kubectl apply --dry-run=server -k clusters/lab/cicd

kubectl apply --dry-run=server -f clusters/lab/bootstrap/namespaces.json
kubectl apply --dry-run=server -f clusters/lab/bootstrap/metallb-pool.json
```

Validation requires the installed Calico/MetalLB/Tekton CRDs and storage components. This repository does not bootstrap the OS, container runtime, Kubernetes control plane, CNI, CSI/provisioner or PXE services.

## Credential boundary

- No Kubernetes `Secret` object, private key, account password, API token or kubeconfig is intentionally committed.
- `helm/monitoring-values.yaml` references the **existing** `monitoring/grafana-admin` Secret. Create/manage it outside Git before a clean install.
- Application credentials remain on the Mac and in application/cluster storage, not Git.
- Use a repository-scoped **read-only deploy key** or appropriately scoped GitHub App for Argo CD. Do not place the Mac's broad GitHub CLI OAuth token in the cluster or a manifest.
- `.gitignore` is a guardrail, not a secret scanner. Review the staged diff and scan before each push.

## Publication boundary

The public repository intentionally includes safe manifests, Helm values, image-lock references, architecture/runbook documentation, and non-secret examples such as [`examples/credentials.example.json`](examples/credentials.example.json). Every secret slot in an example uses the exact placeholder `<insert secret here>`.

The following remain local-only and must never be copied here: kubeconfigs, Terraform state, private keys, deploy keys, credential bundles, real OpenStack or cloud credentials, encrypted credential archives, runtime output, host logs, bulk backups, and generated verification captures. The private Synology operational checkout may contain those local artifacts, but its credential-bearing history is not a source for this public GitHub branch.

## Argo CD adoption

`argocd/lab-applications.yaml` defines the restricted `lab` project and seven generated applications for bootstrap, media, DNS, staging, CI/CD, AI, and home/dashboard configuration. Argo CD reads the Synology mirror with a dedicated read-only deploy key; that key is stored only in the live `cicd` namespace Secret and is not committed.

Synchronization is deliberately manual. There is no `automated` policy, pruning, or self-healing. This avoids an initial bulk takeover of live resources and prevents accidental deletion while ownership differences are reviewed. The one-time PipelineRun remains excluded, so repository reconciliation cannot replay it.

Do not create one unrestricted recursive application at the repository root: bootstrap files include cluster-scoped PVs/RBAC/pools and multiple namespaces. The AppProject allowlists only the required repositories, namespaces, and cluster-scoped kinds. CRDs and Helm releases remain separate operator-managed prerequisites.

The Tekton pipeline also patches the training deployment. Coordinate ownership with Argo CD before enabling self-healing so the two controllers do not conflict.

Helm upgrades need an additional safety review: some chart Service templates can reallocate NodePorts despite the live lab's post-install NodePort hardening. Existing media/DNS manifests explicitly disable LoadBalancer NodePort allocation; verify all Helm-managed services again after upgrades. Values files alone do not capture every post-install hardening operation.

## Training limits

This is not an HA production configuration: there is one control plane, unreplicated local PVCs, and no automated backup/restore system. Most UIs use HTTP on the private LAN; Argo CD uses its current generated certificate. Monitoring excludes several loopback-only control-plane metric endpoints rather than exposing them insecurely. Alert delivery, external indexers/Usenet credentials, Plex claiming, physical PXE installation and joining additional hosts are separate setup tasks.

For the full host/PXE runbook and local verification evidence, use `~/Documents/kubernetes-lab/README.md` on the administration Mac. Those operational logs were intentionally not uploaded.
