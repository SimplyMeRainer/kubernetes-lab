# VibeStudio — local code suggestions

Original stdlib-only adaptation of the workspace-aware prompting concept in Sahil Malik's [CODE article](https://www.codemag.com/Article/2610021/Local-Vibe-Coding-with-Gemma-4-12B). This is not a copy of the article code and not an autonomous coding agent. Runtime target: Python 3.12 on Linux; tests also run on macOS Python 3.11. No pip packages, external UI resources, model downloads, shell tools, or cluster API calls are used by the app.

## ConfigMap mounting / runtime

Mount these **five runtime files together**, read-only, at `/app`:

- `vibestudio.py`
- `cli.py` (optional for web-only use)
- `index.html`
- `app.js`
- `style.css`

Start with `python -B /app/vibestudio.py` in a Python 3.12 image. Use a single replica and one process: inference serialization is process-local, not distributed. Mount the existing workspace PVC at `/workspace`, preferably read-only. It must exist and be readable by the non-root runtime UID. The app does not create it or persist uploads/suggestions. No image build or dependencies are required. ConfigMap volume updates should be followed by a pod restart to keep Python and UI versions consistent.

Deployment controls are the operator's responsibility: run non-root, drop capabilities, read-only root filesystem, `automountServiceAccountToken: false`, no cluster RBAC, resource limits, and network policies permitting the management subnet (192.168.1.0/24) and the explicit trusted Homepage exception below inbound, and only the internal Ollama service/DNS outbound. **No login is implemented. Do not expose this app publicly.** Pin `APP_ORIGIN` to the exact actual browser origin, including port when nonstandard. Do not infer a public URL from this document. Source allowlisting is not authentication or TLS.

Set Ollama's server-side `OLLAMA_NO_CLOUD=1` and deny Internet egress for the Ollama deployment as well as the app. The app rejects public/cloud host URLs and model names containing `cloud`, does not use environment HTTP proxies, and never follows redirects. Operator-controlled DNS, Ollama model aliases, and server-side network policy remain trust boundaries.

Homepage is an explicit **trusted caller** exception to the management-subnet ingress
boundary. The `vibestudio-dashboard-health` NetworkPolicy requires both namespace
`lab-dashboard` and pod label `app=homepage` in the same peer, but allows TCP 8080
for **all VibeStudio endpoints**, not just `/healthz`. NetworkPolicy cannot filter
HTTP paths. There is no application authentication; Homepage (or a compromised
Homepage pod) can list workspace filenames and submit inference requests with the
required Origin header. Origin checks are not caller authentication. Restrict who
can create/relabel pods in that namespace; path-only access would need a separate
L7 proxy or application authorization, neither of which is configured.

Ollama steady state denies **all initiated egress**, including DNS, with
`policyTypes: [Ingress, Egress]` and `egress: []`; permitted inbound requests and
their replies still work with installed weights. Model downloads require the
explicit temporary HTTPS/DNS exception and cleanup-trapped maintenance workflow in
`docs/services.md` (publication tree) or `services-README.md` (operational tree).
Never leave that exception installed or put it into persistent GitOps.

### Environment

| Variable | Default | Meaning |
|---|---|---|
| `OLLAMA_HOST` | `http://ollama.ai.svc.cluster.local:11434` | Local HTTP origin only: localhost, loopback, RFC1918 IPv4, or `*.svc.cluster.local`; no URL auth/query/path. |
| `OLLAMA_MODEL` | `gemma4:12b` | Already installed local model; no pull operation. |
| `NUM_PREDICT` | `512` | Output token limit, integer 1–1024. |
| `OLLAMA_TIMEOUT` | `600` | Connection/read timeout and streaming deadline in seconds, integer 1–600. |
| `WORKSPACE` | `/workspace` | Existing workspace root. No symlink path components. |
| `BIND_HOST` | `0.0.0.0` | Listen interface; use `127.0.0.1` for local-only checks. |
| `PORT` | `8080` | Listen port. |
| `APP_ORIGIN` | empty | Set exact trusted browser origin in deployment. If empty, POST Origin must match `http://` plus incoming Host (direct HTTP development only; not protection against DNS rebinding). No forwarded-header trust. |

Ollama receives `POST /api/generate` with `stream:true`, configured `model`, original system instructions, explicit user/context JSON encoded as the prompt, and options `temperature:0.2`, `top_p:0.9`, `num_ctx:4096`, `num_predict:NUM_PREDICT`. CPU generation may take minutes. This app **does not use a 256K context**. The conservative byte budget is not a tokenizer; it deliberately leaves room for system text and the output budget.

## Actual HTTP contract

