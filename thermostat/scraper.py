import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from padsplit_scraper.slack_notifier import post_slack_message

PORTAL_URL = "https://mytotalconnectcomfort.com/portal"
LOCATIONS_BASE_URL = f"{PORTAL_URL}/Location/GetLocationListData?page={{page}}&filter="
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

TIMEOUT = (10, 30)
AUTH_TIMEOUT = (10, 75)
LOCATION_FETCH_TIMEOUT = (10, 60)
LOCATION_FETCH_RETRIES = 3
LOCATION_FETCH_BACKOFF = 5
LOGIN_RETRIES = 3
LOGIN_BACKOFF = 10


def load_credentials() -> Dict[str, str]:
    env_path = ROOT_DIR / ".env"
    load_dotenv(env_path)

    email = os.getenv("TCC_EMAIL")
    password = os.getenv("TCC_PASSWORD")
    if not email or not password:
        sys.exit("Missing TCC_EMAIL or TCC_PASSWORD in environment or root .env")
    return {"email": email, "password": password}


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
    })
    return session


def login(session: requests.Session, email: str, password: str) -> None:
    for attempt in range(1, LOGIN_RETRIES + 1):
        sys.stderr.write(f"[auth] Login attempt {attempt}/{LOGIN_RETRIES}\n")
        try:
            # GET first — server requires TrueHomeCheckCookie to be set before POST.
            # Must NOT send X-Requested-With here or the server skips setting that cookie.
            session.get(PORTAL_URL, timeout=AUTH_TIMEOUT, headers={"X-Requested-With": None})
            resp = session.post(
                PORTAL_URL,
                data={"UserName": email, "Password": password, "RememberMe": "false", "timeOffset": "480"},
                headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": PORTAL_URL},
                timeout=AUTH_TIMEOUT,
                allow_redirects=False,
            )
            sys.stderr.write(
                f"Login attempt {attempt}/{LOGIN_RETRIES} → status={resp.status_code}, cookies={list(session.cookies.keys())}\n"
            )
            if session.cookies.get(".ASPXAUTH_TRUEHOME"):
                return
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RequestException) as exc:
            sys.stderr.write(f"[auth] Login attempt {attempt}/{LOGIN_RETRIES} failed: {exc}\n")

        if attempt < LOGIN_RETRIES:
            sys.stderr.write(f"[auth] Retrying in {LOGIN_BACKOFF}s\n")
            time.sleep(LOGIN_BACKOFF)
    raise RuntimeError(
        f"Login failed after {LOGIN_RETRIES} attempts: .ASPXAUTH_TRUEHOME cookie not set. "
        f"Cookies present: {list(session.cookies.keys())}"
    )


def fetch_location_names(session: requests.Session) -> Dict[int, str]:
    """Parse /portal/Locations HTML for the true location names keyed by LocationID.

    The GetLocationListData AJAX endpoint does not include location names.
    The portal HTML embeds them in:
        <tr ... data-id="<LocationID>" ...>
            <div class="location-name">Some Name</div>
    """
    import re as _re
    try:
        sys.stderr.write("[fetch:names] Fetching location names page\n")
        r = session.get(
            f"{PORTAL_URL}/Locations",
            timeout=TIMEOUT,
            headers={"X-Requested-With": None},  # HTML page, not AJAX
        )
        r.raise_for_status()
        # Match each table row's data-id then the first location-name div
        pattern = _re.compile(
            r'data-id="(\d+)"[^>]*>.*?class="location-name">\s*(.+?)\s*<',
            _re.DOTALL,
        )
        return {int(m.group(1)): m.group(2).strip() for m in pattern.finditer(r.text)}
    except Exception:
        return {}


