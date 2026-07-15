# WhatsApp Certificate Generation System

Automatically issues course completion certificates over WhatsApp when a
student sends the word **"Certificate"**, using Interakt as the WhatsApp
Business Solution Provider, Google Sheets as the student database, and
Google Drive as certificate storage. All replies are sent as free **session
messages** — no WhatsApp template approval needed, zero per-message cost.

---

## 1. Architecture

```
Student sends "Certificate" on WhatsApp
        │
        ▼
 WhatsApp Business Number (via Meta, routed through Interakt)
        │
        ▼
 Interakt "incoming customer message" Webhook  ──(HMAC-signed POST)──▶  Your FastAPI app
        │                                                                     │
        │                                                    1. Verify signature
        │                                                    2. Extract phone + text
        │                                                    3. Look up student in Google Sheet
        │                                                    4. Branch on Status
        │                                                             │
        │              ┌───────────────── not found ──────────────────┤
        │              │                                               │
        │              ▼                                     not COMPLETED
        │     Send "not registered" session text              │
        │                                                     ▼
        │                                    Send "not completed" session text
        │
        │                                     Status == COMPLETED
        │                                               │
        │                                               ▼
        │                                  Generate certificate PDF (ReportLab + pypdf)
        │                                               │
        │                                               ▼
        │                                  Upload PDF to Google Drive (public link)
        │                                               │
        │                                               ▼
        │                              Send certificate as session document message
        │                                    (via Interakt API)
        │                                               │
        │                                               ▼
        │                              Update Google Sheet: Certificate Sent = "Sent"
        │                              + store Drive URL for future resends
        ▼
   Student receives the certificate as a WhatsApp document
```

### Why session messages (not templates)?

WhatsApp allows businesses to send free-form text and media replies
("session messages") within 24 hours of the customer's last message.
Since the student always messages first ("Certificate") and your server
replies within seconds, you are **always** inside that window.

- **Zero cost**: session messages have no per-message charge from Meta
  (templates cost ~₹0.15–0.50 each).
- **No approval wait**: no 24–48 hour Meta review, no rejection risk.
- **Instant changes**: update reply text any time via `.env` — no
  resubmission needed.

---

## 2. Folder structure

```
whatsapp-certificate-system/
├── app/
│   ├── main.py                  # FastAPI app + webhook endpoint + orchestration
│   ├── config.py                # All environment variables, typed & validated
│   ├── logging_config.py        # Structured (JSON) logging setup
│   ├── models.py                # Pydantic models: webhook payloads, StudentRecord
│   ├── services/
│   │   ├── sheets_service.py    # Google Sheets read/search/update, with caching
│   │   ├── certificate_service.py  # PDF generation (overlay name on template)
│   │   ├── interakt_service.py  # Sending WhatsApp messages via Interakt
│   │   └── storage_service.py   # Google Drive upload
│   └── utils/
│       ├── security.py          # HMAC webhook signature verification
│       └── idempotency.py       # Per-phone-number lock (duplicate prevention)
├── tests/                       # pytest test suite (see TESTING.md)
├── scripts/
│   └── calibrate_certificate.py # Finds X/Y coordinates for the name on your template
├── assets/
│   └── certificate_template.pdf # <-- YOU add this (your existing template)
├── fonts/
│   └── YourFont-Regular.ttf     # <-- YOU add this (your custom font)
├── secrets/
│   └── service-account.json     # <-- YOU add this (Google service account key)
├── requirements.txt             # Production dependencies
├── requirements-dev.txt         # + testing dependencies
├── .env.example                 # Every environment variable, documented
├── Procfile                     # Process definition for Railway/Render
├── DEPLOYMENT.md
├── TESTING.md
└── TROUBLESHOOTING.md
```

---

## 3. Prerequisites

