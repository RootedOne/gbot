# Middle Server Relay Service

This is a stateless FastAPI proxy service to relay VPN selling bot REST API calls to unreachable 3X-UI panel servers.

```
Bot Server ===(Internet)===> Middle Server (Relay) ===(Private/Direct Network)===> Panel Server
```

---

## Configuration

The relay service reads configuration from environment variables:

| Variable | Required | Default | Description |
|---|---|---|---|
| `RELAY_TOKEN` | Yes | `default-secure-relay-token` | Secret token to authenticate request from the bot. Must match `Middle Server Token` in Telegram Panel admin settings. |
| `HOST` | No | `0.0.0.0` | Listen host IP |
| `PORT` | No | `8000` | Listen port |

---

## Installation & Deployment

### Method 1: Automatic Installer (Recommended)

You can install, configure, and secure the Middle Server Relay automatically using our premium interactive installer script:

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/rootedOne/relay-server/main/relay/install-relay.sh)"
```

Alternatively, if you cloned the repository locally:
```bash
sudo chmod +x relay/install-relay.sh
sudo ./relay/install-relay.sh
```

The installer handles:
- Checking/Installing Python 3.10+
- Cloning the latest repository version
- Creating a dedicated non-root security user (`vpnrelay`)
- Generating/Configuring a secure `RELAY_TOKEN`
- Creating a Systemd service
- (Optional) Setting up Nginx reverse proxy + SSL (Let's Encrypt)
- (Optional) Hardening the system with UFW firewall

---

### Method 2: Bare Metal Manual Setup (Alternative)

1. Clone or upload this `relay` directory to the Middle Server.
2. Install Python 3.10+ and setup a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Test running the service:
   ```bash
   RELAY_TOKEN="your-secure-token" uvicorn main:app --host 0.0.0.0 --port 8000
   ```
4. Configure as a systemd service:
   Create `/etc/systemd/system/vpn-relay.service`:
   ```ini
   [Unit]
   Description=VPN Bot Middle Server Relay
   After=network.target

   [Service]
   Type=simple
   WorkingDirectory=/opt/vpn-relay
   ExecStart=/opt/vpn-relay/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
   Environment="RELAY_TOKEN=your-secure-token"
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```
5. Reload and start:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now vpn-relay
   sudo systemctl status vpn-relay
   ```

---

### Method 2: Docker

1. Create a `Dockerfile` in the relay directory:
   ```dockerfile
   FROM python:3.10-slim
   WORKDIR /app
   COPY requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt
   COPY main.py .
   EXPOSE 8000
   CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
   ```
2. Build and run:
   ```bash
   docker build -t vpn-relay .
   docker run -d -p 8000:8000 -e RELAY_TOKEN="your-secure-token" --name vpn-relay vpn-relay
   ```

---

## Health check

You can test the relay connectivity by querying:
```bash
curl http://127.0.0.1:8000/health
```
This returns the uptime and performance statistics (requests, latency, failure rates).
