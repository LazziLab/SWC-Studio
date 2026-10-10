"""RO-Crate export — v1 minimal implementation (PROVENANCE_SPEC §20).

Bundles an SWC + its full materialized history + AI metadata +
per-AI-run ``reproduce.yaml`` files into a portable directory matching the
`RO-Crate <https://www.researchobject.org/ro-crate/>`_ shape:

::

    <crate>/
        ro-crate-metadata.json     # JSON-LD provenance graph (PROV-O vocab)
        data/<source.swc>          # the dataset
        history/events.jsonl       # the log
        history/objects/...        # the blob store
        history/refs/...           # branches + tags
        reproduce/<short>.yaml     # one yaml per AI commit

The ``ro-crate-metadata.json`` is a minimal JSON-LD document that
maps:

* the SWC -> ``schema:Dataset``
* each commit -> ``prov:Activity``
* each actor -> ``prov:Agent``
* AI runs -> additional ``prov:Activity`` with model/env metadata

By default (``with_pii=False``) every OS username is replaced by a
stable pseudonym (``agent-1``, ``agent-2``, ... in order of first
appearance in the log) wherever the export would carry it: the crate
metadata, the copied ``events.jsonl``, the copied ``index.sqlite`` and
the ``@PROV`` header of the copied SWC. ``history/checksums.json`` is
recomputed to match. Commit ids do not cover the actor (spec §4), so
the scrubbed log still verifies. The blobs are copied as-is: they hold
no usernames, hostnames or absolute paths (env capture in
:mod:`provenance.env` deliberately omits hostname/username).
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from swcstudio.core.provenance.archive import _history_entries
from swcstudio.core.provenance.canonical import sha256_hex
from swcstudio.core.provenance.events import canonical_json, iter_events
from swcstudio.core.provenance.header import PROV_PREFIX
from swcstudio.core.provenance.objects import BlobNotFoundError, ObjectStore
from swcstudio.core.provenance.tracked_op import history_dir_for

__all__ = ["export_crate"]


def export_crate(
    swc_path: str | Path,
    out_dir: str | Path,
    *,
    with_pii: bool = False,
) -> Path:
    """Bundle an SWC + history into an RO-Crate directory at ``out_dir``.

    Returns the crate root path. Overwrites ``out_dir`` if it exists
    (caller is expected to choose a fresh path).
    """
    src = Path(swc_path)
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # Copy the dataset.
    data_dir = out / "data"
    data_dir.mkdir()
    if src.exists():
        shutil.copy2(src, data_dir / src.name)

    # Copy history (events.jsonl, objects/, refs/, version, root.json,
    # index.sqlite — but NOT lock or SQLite -wal/-shm side files).
    hist = history_dir_for(src)
    hist_out = out / "history"
    hist_out.mkdir()
    if hist.exists():
        for child in hist.iterdir():
            if child.name == "lock" or child.name.endswith(("-wal", "-shm")):
                continue
            dest = hist_out / child.name
            if child.is_dir():
                shutil.copytree(child, dest)
            else:
                shutil.copy2(child, dest)

    pseudonyms = _actor_pseudonyms(hist / "events.jsonl")
    if not with_pii:
        _scrub_history_copy(hist_out, pseudonyms)
        _scrub_prov_actor(data_dir / src.name, pseudonyms)

    # Per-AI-commit reproduce.yaml files.
    rep_dir = out / "reproduce"
    rep_dir.mkdir()
    store = ObjectStore(hist / "objects") if hist.exists() else None
    if store is not None and (hist / "events.jsonl").exists():
        from swcstudio.cli.history_cli import _format_reproduce_yaml  # local import avoids cycle
        for ev in iter_events(hist / "events.jsonl"):
            ai_refs = [op.get("ai_run_ref") for op in ev.ops if op.get("ai_run_ref")]
            if not ai_refs:
                continue
            try:
                blob = json.loads(store.get(ai_refs[0].removeprefix("sha256:")))
            except (BlobNotFoundError, ValueError):
                continue
            short = (ev.id or "").removeprefix("sha256:")[:12]
            (rep_dir / f"{short}.yaml").write_text(
                _format_reproduce_yaml(ev, blob, src),
                encoding="utf-8",
            )

    # ro-crate-metadata.json (minimal; rich PROV-O export deferred).
    metadata = _build_minimal_crate_metadata(src, hist, with_pii=with_pii, pseudonyms=pseudonyms)
    (out / "ro-crate-metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return out


def _actor_pseudonyms(events_path: Path) -> dict[str, str]:
    """Map each OS username in the log to ``agent-1``, ``agent-2``, ...

    Numbered in order of first appearance, so re-exporting the same
    history gives the same pseudonyms. A hash of the username would
    also be stable, but short usernames are easy to recover from one.
    """
    out: dict[str, str] = {}
    if not events_path.exists():
        return out
    for line in events_path.read_text(encoding="utf-8").splitlines():
        try:
            user = json.loads(line).get("actor", {}).get("os_user")
        except (ValueError, AttributeError):
            continue
        if user is not None:
            out.setdefault(str(user), f"agent-{len(out) + 1}")
    return out


def _scrub_history_copy(hist_out: Path, pseudonyms: dict[str, str]) -> None:
    """Replace usernames in the copied log and index, then re-checksum."""
    events = hist_out / "events.jsonl"
    if events.exists():
        lines = []
        for line in events.read_bytes().splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                lines.append(line)
                continue
            actor = obj.get("actor")
            if isinstance(actor, dict) and "os_user" in actor:
                actor["os_user"] = pseudonyms.get(str(actor["os_user"]), "agent-unknown")
            lines.append(canonical_json(obj))
        events.write_bytes(b"".join(line + b"\n" for line in lines))

    index = hist_out / "index.sqlite"
    if index.exists():
        with closing(sqlite3.connect(index)) as conn:
            for user, alias in pseudonyms.items():
                conn.execute("UPDATE commits SET os_user = ? WHERE os_user = ?", (alias, user))
            conn.commit()
            # Leave one self-contained file, and rebuild it so the old
            # values do not survive in freed pages.
            conn.execute("PRAGMA journal_mode = DELETE")
            conn.execute("VACUUM")

    checksums_path = hist_out / "checksums.json"
    if checksums_path.exists():
        checksums = {
            path.relative_to(hist_out).as_posix(): sha256_hex(path.read_bytes())
            for path in _history_entries(hist_out)
        }
        checksums_path.write_bytes(json.dumps(checksums, sort_keys=True).encode("utf-8"))


def _scrub_prov_actor(swc_path: Path, pseudonyms: dict[str, str]) -> None:
    """Replace the ``actor=`` value in the copied SWC's ``@PROV`` lines."""
    if not swc_path.exists():
        return
    # The header writes whitespace in values as "_" (header._safe).
    by_header_value = {re.sub(r"\s+", "_", user): alias for user, alias in pseudonyms.items()}

    def _alias(match: re.Match) -> str:
        return "actor=" + by_header_value.get(match.group(1), "agent-unknown")

    text = swc_path.read_bytes().decode("utf-8")
    lines = [
        re.sub(r"actor=(\S+)", _alias, line) if line.startswith(PROV_PREFIX) else line
        for line in text.splitlines(keepends=True)
    ]
    swc_path.write_bytes("".join(lines).encode("utf-8"))


