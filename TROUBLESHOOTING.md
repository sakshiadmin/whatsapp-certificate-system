# Troubleshooting Guide

## How to debug this app in general

1. **Logs are JSON, one event per line** (see `app/logging_config.py`).
   Every important step logs an `event` name (e.g.
   `certificate_request_received`, `sheets_cache_refreshed`,
   `certificate_message_sent`). When something goes wrong, find the phone
   number involved and search your log viewer (Railway's log search, or
   locally your terminal) for that phone number — the sequence of events
   around it tells you exactly where the flow stopped.
2. **A traceback in the logs means an exception was caught and logged**
   (search for `.exception(...)` calls in `app/main.py`) — read the
   exception type and message; it almost always points directly at the
   cause (e.g., `FileNotFoundError` → a path in `.env` is wrong).
3. **If nothing happens at all** when a student messages "Certificate",
   the problem is almost always one of: (a) webhook URL misconfigured in
   Interakt, (b) the "Message received from customers" webhook type isn't
   enabled, (c) you're not on the Advanced/Enterprise plan, or (d) the
   payload shape differs from what `_extract_text_and_phone()` expects —
   see `TESTING.md` Section 2, "Capture one real webhook first".

## Common errors and what to do

### `pydantic_core._pydantic_core.ValidationError` on startup
**Cause:** a required environment variable is missing or empty.
**Fix:** compare your `.env` (or Railway variables) against every field in
`.env.example` — the error message names the exact missing field(s).

### Sheets API error: `PERMISSION_DENIED` or `The caller does not have permission`
**Cause:** the Google Sheet was not shared with the service account's
`client_email`, or was shared as "Viewer" instead of "Editor".
**Fix:** re-check README Section 4.6. Open the Sheet's Share dialog and
confirm the service account email is listed with Editor access.

### Sheets API error: `Requested entity was not found` / `SpreadsheetNotFound`
**Cause:** wrong `GOOGLE_SHEET_ID`, or the worksheet tab name doesn't
match `GOOGLE_SHEET_WORKSHEET_NAME`.
**Fix:** re-copy the ID from the Sheet's URL; confirm the tab is literally
named `Students` (or update the env var to match your actual tab name).