- `GET /healthz` → `200 application/json`, `{"status":"ok"}`. Process liveness only; does not probe Ollama or trigger inference. Suitable for startup/liveness/readiness of the UI, not model readiness.
- `GET /` → HTML UI; `GET /app.js`, `GET /style.css` → same-origin static assets. No arbitrary static file serving.
- `GET /api/files` → `200 application/json`, `{"files":["relative/path.py", ...]}`. Bounded, sorted eligible file names, no content. An oversized listing returns 400 with `{"error":"..."}`. Depth beyond 8 is omitted; scanning beyond 2,000 entries fails. Listing eligibility does not imply that content passes later secret checks.
- `POST /api/generate`: `Content-Type: application/json`, exactly one bounded `Content-Length`, and an `Origin` matching `APP_ORIGIN` (or direct HTTP Host fallback). Browser `Sec-Fetch-Site` must not be cross-site. No CORS permissions. Request:

  ```json
  {"prompt":"Suggest a Python improvement","files":["src/main.py"],"uploads":[{"name":"helper.py","content":"print('example')"}]}
  ```

  `prompt` is required; `files` and `uploads` default to empty arrays. Unknown top-level fields are rejected. Uploads are request-local strings, not writes to the PVC. Selected workspace files are re-opened safely at submission time.

  On successful validation/slot acquisition, status is **200**, `Content-Type: application/x-ndjson; charset=utf-8`, connection-close-delimited, no buffering. Each line is one JSON event:

  ```json
  {"type":"delta","text":"suggested text"}
  {"type":"done"}
  ```

  A downstream model error instead terminates with `{"type":"error","error":"..."}`; there is no `done` afterward. Because headers have already been sent, model-unavailable errors are **NDJSON error events under HTTP 200**, not HTTP 503. A disconnect without a terminal event means incomplete output. Partial output must be reviewed as incomplete.

  Pre-stream errors use JSON `{"error":"..."}`: 400 invalid JSON/input/context, 403 origin, 408 body timeout, 411 missing/duplicate/unsupported length or chunked input, 413 body too large, 415 media type, 429 generation slot busy. Unknown routes return 404; unsupported methods use the stdlib 501 response.

Model responses are rendered via `textContent`, never parsed as HTML or Markdown. Copy and `.txt` download are client-side; the app never executes or applies suggestions. Copy has a fallback for LAN HTTP where Clipboard API is unavailable. Security headers include CSP, no-store, nosniff, no-referrer, and no framing.

## Limits and safety boundaries

- Body: 32,768 bytes; prompt: 1,200 UTF-8 bytes.
- At most 8 selected/uploaded files; each at most 2,048 UTF-8 bytes.
- Entire serialized prompt/context JSON: 2,400 UTF-8 bytes, including names and JSON overhead. Nothing is silently truncated.
- Output: 65,536 UTF-8 bytes; upstream NDJSON line: 65,536 bytes; upstream stream: 1 MiB / 8,192 events maximum.
- 16 concurrent HTTP workers, 10-second socket I/O timeout; excess connections close. Only one inference at a time; no inference queue. CLI processes do not share the server lock: do not run concurrent direct CLI jobs against the constrained backend. Ollama should separately enforce a one-request execution limit.
- Workspace access uses descriptor-relative `O_NOFOLLOW` opens for root and every path component. Symlinks, hard-linked files, devices/FIFOs, traversal, absolute paths, hidden files/directories, common dependency folders, credential-like filenames, binary/non-UTF-8 files and private-key/credential-like content are rejected.
- Allowed extensions: `.py .md .txt .js .ts .html .css .json .yaml .yml .toml`. The denylist intentionally favors false positives.
- Secret pattern checking is defense-in-depth, **not a secret-detection guarantee**. Do not put credentials in the workspace or paste them into a prompt. Only explicitly selected context is sent to the local model.
- No file-writing/execution/tool endpoints exist. No terminal escape sequences from model text are printed by the CLI. No prompts, context, responses, or paths are logged by the HTTP access logger.
- Closing the browser does not guarantee immediate cancellation while Ollama is computing its first token; the connection closes on a detected disconnect or deadline. With no login, an allowed LAN caller can monopolize the serialized slot. Network isolation is required.

## CLI

From this directory, with an already running local Ollama and installed model:

```sh
python3.12 -B cli.py --workspace /path/to/project --prompt 'Suggest a small refactor' --file src/main.py
```

The workspace must be explicit. Files are opt-in via repeated `--file`; no automatic scan is sent to the model. Uses the same validation, safe reader and bounded streaming client as the browser. Prints suggestions only; exit 0 on success, 2 on validation/model errors. `--help` does not contact Ollama.

## Historical implementation verification (unit mocks, no inference)

This section describes the initial implementation checks, not the current deployment status. The app was subsequently deployed on Python 3.12 and real local inference passed, including with Ollama outbound HTTPS/DNS blocked. See [the service runbook](../../docs/services.md) for deployment verification.

```sh
cd /Users/rserrano/Documents/kubernetes-lab/apps/vibestudio
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.11 -m unittest -v
node --check app.js
```

Eight unittest cases passed on Python 3.11.15, including multiple subcases and real loopback HTTP requests. Ollama is replaced with explicit unit mocks for successful streams, missing model, timeout, invalid records, stream bounds and HTTP error propagation. Tests cover explicit workspace, traversal, symlinks/hardlinks/hidden files, private keys, context limits, origin/body/JSON/media validation, inference lock, read-only routes and text-safe rendering contracts. Temporary test artifacts are created and removed within this directory; no network outside loopback or package installs are used by tests.

Browser checked at 1280px and 390px: no horizontal overflow, correct workspace listing. A browser-only mocked NDJSON response containing an HTML injection string rendered as literal text with zero child elements; copy was enabled. No real model request was made. `ui-desktop.png` records the initial UI. Design audit: 0/10 template tells; command/inspect composition, restrained green accent, no hero, decorative cards or fake metrics.

Local shell `python3` unexpectedly resolves to Python 3.9.6; those initial tests also passed, but the required verification above explicitly used Homebrew Python 3.11.15. Python 3.12 is the intended container runtime and was not available locally. Direct terminal connections to the standalone background loopback server timed out in this environment; the browser could reach it and unittest in-process loopback HTTP tests passed. No cluster deployment, Git change, Synology access, model pull or inference was performed. Parent/operator must perform deployment and actual inference verification separately.
