"""Human-readable text renderer over the provenance store.

Replaces every ``format_*_report_text`` helper in the old
``swcstudio.core.reporting`` module (PROVENANCE_SPEC §15, M9). The
old helpers had one function per report kind and wrote files
directly. The new design has one renderer that knows how to format
any commit / ops list / diff / AI run, all driven by the canonical
event log + blobs + index.

Why a single renderer rather than one per op kind:

* The on-disk shape *is* the same across all ops (event envelope +
  ops list + diff blob + optional AI-run blob). Rendering them
  uniformly means new op kinds get a useful default presentation
  for free.
* The CLI ``swcstudio history show <sha> --format=text`` and the
  GUI commit-detail panel call the same code, so users can never
  see a mismatch.

Output is plain text, line-oriented, suitable for piping through
``less`` or attaching to an email. No ANSI color codes (let the
caller add them if they want).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from swcstudio.core.provenance.events import Event, iter_events
from swcstudio.core.provenance.index import (
    ensure_schema,
    open_index,
    query_commits,
)
from swcstudio.core.provenance.objects import BlobNotFoundError, ObjectStore

__all__ = [
    "render_commit_text",
    "render_history_log_text",
    "render_diff_text",
    "render_ai_run_text",
]


# ----------------------------------------------------------------------
# top-level entry points
# ----------------------------------------------------------------------


def render_history_log_text(
    history_dir: str | Path,
    *,
    branch: str | None = None,
    actor: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
) -> str:
    """One-line-per-commit summary, most-recent first.

    Equivalent to ``swcstudio history log`` output. Filters mirror
    :func:`index.query_commits`.
    """
    hist = Path(history_dir)
    conn = open_index(hist)
    try:
        ensure_schema(conn)
        rows = query_commits(
            conn,
            branch=branch,
            actor=actor,
            since=since,
            until=until,
            limit=limit,
        )
        if not rows:
            return "(no commits)\n"
        # Compute pretty column widths from data — keeps long usernames
        # or messages from being truncated unnecessarily.
        widths = {
            "sha": 12,
            "ts": max(len(r["ts"] or "") for r in rows),
            "actor": max(8, max(len(r["os_user"] or "") for r in rows)),
            "branch": max(6, max(len(r["branch"] or "") for r in rows)),
        }
        out: list[str] = []
        out.append(
            f"{'sha':<{widths['sha']}}  "
            f"{'ts':<{widths['ts']}}  "
            f"{'actor':<{widths['actor']}}  "
            f"{'branch':<{widths['branch']}}  "
            f"ops  message"
        )
        out.append("-" * (sum(widths.values()) + 20))
        for r in rows:
            short = (r["sha"] or "").removeprefix("sha256:")[:widths["sha"]]
            ops_summary = _ops_one_line(conn, r["sha"])
            out.append(
                f"{short:<{widths['sha']}}  "
                f"{(r['ts'] or ''):<{widths['ts']}}  "
                f"{(r['os_user'] or ''):<{widths['actor']}}  "
                f"{(r['branch'] or ''):<{widths['branch']}}  "
                f"{ops_summary:<3}  {r['message'] or ''}"
            )
        return "\n".join(out) + "\n"
    finally:
        conn.close()


def render_commit_text(
    history_dir: str | Path,
    commit_sha: str,
) -> str:
    """Detailed multi-section view of one commit.

    Sections rendered (in order, omitted if empty):
      - Header (sha, parent, branch, ts, actor, tool, message)
      - Ops (kind, params, summary counts, ai_run_ref if any)
      - AI runs (params, metrics, env_hash, artifacts) — placed
        BEFORE Changes so the AI metadata is visible without
        scrolling past thousands of node-level diff lines.
      - Derived from (cross-file lineage)
      - Changes (node-level + topology, from the diff blob).
        Capped to a reasonable number of entries with a tail count
        so the panel stays scannable for huge diffs.
    """
    hist = Path(history_dir)
    event = _find_event_by_sha(hist, commit_sha)
    if event is None:
        return f"(no commit found for {commit_sha})\n"

    out: list[str] = []
    out.extend(_render_header(event))

    if event.ops:
        out.append("")
        out.append("Ops:")
        for i, op in enumerate(event.ops):
            out.extend(_render_op(i, op))

    # AI run detail (moved above Changes — the diff section for AI
    # ops is typically huge, and AI metadata is the high-signal
    # part the reader most wants to see).
    ai_refs = [op.get("ai_run_ref") for op in event.ops if op.get("ai_run_ref")]
    if ai_refs:
        out.append("")
        out.append("AI runs:")
        for ref in ai_refs:
            blob = _read_blob_json(hist, ref)
            if blob is None:
                out.append(f"  (AI-run blob {ref} unavailable)")
                continue
            out.append(f"  ref={_short(ref)}")
            out.extend("    " + line for line in render_ai_run_text(blob).splitlines())
            # Also expand the env fingerprint that the AI-run blob
            # references — packages/system info, deduplicated across
            # runs but still useful to surface inline once.
            env_hash = blob.get("env_hash")
            if env_hash:
                env_blob = _read_blob_json(hist, env_hash)
                if env_blob is not None:
                    out.append(f"    env: ref={_short(env_hash)}")
                    out.extend(
                        "      " + line
                        for line in render_env_text(env_blob).splitlines()
                    )

    if event.derived_from:
        out.append("")
        out.append("Derived from:")
        df = event.derived_from
        out.append(f"  source root:   {df.get('root_sha', '?')}")
        out.append(f"  source commit: {df.get('commit_sha', '?')}")
        if df.get("path"):
            out.append(f"  source file:   {df['path']}")

    # Diff detail last, capped to keep the panel readable for huge diffs.
    if event.diff_ref:
        out.append("")
        out.append("Changes:")
        diff = _read_blob_json(hist, event.diff_ref)
        if diff is None:
            out.append("  (diff blob unavailable)")
        else:
            out.extend("  " + line for line in render_diff_text(diff).splitlines())

    return "\n".join(out) + "\n"


# Cap the number of per-row changes displayed inline. With AI ops
# the diff can have thousands of entries; printing them all swamps
# the panel. The full content is still in the diff blob — accessible
# via `swcstudio history show <sha> --format=json` or by decompressing
# objects/<diff_ref>.zst directly.
_DIFF_INLINE_LIMIT = 25


def render_env_text(env_blob: dict[str, Any]) -> str:
    """Pretty-print one decoded env-fingerprint blob.

    Shows the system block in full and a compact summary of the
    packages block (count + a handful of high-signal entries:
    numpy, scipy, sklearn, xgboost, swcstudio). The full per-package
    list is in the blob — reachable via --format=json.
    """
    out: list[str] = []
    system = env_blob.get("system") or {}
    if system:
        out.append("system:")
        for k in ("os", "os_version", "python_version", "machine",
                  "cpu_count", "cuda_version", "gpu"):
            if k in system:
                out.append(f"  {k}: {system[k]}")
    pkgs = env_blob.get("packages") or {}
    if pkgs:
        out.append(f"packages: ({len(pkgs)} total)")
        # Highlight the ones that matter most for reproducibility.
        for name in ("numpy", "scipy", "scikit-learn", "xgboost",
                     "pandas", "morphio", "neurom", "swcstudio"):
            if name in pkgs:
                out.append(f"  {name}: {pkgs[name]}")
        out.append(f"  …and {len(pkgs) - 8} other packages (use --format=json for full list)")
    return "\n".join(out) + "\n"


def render_diff_text(diff_blob: dict[str, Any]) -> str:
    """Pretty-print one decoded diff blob.

    Caps the per-row listing to ``_DIFF_INLINE_LIMIT`` entries so the
    detail panel stays readable for AI ops that touch thousands of
    nodes. The trimmed entries are still in the blob and the SQLite
    ``node_changes`` table for `swcstudio history` queries.
    """
    out: list[str] = []
    nodes = list(diff_blob.get("node_changes", []))
    topo = list(diff_blob.get("topology_changes", []))

    if not nodes and not topo:
        return "(no changes)\n"

    if topo:
        out.append(f"Topology: ({len(topo)} change(s))")
        shown_topo = topo[:_DIFF_INLINE_LIMIT]
        for c in shown_topo:
            kind = c.get("kind")
            nid = c.get("id")
            if kind == "add":
                out.append(f"  + node {nid}: {c.get('row', '?')}")
            elif kind == "remove":
                out.append(f"  - node {nid}")
            elif kind == "reparent":
                out.append(f"  ~ node {nid}: parent {c.get('before')} -> {c.get('after')}")
        if len(topo) > _DIFF_INLINE_LIMIT:
            out.append(f"  …and {len(topo) - _DIFF_INLINE_LIMIT} more topology change(s)")

    if nodes:
        if topo:
            out.append("")
        out.append(f"Fields: ({len(nodes)} change(s))")
        shown = nodes[:_DIFF_INLINE_LIMIT]
        for c in shown:
            out.append(
                f"  node {c.get('id')}.{c.get('field')}: "
                f"{c.get('before')} -> {c.get('after')}"
            )
        if len(nodes) > _DIFF_INLINE_LIMIT:
            out.append(
                f"  …and {len(nodes) - _DIFF_INLINE_LIMIT} more field change(s) "
                "(use --format=json or query the index for the full list)"
            )

    return "\n".join(out) + "\n"


def render_ai_run_text(ai_run_blob: dict[str, Any]) -> str:
    """Pretty-print one decoded AI-run blob."""
    out: list[str] = []
    out.append(f"run_id:     {ai_run_blob.get('run_id', '?')}")
    out.append(f"status:     {ai_run_blob.get('status', '?')}")
    out.append(f"started:    {ai_run_blob.get('started_at', '?')}")
    if ai_run_blob.get("finished_at"):
        out.append(f"finished:   {ai_run_blob['finished_at']}")
    sv = ai_run_blob.get("source_version", {}) or {}
    if sv:
        out.append(f"tool:       {sv.get('name', '?')} {sv.get('version', '?')} "
                   f"{('('+sv['git_sha']+')') if sv.get('git_sha') else ''}".rstrip())
    if ai_run_blob.get("env_hash"):
        out.append(f"env_hash:   {ai_run_blob['env_hash']}")
    params = ai_run_blob.get("params") or {}
    if params:
        out.append("params:")
        for k, v in sorted(params.items()):
            out.append(f"  {k}: {v}")
    metrics = ai_run_blob.get("metrics") or {}
    if metrics:
        out.append("metrics:")
        for k, v in sorted(metrics.items()):
            out.append(f"  {k}: {v}")
    arts = ai_run_blob.get("artifacts") or []
    if arts:
        out.append("artifacts:")
        for a in arts:
            out.append(f"  {a.get('name', '?')}: {a.get('sha256', '?')}")
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------
# internals
# ----------------------------------------------------------------------


def _render_header(event: Event) -> list[str]:
    out = [
        f"commit  {event.id}",
        f"parent  {event.parent or '(none — first commit)'}",
        f"branch  {event.branch}",
        f"ts      {event.ts}",
        f"actor   {(event.actor or {}).get('os_user', '?')}",
    ]
    tool = event.tool or {}
    tool_line = f"tool    {tool.get('name', '?')} {tool.get('version', '?')}"
    if tool.get("git_sha"):
        tool_line += f" ({tool['git_sha']})"
    out.append(tool_line)
    if event.input_sha:
        out.append(f"input   sha256:{event.input_sha}")
    out.append(f"output  sha256:{event.output_sha}")
    if event.diff_ref:
        out.append(f"diff    {event.diff_ref}")
    out.append(f"message {event.message or '(empty)'}")
    return out


def _render_op(i: int, op: dict[str, Any]) -> list[str]:
    kind = op.get("kind", "?")
    out = [f"  [{i}] {kind}"]
    params = op.get("params") or {}
    if params:
        out.append("      params:")
        for k, v in sorted(params.items()):
            out.append(f"        {k}: {_format_param_value(v)}")
    summary = op.get("summary") or {}
    if summary:
        # one-line
        kvs = ", ".join(f"{k}={v}" for k, v in sorted(summary.items()))
        out.append(f"      summary: {kvs}")
    if op.get("ai_run_ref"):
        out.append(f"      ai_run_ref: {op['ai_run_ref']}")
    return out


# Param values larger than this threshold are summarized in the inline
# display (single line: shape + sha) instead of dumped in full. The
# full content is still on disk in the commit's JSONL line — users can
# read it raw via `swcstudio history show <sha> --format=json` or by
# opening events.jsonl directly. This keeps the human-readable detail
# view scannable when configs are sprawling (radii_cleaning's
# DEFAULT_CONFIG, for example, is ~1.5 KB of nested dicts).
_PARAM_INLINE_LIMIT = 240  # chars; tuned so most simple configs fit


def _format_param_value(v: Any) -> str:
    """Render a single param value, summarizing huge ones."""
    rendered = str(v) if not isinstance(v, (dict, list)) else _short_repr_of_collection(v)
    if len(rendered) <= _PARAM_INLINE_LIMIT:
        return rendered
    # Too big to inline. Compute a compact descriptor + content sha.
    import json as _json
    import hashlib as _hashlib
    try:
        blob = _json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except Exception:
        return rendered[:_PARAM_INLINE_LIMIT - 4] + " …"
    sha = _hashlib.sha256(blob).hexdigest()[:12]
    if isinstance(v, dict):
        return f"<dict, {len(v)} keys, {len(blob)} bytes, sha256:{sha}> (use --format=json for full content)"
    if isinstance(v, list):
        return f"<list, {len(v)} items, {len(blob)} bytes, sha256:{sha}> (use --format=json for full content)"
    return f"<{type(v).__name__}, {len(blob)} bytes, sha256:{sha}> (use --format=json for full content)"


def _short_repr_of_collection(v: Any) -> str:
    """str(v) but more compact for nested structures."""
    import json as _json
    try:
        return _json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(", ", ": "))
    except Exception:
        return str(v)


def _ops_one_line(conn: sqlite3.Connection, commit_sha: str) -> str:
    """Compact ops display for the log: ``count`` (e.g. ``3``)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM ops WHERE commit_sha = ?", (commit_sha,)
    ).fetchone()
    return str(int(row[0])) if row else "?"


