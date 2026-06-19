# VPN Telegram Bot (3X-UI)

A Telegram bot that sells VPN configs by provisioning clients on a **3X-UI panel** through its REST API. Built with **Python (aiogram 3 + aiohttp)**.

## Features

### 🤖 Telegram Bot (Chat UI)
- `/start` registration, bilingual selection, and main menu
- Browse plans and check out with Card, Stars, or Crypto
- Config link delivery and QR code generation
- **My Services**: Self-serve usage metrics, config renewal, and IP connection resets
- **Admin Commands**: Panels/plans management, receipt review queue, broadcast notifications, and statistics dashboard

## How it maps to the 3X-UI API

| Bot action | Panel endpoint |
|---|---|
| Provision a customer | `POST /panel/api/clients/add` |
| Read client (subId, fields) | `GET /panel/api/clients/get/{email}` |
| Renew / top-up / disable | `POST /panel/api/clients/update/{email}` |
| Delete | `POST /panel/api/clients/del/{email}` |
| Usage | `GET /panel/api/clients/traffic/{email}` |
| Config links | `GET /panel/api/clients/links/{email}` |
| Subscription URLs | `GET /panel/api/clients/subLinks/{subId}` |
| Reset connections | `POST /panel/api/clients/clearIps/{email}` |
| Plan inbound picker | `GET /panel/api/inbounds/options` |
| Admin dashboard | `GET /panel/api/server/status` |

Authentication uses a **Bearer API token** (panel: Settings → Security → API Token).

## Setup

### Method 1: Premium Automatic Installer (Recommended)

You can install, configure, upgrade, and manage the bot automatically using our premium interactive installer script:

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/RootedOne/gbot/main/install-bot.sh)"
```

Alternatively, if you cloned the repository locally:
```bash
sudo chmod +x install-bot.sh
sudo ./install-bot.sh
```

The script manages:
- System prerequisites & dependency resolution
- Virtual environment creation & requirements installation
- Configuration variables interactive wizard (with auto-loading of existing variables on upgrade)
- Automatic download & installation of **xray-core** (if `CONNECT_MODE=XRAY` is chosen)
- Dedicated system user (`vpnbot`) and folder security permissions
- Systemd service configuration, auto-restart, and management options
- (Optional) Nginx reverse proxy + certbot SSL certificates
- (Optional) UFW firewall rules configuration
- (Optional) Safe uninstallation with database backup prompt

---

### Method 2: Manual Setup (Alternative)

```bash
cd vpn-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your BOT_TOKEN, ADMIN_IDS, card/crypto details
```

### Required `.env` keys
- `BOT_TOKEN` — from @BotFather
- `ADMIN_IDS` — comma-separated Telegram numeric IDs
- `CARD_NUMBER` / `CARD_HOLDER` — for card-to-card payments
- `NOWPAYMENTS_API_KEY` / `NOWPAYMENTS_IPN_SECRET` / `PUBLIC_BASE_URL` — only if `CRYPTO_ENABLED=true`
- `WEBHOOK_HOST` / `WEBHOOK_PORT` — web server bind host/port (defaults to `0.0.0.0` and `8080`)

### Telegram connection modes
If the bot host can't reach Telegram directly (e.g. blocked region), pick one of three modes with `CONNECT_MODE`:

1. **DIRECT** (default) — connect straight to Telegram.
2. **PROXY** — route through an existing http/https/socks5 proxy:
   - `CONNECT_MODE=PROXY`
   - `PROXY_URL` — `socks5://host:1080`, `socks5://user:pass@host:1080`, or `http://host:8080`
   - (Legacy `PROXY_MODE=ON` still forces PROXY mode when `CONNECT_MODE` is blank.)
3. **XRAY** — spin up a local **xray-core** from a config link and route Telegram through it:
   - `CONNECT_MODE=XRAY`
   - `XRAY_CONFIG_URL` — a full share link, e.g. `vless://...` (also `vmess://`, `trojan://`, `ss://`; hysteria is **not** supported by xray-core)
   - `XRAY_BIN` — path to the `xray` binary (default `xray`, must be installed on the host)
   - `XRAY_SOCKS_PORT` — local SOCKS port xray listens on (default `10808`)

   In XRAY mode the bot parses the link into an xray outbound, writes a temporary config with a local SOCKS inbound, launches `xray run`, waits for the port to be ready, then connects the bot through `socks5://127.0.0.1:<port>`. The process is stopped automatically on shutdown. If xray fails to start, the bot logs the error and falls back to DIRECT.

SOCKS support is provided by `aiohttp-socks` (already in `requirements.txt`). The connection mode only affects the bot ↔ Telegram link; panel API calls use each plan's assigned panel (configured in **Admin Panel → Panels**).

> Install xray-core: download a release binary from https://github.com/XTLS/Xray-core/releases and put it on your `PATH` (or set `XRAY_BIN` to its full path).

## Run

```bash
python -m bot.main
```

The bot will run long-polling for Telegram updates. Simultaneously, the `aiohttp` web server starts and:
1. Handles NowPayments IPN callbacks if crypto is enabled.
2. Serves health checks at `/health`.

## Notes
- `totalGB` in the panel API is **bytes**; the bot converts GB → bytes. `0` = unlimited.
- `expiryTime` is **epoch milliseconds**; `0` = never expires.
- The database is SQLite by default and tables are auto-created on first run.
