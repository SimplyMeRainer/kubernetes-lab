# Kubernetes lab configuration

Private configuration repository for **SimplyMeRainer's bare-metal Kubernetes training lab**.

This initial import contains reviewed, non-secret desired-state manifests and version-pinned Helm release references. **Creating this repository does not connect Argo CD, deploy resources, or turn on automatic synchronization.** No GitHub Actions workflow or cluster credentials are included.

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

- Control plane: `192.168.1.3`; worker: `192.168.1.4`.
- MetalLB: `192.168.1.6–192.168.1.14`, explicit allocation only.
- Administrative client source range: `192.168.1.109/32`.
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

## Safe GitOps adoption

Start by connecting **only `clusters/lab/staging`** to a restricted Argo CD project/application, with manual sync and no automatic pruning. This connection has not been made by the initial repository creation.

Do not point one unrestricted recursive Argo CD application at the repository root: bootstrap files include cluster-scoped PVs/RBAC/pools and multiple namespaces. Stage adoption, confirm resource ownership, and separately manage CRDs and Helm releases.

The Tekton pipeline also patches the training deployment. Coordinate ownership with Argo CD before enabling self-healing so the two controllers do not conflict.

Helm upgrades need an additional safety review: some chart Service templates can reallocate NodePorts despite the live lab's post-install NodePort hardening. Existing media/DNS manifests explicitly disable LoadBalancer NodePort allocation; verify all Helm-managed services again after upgrades. Values files alone do not capture every post-install hardening operation.

## Training limits

This is not an HA production configuration: there is one control plane, unreplicated local PVCs, and no automated backup/restore system. Most UIs use HTTP on the private LAN; Argo CD uses its current generated certificate. Monitoring excludes several loopback-only control-plane metric endpoints rather than exposing them insecurely. Alert delivery, external indexers/Usenet credentials, Plex claiming, physical PXE installation and joining additional hosts are separate setup tasks.

For the full host/PXE runbook and local verification evidence, use `~/Documents/kubernetes-lab/README.md` on the administration Mac. Those operational logs were intentionally not uploaded.