- Python 3.12 installed (`python3 --version`)
- VS Code installed, with the "Python" extension
- Git installed, and a GitHub account (Railway/Render deploy from a Git repo)
- An Interakt account on the **Advanced** plan (the "incoming customer
  message" webhook is only available on Advanced/Enterprise plans)
- A Google account
- Your existing certificate template as a single-page **PDF**
- Your custom font as a **.ttf** file

---

## 4. Google Cloud + Google Sheets setup

### 4.1 Create the Google Sheet (the "database")

1. Go to [sheets.google.com](https://sheets.google.com) and create a new
   blank spreadsheet. Name it, e.g., "Certificate Students".
2. Rename the first tab (bottom-left) to `Students` (matches
   `GOOGLE_SHEET_WORKSHEET_NAME` in `.env`).
3. In row 1, enter these exact column headers (capitalization matters,
   the code matches on these strings), one per cell, A1 through G1:
   `Phone`, `Name`, `Status`, `Certificate Sent`, `Certificate ID`, `Certificate URL`, `Timestamp`
4. **Important:** select the entire `Phone` column (click the column
   letter, e.g. "A"), then Format → Number → Plain text. If you don't do
   this, Google Sheets will strip leading zeros or convert long numbers to
   scientific notation, and phone lookups will silently fail.
5. Enter a few test rows, e.g.:
   `919999999999 | Asha Rao | COMPLETED | | | |`
   `918888888888 | Ravi Kumar | IN_PROGRESS | | | |`
   Store phone numbers with the country code, digits only, no "+", no
   spaces — this matches exactly what Interakt sends in the webhook.
6. Copy the Sheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/`**`THIS_LONG_ID_STRING`**`/edit`
   — save it, you'll put it in `.env` as `GOOGLE_SHEET_ID`.

### 4.2 Create a Google Cloud Project

1. Go to [console.cloud.google.com](https://console.cloud.google.com).
2. Top-left, click the project dropdown → **New Project**.
3. Name it (e.g. "certificate-bot"), leave organization as default, click
   **Create**. Wait ~10 seconds, then select it from the project dropdown.

### 4.3 Enable the Google Sheets API and Google Drive API

1. In the search bar at the top, type "Google Sheets API" and open it.
2. Click **Enable**. Wait for it to finish (a few seconds).
3. **Also enable "Google Drive API"** — search for it and click **Enable**.
   This is needed for uploading certificates to Google Drive.

### 4.4 Create a Service Account (the "robot user" that reads/writes your Sheet and Drive)

1. In the left sidebar, go to **IAM & Admin → Service Accounts**.
2. Click **+ Create Service Account**.
3. Name it (e.g. "certificate-bot-sa"), click **Create and Continue**.
4. On the "Grant this service account access to project" screen, you can
   click **Continue** without adding a role (we'll grant access at the
   Sheet level instead, which is more restrictive and safer).
5. Click **Done**.

### 4.5 Download the service account's credentials (JSON key)

1. On the Service Accounts list, click the account you just created.
2. Go to the **Keys** tab → **Add Key → Create new key**.
3. Choose **JSON**, click **Create**. A `.json` file downloads automatically.
4. Rename it `service-account.json` and place it in this project's
   `secrets/` folder. **This file is a credential — never commit it to
   Git.** (`.gitignore` in this project already excludes `secrets/`.)
5. Open the JSON file and copy the `client_email` value (looks like
   `certificate-bot-sa@your-project.iam.gserviceaccount.com`) — you need it
   next.

### 4.6 Share the Google Sheet with the service account

1. Open your Google Sheet, click **Share** (top-right).
2. Paste the `client_email` from step 4.5 into the share box.
3. Set its role to **Editor** (it needs to write the "Certificate Sent"
   column back).
4. Uncheck "Notify people" (it's a robot, not a person), click **Share**.

Without this step, every Sheets API call will fail with a permissions
error — this is the single most common setup mistake.

### 4.7 Google Drive for certificate storage (no extra setup needed!)

The service account automatically has its own Google Drive space. The app
will create a "Certificates" folder in it on first use. No manual folder
creation or sharing is needed.

**Optional**: if you want certificates in a specific Drive folder (e.g. one
in your personal Drive), create the folder, share it with the service
account's `client_email` as **Editor**, and put the folder ID in `.env` as
`GOOGLE_DRIVE_FOLDER_ID`. The folder ID is the last part of the folder's
URL: `https://drive.google.com/drive/folders/`**`THIS_PART`**.

---

## 5. Interakt setup

### 5.1 Confirm you're on the right plan

Go to your Interakt dashboard → **Settings → Plans**. You need **Advanced**
(or Enterprise) — the "incoming customer messages" webhook (which is how
your server learns a student typed "Certificate") is not available on
lower plans. If you're not on this plan, upgrade before doing anything else.

### 5.2 Get your API key

1. Dashboard → **Settings → Developer Settings**.
2. Copy the API key shown there → this is `INTERAKT_API_KEY` in `.env`.

### 5.3 Configure the webhook

1. Dashboard → **Settings → Developer Settings → Configure Webhook → Edit**.
2. **Webhook URL**: you don't have this yet on day one — for now use a
   placeholder or your local tunnel URL (see `TESTING.md` Section 2), and
   come back to update this to your real production URL after deployment
   (Section 8 in `DEPLOYMENT.md`). The URL must end in `/webhooks/interakt`,
   e.g. `https://your-app.up.railway.app/webhooks/interakt`.
3. **Secret Key**: click generate (or set your own long random string).
   Copy it → this is `INTERAKT_WEBHOOK_SECRET` in `.env`.
4. Under **Select Required Webhooks**, enable:
   - "Others" → **Message received from customers**
   - (Optional but recommended) Template Messages Sent via API → Sent /
     Delivered / Read / Failed, so you can see delivery status in logs if
     you build that out later — not required for this project's core flow.
5. Click **Submit**.

**No WhatsApp templates needed!** This system uses session messages (free-form
replies within the 24h window), so you do not need to create or submit any
WhatsApp templates for approval. This saves 24–48 hours of waiting time and
eliminates the risk of template rejection.

**Note on the webhook payload shape:** Interakt's public documentation has
shown slightly different field nesting for the "incoming customer message"
webhook across different revisions of their docs. Before going live,
capture ONE real webhook from your own test message and compare it to what
`app/main.py`'s `_extract_text_and_phone()` function expects — see
`TESTING.md` Section 2 ("Capture one real webhook first"). This is a
5-minute check that prevents a subtle "nothing happens when a student
messages us" bug in production.

---

## 6. Local project setup

### 6.1 Get the code onto your machine

```bash
git init whatsapp-certificate-system
cd whatsapp-certificate-system
# copy in all the files from this project
git add .
git commit -m "Initial commit"
```
(`git init` creates a local repository; `git add .` stages every file;
`git commit` saves a snapshot. You'll push this to GitHub in `DEPLOYMENT.md`.)

### 6.2 Create a virtual environment

A virtual environment keeps this project's Python packages separate from
everything else on your machine — without it, package versions from
different projects can conflict.

```bash
python3 -m venv .venv
source .venv/bin/activate      # macOS/Linux
# .venv\Scripts\activate       # Windows (Command Prompt)
```
Your terminal prompt should now start with `(.venv)`. Do this every time
you open a new terminal to work on this project.

### 6.3 Install dependencies

```bash
pip install -r requirements-dev.txt
```
`requirements-dev.txt` includes everything in `requirements.txt` (the
production dependencies) plus `pytest` for running tests.

### 6.4 Create your `.env` file

```bash
cp .env.example .env
```
Open `.env` in VS Code and fill in every value using what you collected in
Sections 4–5 above. Every variable is documented with a comment in
`.env.example` — read each one before filling it in.

### 6.5 Add your template PDF and font

- Copy your certificate template PDF into `assets/certificate_template.pdf`.
- Copy your `.ttf` font file into `fonts/` and update `CERTIFICATE_FONT_PATH`
  / `CERTIFICATE_FONT_NAME` in `.env` to match.

### 6.6 Calibrate where the name goes on the certificate

```bash
python scripts/calibrate_certificate.py assets/certificate_template.pdf
```
Open the resulting `calibration_grid.pdf`, find the (x, y) coordinates
where the name should be centered, and set `CERTIFICATE_NAME_X` /
`CERTIFICATE_NAME_Y` in `.env` accordingly (see the script's docstring for
how PDF coordinates work — origin is bottom-left, not top-left).

### 6.7 Run the app locally

```bash
uvicorn app.main:app --reload --port 8000
```
Visit `http://localhost:8000/healthz` in a browser — you should see
`{"status": "ok"}`. If instead you see a stack trace about a missing
environment variable, re-check `.env` against `.env.example`.

Continue to `TESTING.md` to actually exercise the webhook end-to-end
before deploying.

---

## 7. Where to go next

- **`TESTING.md`** — how to test locally (with a tunnel so Interakt can
  reach your machine) and how to verify things in production after deploy.
- **`DEPLOYMENT.md`** — platform comparison, recommendation, and exact
  deployment steps.
- **`TROUBLESHOOTING.md`** — common errors and what to do about them.
