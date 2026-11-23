# glassbox Pi server: แผนละเอียด

> เป้าหมาย: ใช้ Raspberry Pi 5 (`raspberrypi.local` / `192.168.1.62`) เป็น **glassbox server ประจำบ้าน**
> คือรัน Ollama + glassbox ตลอด 24 ชม. ให้ทุกเครื่องในวง LAN (notebook และมือถือ) ส่งเอกสารมา audit ได้
> แล้วใช้ Pi ทำ **edge benchmark** เป็นข้อมูลชุดใหม่ใน FINDINGS

## ภาพรวม architecture

```
 Windows (glassbox CLI) ──┐
                          ├── HTTP :11434 ──► Ollama  (systemd, LAN-only via ufw)
 มือถือ / browser ─────────┼── HTTP :8787  ──► glassbox serve (systemd)
                          │                     └─ job queue → 1 worker → audit() → Ollama
                          └── SSH :22 (key only)
```

**สถานะ (2026-11-24):** ✅ Phase 2 (`--timeout`, `GLASSBOX_HOST`) · ✅ Phase 3 (`glassbox serve`) · 🟡 Phase 4: เตรียม `deploy/raspberry-pi/setup-pi.sh` ไว้แล้ว รอแค่ SSH key บน Pi (Pi รับเฉพาะ publickey) · ⏳ Phase 0/1/5/6
Pi: Raspberry Pi 5, RAM 8GB, Debian 13 (trixie), OpenSSH 10.0

**Non-goals (v1):** ไม่เปิดออก internet, ไม่ทำ user account, ไม่เก็บ job ลง disk, ไม่รัน `lens` (torch) บน Pi

---

## Phase 0: เข้า Pi และสำรวจเครื่อง (~30 นาที)

| # | งาน | คำสั่ง / วิธี |
|---|---|---|
| 0.1 | สร้าง SSH key บน Windows | `ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\id_ed25519` |
| 0.2 | ส่ง key ขึ้น Pi | `type $env:USERPROFILE\.ssh\id_ed25519.pub \| ssh <user>@192.168.1.62 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys"` |
| 0.3 | สำรวจ hardware | `cat /proc/device-tree/model; uname -m; free -h; df -h /; lsblk` |
| 0.4 | สำรวจ OS | `cat /etc/os-release; python3 --version` |
| 0.5 | ดูว่าตอนนี้อะไรใช้ port ไหน | `sudo ss -tlnp` (port 80 เปิดอยู่ ต้องรู้ว่าเป็นอะไร) |
| 0.6 | อุณหภูมิและ throttling | `vcgencmd measure_temp; vcgencmd get_throttled` |
| 0.7 | ล็อก IP ไว้ | ตั้ง DHCP reservation `192.168.1.62` ใน router (หรือใช้ `raspberrypi.local` ผ่าน mDNS) |

**Gate (ต้องผ่านทุกข้อก่อนไปต่อ):**
- `uname -m` = `aarch64` (OS 64-bit) เพราะ Ollama ไม่รองรับ 32-bit
- RAM ≥ 4GB (ถ้าจะใช้ 3b ควรมี 8GB)
- disk ว่าง ≥ 10GB
- มีพัดลมหรือ active cooler เพราะ inference ทำให้ CPU 100% ยาวๆ และ Pi 5 จะเริ่ม throttle ที่ ~85°C
- (แนะนำ) ถ้า boot จาก SD card และมี NVMe/SSD ควรย้ายไป SSD จะโหลดโมเดลเร็วกว่ามาก

---

## Phase 1: Ollama บน Pi (~2–3 ชม.)

