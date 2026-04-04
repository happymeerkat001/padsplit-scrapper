import json
import os
import re
import sys
from typing import Dict, List

import requests
from dotenv import load_dotenv

LOGIN_URL = "https://mytotalconnectcomfort.com/portal"
LOCATIONS_URL = "https://mytotalconnectcomfort.com/portal/Location/GetLocationListData?page=1&filter="
DEVICE_URL_TEMPLATE = "https://mytotalconnectcomfort.com/portal/Device/CheckDataSession/{device_id}"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

TIMEOUT = (10, 30)  # (connect, read)


def load_credentials() -> Dict[str, str]:
    load_dotenv(os.path.join(os.path.dirname(__file__), "../padsplit_scraper/.env"))
    email = os.getenv("TCC_EMAIL")
    password = os.getenv("TCC_PASSWORD")
    if not email or not password:
        sys.exit("Missing TCC_EMAIL or TCC_PASSWORD")
    return {"email": email, "password": password}


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    return session


def login(session: requests.Session, email: str, password: str) -> None:
    # Fetch the login page first to get the anti-forgery token
    get_resp = session.get(LOGIN_URL, timeout=TIMEOUT)
    get_resp.raise_for_status()
    match = re.search(r'name="__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"', get_resp.text)
    token = match.group(1) if match else ""

    payload = {
        "UserName": email,
        "Password": password,
        "RememberMe": "false",
        "timeOffset": "480",
        "__RequestVerificationToken": token,
    }
    resp = session.post(LOGIN_URL, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=TIMEOUT)
    resp.raise_for_status()
    cookie_names = list(session.cookies.keys())
    print("Cookies after login:", cookie_names)
    if not any(".ASPXAUTH" in c for c in cookie_names):
        raise RuntimeError("Login failed: no auth cookie set")


def fetch_locations(session: requests.Session) -> List[Dict]:
    resp = session.post(
        LOCATIONS_URL,
        headers={"Content-Type": "application/json; charset=utf-8"},
        data=b"",
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else data.get("Locations", [])


def fetch_device_data(session: requests.Session, device_id: str) -> Dict:
    url = DEVICE_URL_TEMPLATE.format(device_id=device_id)
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def print_report(locations: List[Dict]) -> None:
    for loc in locations:
        loc_id = loc.get("LocationID")
        print(f"Location ID: {loc_id}")
        devices = loc.get("Devices") or []
        if not devices:
            print("  No devices found.")
            continue
        for dev in devices:
            dev_name = dev.get("Name", "Thermostat")
            td = dev.get("ThermostatData") or {}
            temp = td.get("IndoorTemperature")
            heat = td.get("ScheduleHeatSp")
            cool = td.get("ScheduleCoolSp")
            humidity = td.get("IndoorHumidity")
            outdoor_temp = td.get("OutdoorTemperature")
            print(f"  Thermostat:   {dev_name}")
            print(f"    Indoor:     {temp}°F  Humidity: {humidity}%")
            print(f"    Outdoor:    {outdoor_temp}°F")
            print(f"    Heat set:   {heat}°F  Cool set: {cool}°F")
        print()


def main() -> None:
    creds = load_credentials()
    with create_session() as session:
        login(session, creds["email"], creds["password"])
        locations = fetch_locations(session)

        print_report(locations)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # broad catch to give a clean message
        sys.stderr.write(f"{exc}\n")
        sys.exit(1)
