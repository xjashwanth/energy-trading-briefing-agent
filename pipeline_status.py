"""Local run records, execution locking, and source coverage."""
import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def coverage(sources, as_of_date, root):
    rows = []
    for key, folder in (("price", "prices"), ("weather", "weather"), ("outage", "outages")):
        data = sources.get(key)
        earlier = sorted(p.stem for p in (root / "data/ingested" / folder).glob("*.json")
                         if p.stem <= as_of_date)
        status = "available" if data and data.get("items") else "empty" if data is not None else "missing"
        rows.append({"source": key, "status": status, "items": len((data or {}).get("items", [])),
                     "latest_saved_date": earlier[-1] if earlier else None})
    windows = []
    for path in (root / "data/ingested/regulatory").glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            end = data.get("window_end", data.get("as_of_date", ""))
            date.fromisoformat(end)
            start = data.get("window_start", end)
            if date.fromisoformat(start) <= date.fromisoformat(as_of_date):
                windows.append(end)
        except (ValueError, TypeError, AttributeError, OSError):
            continue
    # Retrospective windows may cover the target but do not prove contemporaneous access.
    latest = max(windows) if windows else None
    age = max(0, (date.fromisoformat(as_of_date) - date.fromisoformat(latest)).days) if latest else None
    regulatory = sources.get("regulatory")
    status = "missing" if regulatory is None or latest is None else "stale" if age > 7 else "available"
    rows.append({"source": "regulatory", "status": status,
                 "items": len((regulatory or {}).get("items", [])), "latest_saved_date": latest,
                 "age_days": age, "freshness_limit_days": 7})
    return rows


class RunRecord:
    def __init__(self, root, target):
        self.directory = root / "runs"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.data = {"run_id": uuid.uuid4().hex, "date": target, "started_at": now(),
                     "status": "running", "stages": [], "coverage": []}
        self.path = self.directory / f"{target}_{self.data['run_id']}.json"
        self.save()

    def save(self):
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        temp.replace(self.path)

    @contextmanager
    def stage(self, name):
        entry = {"name": name, "status": "running", "started_at": now()}
        self.data["stages"].append(entry)
        self.save()
        start = time.monotonic()
        try:
            yield
            entry["status"] = "success"
        except Exception as exc:
            entry.update(status="failed", error=type(exc).__name__)
            raise
        finally:
            entry["duration_seconds"] = round(time.monotonic() - start, 3)
            self.save()

    def finish(self, status, error=None):
        self.data.update(status=status, finished_at=now())
        if error:
            self.data["error"] = error
        self.save()


@contextmanager
def execution_lock(root):
    """OS-managed lock; automatically released even if a worker crashes."""
    directory = root / "runs"
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / "pipeline.lock").open("a+b")
    locked = False
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise RuntimeError("Another pipeline run is active. Wait for it to finish.") from exc
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def latest_run(root, target):
    records = []
    for path in (root / "runs").glob(f"{target}_*.json"):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            continue
    return max(records, key=lambda r: r.get("started_at", "")) if records else None
