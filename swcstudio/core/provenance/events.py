"""Append-only JSONL event log + canonical event encoding.

Implements PROVENANCE_SPEC §4: every commit on every branch is one
line in ``.history/events.jsonl``. The line is a single JSON object
on a single line of UTF-8 text terminated by ``\\n``. Lines are
append-only — never rewritten, never reordered.

This module owns:

* The :class:`Event` dataclass (the in-memory shape of one commit).
* :func:`canonical_json` — a deterministic byte encoding used for
  hashing and for writing event lines (so two writers producing the
  same logical event produce byte-identical lines).
* :func:`compute_event_id` — derives the chain SHA from the subset
  of fields the spec defines as identity-bearing.
* :func:`append_event` — the only sanctioned writer. Acquires the
  caller's lock context, writes one line, fsyncs.
* :func:`iter_events` / :func:`load_events` — readers that
  transparently skip events whose ``schema_version`` exceeds the
  current build's max (forwards-compat per spec §17).

The log file is the **source of truth**. The SQLite index built later
on top is a rebuildable cache. If the JSONL says X and SQLite says Y,
JSONL wins.
"""

from __future__ import annotations

import dataclasses
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from swcstudio.core.provenance.canonical import sha256_hex

__all__ = [
    "Event",
    "MAX_SUPPORTED_SCHEMA_VERSION",
    "append_event",
    "canonical_json",
    "compute_event_id",
    "iter_events",
    "load_events",
    "new_session_id",
]


# Per spec §17: this build understands schema_version up to this value.
# Events with a higher version are skipped on read, never silently
# downgraded, never rewritten (preserving forward-compat).
MAX_SUPPORTED_SCHEMA_VERSION = 1


# Fields that contribute to the event's identity hash (spec §4).
# Adding to this set is a v2 change. Removing or reordering is a v2
# change. Order matters because canonical_json sorts keys, but the
# *set* of keys is the contract.
_ID_FIELDS = ("parent", "branch", "ops", "input_sha", "output_sha")


@dataclass
class Event:
    """In-memory shape of one commit event.

    Mirrors the on-disk JSON envelope from spec §4. Use
    ``Event.to_json_obj()`` to convert to a dict suitable for
    :func:`canonical_json`, and :func:`Event.from_json_obj` to parse
    the reverse.
    """

    schema_version: int
    kind: str                      # always "commit" in v1; reserved for future event kinds
    parent: str | None             # event id of previous commit; None for the first
    branch: str
    ts: str                        # ISO-8601 UTC, second precision, e.g. "2024-01-01T11:45:22Z"
    actor: dict[str, Any]          # {"os_user": "tuo"}
    tool: dict[str, Any]           # {"name", "version", "git_sha"?}
    session_id: str
    message: str
    ops: list[dict[str, Any]]      # see Op shape in spec §4
    input_sha: str | None          # canonical SWC sha before this commit (None for the very first)
    output_sha: str                # canonical SWC sha after this commit
    diff_ref: str | None           # sha of diff blob (None for snapshot-only events e.g. very first commit)
    id: str = ""                   # filled by compute_event_id; do not set manually

    # Optional fields (added with default None so v1.x can grow without breaking readers)
    derived_from: dict[str, Any] | None = None
    is_snapshot: bool = False

    # Catch-all for unknown future fields encountered on read; preserved
    # verbatim on rewrite so unknown-future-field info round-trips.
    _extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # serialization
    # ------------------------------------------------------------------

    def to_json_obj(self) -> dict[str, Any]:
        """Convert to a JSON-ready dict, dropping None-valued optionals.

        The output is what gets written to ``events.jsonl`` and what
        :func:`canonical_json` operates on.
        """
        d: dict[str, Any] = {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "id": self.id,
            "parent": self.parent,
            "branch": self.branch,
            "ts": self.ts,
            "actor": self.actor,
            "tool": self.tool,
            "session_id": self.session_id,
            "message": self.message,
            "ops": self.ops,
            "input_sha": self.input_sha,
            "output_sha": self.output_sha,
            "diff_ref": self.diff_ref,
        }
        if self.derived_from is not None:
            d["derived_from"] = self.derived_from
        if self.is_snapshot:
            d["is_snapshot"] = True
        # Round-trip preserved unknown fields so v1 readers writing a
        # processed v2 event back out don't drop forward-compat data.
        for k, v in self._extra.items():
            d.setdefault(k, v)
        return d

    @classmethod
    def from_json_obj(cls, obj: dict[str, Any]) -> "Event":
        known = {
            "schema_version", "kind", "id", "parent", "branch", "ts",
            "actor", "tool", "session_id", "message", "ops",
            "input_sha", "output_sha", "diff_ref",
            "derived_from", "is_snapshot",
        }
        extra = {k: v for k, v in obj.items() if k not in known}
        return cls(
            schema_version=int(obj["schema_version"]),
            kind=str(obj["kind"]),
            id=str(obj.get("id", "")),
            parent=obj.get("parent"),
            branch=str(obj["branch"]),
            ts=str(obj["ts"]),
            actor=dict(obj.get("actor", {})),
            tool=dict(obj.get("tool", {})),
            session_id=str(obj.get("session_id", "")),
            message=str(obj.get("message", "")),
            ops=list(obj.get("ops", [])),
            input_sha=obj.get("input_sha"),
            output_sha=str(obj.get("output_sha", "")),
            diff_ref=obj.get("diff_ref"),
            derived_from=obj.get("derived_from"),
            is_snapshot=bool(obj.get("is_snapshot", False)),
            _extra=extra,
        )


