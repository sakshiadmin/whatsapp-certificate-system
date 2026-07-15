"""
Duplicate-request protection.

There are two distinct ways a duplicate "generate my certificate" request
can happen, and they need two different defenses:

1. THE SAME WEBHOOK DELIVERED TWICE (network retry).
   Interakt (and WhatsApp providers generally) will retry a webhook if your
   server doesn't respond 200 fast enough. If your first request is still
   mid-way through generating a PDF when the retry arrives, you could end up
   generating two certificates and sending WhatsApp two documents.
   Defense: an in-process per-phone-number lock (this file). While one
   request for a phone number is being processed, a second concurrent
   request for the SAME phone number waits, then re-checks the sheet
   (which will now say "Sent") and stops.

2. THE STUDENT TYPES "Certificate" AGAIN NEXT WEEK.
   This is not a race condition, it's a legitimate repeat request.
   Defense: the "Certificate Sent" column in Google Sheets (the source of
   truth, checked at the START of every request, lock or no lock).

IMPORTANT — if you deploy more than one server process/instance (e.g.
Railway horizontal scaling, multiple gunicorn workers), this in-process lock
ONLY protects against duplicates within the SAME process. Google Sheets is
your real cross-process source of truth; the lock is just a fast, cheap
first line of defense that also avoids hammering the Sheets API with
simultaneous read+write races. For very high concurrency you would upgrade
this to a Redis-based distributed lock — not necessary at 20,000 students
sending certificate requests at human typing speed, but noted here so you
know the boundary of what this protects against.
"""

import asyncio
from collections import defaultdict


class PhoneLockManager:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def get_lock(self, phone_number: str) -> asyncio.Lock:
        return self._locks[phone_number]


# Single shared instance for the process's lifetime.
phone_lock_manager = PhoneLockManager()