def _find_event_by_sha(hist: Path, commit_sha: str) -> Event | None:
    """Walk events.jsonl looking for the matching commit.

    We accept either the full ``sha256:<64hex>`` form or any unique
    short prefix (>= 6 chars) of the hex part — same convention git
    uses for ``git show <short>``.
    """
    target = commit_sha
    if not target.startswith("sha256:"):
        target_hex = target.lower()
    else:
        target_hex = target.removeprefix("sha256:").lower()
    if len(target_hex) < 6:
        return None

    matches: list[Event] = []
    for ev in iter_events(hist / "events.jsonl"):
        ev_hex = (ev.id or "").removeprefix("sha256:").lower()
        if ev_hex.startswith(target_hex) or ev.id == commit_sha:
            matches.append(ev)
    if len(matches) == 1:
        return matches[0]
    # Ambiguous prefix or none.
    return None


def _read_blob_json(hist: Path, ref: str) -> dict[str, Any] | None:
    """Decompress a blob and parse as JSON. Returns None on miss/error."""
    if not ref:
        return None
    sha = ref.removeprefix("sha256:")
    try:
        store = ObjectStore(hist / "objects")
        data = store.get(sha)
        return json.loads(data)
    except (BlobNotFoundError, json.JSONDecodeError, OSError):
        return None


def _short(sha: str) -> str:
    return (sha or "").removeprefix("sha256:")[:12]