### A student who should be found gets the "not registered" reply
**Cause, in order of likelihood:**
1. Phone number format mismatch — check the `Phone` column value against
   what Interakt sends (see the debug technique in `TESTING.md`). Common
   issue: the sheet has a `+` prefix or spaces; the code normalizes for
   this (`app/services/sheets_service.py`'s `normalize_phone()`), but if
   you see a mismatch anyway, log the two values side by side to compare.
2. The `Phone` column was formatted as a **Number** instead of **Plain
   text** in Google Sheets, so a leading zero or a long number got
   mangled by Sheets itself, before your code ever saw it. Re-check README
   Section 4.1, step 4.
3. Stale cache — should self-heal within 60 seconds, or immediately via
   the cache-miss force-refresh in `find_student()`; if it still fails,
   confirm the row was actually saved in the Sheet (not left in an
   un-committed edit).

### Google Drive upload error: `HttpError 403` or `Insufficient Permission`
**Cause:** The Google Drive API is not enabled for your project, or the
service account doesn't have access to the target folder.
**Fix:**
1. Go to Google Cloud Console → APIs & Services → confirm "Google Drive
   API" is enabled.
2. If using a custom `GOOGLE_DRIVE_FOLDER_ID`, ensure the folder is shared
   with the service account's `client_email` as Editor.
3. If not using a custom folder ID, the service account creates its own
   folder — this should always work. Check the logs for the exact error.

### Google Drive quota exceeded
**Cause:** Google Drive has a free quota of 15 GB per account. At ~150 KB
per certificate, this supports ~100,000 certificates.
**Fix:** if you hit this (very unlikely at 20K students), either clean out
old certificates or use a separate Google account for the service account.

### Interakt session message returns 4xx
**Cause:** The session message failed. Common reasons:
1. The 24-hour session window has expired — this should never happen in
   normal flow (student messages → you reply within seconds), but could
   happen if your server was down when the student messaged, and you're
   processing a queued webhook hours later.
2. Invalid phone number format or the phone number is not a WhatsApp user.
3. Interakt API key is wrong or expired.
**Fix:** check the Interakt API response body in your logs — it usually
contains a descriptive error message.

### Interakt API returns 429
**Cause:** rate limit exceeded for your plan.
**Fix:** the retry logic in `app/services/interakt_service.py` only
retries on 5xx, not 429 (retrying immediately on a rate limit makes it
worse). If you see sustained 429s during a launch rush, this means
message volume genuinely exceeds your plan's per-minute limit — check
Interakt's plan limits and consider upgrading, or add a small delay/queue
in front of sends if this becomes frequent.

### Certificate PDF has the name in the wrong place, wrong font, or missing
- **Wrong place:** re-run `scripts/calibrate_certificate.py` and re-check
  `CERTIFICATE_NAME_X` / `_Y` in `.env`. Remember PDF coordinates are
  bottom-left origin.
- **Wrong/default font (e.g. looks like Helvetica, not your font):**
  confirm `CERTIFICATE_FONT_PATH` points at a real `.ttf` file (not `.otf`
  — see `fonts/PUT_YOUR_FONT_HERE.txt`) and the path is correct relative
  to where the app runs (in production, relative to the app's working
  directory, not your laptop's).
- **Name missing entirely / blank certificate:** confirm
  `CERTIFICATE_TEMPLATE_PATH` points at a real, single-page PDF, and that
  file was actually deployed (check it's not excluded by `.gitignore` —
  it shouldn't be, only `secrets/` and `.env` are excluded).

### Certificate sent twice to the same student
This should not happen due to the per-phone lock + Sheet check (see
`app/utils/idempotency.py` and the "already sent → resend existing" branch
in `app/main.py`), but if you do see it:
1. Check whether you're running more than 1 worker process (the `Procfile`
   in this repo uses `-w 1` deliberately — see `DEPLOYMENT.md` Section 5).
   If someone changed this to `-w 2+`, the in-memory lock and cache are no
   longer shared across workers, reopening the race condition.
2. Check whether the SAME webhook delivery was received twice within the
   cache-refresh window in a way that both requests read "not yet sent"
   from the Sheet before either write landed — this is the scenario the
   lock exists to prevent; if you see it despite the lock and single
   worker, please double check no code path bypasses
   `_handle_certificate_request`'s `async with lock:` block.

### Webhook returns 401 in production, but your local test worked
**Cause:** `INTERAKT_WEBHOOK_SECRET` in your production environment
variables doesn't match the Secret Key currently configured in Interakt's
dashboard — often because it was regenerated in the dashboard after you
last copied it, or copied with trailing whitespace.
**Fix:** re-copy the secret from Interakt's dashboard into your production
env vars, redeploy, retest.

### App works, but stays permanently "not completed" for a student who just finished
**Cause:** whatever system marks a student `COMPLETED` in the Sheet
(presumably a separate process outside this project — an LMS export,
manual entry, etc.) hasn't run yet, or wrote a status string that doesn't
exactly match `COMPLETED` (e.g. trailing space, lowercase).
**Fix:** the code compares case-insensitively and trims whitespace
(`StudentRecord.is_completed` in `app/models.py`), but if the actual value
in the sheet is something like `Complete` or `Done`, it won't match — the
brief in this project specifies exactly `COMPLETED` as the only valid
"done" value, so keep whatever marks students complete consistent with
that.

### Interakt disabled your webhook ("5 failures in 10 minutes")
**Cause:** your server returned non-200 or timed out 5 times within 10
minutes — check Railway's logs for the corresponding time window for
tracebacks, OOM kills, or a deploy that briefly took the app down.
**Fix:** once the underlying issue is fixed, go back to Interakt Dashboard
→ Developer Settings → Configure Webhook and re-enable it manually (it
does not re-enable itself automatically).

## Retry logic summary (where retries happen and why)

| Failure | Retried? | Where |
|---|---|---|
| Interakt API 5xx / network error | Yes, 3 attempts, exponential backoff | `app/services/interakt_service.py` |
| Interakt API 4xx | No — logged and raised immediately | same file |
| Google Sheets transient network error | No automatic retry currently; surfaces as a 500-level exception, logged, and the Sheet is NOT marked "Sent" so the student can safely resend "Certificate" | `app/services/sheets_service.py` / `app/main.py` |
| Google Drive upload failure | Same as above — surfaces, logged, Sheet not marked "Sent", safe to retry via the student resending the message | `app/services/storage_service.py` |
| Interakt webhook delivery itself | Interakt retries automatically on their side if you don't return 200 within their timeout — this is why the webhook handler avoids any slow/blocking work before returning | `app/main.py` |

The overall design principle: **never mark the Sheet as "Sent" unless the
WhatsApp send call actually succeeded.** This means a failure anywhere in
the certificate-generation chain is always safely recoverable by the
student simply sending "Certificate" again — see the `except Exception`
block in `_handle_certificate_request()` in `app/main.py`.
