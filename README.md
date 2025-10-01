# Claude Playwright Agent (MCP)

Automated receipt and refund processing with a FastAPI backend, a browser automation agent powered by Anthropic Claude via the Model Context Protocol (MCP), and a split-screen UI embedding a live noVNC session for manual interventions (e.g., 30‑day device verification codes).

## Overview
- Backend: FastAPI (Python 3.12)
- Browser automation: Playwright MCP server (`@playwright/mcp`) launched via `npx` (inside the container)
- AI: Anthropic Claude Messages API
- Credentials: Bitwarden SDK (service account access token) to retrieve login secrets
- UI: Dashboard (forms for receipts/refunds) + Split interface (dashboard + VNC)
- Headless/headed: Runs headed inside an Xvfb display; accessible via embedded noVNC (port 6080)
- Persistence: Browser profile stored in a volume-mounted directory so cookies survive restarts

## Directory structure (key files)
- `main.py` — FastAPI app and REST endpoints
- `client.py` — STDIO MCP client that launches `@playwright/mcp` and proxies tool calls
- `dashboard.html` — Control panel (send receipt, process refund, start/finish verification)
- `split-interface.html` — Split view: dashboard on left, noVNC (browser) on right
- `docker-compose.yml`, `Dockerfile`, `supervisord.conf`, `docker-entrypoint.sh` — Container orchestration
- `browser-profiles/` — Host directory volume-mounted to `/app/browser-profiles` for persistent browser data
- `client.log` — Combined application log

## Prerequisites
- Docker and Docker Compose installed on the host (Mac mini for production)
- Valid Anthropic API key
- Bitwarden service account access token with the `secrets` scope
- A Bitwarden Secret containing JSON credentials for your gateway (username/password)

## Environment variables (.env)
Create a `.env` file at the project root. Do not commit it. Required variables:

- `ANTHROPIC_API_KEY` — Claude API key
- `ACCESS_TOKEN` — Bitwarden service account access token (scoped to secrets)
- `ZERO5_SECRET_ID` — Bitwarden Secret ID that contains JSON with credentials for the gateway. The JSON should look like:

```json path=null start=null
{
  "username": "your_gateway_username",
  "password": "your_gateway_password"
}
```


Example `.env` skeleton:
```bash path=null start=null
ANTHROPIC_API_KEY={{YOUR_ANTHROPIC_API_KEY}}
ACCESS_TOKEN={{YOUR_BITWARDEN_SERVICE_ACCOUNT_ACCESS_TOKEN}}
ZERO5_SECRET_ID={{YOUR_BITWARDEN_SECRET_ID_WITH_GATEWAY_CREDS}}
```

Note: Do not include real keys in version control.

## First-time setup
1) Ensure the browser profile directory exists on the host (so cookies persist):
```bash path=null start=null
mkdir -p browser-profiles/test-profile-3
```

2) (Optional) Seed cookies if you have a known-good profile:
- Place your `cookies.sqlite` at `browser-profiles/test-profile-3/cookies.sqlite` before first run.
- The app will reuse and update this file going forward.

3) Build and start the stack:
```bash path=null start=null
docker compose build
docker compose up -d
```

4) Open the UI:
- Split interface (recommended): http://localhost:5000/
- Dashboard only: http://localhost:5000/dashboard
- noVNC direct (optional): http://localhost:6080/vnc.html (password defaults to `vncpassword`)

## Usage
### Device verification (30-day code)
Some gateways occasionally prompt for a device recognition/verification code. Use the dashboard’s Device Verification section:
1) Click "Start Verification". This launches the browser with a persistent profile and navigates to the gateway.
2) In the right panel (VNC), enter the verification code on the site.
3) Click "Finish & Save Cookies". The session closes and the updated cookies remain in `browser-profiles/test-profile-3`.

The agent will reuse this profile for future automated actions (receipts/refunds).