def fetch_locations_page(session: requests.Session, page: int) -> List[Dict]:
    for attempt in range(1, LOCATION_FETCH_RETRIES + 1):
        sys.stderr.write(
            f"[fetch:locations] Requesting page {page} (attempt {attempt}/{LOCATION_FETCH_RETRIES})\n"
        )
        try:
            resp = session.post(
                LOCATIONS_BASE_URL.format(page=page),
                headers={"Content-Type": "application/json; charset=utf-8"},
                data=b"",
                timeout=LOCATION_FETCH_TIMEOUT,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not isinstance(batch, list):
                raise RuntimeError(f"Location page {page} returned malformed JSON payload")
            return batch
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt == LOCATION_FETCH_RETRIES:
                raise
            time.sleep(LOCATION_FETCH_BACKOFF)
    raise RuntimeError(f"Location page {page} exceeded retry budget")


def fetch_locations(session: requests.Session) -> List[Dict]:
    # Returns a bare JSON array (not {"Locations": [...]}).
    # Paginate until the API returns an empty array.
    all_locations: List[Dict] = []
    page = 1
    while True:
        batch = fetch_locations_page(session, page)
        if not isinstance(batch, list) or not batch:
            break
        all_locations.extend(batch)
        page += 1
    return all_locations


def fetch_device_uidata(session: requests.Session, device_id: int) -> Dict:
    """Fetch active setpoints via CheckDataSession.

    GetLocationListData always returns HeatSetpoint/CoolSetpoint as null;
    the real active (possibly held) setpoints live in CheckDataSession uiData.
    """
    try:
        r = session.get(
            f"{PORTAL_URL}/Device/CheckDataSession/{device_id}",
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("success"):
            return (data.get("latestData") or {}).get("uiData") or {}
    except Exception:
        pass
    return {}


def extract_device(dev: Dict) -> Dict:
    td = dev.get("ThermostatData") or {}

    def first_present(keys: List[str]) -> Optional[float]:
        for k in keys:
            v = td.get(k)
            if v is not None:
                return v
        return None

    # HeatSetpoint/CoolSetpoint are patched from CheckDataSession before this runs;
    # fall back to schedule values only if patching failed.
    heat_sp = first_present(["HeatSetpoint", "ScheduleHeatSp"])
    cool_sp = first_present(["CoolSetpoint", "ScheduleCoolSp"])

    return {
        "id": dev.get("DeviceID"),
        "name": dev.get("Name"),
        "temp": td.get("IndoorTemperature"),
        "heat_setpoint": heat_sp,
        "cool_setpoint": cool_sp,
        "humidity": td.get("IndoorHumidity"),
        "outdoor_temp": td.get("OutdoorTemperature") if td.get("OutdoorTemperatureAvailable") else None,
        "mode": td.get("Mode"),
        "equipment_status": td.get("EquipmentOutputStatus"),
    }


def print_report(locations: List[Dict]) -> None:
    for loc in locations:
        print(f"Location ID: {loc['id']}")
        for dev in loc["devices"]:
            temp = dev["temp"]
            heat = dev["heat_setpoint"]
            cool = dev["cool_setpoint"]
            hum = dev["humidity"]
            outdoor = dev["outdoor_temp"]
            print(f"  {dev['name']}")
            print(f"    Temp: {temp}°F  |  Heat set: {heat}°F  Cool set: {cool}°F")
            print(f"    Humidity: {hum}%  |  Outdoor: {outdoor}°F")
        print()


def build_output(raw_locations: List[Dict], location_names: Dict[int, str]) -> Dict:
    locations_out: List[Dict] = []
    for loc in raw_locations:
        loc_id = loc.get("LocationID")
        devices_out = [extract_device(dev) for dev in (loc.get("Devices") or [])]
        # Use the true portal location name; fall back to device name if unavailable
        first_device_name = devices_out[0]["name"] if devices_out else None
        loc_name = location_names.get(loc_id) or first_device_name or str(loc_id)
        locations_out.append({
            "id": loc_id,
            "name": loc_name,
            "devices": devices_out,
        })

    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"scraped_at": scraped_at, "locations": locations_out}


def fetch_fresh_output(session: requests.Session) -> Dict:
    location_names = fetch_location_names(session)
    sys.stderr.write("[fetch:locations] Fetching thermostat locations\n")
    raw_locations = fetch_locations(session)

    sys.stderr.write("[fetch:devices] Fetching active setpoints\n")
    # Patch each device's ThermostatData with active setpoints from CheckDataSession.
    # GetLocationListData always returns HeatSetpoint/CoolSetpoint as null; the real
    # active values (including holds) come from CheckDataSession uiData.
    for loc in raw_locations:
        for dev in (loc.get("Devices") or []):
            ui = fetch_device_uidata(session, dev.get("DeviceID"))
            if ui:
                td = dev.get("ThermostatData") or {}
                dev["ThermostatData"] = td
                for key in ["HeatSetpoint", "CoolSetpoint"]:
                    if ui.get(key) is not None:
                        td[key] = ui[key]

    return build_output(raw_locations, location_names)


def validate_snapshot_payload(payload: Dict, source: Path) -> Dict:
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid stale snapshot in {source}: expected object payload")
    scraped_at = payload.get("scraped_at")
    locations = payload.get("locations")
    if not isinstance(scraped_at, str) or not scraped_at.strip():
        raise RuntimeError(f"Invalid stale snapshot in {source}: missing scraped_at")
    if not isinstance(locations, list):
        raise RuntimeError(f"Invalid stale snapshot in {source}: missing locations list")
    return payload


def load_stale_snapshot(path: Optional[Path] = None) -> Dict:
    path = path or (OUTPUT_DIR / "latest.json")
    sys.stderr.write(f"[fallback] Loading stale snapshot from {path}\n")
    if not path.exists():
        raise RuntimeError(f"No stale thermostat snapshot found at {path}")
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid stale thermostat snapshot at {path}: {exc}") from exc
    return validate_snapshot_payload(payload, path)


def persist_output(output: Dict, out_dir: Optional[Path] = None) -> None:
    out_dir = out_dir or OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = output["scraped_at"].replace(":", "-") + ".json"
    snapshot_json = json.dumps(output, indent=2)
    (out_dir / filename).write_text(snapshot_json)
    (out_dir / "latest.json").write_text(snapshot_json)
    sys.stderr.write(f"[persist] Saved to {out_dir / filename}\n")


def notify_stale_fallback(scraped_at: str) -> None:
    message = (
        f"⚠️ Thermostat API Alert: Timeout detected. Using stale data from {scraped_at}. "
        "Morning pipeline continuing."
    )
    try:
        post_slack_message(message)
        sys.stderr.write("[fallback] Sent Slack stale-data alert\n")
    except Exception as exc:
        sys.stderr.write(f"[fallback] Failed to send Slack stale-data alert: {exc}\n")


def main() -> int:
    creds = load_credentials()
    with create_session() as session:
        sys.stderr.write("[auth] Starting thermostat login\n")
        login(session, creds["email"], creds["password"])
        try:
            output = fetch_fresh_output(session)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            sys.stderr.write(f"[fallback] Fresh thermostat fetch failed after login: {exc}\n")
            stale_output = load_stale_snapshot()
            notify_stale_fallback(stale_output["scraped_at"])
            print_report(stale_output["locations"])
            sys.stderr.write(
                f"[fallback] Continuing with stale thermostat snapshot from {stale_output['scraped_at']}\n"
            )
            return 0

        print_report(output["locations"])
        persist_output(output)
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except requests.exceptions.Timeout as exc:
        sys.stderr.write(f"[error] Timeout during thermostat scrape: {exc}\n")
    except requests.exceptions.ConnectionError as exc:
        sys.stderr.write(f"[error] Network error during thermostat scrape: {exc}\n")
    except requests.exceptions.RequestException as exc:
        sys.stderr.write(f"[error] HTTP error during thermostat scrape: {exc}\n")
    except RuntimeError as exc:
        sys.stderr.write(f"[error] {exc}\n")
    sys.exit(1)
