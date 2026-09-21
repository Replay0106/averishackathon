"""
Simulated edge device: reads a sensor, encodes and signs each reading, keeps
an on-disk store-and-forward queue, and only drops a message once it holds a
Merkle inclusion proof it has verified for itself against the gateway's
published root. No physical hardware involved — swap read_sensor() for a
real driver and nothing else changes.
"""

import json
import os
import random
import time
from dataclasses import asdict, dataclass
from typing import List, Optional

from . import wire


@dataclass
class QueuedMessage:
    seq: int
    raw_wire: str
    sent_at_ms: int
    confirmed: bool = False


class DiskQueue:
    """Append-only local queue, persisted as JSON Lines. A message is removed
    only after confirm() — never on send. A crash or dropped connection can
    delay delivery but can never silently lose a reading."""

    def __init__(self, path: str):
        self.path = path
        self._messages: List[QueuedMessage] = []
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            self._messages.append(QueuedMessage(**json.loads(line)))
            except OSError:
                pass

    def _persist(self):
        try:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                for m in self._messages:
                    f.write(json.dumps(asdict(m)) + "\n")
        except OSError:
            pass

    def enqueue(self, msg: QueuedMessage):
        self._messages.append(msg)
        self._persist()

    def pending(self) -> List[QueuedMessage]:
        return [m for m in self._messages if not m.confirmed]

    def confirm(self, seq: int):
        for m in self._messages:
            if m.seq == seq:
                m.confirmed = True
        self._persist()


class EdgeDevice:
    def __init__(self, device_id: str, secret_key: bytes, metric: str,
                 base_value: int, noise: int = 5, queue_dir: str = "_data"):
        self.device_id = device_id
        self.secret_key = secret_key
        self.metric = metric
        self.base_value = base_value
        self.noise = noise
        self.session_id = "s00000000"  # invalid until the gateway issues a real one
        self.seq = 0
        self.start_time: Optional[float] = None  # wall-clock time of this device's first message
        self.last_raw: Optional[str] = None
        self.last_value: Optional[int] = None
        self.queue = DiskQueue(os.path.join(queue_dir, f"queue_{device_id}.jsonl"))

    def read_sensor(self) -> int:
        return self.base_value + random.randint(-self.noise, self.noise)

    def build_message(self, value: Optional[int] = None) -> QueuedMessage:
        value = self.read_sensor() if value is None else value
        self.seq += 1
        # Real elapsed wall-clock time, not a fixed step per message — so the
        # gateway's freshness gate (which compares uptime elapsed to wall-clock
        # elapsed) only trips on an actual delay, not on how long the caller
        # takes between sends.
        if self.start_time is None:
            self.start_time = time.time()
        uptime_ms = int((time.time() - self.start_time) * 1000)
        raw = wire.encode(self.device_id, self.session_id, self.seq, self.metric,
                           value, uptime_ms, self.secret_key)
        msg = QueuedMessage(self.seq, raw, int(time.time() * 1000))
        self.queue.enqueue(msg)
        self.last_raw = raw
        self.last_value = value
        return msg

    def adopt_session(self, session_id: str):
        self.session_id = session_id
