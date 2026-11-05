"""Load property configuration without embedding operational data in source."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_house_profiles(path=None):
    path = Path(path or os.environ.get("PADSPLIT_LOCKOUT_HOUSE_PROFILES") or ROOT / ".private-config" / "lockout-house-profiles.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ValueError("Invalid private lockout profile configuration") from exc
    fields = {"label", "aliases", "require_front", "require_back", "sifely_back", "front_keys", "back_keys", "expect_room_code"}
    if not isinstance(data, dict) or len(data) > 1000:
        raise ValueError("Invalid private lockout profile configuration")
    seen = set()
    for slug, profile in data.items():
        if not isinstance(slug, str) or not slug or len(slug) > 200 or not isinstance(profile, dict) or set(profile) != fields:
            raise ValueError("Invalid private lockout profile configuration")
        if not isinstance(profile["label"], str) or not profile["label"] or len(profile["label"]) > 200:
            raise ValueError("Invalid private lockout profile configuration")
        for key in ("require_front", "require_back", "sifely_back", "expect_room_code"):
            if not isinstance(profile[key], bool):
                raise ValueError("Invalid private lockout profile configuration")
        for key in ("aliases", "front_keys", "back_keys"):
            values = profile[key]
            if not isinstance(values, list) or len(values) > 100 or any(not isinstance(v, str) or not v or len(v) > 200 for v in values):
                raise ValueError("Invalid private lockout profile configuration")
        for alias in profile["aliases"]:
            if alias.lower() in seen:
                raise ValueError("Invalid private lockout profile configuration")
            seen.add(alias.lower())
    return data
