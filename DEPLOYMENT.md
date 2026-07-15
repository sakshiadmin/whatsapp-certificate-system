# Deployment Guide

## 1. What this app actually needs from a host

Before comparing platforms, it helps to be clear about the actual
requirements, since "20,000 students" sounds like it needs serious
infrastructure but doesn't, for this specific workload:

- Traffic pattern: WhatsApp messages arriving one at a time as students
  type "Certificate" — even in a worst-case rush right after a course
  deadline, this is tens of requests per minute, not per second. There is
  no bulk/batch endpoint being hit.
- Each request does a few seconds of work (Sheets lookup, PDF generation,
  Google Drive upload, one Interakt API call) — CPU and memory needs are
  modest; no GPU, no heavy background jobs, no database server to run
  (Google Sheets is the database).
- It must always be reachable and respond within a few seconds — Interakt
  will treat a slow/non-200 response as a webhook failure and, after 5
  failures in 10 minutes, will **disable your webhook** until you manually
  re-enable it. This means "spins down when idle, cold-starts on the next
  request" hosting is actively dangerous for this app, not just slow.
- No special networking (no WebSockets, no long-lived connections, no
  custom ports) — just one HTTPS endpoint.

## 2. Platform comparison

| | **Railway** | **Render** | **DigitalOcean** |
|---|---|---|---|
| Deploy model | Git push → auto-build & deploy | Git push → auto-build & deploy | App Platform (managed) or Droplet (raw VM) |
| Fits a Python/FastAPI app with native deps (reportlab, pypdf, gspread)? | Yes, standard Docker/Nixpacks build | Yes, standard build | Yes |
| Always-on (no cold start) | Yes, on paid Hobby plan | Only on paid instances — **free tier spins down after 15 min idle**, causing a cold-start on the next webhook | Yes, always-on |
| Setup effort for a beginner | Low — dashboard-driven, env vars in UI, auto HTTPS | Low — very similar to Railway | Medium (App Platform) to High (Droplet) |
| Cost at this scale | ~$5–10/month | ~$7+/month for an always-on instance | ~$5–12/month (App Platform) |

## 3. Recommendation: **Railway**, for the compute/API hosting

- It avoids Render's free-tier cold-start risk without you needing to
  remember to pick the right paid tier — Railway's standard plan is
  always-on by default.
- It avoids DigitalOcean Droplet's sysadmin overhead (you'd be manually
  configuring nginx/systemd/SSL/firewall) — not a good use of time for a
  first production deployment on a tight deadline.

## 4. Deploy to Railway — step by step

### 4.1 Push your code to GitHub

