from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field, fields


class Status:
    QUEUED = "Queued"
    CONNECTING = "Connecting"
    DOWNLOADING = "Downloading"
    PROCESSING = "Processing"
    PAUSED = "Paused"
    COMPLETED = "Completed"
    ERROR = "Error"

    ACTIVE = {CONNECTING, DOWNLOADING, PROCESSING}


EXPIRED = "Link expired"              # error prefixes the GUI keys off
LOGIN_NEEDED = "Login required"


@dataclass
class Segment:
    start: int
    end: int          # inclusive
    done: int = 0
    active: bool = field(default=False, compare=False)   # runtime only
    speed: float = field(default=0.0, compare=False)     # runtime only, bytes/s of its connection

    @property
    def pos(self) -> int:
        return self.start + self.done

    @property
    def remaining(self) -> int:
        return max(0, self.end - self.pos + 1)

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "done": self.done}


@dataclass
class DownloadItem:
    url: str
    kind: str = "file"                 # "file" or "video"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    filename: str = ""
    save_dir: str = ""                 # empty = resolve from settings/category
    category: str = "Other"
    total: int = -1
    downloaded: int = 0
    resumable: bool = False
    status: str = Status.QUEUED
    error: str = ""
    added: float = field(default_factory=time.time)
    finished: float = 0.0
    connections: int = 8
    auto_connections: bool = False     # YDM picks the count: climbs while more connections add speed
    segments: list[Segment] = field(default_factory=list)
    headers: dict = field(default_factory=dict)
    video_format: str = ""
    title: str = ""
    seen: bool = True                  # False = finished but the user hasn't looked at it yet
    pending: bool = False              # started before the user confirmed (IDM-style pre-start)
    dest_dir: str = ""                 # chosen while it was already downloading: applied at the end
    dest_name: str = ""
    convert: str = ""                  # video: "mp3" = extract audio as MP3
    parts: list = field(default_factory=list)   # video: per-stream progress, so a pause resumes
    auth_user: str = ""                # login for this download (Basic / Digest / NTLM / FTP)
    auth_pass: str = ""                # protected with vault.protect (DPAPI on Windows)
    # runtime only
    speed: float = 0.0
    eta: float = -1.0
    retries: int = 0

    RUNTIME = ("speed", "eta", "retries")

    @property
    def path(self) -> str:
        import os
        return os.path.join(self.save_dir, self.filename) if self.save_dir and self.filename else ""

    @property
    def part_path(self) -> str:
        return self.path + ".ydm" if self.path else ""

    @property
    def expired(self) -> bool:
        """The link stopped working (expired, or needs the page again): renew the address."""
        return self.status == Status.ERROR and self.error.startswith(EXPIRED)

    @property
    def needs_login(self) -> bool:
        return self.status == Status.ERROR and self.error.startswith(LOGIN_NEEDED)

    @property
    def progress(self) -> float:
        if self.status == Status.COMPLETED:
            return 1.0
        if self.total and self.total > 0:
            return min(1.0, self.downloaded / self.total)
        return 0.0

    def to_dict(self) -> dict:
        d = {f.name: getattr(self, f.name) for f in fields(self) if f.name not in self.RUNTIME}
        d["segments"] = [s.to_dict() for s in self.segments]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DownloadItem":
        known = {f.name for f in fields(cls)} - set(cls.RUNTIME)
        data = {k: v for k, v in d.items() if k in known}
        data["segments"] = [Segment(**s) for s in d.get("segments", [])]
        return cls(**data)
