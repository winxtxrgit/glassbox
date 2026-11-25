#!/usr/bin/env bash
# Turn a Raspberry Pi 5 (64-bit Raspberry Pi OS / Debian) into a glassbox server:
#   Ollama (LAN-only) + qwen2.5 models + `glassbox serve` under systemd + ufw.
#
# Run it as your normal user (it calls sudo where needed), from this directory:
#   bash setup-pi.sh
# Safe to re-run: every step checks before it changes anything.
#
# Settings (override with env vars):
#   LAN_CIDR   subnet allowed to reach the Pi        (default: detected, e.g. 192.168.1.0/24)
#   MODELS     models to pull                         (default: "qwen2.5:1.5b qwen2.5:3b")
#   SERVE_MODEL model the web server uses             (default: qwen2.5:1.5b)
#   GLASSBOX_SOURCE what pipx installs                (default: the GitHub repo)
#   SKIP_UFW=1 leave the firewall alone
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS="${MODELS:-qwen2.5:1.5b qwen2.5:3b}"
SERVE_MODEL="${SERVE_MODEL:-qwen2.5:1.5b}"
GLASSBOX_SOURCE="${GLASSBOX_SOURCE:-git+https://github.com/winxtxrgit/glassbox}"
RUN_USER="$(id -un)"
RUN_HOME="$HOME"

