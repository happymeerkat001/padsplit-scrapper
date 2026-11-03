import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Set

import firebase_admin
from firebase_admin import credentials, firestore

from scraper import create_session, load_credentials, login, update_task_status


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _load_latest_payload(base_dir: Path) -> Dict:
    candidates = [
        base_dir / "docs" / "data" / "latest.json",
        base_dir / "output" / "latest.json",
    ]
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text())
    raise FileNotFoundError("Could not find latest.json in docs/data or output")


def _load_processed_doc_ids(path: Path) -> Set[str]:
    data = _load_json(path, {"processed_doc_ids": []})
    if isinstance(data, list):
        return set(str(x) for x in data)
    values = data.get("processed_doc_ids") if isinstance(data, dict) else []
    return set(str(x) for x in values or [])


def _save_processed_doc_ids(path: Path, processed_doc_ids: Set[str]) -> None:
    payload = {
        "processed_doc_ids": sorted(processed_doc_ids),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _build_task_map(payload: Dict) -> Dict[str, Dict]:
    task_map: Dict[str, Dict] = {}
    tasks = payload.get("tasks") or {}
    for bucket_tasks in tasks.values():
        for task in bucket_tasks or []:
            task_id = task.get("id")
            if task_id is None:
                continue
            task_map[str(task_id)] = task
    return task_map


def _init_firestore_client() -> firestore.Client:
    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if not service_account_json:
        raise RuntimeError("Missing FIREBASE_SERVICE_ACCOUNT_JSON")

    service_account_info = json.loads(service_account_json)
    cred = credentials.Certificate(service_account_info)

    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)

    return firestore.client()


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    processed_path = base_dir / "docs" / "data" / "processed_firestore_docs.json"

    payload = _load_latest_payload(base_dir)
    task_map = _build_task_map(payload)
    processed_doc_ids = _load_processed_doc_ids(processed_path)

    db = _init_firestore_client()
    docs = db.collection("task_messages").where("status", "==", "In Progress").stream()

    creds = load_credentials()
    updated_count = 0

    with create_session() as session:
        login(session, creds["email"], creds["password"])

        for doc in docs:
            doc_id = str(doc.id)
            if doc_id in processed_doc_ids:
                continue

            data = doc.to_dict() or {}
            task_id = data.get("taskId") or data.get("task_id")
            if task_id is None:
                continue

            task = task_map.get(str(task_id))
            if not task:
                continue

            current_status = task.get("status")
            if current_status != "in_progress":
                update_task_status(session, creds, int(task_id), "in_progress")
                updated_count += 1

            processed_doc_ids.add(doc_id)

    _save_processed_doc_ids(processed_path, processed_doc_ids)
    print(f"Processed Firestore docs: {len(processed_doc_ids)} | Updated PadSplit tasks: {updated_count}")


if __name__ == "__main__":
    main()