def _build_minimal_crate_metadata(
    src: Path,
    hist: Path,
    *,
    with_pii: bool,
    pseudonyms: dict[str, str],
) -> dict[str, Any]:
    """Produce a JSON-LD doc with the standard RO-Crate spine.

    Includes the dataset and one Activity per commit, with minimal
    PROV-O typing. Fuller PROV-O graph (Entity/Activity/Agent triples
    for every node-level change) is intentionally deferred — this
    minimal form already enables RO-Crate readers to ingest the
    dataset and surface the commit timeline.
    """
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    graph: list[dict[str, Any]] = [
        {
            "@id": "ro-crate-metadata.json",
            "@type": "CreativeWork",
            "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
            "about": {"@id": "./"},
        },
        {
            "@id": "./",
            "@type": "Dataset",
            "name": src.stem,
            "datePublished": now_iso,
            "hasPart": [{"@id": f"data/{src.name}"}],
        },
        {
            "@id": f"data/{src.name}",
            "@type": ["File", "Dataset"],
            "encodingFormat": "text/plain",
            "name": src.name,
        },
    ]

    if hist.exists() and (hist / "events.jsonl").exists():
        for ev in iter_events(hist / "events.jsonl"):
            short = (ev.id or "").removeprefix("sha256:")[:12]
            actor_user = str((ev.actor or {}).get("os_user", "unknown"))
            actor_name = actor_user if with_pii else pseudonyms.get(actor_user, "agent-unknown")
            actor_id = f"#agent-{actor_user}" if with_pii else f"#{actor_name}"
            graph.append({
                "@id": f"#commit-{short}",
                "@type": ["CreateAction", "Activity"],
                "name": (ev.message or short),
                "startTime": ev.ts,
                "agent": {"@id": actor_id},
                "instrument": {
                    "@type": "SoftwareApplication",
                    "name": (ev.tool or {}).get("name"),
                    "softwareVersion": (ev.tool or {}).get("version"),
                },
                "result": {"@id": f"data/{src.name}"},
                "identifier": ev.id,
            })
            # Add agent node if not already present.
            if not any(g.get("@id") == actor_id for g in graph):
                graph.append({
                    "@id": actor_id,
                    "@type": "Person",
                    "name": actor_name,
                })

    return {
        "@context": [
            "https://w3id.org/ro/crate/1.1/context",
            {"prov": "http://www.w3.org/ns/prov#",
             "Activity": "prov:Activity",
             "Agent": "prov:Agent",
             "Entity": "prov:Entity"},
        ],
        "@graph": graph,
    }