| # | งาน | รายละเอียด |
|---|---|---|
| 1.1 | อัปเดตระบบ | `sudo apt update && sudo apt full-upgrade -y && sudo reboot` |
| 1.2 | ติดตั้ง Ollama | `curl -fsSL https://ollama.com/install.sh -o install.sh` → **อ่านสคริปต์ก่อน** → `sh install.sh` (installer จะสร้าง `ollama.service` ให้) |
| 1.3 | ตั้งค่า service | `sudo systemctl edit ollama` แล้วใส่ block ข้างล่าง |
| 1.4 | Firewall | ดูส่วน ufw ข้างล่าง (**ต้อง allow SSH ก่อนสั่ง enable**) |
| 1.5 | Pull โมเดล | `ollama pull qwen2.5:0.5b` / `qwen2.5:1.5b` / `qwen2.5:3b` (ประมาณ 0.4 / 1.0 / 1.9 GB) |
| 1.6 | วัดความเร็ว | `ollama run qwen2.5:1.5b --verbose "hello"` แล้วจด `eval rate` ของทุกโมเดล และ `vcgencmd measure_temp` ระหว่างรัน |
| 1.7 | Smoke test จาก Windows | `curl http://192.168.1.62:11434/api/tags` |
| 1.8 | glassbox จาก Windows | `glassbox audit examples\<file> --host http://192.168.1.62:11434 --model qwen2.5:1.5b` |

**1.3: Ollama override**
```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_KEEP_ALIVE=30m"
```
- `NUM_PARALLEL=1`: Pi มีแรงพอรันได้ทีละ request เท่านั้น
- `KEEP_ALIVE=30m`: ไม่ต้องโหลดโมเดลใหม่ทุกครั้ง เพราะ cold load ใช้เวลาหลายวินาที

**1.4: ufw**
```bash
sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw allow from 192.168.1.0/24 to any port 22 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 11434 proto tcp
sudo ufw enable && sudo ufw status verbose
```
> ⚠️ Ollama **ไม่มี auth** firewall จึงเป็นด่านเดียวที่กันไว้ ห้าม port-forward 11434 ออก internet เด็ดขาด

**Acceptance:** สั่ง `audit` จาก Windows ผ่าน Pi ได้ผลครบทั้ง grounded/faithful/robust และจดค่า tok/s ของทุกโมเดลไว้แล้ว

---

## Phase 2: ปรับฝั่ง client ใน glassbox (~ครึ่งวัน, ส่วนแรกของ v0.9.0)

ปัญหาที่เจอจากการอ่านโค้ด:
- `OllamaBackend` มี `timeout=120.0` (`src/glassbox/backends.py:87`) แต่ CLI ไม่ได้เปิดให้ปรับ บน Pi ถ้าใช้ 3b กับเอกสารยาวและโมเดลยังไม่ได้โหลด อาจเกิน 120 วินาทีแล้วขึ้น `BackendError` ซึ่งเป็น error หลอก
- ต้องพิมพ์ `--host` ทุกครั้ง

| # | งาน | ไฟล์ |
|---|---|---|
| 2.1 | เพิ่ม `--timeout SECONDS` (default 120) แล้วส่งต่อให้ `OllamaBackend` และ `OpenAICompatBackend` | `cli.py` (`_add_model_args`, `_make_backend`) |
| 2.2 | ให้ `--host` อ่านค่า default จาก env `GLASSBOX_HOST` ลำดับความสำคัญคือ flag > env > `DEFAULT_HOST` | `cli.py` |
| 2.3 | เพิ่ม `--timeout` ใน `run_benchmark.py` ด้วย | `benchmarks/run_benchmark.py` |
| 2.4 | Tests: env ถูกอ่านจริง (monkeypatch), timeout ส่งถึง backend | `tests/test_cli.py` |
| 2.5 | README: หัวข้อ "Run the model on another machine" | `README.md` |

**Acceptance:** `set GLASSBOX_HOST=http://192.168.1.62:11434` แล้ว `glassbox audit x.txt --timeout 600` ใช้งานได้, CI เขียวทั้ง 6 job

---

## Phase 3: `glassbox serve` (~2–3 วัน, core ของ v0.9.0)