### Send receipt
1) Enter Transaction ID
2) Choose “Send Receipt” and enter Client Email
3) Click "Send Receipt"

The backend will:
- Retrieve credentials from Bitwarden using `ZERO5_SECRET_ID`
- Launch the Playwright MCP client
- Login to `zero5.transactiongateway.com/merchants/`
- Locate the transaction and email the receipt to the client

### Process refund
1) Enter Transaction ID
2) Choose “Process Refund” and enter Refund Amount
3) Click "Process Refund"

The backend will:
- Retrieve credentials from Bitwarden using `ZERO5_SECRET_ID`
- Launch the Playwright MCP client
- Navigate to the refund flow and perform the refund

### API endpoints
- Health/Status
  - `GET /api/status` — JSON with backend status and Bitwarden connection flag
  - `GET /api/logs` — Returns recent log lines (for debugging)
- Bitwarden
  - `POST /api/bitwarden/connect` — Initializes Bitwarden client using `ACCESS_TOKEN`
- Operations
  - `POST /api/send_receipt`
    - Body:
    ```json path=null start=null
    {
      "transactionId": "1234567890",
      "clientEmail": "client@example.com"
    }
    ```
  - `POST /api/give_refund`
    - Body:
    ```json path=null start=null
    {
      "transactionId": "1234567890",
      "refundAmount": 12.34
    }
    ```
- Verification
  - `POST /api/verification/start` — Launches MCP with the persistent profile and navigates to the gateway
  - `POST /api/verification/finish` — Closes the session; cookies remain in the profile directory

## Data persistence
- Browser profile is stored in the host directory `./browser-profiles/test-profile-3` and mounted at `/app/browser-profiles/test-profile-3`.
- Cookies and site data persist across container restarts and deployments.
- Application logs are written to `client.log` in the project root (also accessible via `GET /api/logs`).

## Deployment (Mac mini)
- Copy project files to the Mac mini
- Provide a production `.env` with the required keys
- Ensure ports 5000 (API/UI) and 6080 (noVNC) are open or reverse-proxied as needed
- Start with `docker compose up -d`
- Access the split interface at `http://<mac-mini-host>:5000/`

## Troubleshooting
- VNC shows black/blank screen or no window:
  - The first run may need to download browser binaries (`browser_install`). Give it 1–2 minutes on first start.
  - Click “Refresh” above the VNC panel in the split interface.
  - Ensure the container has `DISPLAY=:99`. This is configured via supervisor.
  - Optionally set `PLAYWRIGHT_HEADLESS=0` in `.env` and add it to the compose service environment.
- “Browser not installed” / `launchPersistentContext` errors:
  - The app calls `browser_install` before navigating. First run downloads can be slow.
  - Check `client.log` for details: `docker compose logs -f` or `GET /api/logs`.
- Bitwarden connection fails:
  - Confirm `ACCESS_TOKEN` is valid and scoped to `secrets`.
  - Confirm `ZERO5_SECRET_ID` refers to a Secret that contains the expected JSON (username/password).
- Cookies are not persisting:
  - Ensure the host directory `./browser-profiles/test-profile-3` exists and is writable before starting.
  - Do not overwrite cookies on startup; this project intentionally avoids copying seed cookies after first run.

## Security notes
- Never commit `.env` or `cookies.sqlite` to version control.
- Limit network exposure of ports 5000 and 6080 (consider a reverse proxy with auth, VPN, or LAN-only access).
- In production, restrict CORS in `main.py` to known origins.

## Commands reference
- Build and run:
```bash path=null start=null
docker compose build && docker compose up -d
```
- Tail logs:
```bash path=null start=null
use command of choice to view file within docker container vie docker exec | log file is client.log
```
- Stop:
```bash path=null start=null
docker compose down
```

---
If you run into issues or want to extend the dashboard or automation flows, see `main.py` and `client.py` for the HTTP endpoints and MCP integration points.