say()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32mok\033[0m %s\n' "$*"; }
warn() { printf '    \033[33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[31mxx %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "run as your normal user, not root (the service runs as that user)"

# --- 0. preflight -------------------------------------------------------------
say "Preflight"
[ "$(uname -m)" = "aarch64" ] || die "need 64-bit OS (uname -m = $(uname -m)); Ollama has no 32-bit ARM build"
ok "64-bit ($(tr -d '\0' </proc/device-tree/model 2>/dev/null || uname -m))"
mem_gb=$(awk '/MemTotal/ {printf "%.1f", $2/1048576}' /proc/meminfo)
ok "RAM ${mem_gb} GB"
avail_gb=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
[ "$avail_gb" -ge 6 ] || die "only ${avail_gb} GB free on /; need ~6 GB for the models"
ok "disk ${avail_gb} GB free"
temp=$(vcgencmd measure_temp 2>/dev/null | cut -d= -f2 || true)
[ -n "$temp" ] && ok "temperature $temp (inference runs hot: use the active cooler)"

if [ -z "${LAN_CIDR:-}" ]; then
  LAN_CIDR=$(ip -o -f inet route show scope link | awk '$1 ~ /\// && $1 !~ /^169\.254/ {print $1; exit}')
fi
[ -n "$LAN_CIDR" ] || die "could not detect the LAN subnet; set LAN_CIDR=192.168.1.0/24"
ok "LAN subnet $LAN_CIDR"

# --- 1. packages ----------------------------------------------------------------
say "Packages"
sudo apt-get update -qq
sudo apt-get install -y -qq curl git pipx ufw >/dev/null
ok "curl git pipx ufw"

# --- 2. Ollama ------------------------------------------------------------------
say "Ollama"
if command -v ollama >/dev/null; then
  ok "already installed ($(ollama --version 2>/dev/null | tail -1))"
else
  tmp=$(mktemp -d)
  curl -fsSL https://ollama.com/install.sh -o "$tmp/install.sh"
  ok "downloaded installer, sha256 $(sha256sum "$tmp/install.sh" | cut -c1-16)..."
  sh "$tmp/install.sh"
  rm -rf "$tmp"
fi
sudo install -D -m 644 "$HERE/ollama-override.conf" /etc/systemd/system/ollama.service.d/glassbox.conf
sudo systemctl daemon-reload
sudo systemctl enable ollama >/dev/null 2>&1
sudo systemctl restart ollama
for _ in $(seq 1 30); do curl -fs http://127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done
curl -fs http://127.0.0.1:11434/api/version >/dev/null || die "Ollama did not come up: journalctl -u ollama"
ok "running, listening on :11434"

# --- 3. firewall (before anything is exposed) -----------------------------------
say "Firewall"
if [ "${SKIP_UFW:-0}" = "1" ]; then
  warn "SKIP_UFW=1: not touching the firewall. Ollama (:11434) has NO auth."
else
  # The LAN, over IPv4 and IPv6: link-local plus the on-link /64 prefix(es), so
  # `ssh pi.local` (which often resolves to IPv6) keeps working once ufw is on.
  LAN_SRCS="$LAN_CIDR fe80::/10 $(ip -o -6 route show proto kernel | awk '$1 ~ /\/64$/ && $1 !~ /^fe80/ {print $1}' | sort -u | tr '\n' ' ')"
  allow() { # allow <port> <proto> <comment>
    for src in $LAN_SRCS; do
      sudo ufw allow from "$src" to any port "$1" proto "$2" comment "$3" >/dev/null
    done
  }
  listening() { sudo ss -lnH "$1" "( sport = :$2 )" | grep -q .; }
  # SSH first, so enabling the firewall can never lock us out.
  allow 22 tcp 'ssh (LAN)'
  allow 11434 tcp 'ollama (LAN)'
  allow 8787 tcp 'glassbox (LAN)'
  # Keep services that already run here (a web UI, Pi-hole, ...) reachable from the LAN.
  for p in 80 443 53; do
    if listening -t "$p"; then allow "$p" tcp "existing :$p (LAN)"; ok "kept tcp/$p open to the LAN"; fi
  done
  for p in 53 67 123; do
    if listening -u "$p"; then allow "$p" udp "existing :$p (LAN)"; ok "kept udp/$p open to the LAN"; fi
  done
  sudo ufw default deny incoming >/dev/null
  sudo ufw default allow outgoing >/dev/null
  sudo ufw --force enable >/dev/null
  ok "deny incoming except the above from: $LAN_SRCS"
fi

# --- 4. models -----------------------------------------------------------------
say "Models"
for m in $MODELS; do
  if ollama list | awk 'NR>1 {print $1}' | grep -qx "$m"; then
    ok "$m already pulled"
  else
    ollama pull "$m"
    ok "$m pulled"
  fi
done

# --- 5. glassbox ---------------------------------------------------------------
say "glassbox"
pipx install --force "$GLASSBOX_SOURCE" >/dev/null
pipx ensurepath >/dev/null 2>&1 || true
GB="$RUN_HOME/.local/bin/glassbox"
[ -x "$GB" ] || die "glassbox not found at $GB after pipx install"
ok "$($GB --version)"

if sudo test -s /etc/glassbox.env; then
  ok "token file exists (/etc/glassbox.env), keeping it"
else
  token=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
  # umask 077 so the file is never readable by others, not even for a moment.
  printf 'GLASSBOX_TOKEN=%s\n' "$token" | sudo sh -c 'umask 077; cat > /etc/glassbox.env'
  ok "generated a token in /etc/glassbox.env"
fi

sed -e "s|@USER@|$RUN_USER|g" -e "s|@HOME@|$RUN_HOME|g" -e "s|@MODEL@|$SERVE_MODEL|g" \
  "$HERE/glassbox.service" | sudo tee /etc/systemd/system/glassbox.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable glassbox >/dev/null 2>&1
sudo systemctl restart glassbox
for _ in $(seq 1 20); do curl -fs http://127.0.0.1:8787/healthz >/dev/null && break; sleep 1; done
curl -fs http://127.0.0.1:8787/healthz >/dev/null || die "glassbox did not come up: journalctl -u glassbox"
ok "glassbox.service running on :8787"

# --- 6. smoke test --------------------------------------------------------------
say "Smoke test (first run loads the model, can take a minute)"
token=$(sudo sed -n 's/^GLASSBOX_TOKEN=//p' /etc/glassbox.env)
job=$(curl -fs -X POST http://127.0.0.1:8787/api/jobs \
  -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
  -d '{"verb":"triage","document":"Your mailbox is full. Verify your password now at http://mail-verify.example or lose access."}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
for _ in $(seq 1 300); do
  status=$(curl -fs "http://127.0.0.1:8787/api/jobs/$job" -H "Authorization: Bearer $token" \
    | python3 -c 'import json,sys; j=json.load(sys.stdin); r=j.get("result") or {}; print(j["status"], j.get("elapsed_s"), r.get("label",""), r.get("grounded",""), j.get("error") or "")')
  case "$status" in done*|error*) break ;; esac
  sleep 2
done
echo "    $status"

ip_addr=$(hostname -I | awk '{print $1}')
say "Done"
cat <<EOF
    Web page : http://$(hostname).local:8787/   (or http://$ip_addr:8787/)
    Token    : $token
               (also in /etc/glassbox.env -- paste it into the page once; the browser remembers it)
    From your PC:
      PowerShell> setx GLASSBOX_HOST http://$(hostname).local:11434
      (new terminal) glassbox audit email.txt --model $SERVE_MODEL --timeout 600
    Logs     : journalctl -u glassbox -f      |  journalctl -u ollama -f
EOF