### Design
- module ใหม่ `src/glassbox/server.py` ใช้ **stdlib อย่างเดียว** (`http.server.ThreadingHTTPServer` + `queue.Queue` + worker thread 1 ตัว) เพื่อให้ core ยัง zero-dep
- **ใช้ logic เดิมทั้งหมด:** `audit()` / `triage()` + `serialize.audit_dict` / `verdict_dict` ไม่เขียน logic ซ้ำ
- เป็น job queue แบบ async เพราะ `audit` เรียกโมเดลหลายรอบ (triage, probe ทีละ citation, erase-all, perturbation) บน Pi อาจใช้หลายนาที ถ้าใช้ request แบบ sync จะ timeout ที่ browser

### API
| Method | Path | Body / ผลลัพธ์ |
|---|---|---|
| GET | `/` | หน้าเว็บ HTML ไฟล์เดียว (inline ทั้งหมด ไม่ใช้ CDN) มี textarea + เลือก verb + poll ผล |
| GET | `/healthz` | `{"ok": true, "model": "...", "ollama": true/false, "queue": n}` |
| POST | `/api/jobs` | `{"verb": "audit"\|"triage", "document": "...", "prompt": "plain"\|"few-shot"}` → `202 {"id": "..."}` |
| GET | `/api/jobs/<id>` | `{"status": "queued\|running\|done\|error", "position": n, "elapsed_s": x, "result": {...}, "error": "..."}` |
| GET | `/api/jobs` | รายการ 20 job ล่าสุด (เฉพาะ metadata) |

### CLI
```
glassbox serve [--bind 127.0.0.1] [--port 8787] [--model ...] [--host ...] [--prompt ...] [--timeout ...]
```
- ใช้ `_add_model_args` ร่วมกับคำสั่งอื่น
- ตั้งชื่อ address ที่จะ listen ว่า **`--bind`** เพราะ `--host` ถูกใช้เป็น Ollama URL ไปแล้ว
- default bind `127.0.0.1` ถ้าจะเปิดให้ LAN เข้าได้ต้องสั่ง `--bind 0.0.0.0` เอง (safe by default)
- port 8787 เพราะ 80 บน Pi มีอย่างอื่นใช้อยู่

### Security (ส่วนนี้สำคัญที่สุด)
| ความเสี่ยง | วิธีป้องกัน |
|---|---|
| **XSS**: ผลลัพธ์มีข้อความที่ quote มาจากเอกสาร ซึ่งเป็น input ที่ไม่น่าเชื่อถือ (เช่น phishing email) | หน้าเว็บแสดงผลด้วย `textContent` เท่านั้น ห้ามใช้ `innerHTML`, ใส่ header `Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'` และ `X-Content-Type-Options: nosniff` |
| DoS / ส่ง payload ใหญ่ | จำกัด body 64KB (413), queue สูงสุด 20 (429), job store เก็บแค่ 100 อันล่าสุด (evict อันเก่า) |
| Path traversal | รับเฉพาะข้อความ ไม่รับ path ของไฟล์ |
| ใครก็ได้ใน LAN ใช้ได้ | ถ้าตั้ง env `GLASSBOX_TOKEN` จะบังคับ `Authorization: Bearer` ทุก `/api/*` (เทียบ token ด้วย `hmac.compare_digest`) |
| CSRF จากเว็บอื่น | บังคับ `Content-Type: application/json` ทุก POST และไม่ส่ง CORS header |
| Input แปลกๆ | whitelist verb/prompt, document ต้องเป็น string ที่ไม่ว่าง (400), path ที่ไม่รู้จักตอบ 404 |
| Shutdown | จับ SIGTERM ให้ `server.shutdown()` แบบ graceful เพื่อให้ systemd stop ได้สะอาด |

### Tests (`tests/test_server.py`)
- start server บน port 0 ใน thread แยก โดยใช้ `CallableBackend` ไม่ต้องมีโมเดลจริง
- POST แล้ว poll จนได้ `done` และ result ตรงกับ `audit_dict` ที่รันตรงๆ
- 400 (body ผิด), 404, 413, 429 (queue เต็ม), 401 (token ผิดหรือไม่ส่ง)
- backend throw `BackendError` แล้ว job ต้องเป็น `status: error` และ worker ไม่ตาย
- หน้า `/` ไม่มี `innerHTML` และ response มี CSP header
- ต้องผ่านบน Windows, macOS และ Linux ใน CI

