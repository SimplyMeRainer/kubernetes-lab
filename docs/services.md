# Local AI, Home Assistant and lab dashboard

## Access

| Service | URL | Deployment |
|---|---|---|
| Home Assistant | http://192.168.1.15 | `home/homeassistant` |
| Homepage lab dashboard | http://192.168.1.16 | `lab-dashboard/homepage` |
| VibeStudio | http://192.168.1.17 | `ai/vibestudio` |

External access to all three new UIs is restricted to the administration Mac **192.168.1.109/32** by LoadBalancer source ranges and ingress NetworkPolicies. Keep that DHCP reservation. They use HTTP, not TLS; do not publish them to the Internet. Homepage and VibeStudio have no application login. Explicitly review access controls before adding other clients. NodePorts are disabled. The native VLAN-1 L2Advertisement must reference all three pools: `lab-reserved`, `lab-home-dashboard`, `lab-vibestudio`.

## VibeStudio and Gemma

An original, bounded adaptation of [Local Vibe Coding with Gemma 4 12B](https://www.codemag.com/Article/2610021/Local-Vibe-Coding-with-Gemma-4-12B), rather than a full VS Code installation or autonomous coding agent.

- Ollama 0.34.3 with actual `gemma4:12b` weights; images are digest-pinned. Model download is a separate one-time operation, not a GitOps Job.
- CPU-only on `o-3090-02`: 4096-token context, one model/request at a time, 512 generated tokens by default. This is not the article's GPU/256K-context setup.
- Browser accepts a prompt and explicitly selected small source files, streams suggestions, and supports copy/download. It never executes or automatically applies generated code.
- Prompt limit 1200 UTF-8 bytes; each file 2048 bytes; total packaged prompt/context 2400 bytes; eight files maximum. This intentionally targets small examples, not entire repositories.
- Files uploaded in the browser are request context, not persistent workspace writes. `/workspace` has a 2Gi PVC mounted read-only; use a separately controlled maintenance process if populating it.
- Model store has a 40Gi local-path PVC. Ollama is ClusterIP-only, cloud functionality disabled, and app egress is limited to Ollama and cluster DNS.
- The app's `/healthz` means the web process is alive, not that inference has passed. The local `vibestudio-inference-verification.json` records the separate real-model test when complete.
- Python stdlib app and CLI source: `apps/vibestudio/`; see its README for options and tests. Generated output always requires human review.

### Temporary model-download exception (operator only)

Steady state: `ollama-internal-only` selects `app=ollama` with
`policyTypes: [Ingress, Egress]` and `egress: []`. Ollama cannot initiate
outbound connections, including DNS. Already-downloaded local inference needs
no outbound connections; replies on permitted inbound connections are allowed
by NetworkPolicy connection tracking. VibeStudio retains only Ollama/DNS egress.
No extra Ollama DNS permission is needed in steady state.

For a deliberate model install/update only, run the entire subshell below with
the dedicated kubeconfig. This additive exception allows Ollama TCP 443 to public
IPv4 addresses (not LAN, pod, Service, link-local or other reserved ranges), plus
UDP/TCP 53 to `kube-system` pods labeled `k8s-app=kube-dns`. It is not a registry
hostname allowlist: any public HTTPS destination is reachable during this window.
Do not serve untrusted inference requests during maintenance. IPv6 remains denied.
The policy is created from stdin, never added to persistent GitOps resources.
Do not run concurrent pull workflows. Existing weights do not require this step.

```sh
(
  set -eu
  export KUBECONFIG="$HOME/.kube/optiplex-lab.yaml"
  policy=ollama-temporary-model-download
  # Fail on API errors or a stale exception, rather than silently retaining it.
  existing=$(kubectl -n ai get networkpolicy "$policy" --ignore-not-found -o name)
  [ -z "$existing" ] || { printf '%s\n' "Remove/review stale $policy before continuing" >&2; exit 1; }
  cleanup() {
    rc=$?
    trap - EXIT HUP INT TERM
    if ! kubectl --request-timeout=30s -n ai delete networkpolicy "$policy" --ignore-not-found --wait=true; then
      printf '%s\n' "URGENT: exception removal failed; manually delete $policy in ai" >&2
      exit 1
    fi
    remaining=$(kubectl --request-timeout=30s -n ai get networkpolicy "$policy" --ignore-not-found -o name) || exit 1
    [ -z "$remaining" ] || { printf '%s\n' "URGENT: $policy still exists" >&2; exit 1; }
    exit "$rc"
  }
  trap cleanup EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
  kubectl -n ai create -f - <<'POLICY'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: ollama-temporary-model-download
  namespace: ai
spec:
  podSelector:
    matchLabels:
      app: ollama
  policyTypes: [Egress]
  egress:
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 0.0.0.0/8
              - 10.0.0.0/8
              - 100.64.0.0/10
              - 127.0.0.0/8
              - 169.254.0.0/16
              - 172.16.0.0/12
              - 192.0.0.0/24
              - 192.0.2.0/24
              - 192.88.99.0/24
              - 192.168.0.0/16
              - 198.18.0.0/15
              - 198.51.100.0/24
              - 203.0.113.0/24
              - 224.0.0.0/4
              - 240.0.0.0/4
      ports:
        - {protocol: TCP, port: 443}
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              k8s-app: kube-dns
      ports:
        - {protocol: UDP, port: 53}
        - {protocol: TCP, port: 53}
POLICY
  kubectl -n ai rollout status deployment/ollama --timeout=180s
  kubectl --request-timeout=35m -n ai exec deployment/ollama -- ollama pull gemma4:12b
  kubectl -n ai exec deployment/ollama -- ollama list
)
```

The EXIT trap removes and reads back the exception on success, failure, or handled
signals. No shell can clean up after SIGKILL, host loss, or an unreachable API:
if interrupted that way, immediately run
`kubectl --kubeconfig "$HOME/.kube/optiplex-lab.yaml" -n ai delete networkpolicy ollama-temporary-model-download --ignore-not-found`
and verify absence before resuming service. NetworkPolicies are additive: any
other egress-granting policy selecting Ollama must also be absent. After removing
the exception, validate fresh outbound HTTPS/DNS denial and real local inference;
static tests and unit mocks do not prove CNI enforcement or live inference.


For local VS Code/CLI access, keep the tunnel loopback-only:

```sh
kubectl -n ai port-forward --address 127.0.0.1 service/ollama 11434:11434
# In another terminal, follow apps/vibestudio/README.md with:
# OLLAMA_HOST=http://127.0.0.1:11434
```

## Home Assistant

Home Assistant **Container 2026.9.3**, one instance with Recreate updates and a 10Gi `/config` local-path PVC on `o-3090-01`. The empty configuration volume bootstraps through Home Assistant itself. No owner account or device credentials were created by automation.

Open its URL from the Mac and complete owner onboarding. The configured initial timezone is `America/New_York`; confirm timezone, location and units during setup. Credentials and private `.storage` data stay on the PVC, never in Git.

This is Container edition: no Supervisor/add-on store. It uses normal Calico networking, no privileged container, no Kubernetes API token, and no USB/D-Bus mounts. Unicast LAN integrations may be configured manually; mDNS/SSDP, Bluetooth, USB radios and cross-VLAN discovery are not configured. Host networking requires a separate reviewed change, including node-port access protection, after owner setup.

Back up `/config` using Home Assistant's supported backup mechanisms and export backups off-node. Retain PVCs are not backups. Do not run two instances against the SQLite/configuration volume.

## Homepage

Homepage **v2.4.0** provides grouped links for infrastructure, local AI, home automation, media and the three nodes. Home Assistant and VibeStudio have HTTP status checks. Grafana/Prometheus provide the detailed live cluster metrics; the node descriptions on Homepage are inventory, not live telemetry.

Configuration is declarative in a ConfigMap. No Kubernetes API permissions, mounted service-account token, secret access, Docker socket or API credentials are granted. No metrics-server was added. Configuration changes require a rollout because files use subPath mounts.

Homepage is an explicit **trusted caller** exception to the owner-only ingress
boundary. The `vibestudio-dashboard-health` NetworkPolicy requires both namespace
`lab-dashboard` and pod label `app=homepage` in the same peer, but allows TCP 8080
for **all VibeStudio endpoints**, not just `/healthz`. NetworkPolicy cannot filter
HTTP paths. There is no application authentication; Homepage (or a compromised
Homepage pod) can list workspace filenames and submit inference requests with the
required Origin header. Origin checks are not caller authentication. Restrict who
can create/relabel pods in that namespace; path-only access would need a separate
L7 proxy or application authorization, neither of which is configured.
The Home Assistant ingress rule similarly trusts Homepage on the full TCP8123 container port, including onboarding endpoints—not only its status page. Keep Homepage and its namespace under administrator control.

## Validation and recovery

Use the dedicated kubeconfig, never an unrelated default context:

```sh
export KUBECONFIG="$HOME/.kube/optiplex-lab.yaml"
kubectl apply --dry-run=server -k clusters/lab/ai
kubectl apply --dry-run=server -k clusters/lab/home-dashboard
kubectl -n home rollout status deployment/homeassistant
kubectl -n lab-dashboard rollout status deployment/homepage
curl --fail http://192.168.1.15/api/onboarding
curl --fail http://192.168.1.16/api/healthcheck
curl --fail http://192.168.1.17/healthz
```

The local full-lab verifier is `~/Documents/kubernetes-lab/verify-lab.py`. All storage is node-local/unreplicated. The 3060 NVMe remains untouched. Preserve PVCs when uninstalling or troubleshooting; local-path Retain does not make the applications highly available.

## Git destinations

GitHub: private `SimplyMeRainer/kubernetes-lab`. Argo CD has not been connected and automatic reconciliation remains disabled.

Synology at `192.168.1.107` is reachable, but the attempted key-only SSH account `rserrano` rejected authentication. No repository was created or remote configured there. Adding the requested second Git remote requires its SSH URL (username, optional port and repository path), or an authorized account and intended shared-folder path. Do not put passwords or private keys in Git URLs, configuration files or chat.

## Observed expansion verification

The deployed Python 3.12 app returned a real streamed Gemma answer in 26.21 seconds for a small addition-function prompt. Explicit `think: false` prevents the limited generation budget being consumed entirely by hidden reasoning; empty answers now raise an error. Eight local Python 3.11 unit tests pass. Expanded lab checks passed with 3 Ready nodes, 35 ready workload controllers, 13 bound PVCs, 12 HTTP checks and 22/22 Prometheus targets. These are point-in-time checks, not uptime guarantees. Homepage services/validation APIs and its internal Home Assistant/VibeStudio health paths were also exercised.