def _with_string_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            (k if isinstance(k, str) or not isinstance(k, (int, float, bool, type(None))) else json.dumps(k)):
                _with_string_keys(v)
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [_with_string_keys(v) for v in obj]
    return obj


def canonical_json(obj: Any) -> bytes:
    """Deterministic UTF-8 JSON encoding for hashing and writing.

    Two callers producing the same logical object **must** produce
    byte-identical output, which is why we fix:

    * sorted keys
    * compact separators (``,`` and ``:`` with no spaces)
    * ``ensure_ascii=False`` so unicode strings are not escaped to
      ``\\u`` sequences (escaping is also deterministic, but readable
      output is friendlier for ``cat`` debugging)
    * ``allow_nan=False`` — NaN/Inf are not JSON-representable; if a
      caller tries, we raise rather than silently produce a
      non-portable value
    * non-string keys converted to their JSON text first — ``json``
      writes ``{2: …, 10: …}`` with keys sorted as numbers, but once read
      back from the log they are strings and sort as ``"10"``, ``"2"``,
      so the same object would hash differently on re-check
    """
    return json.dumps(
        _with_string_keys(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def compute_event_id(event_obj: dict[str, Any]) -> str:
    """Derive the chain SHA for an event from its identity-bearing subset.

    Per spec §4: ``id = sha256(canonical_json({parent, branch, ops,
    input_sha, output_sha}))``. Fields like ``ts``, ``actor``,
    ``message`` are descriptive metadata, not part of identity, so two
    commits with the same logical effect from the same parent collapse
    to the same id.
    """
    payload = {k: event_obj.get(k) for k in _ID_FIELDS}
    return "sha256:" + sha256_hex(canonical_json(payload))


# ----------------------------------------------------------------------
# session ids — opaque, sortable
# ----------------------------------------------------------------------


def new_session_id() -> str:
    """Generate a sortable, opaque session id.

    We avoid ULID/UUID dependencies — this is just a millisecond
    timestamp + random hex. Collisions are statistically impossible at
    SWC-Studio's volume; sortability makes log-grepping by time easy.
    """
    return f"{int(time.time()*1000):013x}-{secrets.token_hex(6)}"


# ----------------------------------------------------------------------
# log writing
# ----------------------------------------------------------------------


def append_event(jsonl_path: str | os.PathLike[str], event: Event) -> None:
    """Append one event line to the JSONL log, fsynced.

    The caller MUST hold the ``.history/lock`` (LockFile) for the
    duration of this call. We do not acquire it here because tracked_op
    holds it across multiple writes (events + objects + index + refs).

    The event is serialized with :func:`canonical_json` so two writers
    producing the same logical event produce byte-identical lines.
    """
    if not event.id:
        raise ValueError("Event.id must be set before appending; call compute_event_id")

    path = Path(jsonl_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    line = canonical_json(event.to_json_obj()) + b"\n"

    # Append-only with fsync for durability. We open in "ab" so
    # writes hit EOF atomically on POSIX (writes < PIPE_BUF are
    # atomic; in practice a single ``write`` call is what we need).
    with open(path, "ab") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


# ----------------------------------------------------------------------
# log reading
# ----------------------------------------------------------------------


def iter_events(jsonl_path: str | os.PathLike[str]) -> Iterator[Event]:
    """Yield events from the log in append order.

    Forwards-compatibility: events whose ``schema_version`` exceeds
    :data:`MAX_SUPPORTED_SCHEMA_VERSION` are silently skipped (spec
    §17). Malformed lines are skipped with no crash — the log is
    durable, individual lines should never be malformed in practice,
    but we don't want one bad line to make the whole log unreadable.
    """
    path = Path(jsonl_path)
    if not path.exists():
        return
    with open(path, "rb") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            sv = obj.get("schema_version")
            if not isinstance(sv, int) or sv > MAX_SUPPORTED_SCHEMA_VERSION:
                continue
            try:
                yield Event.from_json_obj(obj)
            except (KeyError, ValueError, TypeError):
                continue


def load_events(jsonl_path: str | os.PathLike[str]) -> list[Event]:
    """Materialize the entire log as a list. Convenience over iter_events."""
    return list(iter_events(jsonl_path))
