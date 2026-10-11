"""Bounded incremental MATLAB log reader with durable cursors and retry spool.

No crash-dump files are added automatically. Configure only MATLAB runtime log globs.
"""

import glob
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.harness.evidence import redact

KEY_LINE = re.compile(
    r"error|warning|exception|stack|Error using|MATLAB:[\w:]+|^\s*(at|in)\s+", re.I
)


class IncrementalLogCollector:
    def __init__(
        self,
        patterns,
        state_path,
        *,
        max_files=8,
        read_bytes=65536,
        log_chars=4096,
        spool_entries=128,
        spool_bytes=262144,
    ):
        self.patterns = patterns
        self.state_path = Path(state_path)
        self.max_files, self.read_bytes, self.log_chars = max_files, read_bytes, log_chars
        self.spool_entries, self.spool_bytes = spool_entries, spool_bytes
        try:
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {"files": {}, "pending": [], "dropped": 0}

    def _save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.state_path)

    def collect(self):
        paths = sorted({name for pattern in self.patterns for name in glob.glob(pattern)})
        remaining = self.read_bytes
        for name in paths[: self.max_files]:
            if remaining <= 0:
                break
            path = Path(name)
            try:
                stat = path.stat()
                identity = f"{stat.st_dev}:{stat.st_ino}"
                cursor = self.state["files"].get(identity)
                with path.open("rb") as stream:
                    if cursor:
                        prefix = stream.read(cursor["prefix_len"])
                        changed = hashlib.sha256(prefix).hexdigest() != cursor["prefix_hash"]
                    else:
                        changed = True
                    if not cursor or stat.st_size < cursor["offset"] or changed:
                        stream.seek(0)
                        prefix = stream.read(min(64, stat.st_size))
                        cursor = {
                            "offset": 0,
                            "generation": uuid.uuid4().hex,
                            "prefix_len": len(prefix),
                            "prefix_hash": hashlib.sha256(prefix).hexdigest(),
                            "context": [],
                            "after": 0,
                            "skip_long": False,
                        }
                    self.state["files"][identity] = cursor
                    if cursor["prefix_len"] == 0 and stat.st_size > 0:
                        stream.seek(0)
                        prefix = stream.read(min(64, stat.st_size))
                        cursor["prefix_len"] = len(prefix)
                        cursor["prefix_hash"] = hashlib.sha256(prefix).hexdigest()
                    stream.seek(cursor["offset"])
                    while remaining > 0:
                        offset = stream.tell()
                        raw = stream.readline(min(remaining, self.log_chars * 4))
                        if not raw:
                            break
                        remaining -= len(raw)
                        # Partial normal lines stay unread until the next poll; giant
                        # lines are discarded in bounded chunks to avoid memory growth.
                        if not raw.endswith(b"\n"):
                            if (
                                len(raw) >= min(self.read_bytes, self.log_chars * 4)
                                or cursor["skip_long"]
                            ):
                                cursor["skip_long"] = True
                                cursor["offset"] = stream.tell()
                            break
                        cursor["offset"] = stream.tell()
                        if cursor["skip_long"]:
                            cursor["skip_long"] = False
                            continue
                        text = redact(
                            raw.decode("utf-8", errors="replace").rstrip(), self.log_chars
                        )
                        if KEY_LINE.search(text) or cursor["after"] > 0:
                            event_id = hashlib.sha256(
                                f"{identity}:{cursor['generation']}:{offset}".encode()
                            ).hexdigest()
                            self.state["pending"].append(
                                {
                                    "event_id": event_id,
                                    "source": redact(path.name, 256),
                                    "offset": offset,
                                    "ts": datetime.now(UTC).isoformat(),
                                    "text": redact(
                                        "\n".join(cursor["context"] + [text]), self.log_chars
                                    ),
                                }
                            )
                            cursor["after"] = 5 if KEY_LINE.search(text) else cursor["after"] - 1
                        cursor["context"] = (cursor["context"] + [text])[-3:]
            except OSError:
                continue
        # Bound cursor metadata as well as content, including old rotations.
        self.state["files"] = dict(list(self.state["files"].items())[-64:])
        while (
            len(self.state["pending"]) > self.spool_entries
            or len(json.dumps(self.state["pending"]).encode()) > self.spool_bytes
        ):
            self.state["pending"].pop(0)
            self.state["dropped"] += 1
        self._save()

    def batch(self, max_bytes=65536, max_entries=100):
        batch = []
        for entry in self.state["pending"][:max_entries]:
            candidate = batch + [entry]
            if len(json.dumps(candidate, ensure_ascii=False).encode()) > max_bytes:
                break
            batch = candidate
        return batch

    def acknowledge(self, batch):
        accepted = {entry["event_id"] for entry in batch}
        self.state["pending"] = [
            entry for entry in self.state["pending"] if entry["event_id"] not in accepted
        ]
        self._save()
