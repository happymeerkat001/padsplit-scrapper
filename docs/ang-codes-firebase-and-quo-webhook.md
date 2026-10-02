# Codes page Firebase key and Quo post-call webhook

For Ang. Both sections are what the code in this repo does today. This file contains no key material. Do not paste a service-account JSON, a Quo signing secret, or any other credential into chat, into this file, or into git.

---

## A. Codes page Firebase service account

Published page: [https://happymeerkat001.github.io/padsplit-scrapper/codes.html](https://happymeerkat001.github.io/padsplit-scrapper/codes.html) (`docs/codes.html` on GitHub Pages).

### The page in the browser does not read a service account

`docs/codes.html` signs in with Firebase Auth email and password (`signInWithEmailAndPassword`) and talks to Firestore with the public web config. Firestore security rules in `firestore.rules` allow that signed-in user only when the uid is `8jOJNgLoxpfyseZ0RY1PDZ1DXbi2`. The Admin SDK is not loaded by the page. Putting a service-account JSON in the HTML, in Pages, or in a browser secret would not make the page load, and it would publish the private key.

The web config in that file identifies the project:

| Field | Value |
| --- | --- |
| `projectId` | `padsplit-scrapper` |
| `authDomain` | `padsplit-scrapper.firebaseapp.com` |
| `messagingSenderId` (GCP project number) | `281696703679` |
| `storageBucket` | `padsplit-scrapper.firebasestorage.app` |

There is no Realtime Database. Nothing in the repo sets `databaseURL` or calls the RTDB SDK. Firestore database id used by the REST reader in `padsplit_scraper/new_booking.py` is `(default)`.

### What reads the service account

Two environment variables. The JSON-string variable wins when both are set, because each reader checks it first.

| Env var | What the code does with it |
| --- | --- |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | `json.loads` the value, then `firebase_admin.credentials.Certificate(...)`. The value is the key file’s JSON text, not a file path. |
| `GOOGLE_APPLICATION_CREDENTIALS` | Passed straight to `credentials.Certificate(...)` as a filesystem path to the JSON key file. Used only when `FIREBASE_SERVICE_ACCOUNT_JSON` is unset. |

Call sites that feed the Codes page data:

| File | Function | Loads repo-root `.env`? | Firestore use |
| --- | --- | --- | --- |
| `padsplit_scraper/lock_codes.py` | `update_codes_page` | Yes (`load_dotenv` on `<repo>/.env`) | Merge-set `property_codes/spanish_moss` fields `back_door` and `updatedAt` |
| `padsplit_scraper/codes_history.py` | `catch_up` via `padsplit_scraper/persist.py` `_firestore_client_or_none` | No | List `property_codes`, create and delete `property_codes/{slug}/code_versions/{id}` |
| `padsplit_scraper/lockout_reply.py` | `fetch_property_codes` | Yes | Get `property_codes/{slug}` |

`run_morning.sh` and `run_afternoon.sh` run those three scripts on the Mac (`WORKSPACE="/Users/leon/Documents/Code/padsplit-scraper"`). They do not run in GitHub Actions. `codes_history.py` returns `skip_ci` when `CI` or `GITHUB_ACTIONS` is set, and `lock_codes.py` refuses to rotate or post from CI.

The same two variables are also read by jobs that are not the Codes page. One key stored under `FIREBASE_SERVICE_ACCOUNT_JSON` is shared. A key that can only touch `property_codes` will break these:

| File | Loads `.env`? | Firestore use |
| --- | --- | --- |
| `padsplit_scraper/persist.py` `upload_stats_to_firestore`, called from `padsplit_scraper/scraper.py` | The scraper loads `<repo>/.env` | Set `stats/latest` and `stats/monthly_history` |
| `.github/workflows/scrape.yml` | Injects the GitHub Actions secret into the scraper step | Same stats write. This is the only workflow that references the secret. |
| `padsplit_scraper/discord_notifier.py` `_init_firestore_app` | No | List `property_codes` for AC-filter dates |
| `padsplit_scraper/firestore_status_monitor.py` `_init_firestore_client` | No. This file reads only `FIREBASE_SERVICE_ACCOUNT_JSON`. It does not accept `GOOGLE_APPLICATION_CREDENTIALS`. | Query `task_messages` where `status == "In Progress"` |

`notes/codes` is read and written by the page with the signed-in user. No service-account call site in this repo touches `notes`.

The Admin SDK bypasses `firestore.rules`. IAM on the service account is what limits it.

### IAM role

Predefined role, on project `padsplit-scrapper` only:

**Cloud Datastore User** (`roles/datastore.user`)

That role covers the operations these scripts issue: get, list, create, update, and delete Firestore documents. It does not grant Firebase Auth admin, Storage admin, rules deploy, Owner, or Editor.

Skip the Firebase console button **Generate new private key** under Project settings → Service accounts. That button mints a key for the default `firebase-adminsdk-…@padsplit-scrapper.iam.gserviceaccount.com` account, which carries the broad Firebase Admin SDK service-agent role.

### Where the key lives

Do not commit the JSON. `.gitignore` ignores `.env*` and does not ignore a downloaded `*.json` key. A key file left inside the repo will be committed.

**1. Mac file, outside the repo (preferred file location)**

`/Users/leon/.config/padsplit/padsplit-scrapper-firestore.json`  
Mode `600`. This path is not in git.

**2. Mac env file the Codes writers already load**

`/Users/leon/Documents/Code/padsplit-scraper/.env`

`lock_codes.py` and `lockout_reply.py` load this file. Put this line in it:

```env
GOOGLE_APPLICATION_CREDENTIALS=/Users/leon/.config/padsplit/padsplit-scrapper-firestore.json
```

`codes_history.py` does not load that file. The morning and afternoon launchd jobs have to pass the same variable in, or catch-up logs `skip_no_client` and writes no snapshots. On the Mac, in both `~/Library/LaunchAgents/com.padsplit.scraper.morning.plist` and `~/Library/LaunchAgents/com.padsplit.scraper.afternoon.plist`, add (or merge into an existing `EnvironmentVariables` dict):

```xml
<key>EnvironmentVariables</key>
<dict>
  <key>GOOGLE_APPLICATION_CREDENTIALS</key>
  <string>/Users/leon/.config/padsplit/padsplit-scrapper-firestore.json</string>
</dict>
```

Then reload those two agents (`launchctl bootout` / `launchctl bootstrap` for `gui/$UID`). The plist value is a path. Do not paste the JSON into the plist.

`firestore_status_monitor.py` still needs the JSON text in `FIREBASE_SERVICE_ACCOUNT_JSON` if that script is run. The Codes page path does not.

**3. GitHub Actions repository secret (stats upload, same JSON)**

- Repository: `happymeerkat001/padsplit-scrapper`
- Location: Settings → Secrets and variables → Actions → Repository secrets
- Name: `FIREBASE_SERVICE_ACCOUNT_JSON`
- Value: the entire JSON file contents (GitHub accepts multiple lines)

`.github/workflows/scrape.yml` maps that secret into the scraper step. It does not publish the key to GitHub Pages, and it does not run `lock_codes.py` or `codes_history.py`.

If that secret name is already set and the scrape job’s Firestore stats upload succeeds, leave it. Do not create a second key.

### Create the key (least privilege)

1. Sign in to Google as a user who can create service accounts and keys on `padsplit-scrapper`.
2. Open [Service accounts for padsplit-scrapper](https://console.cloud.google.com/iam-admin/serviceaccounts?project=padsplit-scrapper).
3. Confirm the project picker reads **padsplit-scrapper**. The project number is `281696703679`.
4. Click **Create service account**.
5. Service account name: `padsplit-firestore-data`. Service account ID becomes `padsplit-firestore-data@padsplit-scrapper.iam.gserviceaccount.com`. Description: `Firestore document read/write for Codes, stats, and task_messages.`
6. Click **Create and continue**.
7. Click the role box, choose **Cloud Datastore User**. Leave every other role unchecked. Do not add Owner, Editor, Firebase Admin, or Service Account Admin.
8. Click **Continue**.
9. Leave “Grant users access to this service account” empty. Click **Done**.
10. Click the new account `padsplit-firestore-data@padsplit-scrapper.iam.gserviceaccount.com`.
11. Open the **Keys** tab.
12. Click **Add key** → **Create new key**.
13. Choose **JSON**, then **Create**. The browser downloads one JSON file.
14. Close the download. Do not open it in chat, email, Slack, Discord, or a git commit.
15. On the Mac:

```bash
mkdir -p "$HOME/.config/padsplit"
mv "$HOME/Downloads/"*padsplit-scrapper*.json "$HOME/.config/padsplit/padsplit-scrapper-firestore.json"
chmod 600 "$HOME/.config/padsplit/padsplit-scrapper-firestore.json"
```

Use the actual downloaded filename in `mv`. Then add the `GOOGLE_APPLICATION_CREDENTIALS` line to the repo `.env`, update the two LaunchAgents as above, and, if the GitHub Actions secret is missing, paste the file contents into repository secret `FIREBASE_SERVICE_ACCOUNT_JSON`.

If **Add key** is greyed out, the org policy `iam.disableServiceAccountKeyCreation` is blocking keys. This repo has no other auth path (no workload-identity provider, no metadata-server credential). An org admin has to allow key creation on this project before the scripts can sign in.

After the new key works, delete any older unused key on the previous service account (Keys → the key row → Delete). Deleting a key that is still installed in `.env` or GitHub will break the next run.

### Follow-ups (not changed here)

- `codes_history.py` never calls `load_dotenv`, so a correct repo `.env` is invisible to catch-up unless the parent process already exported the variable.
- One env var serves Codes, stats, and `task_messages`. The repo has no Codes-only credential name to scope more tightly.
- `firestore_status_monitor.py` ignores `GOOGLE_APPLICATION_CREDENTIALS` and does not load `.env`.
- `discord_notifier.py` accepts both variables and does not load `.env`. It is not started by `run_morning.sh` or `run_afternoon.sh`.

---

## B. Quo post-call webhook

### There is no receiver in this repo

Quo (formerly OpenPhone) appears in one place: outbound SMS. `padsplit_scraper/field_mms.py` sends with `POST https://api.quo.com/v1/messages`, header `Quo-Api-Version: 2026-03-30`, and `QUO_API_KEY`. Nothing in the repo accepts an inbound Quo or OpenPhone HTTP call.

Checked and absent:

- No route, serverless function, `vercel.json`, Cloud Function, Cloud Run service, or app server.
- `firebase.json` only points Firestore at `firestore.rules`. No Hosting rewrite, no Functions codebase.
- GitHub Pages (`docs/`) is static. It cannot answer a Quo `POST`.
- GitHub Actions workflows are scheduled or `workflow_dispatch`. None is a webhook endpoint.

### Public URL

Not determinable. There is no path to append to a host, and no deploy config names a host that could serve one.

What would be required before a URL exists: an HTTPS endpoint that accepts `POST`, returns a 2xx within Quo’s timeout, and is deployed somewhere this repo does not deploy today.

### Event types this code expects

None. No handler reads `call.completed` or any other Quo event type.

Quo’s own post-call event, for whenever a handler exists, is `call.completed` (call finished, answered or not, and it may include voicemail). Related events Quo can send later, which this repo also does not handle: `call.recording.completed`, `call.summary.completed`, `call.transcript.completed`, `call.voicemail.completed`. Source: [Quo webhook support](https://support.quo.com/core-concepts/integrations/webhooks) and the [2026-03-30 create-webhook API](https://www.quo.com/docs/2026-03-30/webhooks/create-a-new-webhook).

### Signing secret

None. No env var. No signature check.

Quo’s current API (the same `2026-03-30` version `field_mms.py` already sends) signs deliveries with headers `webhook-id`, `webhook-timestamp`, and `webhook-signature`, and returns a `whsec_…` key when the webhook is created. Quo’s older support article still describes an `openphone-signature` header and a base64 secret behind **Reveal signing secret**. This repo checks neither.

`QUO_API_KEY` is the outbound SMS credential. It is not a webhook signing secret.

### What to do in Quo’s webhook settings

Do not create the webhook yet. There is no URL to paste. A subscription created now will retry against nothing.

When a public HTTPS URL exists, Quo requires workspace Owner or Admin, and the screen is in the web or desktop app (not the mobile app):

1. Open Quo → **Settings** → **Webhooks**.
2. Click **Create webhook**.
3. URL: the future handler, which this repo cannot name.
4. Event types: leave this unset until the handler exists. A post-call subscription would select `call.completed` only if that handler is written for it.
5. Resources: the phone number the handler should watch. Outbound field SMS in this repo uses `+14693732048` (`QUO_FROM_NUMBER`, overridable by `QUO_FROM_NUMBER` or `FIELD_MMS_QUO_FROM`). That number is the SMS sender. It is not evidence that post-call events are wired to it.
6. Label: optional.
7. Save. Copy the signing secret from the create response (`whsec_…`) or from the webhook’s **Reveal signing secret** action. Store it in an env var on the host that receives the POST. That variable does not exist in this repo today. Do not commit it.

### Follow-ups (not changed here)

- No inbound path, so no public URL.
- No signature verification for either Quo’s `webhook-*` / `whsec_` scheme or the older `openphone-signature` scheme.
- No env var reserved for a webhook signing secret.
