# Testing Guide

## 1. Automated tests (run these first, every time you change code)

```bash
pip install -r requirements-dev.txt
pytest -v
```

What's covered, and deliberately what's NOT:
- `tests/test_security.py` — HMAC signature verification (valid, invalid,
  missing, tampered body). Pure logic, no external services.
- `tests/test_certificate_logic.py` — certificate ID format/uniqueness, and
  the "Certificate" trigger-word regex (matches `"Certificate"`, rejects
  `"can I get my certificate please"`).
- `tests/test_webhook_endpoint.py` — full webhook flow (not
  registered / not completed / completed-and-issued / bad signature /
  non-trigger message ignored) using **fake** Sheets/Interakt/Drive services,
  so it runs without any real credentials. It DOES exercise the real PDF
  generation code (reportlab + pypdf) against a throwaway test template.
- **Not covered by automated tests:** actual network calls to Google
  Sheets, Interakt, or Google Drive — those require real credentials and are
  covered by the manual steps below instead, which is also where you'll
  catch account-specific issues that no unit test could catch anyway.

## 2. Testing locally, end-to-end, with a real webhook

Interakt needs to reach your machine over the public internet to deliver
webhooks — `localhost` isn't reachable from Interakt's servers. Use a
tunnel:

1. Install a tunnel tool, e.g. [ngrok](https://ngrok.com) (free tier is
   enough): download, then `ngrok config add-authtoken <your token from
   ngrok's dashboard>`.
2. Start your app locally: `uvicorn app.main:app --reload --port 8000`
3. In a second terminal: `ngrok http 8000`. It prints a public URL like
   `https://abcd1234.ngrok-free.app`.
4. Interakt Dashboard → Developer Settings → Configure Webhook → set
   **Webhook URL** to `https://abcd1234.ngrok-free.app/webhooks/interakt`
   (temporarily — you'll change this again for production in
   `DEPLOYMENT.md`).
5. On your own phone, message your WhatsApp Business test number with
   exactly: `Certificate`

### Capture one real webhook first

Before trusting that the flow works, confirm Interakt's actual payload
shape matches what `app/main.py`'s `_extract_text_and_phone()` expects:

1. Add a temporary debug line at the very top of the `interakt_webhook`
   function in `app/main.py`:
   ```python
   logger.info("raw_webhook_debug", body=raw_body.decode("utf-8", errors="replace"))
   ```
2. Send the test message again, then look at your terminal's log output
   for the `raw_webhook_debug` line — this is the exact JSON Interakt sent.
3. Compare its structure to `app/models.py`'s `InteraktWebhookPayload` /
   `InteraktWebhookData` / `InteraktCustomer` / `InteraktMessage`. If the
   field names differ (Interakt has revised these across doc versions),
   update `_extract_text_and_phone()` in `app/main.py` to match — that
   function is intentionally the only place this mapping lives.
4. Remove the debug line once confirmed.

### Manually simulating a webhook (without WhatsApp), for fast iteration

Once you've confirmed the real shape in step above, you can iterate faster
by POSTing directly with `curl`, without needing a real WhatsApp message
each time. From the project root, with the app running locally:

```bash
python3 - <<'PY'
import hmac, hashlib, json, os

secret = "paste-your-INTERAKT_WEBHOOK_SECRET-here"
body = json.dumps({
    "version": "1.0",
    "timestamp": "2026-07-14T00:00:00Z",
    "type": "message_received",
    "data": {
        "customer": {"id": "test", "channel_phone_number": "919999999999", "traits": {}},
        "message": {"id": "m1", "message": "Certificate", "type": "Text"}
    }
}).encode()
sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
print("BODY:", body.decode())
print("SIGNATURE HEADER:", sig)
PY
```

Then feed that output into:
```bash
curl -X POST http://localhost:8000/webhooks/interakt \
  -H "Content-Type: application/json" \
  -H "Interakt-Signature: <paste the SIGNATURE HEADER value>" \
  -d '<paste the BODY value>'
```

Use the test phone numbers you added to the Sheet in `README.md` Section
4.1 to exercise all three branches (not registered / not completed /
completed) by changing `channel_phone_number` and re-signing each time.

### What to check after each test

- Terminal logs show `certificate_request_received` →
  `certificate_flow_complete` (or the not-registered/not-completed path)
  with no tracebacks.
- The Google Sheet's `Certificate Sent`, `Certificate ID`, `Certificate URL`,
  and `Timestamp` columns updated for the COMPLETED test student.
- Your Google Drive (service account's "Certificates" folder) has a new
  `CERT-...pdf` file.
- Your WhatsApp test number received the certificate as a document, and
  opening it shows the correct name in the correct position.
- Sending `Certificate` again for the SAME already-sent student does not
  create a second Sheet update or a second Drive file — it resends the
  existing certificate (see `app/main.py`'s
  `certificate_already_sent_resending_existing` log line).

## 3. Testing in production

After deploying (see `DEPLOYMENT.md`) and pointing Interakt's webhook at
your real production URL:

1. Visit `https://<your-domain>/healthz` — confirm `{"status": "ok"}`.
2. From a real phone (ideally one NOT used during local testing, to
   simulate a genuine first-time student), message your WhatsApp Business
   number: `Certificate`. Use a phone number already present in your
   Sheet with `Status = COMPLETED`.
3. Check Railway's log viewer (`DEPLOYMENT.md` Section 4.5) for the same
   log sequence as local testing, with no tracebacks.
4. Confirm receipt of the certificate on the test phone, and confirm the
   Sheet + Drive updated.
5. Test the two failure paths for real: message from a phone NOT in the
   sheet (expect "not registered" reply), and from a phone with
   `Status = IN_PROGRESS` (expect "not completed" reply).
6. Test signature rejection is actually wired correctly in production too:
   ```bash
   curl -i -X POST https://<your-domain>/webhooks/interakt \
     -H "Content-Type: application/json" \
     -H "Interakt-Signature: sha256=0000000000000000000000000000000000000000000000000000000000000000" \
     -d '{"type":"message_received"}'
   ```
   Expect `HTTP/1.1 401 Unauthorized`. If you instead get a 200 or a 500,
   something is wrong with the deployed `INTERAKT_WEBHOOK_SECRET` value —
   re-check it matches the Interakt dashboard exactly (no extra
   whitespace — a common copy-paste mistake).
7. Do a small "soak test" before the real launch: have 3–5 people (with
   real Sheet rows in different statuses) message the bot within the same
   few minutes, to catch any concurrency issue before 20,000 students do
   it for real.
