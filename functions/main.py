"""HTTPS Cloud Function for Bland leak-alert callbacks.

Deploy is manual on a machine that already has Firebase credentials.
Do not run this from GitHub Actions and do not deploy from this PR.

    firebase functions:secrets:set BLAND_WEBHOOK_SECRET --project padsplit-scrapper
    firebase functions:secrets:set LEAK_ALERT_TOOL_BEARER --project padsplit-scrapper
    firebase deploy --only functions:leak-alert:leak_alert_bland --project padsplit-scrapper

URL shape (us-central1, 2nd gen also prints a Cloud Run URL at deploy):

    https://us-central1-padsplit-scrapper.cloudfunctions.net/leak_alert_bland
    https://us-central1-padsplit-scrapper.cloudfunctions.net/leak_alert_bland/confirm

BLAND_WEBHOOK_SECRET is Bland's account signing secret (Dev Portal, Account
Settings, Keys), not a secret this repo generates. The function checks
HMAC-SHA256 over the raw body, then over compact re-serialized JSON. It
writes an admin-only Firestore record and enqueues one Quo line. It does
not call Bland or Quo. LEAK_ALERT_TOOL_BEARER is optional at runtime: when
empty, /confirm uses BLAND_WEBHOOK_SECRET. The function binds the name, so
create the secret before deploy (a dedicated bearer, or the same signing
secret if you want one value).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from firebase_admin import firestore, initialize_app
from firebase_functions import https_fn, options
from firebase_functions.params import SecretParam

import leak_alert_bland as impl

initialize_app()
WEBHOOK_SECRET = SecretParam("BLAND_WEBHOOK_SECRET")
TOOL_BEARER = SecretParam("LEAK_ALERT_TOOL_BEARER")


@https_fn.on_request(
    region="us-central1",
    memory=options.MemoryOption.MB_256,
    timeout_sec=30,
    secrets=[WEBHOOK_SECRET, TOOL_BEARER],
)
def leak_alert_bland(req: https_fn.Request) -> https_fn.Response:
    raw = req.get_data() or b""
    signing_secret = WEBHOOK_SECRET.value or os.environ.get("BLAND_WEBHOOK_SECRET", "")
    tool_secret = impl.confirm_bearer(
        TOOL_BEARER.value or os.environ.get("LEAK_ALERT_TOOL_BEARER", ""),
        signing_secret,
    )
    store = impl.FirestoreCallStore(firestore.client())
    now = datetime.now(timezone.utc)
    path = str(getattr(req, "path", "") or "")
    if path.rstrip("/").endswith("/confirm"):
        result = impl.handle_confirm(
            raw,
            req.headers.get("Authorization") if req.headers else None,
            secret=tool_secret,
            store=store,
            now=now,
        )
    else:
        signature = None
        if req.headers:
            signature = req.headers.get("X-Webhook-Signature")
        result = impl.handle_webhook(
            raw,
            signature,
            secret=signing_secret,
            store=store,
            now=now,
            headers=req.headers,
        )
    body = {"ok": True} if result.status == 200 and not result.error else {"error": result.error or "rejected"}
    return https_fn.Response(
        json.dumps(body),
        status=result.status,
        headers={"Content-Type": "application/json"},
    )
