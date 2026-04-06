import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import requests
from dotenv import load_dotenv

PORTAL_URL = "https://mytotalconnectcomfort.com/portal"
LOCATIONS_URL = f"{PORTAL_URL}/Location/GetLocationListData?page=1&filter="

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
    session.post(
        PORTAL_URL,
        data={"UserName": email, "Password": password, "RememberMe": "false", "timeOffset": "480"},
        headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": PORTAL_URL},
        timeout=TIMEOUT,
        allow_redirects=False,
    )
    if not session.cookies.get(".ASPXAUTH_TRUEHOME"):
        raise RuntimeError("Login failed: .ASPXAUTH_TRUEHOME cookie not set — check credentials")


def fetch_locations(session: requests.Session) -> List[Dict]:
    # Returns a bare JSON array (not {"Locations": [...]})
    resp = session.post(
        LOCATIONS_URL,
        headers={"Content-Type": "application/json; charset=utf-8"},
        data=b"",
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


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
        raw_locations = fetch_locations(session)

        locations_out: List[Dict] = []
        for loc in raw_locations:
            devices_out = [extract_device(dev) for dev in (loc.get("Devices") or [])]
            locations_out.append({
                "id": loc.get("LocationID"),
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


if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.ConnectionError:
        sys.stderr.write("Network error: could not reach mytotalconnectcomfort.com\n")
        sys.exit(1)
    except requests.exceptions.Timeout:
        sys.stderr.write("Request timed out\n")
        sys.exit(1)
    except RuntimeError as exc:
        sys.stderr.write(f"{exc}\n")
        sys.exit(1)
