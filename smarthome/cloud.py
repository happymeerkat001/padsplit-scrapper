"""MSmartHome cloud client. No LAN discovery. No Honeywell imports."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from secrets import token_hex
from typing import Any, Callable, Dict, List, Optional

from dotenv import load_dotenv

from smarthome import identity

ROOT_DIR = Path(__file__).resolve().parent.parent
MIN_F = 61
MAX_F = 88
AUTO_MODE = 1
COOL_MODE = 2
AC_TYPES = frozenset({"0xac", "ac", "0xcc", "cc"})
READBACK_TOLERANCE_F = 1.0
SESSION_LIMIT_CODE = 65027
TRANSPARENT_RETRY_CODES = frozenset({1000, 9999})


class SmartHomeError(Exception):
    """Base SmartHome error."""


class SmartHomeAuthError(SmartHomeError):
    """Login failed."""


class SmartHomeSessionLimitError(SmartHomeError):
    """Cloud rejected the login (65027)."""


class UnknownNameError(SmartHomeError):
    """No AC with that display name."""


class AmbiguousNameError(SmartHomeError):
    """More than one AC shares that display name."""


class OfflineError(SmartHomeError):
    """Unit is offline."""


class SetpointRangeError(SmartHomeError):
    """Setpoint outside the library AC range."""


class ReadbackError(SmartHomeError):
    """Cloud ACK did not match live state."""


def f_to_c(fahrenheit: float) -> float:
    return round((fahrenheit - 32) * 5 / 9, 1)


def c_to_f(celsius: float) -> float:
    return (celsius * 9 / 5) + 32


def load_credentials() -> Dict[str, str]:
    load_dotenv(ROOT_DIR / ".env")
    email = os.getenv("SMARTHOME_EMAIL")
    password = os.getenv("SMARTHOME_PASSWORD")
    if not email or not password:
        sys.exit("Missing SMARTHOME_EMAIL or SMARTHOME_PASSWORD in environment or root .env")
    return {"email": email, "password": password}


def _is_ac(item: Dict[str, Any]) -> bool:
    raw = str(item.get("type") or "").lower().replace(" ", "")
    return raw in AC_TYPES or raw.endswith("ac")


def _map_cloud_error(exc: Exception) -> Exception:
    code = getattr(exc, "error_code", None)
    if code == SESSION_LIMIT_CODE:
        return SmartHomeSessionLimitError(str(exc))
    name = type(exc).__name__
    if "Authentication" in name:
        return SmartHomeAuthError(str(exc))
    return exc


def slim_login_body(
    original: Dict[str, Any],
    *,
    device_id: str,
    pushtoken: str,
    appid: str,
    account: str,
) -> Dict[str, Any]:
    """mill1000-style /mj/user/login body: same silo, no 2.22.0 version block."""
    iot = original.get("iotData") or {}
    stamp = iot.get("stamp") or original.get("stamp")
    req_id = original.get("reqId") or iot.get("reqId")
    return {
        "data": {
            "platform": 2,
            "deviceId": device_id,
        },
        "iotData": {
            "appId": str(appid),
            "src": str(appid),
            "clientType": 1,
            "loginAccount": account,
            "iampwd": iot.get("iampwd"),
            "password": iot.get("password"),
            "pushToken": pushtoken,
            "stamp": stamp,
            "reqId": iot.get("reqId") or req_id,
        },
        "reqId": req_id,
        "stamp": stamp,
    }


def _install_slim_login(client: Any) -> None:
    original_login = client._login_proxied

    def wrapped() -> None:
        inner = client.api_request

        def patched(endpoint: str, *args: Any, **kwargs: Any) -> Any:
            data = kwargs.get("data")
            if endpoint == "/mj/user/login" and isinstance(data, dict):
                kwargs = dict(kwargs)
                kwargs["data"] = slim_login_body(
                    data,
                    device_id=client._device_id,
                    pushtoken=client._pushtoken,
                    appid=str(client._appid),
                    account=client._account,
                )
            return inner(endpoint, *args, **kwargs)

        client.api_request = patched
        try:
            return original_login()
        finally:
            client.api_request = inner

    client._login_proxied = wrapped


def _connect_slim(
    *,
    account: str,
    password: str,
    appname: str = identity.SMARTHOME_APPNAME,
    device_id: str,
    pushtoken: str,
    **_kwargs: Any,
) -> Any:
    from midea_beautiful.cloud import MideaCloud
    from midea_beautiful.midea import SUPPORTED_APPS

    if appname != identity.SMARTHOME_APPNAME:
        raise identity.UnknownFingerprintError(f"Slim login stays on {identity.SMARTHOME_APPNAME}")
    app = SUPPORTED_APPS[identity.SMARTHOME_APPNAME]
    client = MideaCloud(
        appkey=app["appkey"],
        account=account,
        password=password,
        appid=app["appid"],
        hmac_key=app.get("hmackey"),
        iot_key=app.get("iotkey"),
        api_url=app["apiurl"],
        proxied=app.get("proxied"),
        sign_key=app["signkey"],
        pushtoken=pushtoken,
        device_id=device_id,
    )
    _install_slim_login(client)
    client.authenticate()
    return client


def _connect_for_spec(spec: Dict[str, Any]) -> Callable[..., Any]:
    if spec.get("slim"):
        return _connect_slim
    from midea_beautiful import connect_to_cloud

    return connect_to_cloud


def connect(
    *,
    credentials: Optional[Dict[str, str]] = None,
    connect_fn: Optional[Callable[..., Any]] = None,
    identity_path: Optional[Path] = None,
) -> Any:
    creds = credentials or load_credentials()
    record = identity.load_or_create(identity_path)
    spec = identity.fingerprint_spec(record["fingerprint"])
    if connect_fn is None:
        connect_fn = _connect_for_spec(spec)
    try:
        cloud = connect_fn(
            account=creds["email"],
            password=creds["password"],
            appname=spec["appname"],
            device_id=record["device_id"],
            pushtoken=record["pushToken"],
        )
    except identity.UnknownFingerprintError:
        raise
    except Exception as exc:
        raise _map_cloud_error(exc) from exc
    _install_appliance_code_retry(cloud)
    return cloud


def _use_appliance_code(endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if "transparent/send" in str(endpoint) and "applianceId" in payload:
        payload = dict(payload)
        payload["applianceCode"] = payload.pop("applianceId")
    return payload


def _transparent_error_code(exc: Exception) -> Optional[int]:
    code = getattr(exc, "error_code", None)
    if code in TRANSPARENT_RETRY_CODES:
        return int(code)
    text = str(exc)
    for candidate in TRANSPARENT_RETRY_CODES:
        if f"({candidate})" in text or str(candidate) in text:
            return candidate
    return None


def _transparent_send_envelope(cloud: Any, req_id: Optional[str] = None) -> Dict[str, Any]:
    from midea_beautiful.cloud import CLOUD_API_CLIENT_TYPE, CLOUD_API_FORMAT, CLOUD_API_LANGUAGE

    return {
        "appId": getattr(cloud, "_appid", 1010),
        "format": CLOUD_API_FORMAT,
        "clientType": CLOUD_API_CLIENT_TYPE,
        "language": CLOUD_API_LANGUAGE,
        "src": getattr(cloud, "_appid", 1010),
        "stamp": datetime.now().strftime("%Y%m%d%H%M%S"),
        "deviceId": getattr(cloud, "_device_id", ""),
        "reqId": req_id or token_hex(16),
    }


def _install_appliance_code_retry(cloud: Any) -> None:
    """US SmartHome send rejects applianceId and appVNum. Seed reqId so the library skips those fields."""
    if hasattr(cloud, "api_request"):
        inner = cloud.api_request

        def patched_request(
            endpoint: str,
            args: Optional[Dict[str, Any]] = None,
            authenticate: bool = True,
            key: Any = None,
            data: Any = None,
            req_id: Any = None,
            instant: Any = None,
        ) -> Any:
            payload = dict(args) if isinstance(args, dict) else {}
            extra: Dict[str, Any] = {}
            if "transparent/send" in str(endpoint):
                payload = _use_appliance_code(endpoint, payload)
                if data is None:
                    extra["data"] = _transparent_send_envelope(cloud, req_id)
            if extra:
                return inner(
                    endpoint,
                    payload,
                    authenticate=authenticate,
                    key=key,
                    req_id=req_id,
                    instant=instant,
                    **extra,
                )
            return inner(
                endpoint,
                payload if args is not None or payload else args,
                authenticate=authenticate,
                key=key,
                data=data,
                req_id=req_id,
                instant=instant,
            )

        cloud.api_request = patched_request


def list_acs(cloud: Any) -> List[Dict[str, Any]]:
    raw = cloud.list_appliances() or []
    return [item for item in raw if _is_ac(item)]


def find_ac(cloud: Any, name: str) -> Dict[str, Any]:
    wanted = name.strip().casefold()
    matches = [item for item in list_acs(cloud) if str(item.get("name") or "").strip().casefold() == wanted]
    if not matches:
        raise UnknownNameError(f"No SmartHome AC named {name!r}")
    if len(matches) > 1:
        raise AmbiguousNameError(f"Multiple SmartHome ACs named {name!r}")
    return matches[0]


def _device(
    cloud: Any,
    appliance_id: str,
    *,
    appliance_state_fn: Optional[Callable[..., Any]] = None,
) -> Any:
    if appliance_state_fn is None:
        from midea_beautiful import appliance_state
        from midea_beautiful.midea import APPLIANCE_TYPE_AIRCON

        appliance_state_fn = appliance_state
        appliance_type = APPLIANCE_TYPE_AIRCON
    else:
        appliance_type = "0xac"
    return appliance_state_fn(
        cloud=cloud,
        use_cloud=True,
        appliance_id=str(appliance_id),
        appliance_type=appliance_type,
    )


def _check_online(dev: Any, name: str) -> None:
    if getattr(dev, "online", True) is False:
        raise OfflineError(f"{name} is offline")


def _readback_temp_f(dev: Any) -> Optional[float]:
    state = getattr(dev, "state", None)
    target = getattr(state, "target_temperature", None) if state is not None else None
    if target is None:
        target = getattr(dev, "target_temperature", None)
    if target is None:
        return None
    return c_to_f(float(target))


def _readback_running(dev: Any) -> Optional[bool]:
    state = getattr(dev, "state", None)
    running = getattr(state, "running", None) if state is not None else None
    if running is None:
        running = getattr(dev, "running", None)
    return None if running is None else bool(running)


def _refresh(dev: Any, cloud: Any) -> None:
    refresh = getattr(dev, "refresh", None)
    if callable(refresh):
        refresh(cloud)


def set_temp(
    cloud: Any,
    name: str,
    fahrenheit: float,
    *,
    mode: int = AUTO_MODE,
    appliance_state_fn: Optional[Callable[..., Any]] = None,
) -> None:
    if fahrenheit < MIN_F or fahrenheit > MAX_F:
        raise SetpointRangeError(f"Setpoint {fahrenheit}°F is outside {MIN_F}–{MAX_F}°F")
    meta = find_ac(cloud, name)
    try:
        dev = _device(cloud, meta["id"], appliance_state_fn=appliance_state_fn)
        _check_online(dev, name)
        dev.set_state(
            running=True,
            mode=mode,
            target_temperature=f_to_c(fahrenheit),
            cloud=cloud,
        )
        _refresh(dev, cloud)
        _check_online(dev, name)
        live_f = _readback_temp_f(dev)
        live_on = _readback_running(dev)
        if live_on is False:
            raise ReadbackError(f"{name} did not stay on after set")
        if live_f is not None and abs(live_f - fahrenheit) > READBACK_TOLERANCE_F:
            raise ReadbackError(f"{name} read back {live_f:.1f}°F, wanted {fahrenheit}°F")
    except (SmartHomeError, SystemExit):
        raise
    except Exception as exc:
        raise _map_cloud_error(exc) from exc


def turn_off(
    cloud: Any,
    name: str,
    *,
    appliance_state_fn: Optional[Callable[..., Any]] = None,
) -> None:
    meta = find_ac(cloud, name)
    try:
        dev = _device(cloud, meta["id"], appliance_state_fn=appliance_state_fn)
        _check_online(dev, name)
        dev.set_state(running=False, cloud=cloud)
        _refresh(dev, cloud)
        _check_online(dev, name)
        if _readback_running(dev) is True:
            raise ReadbackError(f"{name} is still on after off")
    except (SmartHomeError, SystemExit):
        raise
    except Exception as exc:
        raise _map_cloud_error(exc) from exc
