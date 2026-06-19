# VPN Telegram Bot — Full Guide

A complete, production-oriented guide for the **VPN selling Telegram bot** that provisions configs on a **3X-UI panel** via its REST API.

Built with **Python 3.11+ and aiogram 3**. Sells admin-defined plans, accepts **card-to-card**, **Telegram Stars**, and **crypto (NowPayments)** payments, and auto-delivers config links + QR codes. Connects to Telegram **directly**, through a **proxy**, or through a **local xray-core** built from a config link.

---

## Table of contents

1. [Architecture overview](#1-architecture-overview)
2. [How it maps to the 3X-UI API](#2-how-it-maps-to-the-3x-ui-api)
3. [Project structure](#3-project-structure)
4. [Prerequisites](#4-prerequisites)
5. [Installation](#5-installation)
6. [Configuration reference (.env)](#6-configuration-reference-env)
7. [Telegram connection modes](#7-telegram-connection-modes)
8. [Payment methods](#8-payment-methods)
9. [Running the bot](#9-running-the-bot)
10. [Customer experience walkthrough](#10-customer-experience-walkthrough)
11. [Admin panel walkthrough](#11-admin-panel-walkthrough)
12. [Data model](#12-data-model)
13. [Provisioning & lifecycle logic](#13-provisioning--lifecycle-logic)
14. [Deployment (systemd / Docker)](#14-deployment-systemd--docker)
15. [Troubleshooting](#15-troubleshooting)
16. [Security notes](#16-security-notes)
17. [FAQ](#17-faq)


---

## 1. Architecture overview

```mermaid
flowchart TD
  U["Customer (Telegram)"] -->|"/start, buy"| B[aiogram Bot]
  ADM["Admin (Telegram)"] -->|manage| B
  B --> DB[("SQLite: users, plans, orders, services")]
  B --> PAY{Payment method}
  PAY -->|Stars| TG["Telegram successful_payment"]
  PAY -->|Crypto| NP["NowPayments invoice + IPN"]
  PAY -->|Card| RV["Admin approves receipt"]
  TG --> FUL[Fulfillment]
  NP --> FUL
  RV --> FUL
  FUL --> PROV[Provisioning]
  PROV -->|"clients/add + get + links"| PANEL["3X-UI Panel API"]
  PROV --> DB
  FUL -->|"config URLs + QR + sub link"| U
  WH["aiohttp IPN server"] -->|callback| NP
  B -. "DIRECT / PROXY / XRAY" .-> TAPI["Telegram API"]
```

**Flow in words:**
1. A customer opens the bot, browses admin-defined plans, and chooses a payment method.
2. Payment confirmation arrives via one of three paths (Stars callback, NowPayments IPN, or admin receipt approval).
3. The **fulfillment** layer provisions a client on the 3X-UI panel and stores a `Service` row.
4. The bot delivers config links, a QR code, and a subscription URL to the customer.
5. Customers self-manage usage, renewals, and connection resets from **My Services**.

---

## 2. How it maps to the 3X-UI API

The panel is a **3X-UI Panel API** under `/panel/api/*`, authenticated with a **Bearer API token** (panel → Settings → Security → API Token).

| Bot action | Panel endpoint | Notes |
|---|---|---|
| Provision a customer | `POST /panel/api/clients/add` | `{client:{email,totalGB,expiryTime,tgId,limitIp,enable}, inboundIds:[...]}` |
| Read client (subId, secrets) | `GET /panel/api/clients/get/{email}` | used to read `subId` and merge on update |
| Renew / top-up / enable-disable | `POST /panel/api/clients/update/{email}` | **replaces** the row → read-merge-write |
| Delete | `POST /panel/api/clients/del/{email}` | |
| Usage (up/down/total/expiry) | `GET /panel/api/clients/traffic/{email}` | |
| Config links | `GET /panel/api/clients/links/{email}` | returns `vless://`, `vmess://`, … |
| Subscription URLs | `GET /panel/api/clients/subLinks/{subId}` | |
| Reset connections | `POST /panel/api/clients/clearIps/{email}` | |
| Plan inbound picker | `GET /panel/api/inbounds/options` | id, remark, protocol, port |
| Admin dashboard health | `GET /panel/api/server/status` | CPU/mem/xray/net |

**Key facts the bot relies on:**
- `totalGB` is **bytes** (`0` = unlimited). The bot converts GB → bytes.
- `expiryTime` is **epoch milliseconds** (`0` = never expires).
- UUID/subId are generated server-side on `add`; the bot reads them back via `get`.
- `update` replaces the whole client row, so the bot fetches the current row, merges new totals/secrets, then writes back (so quotas/secrets aren't clobbered).

---

## 3. Project structure

```
vpn-bot/
├── bot/
│   ├── main.py                  # entrypoint: dispatcher, routers, session, polling + IPN server
│   ├── config.py                # pydantic-settings env config + computed properties
│   ├── filters.py               # IsAdmin filter
│   ├── db/
│   │   ├── base.py              # async engine, session factory, init_db()
│   │   ├── models.py            # User, Plan, Order, Service (+ enums)
│   │   └── repo.py              # CRUD helpers
│   ├── panel/
│   │   ├── client.py            # async httpx 3X-UI client (Bearer, success-checked)
│   │   └── schemas.py           # InboundOption, ClientInfo, ClientTraffic
│   ├── services/
│   │   ├── provisioning.py      # create/renew/extend/disable/delete + links/QR
│   │   ├── pricing.py           # plan pricing, available methods, captions
│   │   ├── fulfillment.py       # post-payment: provision + deliver + notify
│   │   └── delivery.py          # config text + QR sending
│   ├── payments/
│   │   ├── base.py              # PaymentProvider ABC + registry
│   │   ├── manual.py            # card-to-card receipt
│   │   ├── stars.py             # Telegram Stars
│   │   └── crypto.py            # NowPayments invoice
│   ├── connectivity/
│   │   ├── links.py             # share-link → xray outbound parser
│   │   └── xray.py              # local xray-core process manager
│   ├── web/
│   │   └── ipn.py               # aiohttp NowPayments IPN webhook (HMAC verified)
│   ├── handlers/
│   │   ├── user/{start,plans,checkout,myservices}.py
│   │   └── admin/{dashboard,plans_admin,orders_admin,users_admin,broadcast}.py
│   ├── keyboards/{user_kb,admin_kb}.py
│   ├── states/forms.py          # FSM states
│   └── utils/{format,qr}.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
└── GUIDE.md                     # this file
```

---

## 4. Prerequisites

- **Python 3.11+** (works on 3.9+, but 3.11+ recommended).
- A running **3X-UI panel** with an **API token** (Settings → Security → API Token).
- A **Telegram bot token** from [@BotFather](https://t.me/BotFather).
- Your **Telegram numeric user ID** (get it from [@userinfobot](https://t.me/userinfobot)) for admin access.
- *(Only for XRAY connection mode)* the **xray-core** binary installed on the host.
- *(Only for crypto)* a **NowPayments** account (API key + IPN secret) and a public URL.

---

## 5. Installation

```bash
cd vpn-bot

# create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# create your config
cp .env.example .env
# then edit .env (see section 6)
```

The SQLite database and tables are created automatically on first run — no migration step needed.

---

## 6. Configuration reference (.env)

### Telegram

| Key | Required | Default | Description |
|---|---|---|---|
| `BOT_TOKEN` | ✅ | — | Bot token from @BotFather |
| `ADMIN_IDS` | ✅ | — | Comma-separated Telegram numeric IDs with admin access (e.g. `111,222`) |

### Telegram connection mode

| Key | Required | Default | Description |
|---|---|---|---|
| `CONNECT_MODE` | — | `DIRECT` | `DIRECT` \| `PROXY` \| `XRAY` |
| `PROXY_MODE` | — | `OFF` | Legacy switch; `ON` forces PROXY when `CONNECT_MODE` is blank |
| `PROXY_URL` | for PROXY | — | `socks5://host:1080`, `socks5://user:pass@host:1080`, or `http://host:8080` |
| `XRAY_CONFIG_URL` | for XRAY | — | A full share link: `vless://…`, `vmess://…`, `trojan://…`, `ss://…` |
| `XRAY_BIN` | for XRAY | `xray` | Path to the xray binary (or just `xray` if on `PATH`) |
| `XRAY_SOCKS_PORT` | — | `10808` | Local SOCKS port xray exposes for the bot |

See [section 7](#7-telegram-connection-modes) for details.

### 3X-UI panels

Panels are **not** configured in `.env`. Add them in Telegram: **Admin Panel → Panels**. Each panel stores:

- Display name, base URL, API token, TLS verify flag, optional subscription base URL
- Each plan is bound to one panel; each service remembers which panel it was created on

### Database & UX

| Key | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | — | `sqlite+aiosqlite:///./vpnbot.db` | Any SQLAlchemy async URL |
| `DEFAULT_LANG` | — | `en` | Default language tag |
| `SUPPORT_CONTACT` | — | `@support` | Shown in Support menu |
| `BRAND_NAME` | — | `My VPN` | Shown in welcome message |

### Payments

| Key | Required | Default | Description |
|---|---|---|---|
| `CARD_NUMBER` | for card | — | Destination card for card-to-card |
| `CARD_HOLDER` | for card | — | Cardholder name |
| `FIAT_CURRENCY` | — | `IRR` | Currency label for card/manual payments |
| `STARS_ENABLED` | — | `true` | Enable Telegram Stars |
| `CRYPTO_ENABLED` | — | `false` | Enable crypto (NowPayments) |
| `NOWPAYMENTS_API_KEY` | for crypto | — | NowPayments API key |
| `NOWPAYMENTS_IPN_SECRET` | for crypto | — | IPN secret for HMAC verification |
| `PUBLIC_BASE_URL` | for crypto | — | Public URL of the bot host for IPN callbacks |
| `WEBHOOK_HOST` | — | `0.0.0.0` | aiohttp IPN server bind host |
| `WEBHOOK_PORT` | — | `8080` | aiohttp IPN server port |

> A payment method only appears for a plan if its **price for that method is set** *and* the method is enabled/configured globally.

---

## 7. Telegram connection modes

Choose how the bot reaches the Telegram API with `CONNECT_MODE`. This only affects the **bot ↔ Telegram** link; panel API calls use the panel assigned to each plan/service.

### DIRECT (default)
```env
CONNECT_MODE=DIRECT
```
Standard connection. Use when the host can reach Telegram.

### PROXY
Route through an existing proxy (e.g. you already run a SOCKS/HTTP proxy):
```env
CONNECT_MODE=PROXY
PROXY_URL=socks5://127.0.0.1:1080
```
- Supports `http`, `https`, and `socks5` (with optional `user:pass@`).
- SOCKS support comes from `aiohttp-socks` (already in `requirements.txt`).

### XRAY
Spin up a **local xray-core** instance from a config link and route Telegram through it. Ideal when you have a working VPN config but no separate proxy.
```env
CONNECT_MODE=XRAY
XRAY_CONFIG_URL=vless://uuid@host:443?type=ws&security=tls&sni=cdn.example.com&host=cdn.example.com&path=%2Fws&fp=chrome#MyNode
XRAY_BIN=xray
XRAY_SOCKS_PORT=10808
```

**What happens at startup:**
1. The link is parsed into an xray outbound (`bot/connectivity/links.py`).
2. A temporary config is written with a local SOCKS inbound on `127.0.0.1:XRAY_SOCKS_PORT`.
3. `xray run -c <config>` is launched as a subprocess.
4. The bot waits until the SOCKS port is ready, then connects via `socks5://127.0.0.1:<port>`.
5. On shutdown, the xray process is terminated and the temp config removed.
6. If xray fails to start, the bot logs the error and **falls back to DIRECT**.

**Supported link types:** `vless`, `vmess`, `trojan`, `ss` (shadowsocks).
**Not supported:** `hysteria` / `hy2` (these are not xray-core protocols).

**Installing xray-core:**
```bash
# Linux x86_64 example
mkdir -p /opt/xray && cd /opt/xray
curl -L -o xray.zip https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip
unzip xray.zip
chmod +x xray
# then set in .env:  XRAY_BIN=/opt/xray/xray
```
Or install it system-wide so `xray` is on `PATH` and keep `XRAY_BIN=xray`.

---

## 8. Payment methods

All methods converge on `services/fulfillment.py:fulfill_order()`, which provisions the service and delivers configs. Fulfillment is **idempotent** (a paid order won't be provisioned twice).

### Card-to-card (manual receipt)
1. Customer picks the card method; the bot shows `CARD_NUMBER` / `CARD_HOLDER` and the amount.
2. Customer uploads a **receipt photo**; the order moves to `awaiting_review`.
3. Admins receive the photo with **Approve / Reject** buttons.
4. **Approve** → auto-provision + deliver. **Reject** → customer is notified.

Requires: `CARD_NUMBER`, `CARD_HOLDER`, and a plan `price_fiat > 0`.

### Telegram Stars
Native in-app payment (currency `XTR`). The bot sends an invoice; `pre_checkout_query` is approved automatically; on `successful_payment` the order is fulfilled instantly.

Requires: `STARS_ENABLED=true` and a plan `price_stars > 0`. No external credentials.

### Crypto (NowPayments)
1. The bot creates a hosted invoice via the NowPayments API and sends the pay link.
2. NowPayments calls the bot's **IPN endpoint** `POST /nowpayments/ipn` (HMAC-SHA512 verified).
3. On a paid status (`finished`/`confirmed`/`sending`), the order is fulfilled automatically.

Requires: `CRYPTO_ENABLED=true`, `NOWPAYMENTS_API_KEY`, `NOWPAYMENTS_IPN_SECRET`, a publicly reachable `PUBLIC_BASE_URL`, and a plan `price_usd > 0`.

> When `CRYPTO_ENABLED=true`, an aiohttp server starts on `WEBHOOK_HOST:WEBHOOK_PORT`. Expose it publicly (reverse proxy / port-forward) so the IPN path `PUBLIC_BASE_URL/nowpayments/ipn` is reachable from the internet. The bot runs fine on Stars + card-to-card alone with crypto disabled (no inbound webhook needed).

---

## 9. Running the bot

```bash
source .venv/bin/activate
python -m bot.main
```

Expected startup logs:
```
INFO | vpnbot | Telegram connection: DIRECT        # or PROXY / XRAY
INFO | vpnbot | Starting bot polling for My VPN …
```

The bot uses **long-polling** for Telegram (no public URL required unless you enable crypto IPN).

First-time setup inside Telegram:
1. Send `/start` to your bot.
2. As an admin (your ID in `ADMIN_IDS`), tap **🛠 Admin Panel → 🖥 Panels → ➕ Add panel** and register your 3X-UI panel (URL + API token).
3. Tap **🗂 Plans → ➕ New Plan**, pick the panel, select inbounds, and set prices.
4. Customers can now buy.

---

## 10. Customer experience walkthrough

- **/start** → registers the user and shows the main menu.
- **🛒 Buy Plan** → lists active plans → tap a plan → see details → choose a payment method.
- **Payment** → complete via card/Stars/crypto (see [section 8](#8-payment-methods)).
- **Delivery** → on success the bot sends the subscription link, config links, and a scannable **QR code**.
- **🛡 My Services** → lists purchased services. For each:
  - **📊 Usage** — progress bar, used/total traffic, days left.
  - **📋 Configs / QR** — config links, subscription URL, QR image.
  - **🔄 Renew** — pick a plan and pay to extend/top-up.
  - **🧹 Reset connections** — clears active IPs (`clearIps`) to log out other devices.
- **💬 Support / ℹ️ Help** — contact info and usage instructions.

Customers import the subscription link or a config into apps like **v2rayNG**, **Hiddify**, **Streisand**, **NekoBox**, etc.

---

## 11. Admin panel walkthrough

Open with the **🛠 Admin Panel** button (visible only to `ADMIN_IDS`).

- **📊 Dashboard** — counts (users, active services, pending receipts) + live health for each active panel.
- **🖥 Panels** — add/edit/test/enable-disable/delete 3X-UI panels. Each panel stores URL, API token, TLS verify, and optional subscription base URL. Delete is blocked while plans or services are linked; use disable instead. **Backfill orphan services** assigns `panel_id` to legacy services whose plan already points at this panel.
- **🗂 Plans** — list/create/enable-disable/delete plans. Creating a plan is a guided flow:
  1. Title → 2. Description → 3. Traffic GB (`0` = unlimited) → 4. Duration days (`0` = never) → 5. Device/IP limit (`0` = unlimited) → 6. **Panel picker** → 7. **Inbound picker** (multi-select from that panel's `inbounds/options`; or type IDs like `3,5` as a fallback) → 8. Card price → 9. Stars price → 10. USD price.
  - A price of `0` disables that payment method for the plan.
  - Changing a plan's panel clears its inbounds (inbound IDs are panel-local).
- **🧾 Pending Receipts** — review card-to-card receipts; **Approve** auto-provisions, **Reject** notifies the buyer.
- **👤 Manage Service**
  - **🔍 Find service by email** → shows status/usage with actions: **Extend** (`days[,gb]`, e.g. `30,50`), **Enable/Disable**, **Configs**, **Delete**.
  - **🎁 Grant service to user** → enter a Telegram ID and pick a plan to provision **free of charge** (e.g. trials, comps).
- **📣 Broadcast** — send a message (any content) to all users, rate-limited.
- **⚙️ Settings** — read-only view of the active configuration (edit via `.env` + restart).

---

## 12. Data model

SQLite via SQLAlchemy 2.0 async. Tables auto-created on startup.

- **panels** — `id`, `name`, `base_url`, `api_token`, `verify_tls`, `sub_base_url`, `is_active`, `sort_order`, `created_at`
- **users** — `tg_id` (PK), `username`, `full_name`, `lang`, `is_admin`, `is_blocked`, `balance`, `created_at`
- **plans** — `id`, `title`, `description`, `traffic_gb`, `duration_days`, `limit_ip`, `inbound_ids` (JSON), `panel_id`, `price_fiat`, `price_usd`, `price_stars`, `is_active`, `is_trial`, `sort_order`, `created_at`
- **orders** — `id`, `user_tg_id`, `plan_id`, `renew_service_id`, `method` (`card`/`stars`/`crypto`/`manual`), `amount`, `currency`, `status` (`pending`/`awaiting_review`/`paid`/`rejected`/`cancelled`), `provider_ref`, `receipt_file_id`, timestamps
- **services** — `id`, `user_tg_id`, `plan_id`, `order_id`, `panel_id`, `email` (UNIQUE — the join key into the panel), `sub_id`, `inbound_ids` (JSON), `total_bytes`, `expiry_time`, `status` (`active`/`disabled`/`expired`/`deleted`), `created_at`

The `email` field is the canonical link between a bot `Service` and the panel client.

---

## 13. Provisioning & lifecycle logic

Implemented in `bot/services/provisioning.py`.

- **Email generation:** `u{tg_id}-p{plan_id}-{timestamp+rand}` — unique and stable.
- **Create** (`provision_for_plan`): `clients/add` with `totalGB = traffic_gb × 1024³` (0 if unlimited), `expiryTime = now_ms + days × 86400000` (0 if unlimited), `tgId`, `limitIp`, plan's `inboundIds`; then `get/{email}` for `subId`; then `links/{email}` for URLs.
- **Renew** (`renew_service`): reads current client, **extends from the later of now/current expiry** so unused time isn't lost; tops up quota for limited plans, keeps unlimited as unlimited; preserves secrets (`uuid`, `subId`, `flow`, …) on the merged `update`.
- **Extend (admin)** (`extend_service`): adds days and optionally GB; **days-only extension leaves the quota untouched** (does not reset to unlimited).
- **Enable/disable** (`set_service_enabled`): merged `update` with `enable` flag.
- **Delete** (`delete_service`): `del/{email}` + marks the row deleted.
- **Reset connections** (`reset_connections`): `clearIps/{email}`.
- **Subscription URL:** `{panel.sub_base_url or panel.base_url}/sub/{subId}`.

---

## 14. Deployment (systemd / Docker)

### systemd (Linux)

`/etc/systemd/system/vpnbot.service`:
```ini
[Unit]
Description=VPN Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/vpn-bot
ExecStart=/opt/vpn-bot/.venv/bin/python -m bot.main
EnvironmentFile=/opt/vpn-bot/.env
Restart=always
RestartSec=5
User=vpnbot

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vpnbot
sudo journalctl -u vpnbot -f      # logs
```

> Tip: `EnvironmentFile` lines must be plain `KEY=value` (no quotes needed). The bot also reads `.env` from its working directory, so either approach works.

### Docker

`Dockerfile`:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "-m", "bot.main"]
```

`docker-compose.yml`:
```yaml
services:
  vpnbot:
    build: .
    env_file: .env
    restart: unless-stopped
    volumes:
      - ./data:/app/data          # persist SQLite if DATABASE_URL points to /app/data
    ports:
      - "8080:8080"               # only needed if CRYPTO_ENABLED=true
```
For persistence, set `DATABASE_URL=sqlite+aiosqlite:////app/data/vpnbot.db`.

> For XRAY mode in Docker, install xray-core inside the image (add a download step to the Dockerfile) and set `XRAY_BIN` accordingly.

---

## 15. Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| Bot doesn't respond | Check `BOT_TOKEN`; check logs; if region-blocked, set `CONNECT_MODE=PROXY` or `XRAY`. |
| `the greenlet library is required` | `pip install greenlet` (already pinned in `requirements.txt`). |
| Admin buttons not visible | Your numeric ID must be in `ADMIN_IDS` (comma-separated). Restart after editing `.env`. |
| “No payment methods configured” on a plan | Set at least one price (`price_fiat`/`price_stars`/`price_usd`) **and** enable that method globally (`CARD_NUMBER`, `STARS_ENABLED`, `CRYPTO_ENABLED`). |
| Provisioning fails after payment | Ensure the plan has a panel assigned; test the panel in **Panels → Test**; verify inbound IDs exist on that panel. Admins get a failure notification. |
| Configs come back empty | The client may have no enabled inbound/URL-capable protocol; check the inbound on the panel; retry from **My Services → Configs**. |
| `xray SOCKS port did not become ready` | `XRAY_CONFIG_URL` invalid or server unreachable; verify the link works in a client; check `XRAY_BIN` path. |
| `xray binary not found` | Install xray-core or set `XRAY_BIN` to its full path. |
| Crypto payments never confirm | `PUBLIC_BASE_URL` must be publicly reachable; verify NowPayments IPN URL and `NOWPAYMENTS_IPN_SECRET`. |
| `Port already in use` (8080) | Change `WEBHOOK_PORT` (only relevant when crypto is enabled). |
| TLS error contacting panel | Edit the panel in **Panels** and set TLS verify to `false` (only if you trust the cert). |

Enable more detail by reading the console logs — every panel call failure logs the panel's own error message.

---

## 16. Security notes

- **Never commit `.env`** — it contains the bot token and payment credentials. It's already in `.gitignore`.
- **Panel API tokens** are stored in the bot database and have full panel power — restrict server access and rotate tokens if leaked.
- Use **HTTPS** for panel base URLs and `PUBLIC_BASE_URL`.
- NowPayments IPN is **HMAC-verified**; keep `NOWPAYMENTS_IPN_SECRET` secret and correct, or callbacks are rejected (401).
- Card-to-card relies on **manual admin verification** — always confirm receipts before approving.
- Restrict the SQLite file's permissions; it holds order/customer metadata.

---

## 17. FAQ

**Can I route the panel API calls through the proxy/xray too?**
Not currently — only the bot ↔ Telegram link uses the connection mode. The `httpx` panel client connects to each panel's configured base URL directly. This can be added on request.

**Upgrading from single-panel `.env` setup?**
Remove `PANEL_URL`, `PANEL_TOKEN`, `PANEL_VERIFY_TLS`, and `SUB_BASE_URL` from `.env`. Add your panel(s) in **Admin Panel → Panels**, assign each plan to a panel, then use **Backfill orphan services** if you have existing services without `panel_id`.

**Do I need a public server / domain?**
Only for crypto (NowPayments IPN). Stars and card-to-card work entirely over long-polling.

**Which apps can customers use?**
Any that accept the subscription link or the per-protocol URLs (v2rayNG, Hiddify, Streisand, NekoBox, etc.).

**How are renewals handled so customers don't lose time/quota?**
Renewals extend from the later of now/current expiry and top up quota; admin day-only extensions never reset quota.

**Can plans attach to multiple servers/inbounds?**
Yes — the plan inbound picker is multi-select; a plan can attach a client to several inbounds at once.

**Is multi-language supported?**
Yes, both English and Persian languages are supported.

---

*Built as an MVP — extend freely. For changes to provisioning, payments, or connection modes, see the corresponding modules under `bot/`.*