### Docs
- README หัวข้อ "Server mode"
- **แก้ README:285** ที่เขียนว่า "opens no network listener" ให้เป็น "ไม่เปิด listener ยกเว้นตอนสั่ง `serve`, default bind localhost" และอธิบาย trust boundary ใหม่
- เพิ่ม CHANGELOG `Unreleased`

**Acceptance:** `glassbox serve` บน Windows (localhost) ใช้ผ่าน browser ได้, ไม่มี dependency ใหม่, CI เขียว, coverage ไม่ลดลง

---

## Phase 4: Deploy บน Pi (~ครึ่งวัน)

| # | งาน | รายละเอียด |
|---|---|---|
| 4.1 | ติดตั้ง glassbox | `sudo apt install -y pipx && pipx install git+https://github.com/winxtxrgit/glassbox` (หลัง release ใช้ `pipx install glassbox-audit`) |
| 4.2 | Token | `openssl rand -hex 24` แล้วเก็บใน `/etc/glassbox.env` (`chmod 600`) |
| 4.3 | systemd unit | ไฟล์ข้างล่าง |
| 4.4 | Firewall | `sudo ufw allow from 192.168.1.0/24 to any port 8787 proto tcp` |
| 4.5 | ทดสอบ | เปิด `http://raspberrypi.local:8787` จากมือถือ แล้ววาง email ทดสอบ |
| 4.6 | Reboot test | `sudo reboot` แล้วเช็คว่า service ทั้งสองขึ้นเอง |

**`/etc/systemd/system/glassbox.service`**
```ini
[Unit]
Description=glassbox audit server
After=network-online.target ollama.service
Wants=network-online.target
Requires=ollama.service

[Service]
User=<user>
EnvironmentFile=/etc/glassbox.env
ExecStart=/home/<user>/.local/bin/glassbox serve --bind 0.0.0.0 --port 8787 --model qwen2.5:1.5b --timeout 600
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```
แล้วสั่ง `sudo systemctl daemon-reload && sudo systemctl enable --now glassbox` และดู log ด้วย `journalctl -u glassbox -f`

**Acceptance:** หลัง reboot ใช้งานจากมือถือได้ทันที และ request ที่ไม่มี token โดนปฏิเสธ (401)

---

## Phase 5: Edge benchmark (~1 วัน ส่วนใหญ่เป็นเวลารอ)

คำถามวิจัย: **โมเดลเล็กพอจะรันบน edge ยัง auditable อยู่ไหม**
ผลเดิม (40 docs, plain): 3B trust 45% vs 7B 18% ถ้า trend "เล็กกว่า = quote ตรงกว่า" ยังจริงที่ 1.5B/0.5B จะเป็น finding ที่แข็งแรงมาก แต่ถ้า 0.5B parse JSON พังก็เป็น finding เหมือนกัน

| # | งาน | รายละเอียด |
|---|---|---|
| 5.1 | Clone repo บน Pi | `git clone https://github.com/winxtxrgit/glassbox && cd glassbox && python3 -m venv .venv && .venv/bin/pip install -e .` |
| 5.2 | Pilot run (5 docs) | ทดสอบ pipeline ก่อนรันเต็ม และวัดเวลาต่อ doc |
| 5.3 | Full run | `tmux new -s bench` แล้ว `.venv/bin/python benchmarks/run_benchmark.py --models qwen2.5:0.5b,qwen2.5:1.5b,qwen2.5:3b --timeout 600` (รันใน tmux จะได้ไม่หลุดถ้า SSH ตัด) |
| 5.4 | Monitor | ระหว่างรันเก็บ `vcgencmd measure_temp` และ `get_throttled` ทุก 60 วินาทีลง log |
| 5.5 | ดึงผลกลับ | `scp <user>@raspberrypi.local:glassbox/benchmarks/RESULTS.md .` |
| 5.6 | Writeup | เพิ่มหัวข้อ "Edge (Raspberry Pi 5)" ใน `benchmarks/RESULTS.md` + FINDINGS โดยรายงาน accuracy/grounded/faithful/robust/trust/ECE **และ** s/doc, °C และสถานะ throttled |

