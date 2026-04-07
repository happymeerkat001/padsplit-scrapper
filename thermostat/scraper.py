import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import requests
from dotenv import load_dotenv

PORTAL_URL = "https://mytotalconnectcomfort.com/portal"
LOCATIONS_BASE_URL = f"{PORTAL_URL}/Location/GetLocationListData?page={{page}}&filter="

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

TIMEOUT = (10, 30)


def load_credentials() -> Dict[str, str]:
    env_paths = [
        Path(__file__).parent / ".env",
        Path(__file__).parent.parent / ".env",
        Path(__file__).parent.parent / "padsplit_scraper" / ".env",
    ]
    for path in env_paths:
        load_dotenv(path)

    email = os.getenv("TCC_EMAIL")
    password = os.getenv("TCC_PASSWORD")
    if not email or not password:
        sys.exit("Missing TCC_EMAIL or TCC_PASSWORD in environment/.env")
    return {"email": email, "password": password}


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
    })
    return session


def login(session: requests.Session, email: str, password: str) -> None:
    # GET first — server requires TrueHomeCheckCookie to be set before POST.
    # Must NOT send X-Requested-With here or the server skips setting that cookie.
    session.get(PORTAL_URL, timeout=TIMEOUT, headers={"X-Requested-With": None})
    resp = session.post(
        PORTAL_URL,
        data={"UserName": email, "Password": password, "RememberMe": "false", "timeOffset": "480"},
        headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": PORTAL_URL},
        timeout=TIMEOUT,
        allow_redirects=False,
    )
    sys.stderr.write(
        f"Login POST → status={resp.status_code}, cookies={list(session.cookies.keys())}\n"
    )
    if not session.cookies.get(".ASPXAUTH_TRUEHOME"):
        raise RuntimeError(
            f"Login failed: .ASPXAUTH_TRUEHOME cookie not set — check credentials or site changes. "
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


def fetch_locations(session: requests.Session) -> List[Dict]:
    # Returns a bare JSON array (not {"Locations": [...]}).
    # Paginate until the API returns an empty array.
    all_locations: List[Dict] = []
    page = 1
    while True:
        resp = session.post(
            LOCATIONS_BASE_URL.format(page=page),
            headers={"Content-Type": "application/json; charset=utf-8"},
            data=b"",
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            break
        all_locations.extend(batch)
        page += 1
    return all_locations


def extract_device(dev: Dict) -> Dict:
    td = dev.get("ThermostatData") or {}
    # Active setpoints are null when running on schedule — fall back to schedule values
    heat_sp = td.get("HeatSetpoint") or td.get("ScheduleHeatSp")
    cool_sp = td.get("CoolSetpoint") or td.get("ScheduleCoolSp")
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


def main() -> None:
    creds = load_credentials()
    with create_session() as session:
        login(session, creds["email"], creds["password"])
        location_names = fetch_location_names(session)
        raw_locations = fetch_locations(session)

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

        print_report(locations_out)

        scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        output = {"scraped_at": scraped_at, "locations": locations_out}

        out_dir = Path(__file__).resolve().parent / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = scraped_at.replace(":", "-") + ".json"
        snapshot_json = json.dumps(output, indent=2)
        (out_dir / filename).write_text(snapshot_json)
        (out_dir / "latest.json").write_text(snapshot_json)
        sys.stderr.write(f"# Saved to {out_dir / filename}\n")


MAX_RETRIES = 3
RETRY_BACKOFF = 30  # seconds


if __name__ == "__main__":
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            main()
            sys.exit(0)
        except requests.exceptions.ConnectionError as exc:
            last_exc = exc
            sys.stderr.write(f"Attempt {attempt}/{MAX_RETRIES}: Network error — {exc}\n")
        except requests.exceptions.Timeout as exc:
            last_exc = exc
            sys.stderr.write(f"Attempt {attempt}/{MAX_RETRIES}: Timeout — {exc}\n")
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            sys.stderr.write(f"Attempt {attempt}/{MAX_RETRIES}: HTTP error — {exc}\n")
        except RuntimeError as exc:
            sys.stderr.write(f"{exc}\n")
            sys.exit(1)

        if attempt < MAX_RETRIES:
            sys.stderr.write(f"Retrying in {RETRY_BACKOFF}s...\n")
            time.sleep(RETRY_BACKOFF)

    sys.stderr.write(f"All {MAX_RETRIES} attempts failed. Last error: {last_exc}\n")
    sys.exit(1)
