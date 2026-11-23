# glassbox on a Raspberry Pi 5

Turn a Pi on your LAN into an always-on glassbox server: Ollama runs the model,
`glassbox serve` gives you a web page (phone-friendly) and a job API, and any
PC on the LAN can point the CLI at it.

```
 PC  (glassbox CLI, GLASSBOX_HOST) ──► :11434  Ollama ─────────┐
 phone / browser ───────────────────► :8787   glassbox serve ──┘ (job queue, token)
                         ufw: only your LAN subnet gets in
```

Tested target: Raspberry Pi 5 (8 GB), 64-bit Raspberry Pi OS (Debian 13 "trixie").
4 GB works with `qwen2.5:1.5b`; 32-bit OS does not (no Ollama build).

## 1. Get SSH access with a key

If your Pi only accepts keys (`Permission denied (publickey)`), add your PC's
public key **from somewhere that can already log in**:

* **Another machine that already has SSH access:** copy the PC's public key
  (`type $env:USERPROFILE\.ssh\id_ed25519.pub`) over, then
  `cat pc.pub | ssh <user>@raspberrypi.local 'cat >> ~/.ssh/authorized_keys'`
* **Keyboard + monitor on the Pi:** log in, then pull the key from GitHub (after
  adding it to your GitHub account) with
  `curl -fsSL https://github.com/<github-user>.keys >> ~/.ssh/authorized_keys`
* **Fresh SD card:** in Raspberry Pi Imager → OS customisation → Services →
  "Allow public-key authentication only", and paste the public key there.

Check: `ssh <user>@raspberrypi.local hostname`

## 2. Run the setup (≈10 min plus model downloads)

From the repo on your PC:

```bash
scp -r deploy/raspberry-pi <user>@raspberrypi.local:~/glassbox-setup
ssh -t <user>@raspberrypi.local 'bash ~/glassbox-setup/setup-pi.sh'
```

`setup-pi.sh` is idempotent (safe to re-run) and does, in order:

| Step | What |
|---|---|
| preflight | refuses a 32-bit OS, checks RAM / free disk / temperature, detects the LAN subnet |
| packages | `curl git pipx ufw` |
| Ollama | installs it (if missing) + `ollama-override.conf`: listen on LAN, 1 request at a time, keep the model loaded 30 min |
| firewall | ufw: deny all incoming except 22 / 11434 / 8787 **from the LAN subnet** (and 80 if something already serves it). SSH is allowed *before* enabling, so it cannot lock you out |
| models | pulls `qwen2.5:1.5b` and `qwen2.5:3b` |
| glassbox | `pipx install` from GitHub, generates a token in `/etc/glassbox.env` (root, 600), installs the hardened `glassbox.service` |
| smoke test | queues a real triage job and prints the result, URL and token |

Knobs: `LAN_CIDR=192.168.1.0/24`, `MODELS="qwen2.5:1.5b"`, `SERVE_MODEL=qwen2.5:3b`,
`GLASSBOX_SOURCE=glassbox-audit` (PyPI instead of GitHub), `SKIP_UFW=1`, e.g.
`ssh -t pi 'SERVE_MODEL=qwen2.5:3b bash ~/glassbox-setup/setup-pi.sh'`.

## 3. Use it

**Phone / browser:** `http://raspberrypi.local:8787/`, paste the token once.

**CLI on your PC** (Windows):

```powershell
setx GLASSBOX_HOST http://raspberrypi.local:11434
# new terminal:
glassbox audit email.txt --model qwen2.5:1.5b --timeout 600
```

**API** (scripts, other apps):

```bash
curl -X POST http://raspberrypi.local:8787/api/jobs \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"verb":"audit","document":"...","prompt":"plain"}'      # -> {"id": ...}
curl http://raspberrypi.local:8787/api/jobs/<id> -H "Authorization: Bearer $TOKEN"
```

| Endpoint | |
|---|---|
| `GET /` | web page |
| `GET /healthz` | version, model, whether Ollama answers, queue length (no token needed) |
| `POST /api/jobs` | `{"verb": "audit"\|"triage", "document": str, "prompt"?: "plain"\|"few-shot"}` → 202 + id |
| `GET /api/jobs/<id>` | `status` queued/running/done/error, `position`, `elapsed_s`, `result` (same JSON as `--json`) |
| `GET /api/jobs` | the 20 most recent jobs (metadata only) |

## Operations

```bash
journalctl -u glassbox -f            # requests + errors (bodies are never logged)
journalctl -u ollama -f
sudo systemctl restart glassbox
vcgencmd measure_temp; vcgencmd get_throttled   # 0x0 = never throttled
sudo cat /etc/glassbox.env           # the token
```

Rotate the token: `sudo rm /etc/glassbox.env` and re-run the setup.
Update glassbox: re-run the setup (it reinstalls with `--force`).

## Security notes

* **Ollama has no authentication.** The firewall is the only thing between it
  and the network. Never port-forward 11434 or 8787 to the internet.
* `glassbox serve` requires the token on every `/api/*` call, caps bodies at
  64 KB and the queue at 20, accepts text only (no file paths), and renders
  results with `textContent` under a strict CSP, because the results quote the
  submitted document, which may be hostile.
* The service runs as your user with `ProtectSystem=strict`,
  `ProtectHome=read-only`, `NoNewPrivileges` and friends.
* Plain HTTP: anyone on the same Wi-Fi can sniff the token. Fine for a home LAN;
  don't run it on shared networks.