**ประมาณเวลา (ยังไม่ได้วัดจริง ต้องยืนยันใน 5.2):** audit 1 doc ≈ 8–12 model calls → 40 docs ≈ 400 calls
1.5b น่าจะใช้ ~1–2 ชม., 3b ~3–5 ชม. ถ้ารันครบทั้งสามโมเดลแนะนำให้รันข้ามคืน

**Caveats ที่ต้องเขียนกำกับ:** รอบละ 1 run, CPU inference (ผลอาจต่างจาก GPU เล็กน้อยเพราะ numeric path ต่างกัน), corpus มีแค่ 40 docs

---

## Phase 6: Release และคอนเทนต์ (~ครึ่งวัน)

| # | งาน |
|---|---|
| 6.1 | bump `__version__` เป็น `0.9.0`, ย้าย CHANGELOG Unreleased ไปเป็น 0.9.0 |
| 6.2 | `gh release create v0.9.0 --notes-file docs/releases/v0.9.0.md` ซึ่งจะ trigger PyPI publish อัตโนมัติ |
| 6.3 | บน Pi: `pipx install --force glassbox-audit` แล้วแก้ unit ให้ใช้ตัวจาก PyPI |
| 6.4 | คอนเทนต์: blog/คลิป "Explainable LLM บน Raspberry Pi" + ผล edge benchmark |

---

## Timeline (ทำแบบชิลๆ)

| สัปดาห์ | Phase | ผลลัพธ์ |
|---|---|---|
| 1 | 0 → 1 → 2 | Pi รัน Ollama, Windows ใช้ glassbox ผ่าน Pi ได้, `--timeout`/`GLASSBOX_HOST` merge แล้ว |
| 2 | 3 | `glassbox serve` + tests + docs, CI เขียว |
| 3 | 4 → 5 | Deploy เสร็จ ใช้จากมือถือได้, benchmark รันข้ามคืน |
| 4 | 6 | v0.9.0 บน PyPI + writeup |

## Risks

| ความเสี่ยง | ผลกระทบ | วิธีรับมือ |
|---|---|---|
| Thermal throttling | ช้าลงและตัวเลข benchmark เพี้ยน | ใช้ active cooler, log `get_throttled`, รายงานไว้ใน RESULTS |
| RAM 4GB กับโมเดล 3b | swap แล้วช้ามาก | ใช้ 1.5b เป็น default, 3b รันเฉพาะ benchmark |
| SD card ช้าหรือเสื่อม | โหลดโมเดลช้า | ใช้ SSD ถ้ามี, ตั้ง `KEEP_ALIVE` ให้โมเดลค้างใน RAM |
| Timeout 120s | error หลอก | Phase 2.1 |
| Ollama ไม่มี auth | คนใน LAN ใช้ได้หมด | ufw LAN-only, ห้าม port-forward |
| XSS จาก output ของโมเดล | script รันใน browser | `textContent` + CSP + test |
| IP ของ Pi เปลี่ยน | client หา server ไม่เจอ | DHCP reservation หรือใช้ `raspberrypi.local` |
| Port 80 ชน | — | ใช้ 8787 |

## เรื่องที่ต้องตัดสินใจหรือให้ข้อมูลก่อนเริ่ม
1. **username บน Pi** และตั้ง SSH key (Phase 0.1–0.2)
2. **RAM ของ Pi** (4 หรือ 8GB) ใช้เลือกโมเดล default
3. ต้องการ **token auth** ไหม (แนะนำให้มี)
4. port 80 บน Pi ตอนนี้เป็นอะไร ต้องเก็บไว้ไหม