1. Go to [github.com/new](https://github.com/new), create a new
   **private** repository (private, since — even though secrets aren't
   committed — there's no reason to make this public), e.g.
   `whatsapp-certificate-system`. Do not initialize with a README (you
   already have one).
2. In your terminal, inside the project folder:
   ```bash
   git remote add origin https://github.com/YOUR_USERNAME/whatsapp-certificate-system.git
   git branch -M main
   git push -u origin main
   ```
3. Double-check `secrets/service-account.json` and `.env` were **not**
   pushed — run `git status` and confirm they don't appear (they're
   excluded by `.gitignore`). If you ever see them staged, run
   `git rm --cached <file>` before committing.

### 4.2 Create the Railway project

1. Go to [railway.app](https://railway.app), sign up/log in (GitHub login
   is easiest since you already have the repo there).
2. Click **New Project → Deploy from GitHub repo**.
3. Authorize Railway to access your GitHub account if prompted, then
   select the `whatsapp-certificate-system` repo.
4. Railway detects the `Procfile` and Python project automatically and
   starts a build. Let it run — this first build will likely fail because
   environment variables aren't set yet; that's expected, continue below.

### 4.3 Set environment variables

1. In your Railway project, click the service (the box representing your
   app) → **Variables** tab.
2. Click **Raw Editor** and paste in every variable from your local
   `.env` file **except** `GOOGLE_SERVICE_ACCOUNT_FILE` (handled
   differently, see 4.4) — for the rest, copy real values, not the
   `.env.example` placeholders.
3. Add one more variable Railway provides automatically: you do not need
   to set `PORT` yourself — Railway injects it, and the `Procfile` already
   uses `$PORT`.
4. Click **Save/Update Variables**. This triggers a redeploy.

### 4.4 Get the Google service account credentials onto the server

You cannot commit `service-account.json` to Git, but the app needs to read
it as a file. The simplest approach:

1. Open your local `secrets/service-account.json`, copy its **entire
   contents** (it's one JSON object).
2. In Railway's Variables tab, add a new variable
   `GOOGLE_SERVICE_ACCOUNT_JSON` and paste the full JSON as its value.
3. In Railway's service **Settings → Deploy → Custom Start Command**, use:
   ```
   bash -c 'echo "$GOOGLE_SERVICE_ACCOUNT_JSON" > secrets/service-account.json && gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 1 -b 0.0.0.0:$PORT --timeout 60'
   ```
   This writes the JSON from the environment variable to the exact file
   path the app already expects, immediately before starting the server,
   every time the app boots — so the code in `sheets_service.py` and
   `storage_service.py` never needs to know it's running on Railway vs.
   your laptop.
4. Keep `GOOGLE_SERVICE_ACCOUNT_FILE=secrets/service-account.json` set as
   a normal variable too (same value as in `.env.example`).

### 4.5 Verify the deploy

1. Railway → your service → **Deployments** tab → wait for the latest
   deployment to show **Success**.
2. Click into it → **View Logs**. You should see
   `"app_startup_complete"` in the JSON logs, with no tracebacks.
3. Railway → **Settings → Networking → Generate Domain** to get a public
   `*.up.railway.app` URL. Visit `https://<that-url>/healthz` — confirm
   you get `{"status": "ok"}`.

### 4.6 Point your own domain at it (optional, but recommended)

If you use Cloudflare for DNS:

1. Railway → **Settings → Networking → Custom Domain → Add Domain**,
   enter e.g. `certbot-api.yourdomain.com`. Railway shows you a CNAME
   target.
2. Cloudflare DNS dashboard → **Add record** → Type `CNAME`, Name
   `certbot-api`, Target = the value Railway gave you, Proxy status: you
   can leave Cloudflare's orange-cloud proxy ON (recommended — extra
   DDoS/TLS layer) or set to DNS-only; either works with Railway.
3. Wait a few minutes for DNS to propagate, then confirm
   `https://certbot-api.yourdomain.com/healthz` works.

### 4.7 Point Interakt's webhook at your real URL

1. Interakt Dashboard → **Settings → Developer Settings → Configure
   Webhook → Edit**.
2. Set **Webhook URL** to `https://certbot-api.yourdomain.com/webhooks/interakt`
   (or the `*.up.railway.app` URL if you skipped the custom domain).
3. Confirm the **Secret Key** here matches `INTERAKT_WEBHOOK_SECRET` in
   Railway's variables exactly.
4. Save, then send a real test WhatsApp message per `TESTING.md` Section 3
   ("Testing in production").

## 5. Scaling notes for later (not needed for launch)

- This app runs a **single worker process** on purpose (see the `Procfile`
  and the docstring in `app/utils/idempotency.py`) — the in-memory Sheets
  cache and duplicate-request lock are only consistent within one process.
  At 20,000 total students with realistic message-arrival rates, one
  worker on a small Railway instance is more than sufficient.
- If you ever need multiple workers/instances (e.g., much higher sustained
  traffic), the cache and lock need to move to a shared store (Redis is
  the standard choice) — Google Sheets remains your correctness source of
  truth either way, so this is a performance upgrade, not a correctness
  fix, if/when you need it.
