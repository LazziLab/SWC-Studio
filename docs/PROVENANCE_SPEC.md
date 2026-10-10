# Provenance & Versioning Spec (v1)

**Status:** implemented for GUI-tracked morphology edits; remaining
history features are extended incrementally.

This document specifies the provenance, versioning, and reproducibility
system for SWC-Studio. For GUI morphology editing, it replaces the old
session text-log behavior described in `LOGS_AND_REPORTS.md`.

The goals (in priority order):

1. **Provenance** — for any SWC, answer "what app, whom, did what, to
   this dataset" with no ambiguity.
2. **Revertability** — go back to any past state and optionally branch
   from there to redo work without losing the original path.
3. **AI reproducibility** — record AI parameters and environment well
   enough that a colleague can recompute the same result.
4. **Compactness** — delta-encoded, content-addressed, compressed; the
   sidecar must not balloon as edits accumulate.
5. **Cleanliness** — human-readable event log; SQL-queryable index;
   self-describing files.

The design is "JSON-Lines event log + content-addressed blob store +
SQLite index", inspired by:

- **CAVE / PyChunkedGraph** (typed operations, time-travel queries)
- **DVID** (named-branch DAG)
- **SAM/BAM `@PG` chain** (self-describing file headers)
- **MLflow** (`Run` schema for AI ops)
- **DataLad** (`run` wrapper pattern for capture-by-wrapping)
- **W3C PROV-O / RO-Crate** (interop vocabulary, export bundle)

## 1. On-disk layout

History lives **per file** as a visible archive next to the SWC. The
source SWC is the current editable state and receives compact `@PROV`
pointer lines. A **project-level index** can also be built at the parent
folder root.

```
neuron_001.swc
neuron_001_history.swcstudio       # visible encrypted history repo archive
<user-marked checkpoints>.swc       # optional, see section 7

Inside neuron_001_history.swcstudio after SWC-Studio decrypts/materializes it:
    history/
        repo_manifest.json          # repo_id, SWC name, archive name
        version                     # integer; starts at 1
        events.jsonl                # append-only event log (source of truth)
        objects/<sha[:2]>/<sha>.zst # zstd-compressed content-addressed blobs
        refs/
            HEAD                    # name of currently-active branch
            branches/<name>         # commit sha at the tip of each branch
            tags/<name>             # optional immutable named pointers
        index.sqlite                # rebuildable cache of events.jsonl

<project_root>/
    .swcstudio/
        index.sqlite                # aggregated index across all files in this tree
        version
```

- Per-file `<stem>_history.swcstudio` is the **source of truth**.
  SWC-Studio materializes it into a transient work tree under the OS
  temp directory only while reading or writing, then re-encrypts it and
  removes the work tree.
- SWC-Studio writes/reads the archive with AES zip encryption through
  `pyzipper`. By default it uses an app-managed password so the sidecar
  is not a normal user-editable zip folder. That password ships with
  SWC-Studio, so the default encryption deters casual hand edits but
  does **not** keep the history confidential: anyone with SWC-Studio or
  its source can open the archive. Integrity does not rest on the
  password. Blobs are addressed by their SHA-256, commit ids hash their
  parent and content (§4), and `swcstudio history verify` re-checks
  both. Set `SWCSTUDIO_HISTORY_PASSWORD` to your own secret when the
  history must stay private; every reader then needs the same value.
- Legacy `<stem>_history.swcstudio.zip` archives can still be opened and
  are rewritten to the current `<stem>_history.swcstudio` name on the
  next commit.
- Move the SWC and its `<stem>_history.swcstudio` archive together
  and provenance survives.
- Project-level `.swcstudio/` is a **derived index** rebuilt from the
  per-file logs. Useful for project-wide queries ("every AI run by
  Alice on this dataset") but never authoritative.

## 2. Identity model

### Dataset root identity

`root_sha = SHA-256(canonicalize(original_swc_bytes))`

Canonicalization — the single most important spec, because every hash
depends on it:

| Aspect | Rule |
|---|---|
| Line endings | normalize to `\n` |
| Trailing whitespace | strip per line |
| Float format | pass-through (do not round or reformat) |
| Comment lines (`#`) | included in hash, **except** lines beginning with `# @PROV` |
| Node ordering | as-written, do not sort |
| Hash algorithm | SHA-256 |

Reference implementation:

```python
def canonical_swc(b: bytes) -> bytes:
    text = b.decode("utf-8", errors="strict")
    out_lines = []
    for line in text.splitlines():
        line = line.rstrip()
        if line.startswith("# @PROV"):
            continue
        out_lines.append(line)
    return ("\n".join(out_lines) + "\n").encode("utf-8")

def root_sha(b: bytes) -> str:
    return hashlib.sha256(canonical_swc(b)).hexdigest()
```

### Actor identity

Every event carries:

- `os_user` — `getpass.getuser()`. Required.
- (`lab_user` is not collected in v1; reserved for a future minor.)

### Tool identity

Every event carries:

- `tool` — fixed string `"swcstudio"`
- `tool_version` — value of `swcstudio.__version__`
- `tool_git_sha` — git short SHA if available (best-effort; absent in pip installs)

## 3. Operation taxonomy (v1)

Closed list. Each op has a typed `params` schema (defined in
`swcstudio.core.provenance.ops`).

| Op kind | Mutating? | Recorded? | Params (summary) |
|---|---|---|---|
| `set_type` | yes | yes | node_ids, type |
| `set_radius` | yes | yes | node_ids, radius |
| `manual_radii` | yes | yes | edits[] |
| `auto_fix` | yes | yes | rule_set, threshold |
| `auto_label` (AI) | yes | yes | cell_type, flag_enabled, labeler_version, model_dir, results (stage1_cell_type, class_mismatch, predicted_f1, ...) |
| `dendrogram_edit` | yes | yes | edit_kind, anchors |
| `geometry_edit` | yes | yes | op_kind, params |
| `radii_clean` | yes | yes | params |
| `simplification` | yes | yes | params |
| `index_clean` | yes | yes | (none) |
| `split` | yes | yes | split_spec |
| `plugin_op` | yes | yes | plugin_id, plugin_version, params |
| `validation_run` | no | optional audit only | rule_set |
| `visualization` | no | no | — |

Read-only operations (`validation_run`, `visualization`) are not part
of the chain. `validation_run` may be appended to a separate
`audit.jsonl` if the user enables it.

## 4. Event format

`events.jsonl` is append-only. One JSON object per line. UTF-8, `\n`
line endings, sorted keys for byte-stable output.

### Common envelope

```text
{
  "schema_version": 1,
  "kind": "commit",
  "id": "sha256:g7h8i9j0k1l2...",
  "parent": "sha256:d4e5f6a7b8c9...",
  "branch": "main",
  "ts": "2024-01-01T11:45:22Z",
  "actor": {"os_user": "tuo"},
  "tool": {"name": "swcstudio", "version": "0.2.0", "git_sha": "a1b2c3d"},
  "session_id": "01HX2K3MZRSTQK9WD6YCNQEXAM",
  "message": "Auto-label + manual cleanup of axon",
  "ops": [ ... ],
  "input_sha": "sha256:...",
  "output_sha": "sha256:...",
  "diff_ref": "sha256:..."
}
```

`id` is computed as
`SHA-256(canonical_json({parent, branch, ops, input_sha, output_sha}))`
so the chain is cryptographically linked.

### Sub-op shape

```json
{
  "kind": "set_radius",
  "params": {"node_ids": [5, 7], "radius": 0.4},
  "summary": {"nodes_modified": 2}
}
```

For AI ops the sub-op also carries an `ai_run_ref` blob hash:

```json
{
  "kind": "auto_label",
  "params": {"cell_type": "auto", "flag_enabled": true, "labeler_version": "1.0.0",
             "cell_type_result": "pyramidal", "stage1_cell_type": "pyramidal",
             "class_mismatch": false, "predicted_f1": 0.937},
  "summary": {"nodes_modified": 1283},
  "ai_run_ref": "sha256:..."
}
```

For `auto_label`, `params` records the settings and the labeler
version (`labeler_version`). The settings are the cell class as chosen
(`cell_type`: `auto`, or the labeler's name `pyramidal` /
`interneuron`), `flag_enabled`, `flag_budget` for batch runs, and the
model folder named for the run, if any (`model_dir`). `params` also
records the result: the class used (`cell_type_result`), whether the
user or Stage 1 chose it (`cell_type_source`), Stage 1's prediction
(`stage1_cell_type`, `stage1_confidence`), `class_mismatch`,
`predicted_f1`, and node and type counts. The exact set of keys, and
whether the settings sit inside an `options` dict, depends on the entry
point (CLI, GUI, or batch).

### Diff blob (referenced by `diff_ref`)

Stored in `objects/<sha>.zst`. JSON shape:

```json
{
  "schema_version": 1,
  "from": "sha256:...",
  "to": "sha256:...",
  "node_changes": [
    {"id": 5, "field": "radius", "before": 0.3, "after": 0.5},
    {"id": 7, "field": "type",   "before": 0,   "after": 3}
  ],
  "topology_changes": [
    {"kind": "add",      "id": 12, "row": "12 3 1.0 2.0 0.0 0.4 5"},
    {"kind": "remove",   "id": 99},
    {"kind": "reparent", "id": 14, "before": 13, "after": 15}
  ]
}
```

### AI-run blob (referenced by `ai_run_ref`)

MLflow-compatible field names. Stored in `objects/<sha>.zst`.

```json
{
  "schema_version": 1,
  "run_id": "ulid",
  "started_at": "2024-01-01T11:45:00Z",
  "finished_at": "2024-01-01T11:45:22Z",
  "status": "FINISHED",
  "params":  {"cell_type": "auto", "flag_enabled": true, "labeler_version": "1.0.0", "...": "..."},
  "metrics": {},
  "artifacts": [],
  "source_version": {"tool": "swcstudio", "version": "0.2.0", "git_sha": "a1b2c3d"},
  "env_hash": "..."
}
```

`auto_label` runs keep their settings and results in `params`, the same
dict as the sub-op's `params`. They leave `metrics` and `artifacts`
empty. The labeler version is recorded as `params.labeler_version`.

### Env fingerprint blob (referenced by `env_hash`)

Stored once per unique environment, deduplicated.

```json
{
  "schema_version": 1,
  "system": {
    "os": "Darwin", "os_version": "23.5.0",
    "python_version": "3.12.3", "machine": "arm64",
    "cpu_count": 12, "cuda_version": null, "gpu": null
  },
  "packages": {
    "numpy": "1.26.4", "scipy": "1.13.0", "scikit-learn": "1.5.2",
    "xgboost": "3.2.0",
    "swcstudio": "0.2.0"
    /* full importlib.metadata snapshot */
  }
}
```

## 5. SWC header — bounded `@PROV` business card

Every materialized SWC carries exactly **two** `@PROV` comment lines at
the top:

```
# @PROV root=a1b2c3d4 file=neuron_001.swc created=2024-01-01T09:00:00Z repo=neuron_001_history.swcstudio repo_id=...
# @PROV tip=g7h8i9j0 parent=d4e5f6a7 ops=20 tool=swcstudio@0.2.0 actor=tuo updated=2024-01-01T11:45:22Z sidecar=neuron_001_history.swcstudio repo_id=...
```

- **Line 1 (root)** — written once, never modified.
- **Line 2 (tip)** — overwritten on every save.

Both lines are excluded from `canonical_swc()` so updating the tip
line does not change the file's hash.

A SWC without these lines is treated as an external file with no
SWC-Studio history; first edit creates a new root.

## 6. Branching

Full DAG with named branches.

- `refs/HEAD` is a text file containing the active branch name (default
  `"main"`).
- `refs/branches/<name>` is a text file containing one commit sha.
- `refs/tags/<name>` is a text file containing one commit sha
  (immutable; CLI rejects overwrite).

Verbs:

| Verb | Effect |
|---|---|
| `checkout <sha>` | materializes a read-only SWC at the requested state; HEAD is unchanged |
| `branch <name> [from=<sha>]` | creates a new branch at the given sha (default: HEAD) |
| `switch <name>` | sets HEAD to the named branch |
| `tag <name> [<sha>]` | creates an immutable named pointer |

Mutating ops always commit on the active branch. To mutate from a past
state, branch first.

The GUI's **Revert to selected state** action does not move or rewrite
existing history. It creates a new commit on the active branch whose
content matches the selected operation/version, and records the source
operation and technical version in that new commit's parameters.

## 7. Output-file model

For GUI-tracked morphology edits:

- `<stem>.swc` - the source file is the current materialized state and
  is overwritten on each committed edit.
- `<stem>_history.swcstudio` - visible encrypted history archive stored
  next to the source SWC.
- `<stem>_<label>.swc` - optional, materialized only when the user marks
  an operation/state as a checkpoint via `swcstudio history checkpoint <op-id|sha>
  --label <label>` or the GUI's "Mark as checkpoint" action.

No automatic `_current.swc` or timestamped per-op GUI copies are
written. The full set of past states is always retrievable via history
checkout/checkpoint actions.

## 8. The `tracked_op()` API

Single mandatory entry point for any code that mutates an SWC. CLI
handlers, GUI actions, and plugins all go through this — no other path
is sanctioned.

```python
from swcstudio.core.provenance import tracked_op, OpKind

with tracked_op(
    dataset_path="/path/to/neuron_001.swc",
    kind=OpKind.AUTO_LABEL,
    params={"cell_type": "auto", "flag_enabled": True, "labeler_version": "1.0.0"},
    message="Auto-label apical/basal",
    is_ai=True,
) as op:
    new_swc_bytes = run_auto_label(op.input_bytes, ...)
    op.set_output(new_swc_bytes)
```

Lifecycle:

1. Materialize `<stem>_history.swcstudio` into the transient temp work
   tree, or initialize a new work tree if no archive exists.
2. Acquire `.history/lock`. Fail loudly if already held.
3. Read the active branch tip and capture the input state.
4. If `is_ai=True`, capture an environment fingerprint (or reference an existing one).
5. Run the body.
6. Validate output was set; compute `output_sha`.
7. Compute structured diff vs input; write diff blob.
8. If `is_ai=True`, finalize and write AI-run blob.
9. Write commit event to `events.jsonl` (atomic append).
10. Update `refs/branches/<active>` to new commit sha (atomic rename).
11. Update the rebuildable SQLite index.
12. Rewrite the source `<stem>.swc` with updated `@PROV` tip line while
    preserving non-`@PROV` comment headers, including SWC+ blocks.
13. Release lock.
14. Re-encrypt the temp work tree into `<stem>_history.swcstudio`
    and remove the work tree.

Errors at any step roll back: no partial events, no orphan blobs are
referenced, lock is released in `finally`.

Current GUI editing actions create one tracked commit per applied
operation. Mutating GUI batch tools also create one independent commit
per source file, so each file keeps its own operation-number sequence
and a failure in one file does not merge or corrupt another file's
history.

## 9. State reconstruction

The current v1 implementation does not yet write periodic full-state
snapshot blobs. The source SWC is the materialized state at the active
branch tip, while each history event stores a structured diff.

`checkout(sha)` algorithm:

1. Start from the current materialized source SWC at the active branch
   tip.
2. Walk backward toward the requested ancestor, applying inverse diffs
   for intervening commits.
3. Return the reconstructed canonical SWC bytes.

Periodic snapshots are a future optimization. If added, they can bound
replay work without changing operation IDs or the append-only event
history.

## 10. Concurrency

`.history/lock` is an OS-level advisory lock (`fcntl.flock` on POSIX,
`msvcrt.locking` on Windows) inside the transient extracted work tree.
It is acquired by `tracked_op()`, `tracked_session()`, and any
maintenance verb, and is not stored in the durable archive. Held only
for the duration of one op or session.

If acquisition fails, the verb exits with a clear error naming the
holding PID and start time. No silent retry.

The SQLite index is opened in WAL mode and updated within the same
critical section as the event-log append.

## 11. SQLite index schema (rebuildable)

`.history/index.sqlite`:

```sql
CREATE TABLE meta (
    key TEXT PRIMARY KEY, value TEXT
);  -- 'schema_version', 'last_event_offset', 'rebuilt_at'

CREATE TABLE commits (
    sha          TEXT PRIMARY KEY,
    parent       TEXT,
    branch       TEXT,
    ts           TEXT,
    os_user      TEXT,
    tool_version TEXT,
    tool_git_sha TEXT,
    session_id   TEXT,
    message      TEXT,
    input_sha    TEXT,
    output_sha   TEXT,
    diff_ref     TEXT,
    is_snapshot  INTEGER
);
CREATE INDEX commits_parent ON commits(parent);
CREATE INDEX commits_ts     ON commits(ts);
CREATE INDEX commits_user   ON commits(os_user);

CREATE TABLE ops (
    op_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    commit_sha    TEXT REFERENCES commits(sha),
    op_index      INTEGER,
    kind          TEXT,
    params_json   TEXT,
    summary_json  TEXT,
    ai_run_ref    TEXT
);
CREATE INDEX ops_commit ON ops(commit_sha);
CREATE INDEX ops_kind   ON ops(kind);

CREATE TABLE node_changes (
    op_id    INTEGER REFERENCES ops(op_id),
    node_id  INTEGER,
    field    TEXT,
    before   TEXT,
    after    TEXT
);
CREATE INDEX node_changes_node ON node_changes(node_id);
CREATE INDEX node_changes_op   ON node_changes(op_id);

CREATE TABLE ai_runs (
    ai_run_ref     TEXT PRIMARY KEY,
    commit_sha     TEXT REFERENCES commits(sha),
    model_sha      TEXT,
    model_version  TEXT,
    started_at     TEXT,
    finished_at    TEXT,
    env_hash       TEXT,
    metrics_json   TEXT,
    params_json    TEXT
);
CREATE INDEX ai_runs_model ON ai_runs(model_sha);

CREATE TABLE refs (
    name TEXT PRIMARY KEY,   -- 'HEAD', 'branches/main', 'tags/v1'
    sha  TEXT
);
```

`ai_runs.model_sha` and `ai_runs.model_version` are filled only when an
AI op's params carry `model_sha` or `model_version`. `auto_label` runs
do not; they record the labeler version as `labeler_version` inside
`params_json`.

The project-level `.swcstudio/index.sqlite` has the same schema plus
a `dataset` column on `commits`, `ops`, `ai_runs` keyed by the
per-file `root_sha`.

The index is **always rebuildable** from the per-file `events.jsonl`
files. `swcstudio history reindex` does this.

## 12. Reproduction (`reproduce.yaml`)

For any AI op, `swcstudio history reproduce <file> <op-id|sha>` emits a
small `reproduce.yaml`:

```yaml
schema_version: 1
op:        auto_label
input:     {sha256: ..., path: 'neuron_001.swc'}
output:    {sha256: ...}
params:    {"cell_type": "pyramidal", "class_mismatch": false, "flag_enabled": true, "labeler_version": "1.0.0", ...}
env_hash:  ...
command:   'swcstudio auto-label --cell-class 3-class neuron_001.swc'
```

`params` is the op's recorded params dict. `command` repeats the
options recorded at the top level of `params`, as the CLI records them:
`--cell-class` when a cell class was chosen, `--no-flag` when the
quality flag was off, and `--model-dir` when a model folder was named.

To reproduce the result, a colleague runs `command` on a file whose
hash matches `input.sha256`, with the labeler version in
`params.labeler_version` and an environment matching `env_hash`, and
compares the result with `output.sha256`.

## 13. CLI surface (v1)

The current CLI ships these 13 history commands:

```
swcstudio history log <file> [--branch=<n>] [--actor=<u>] [--limit=N] [--technical]
swcstudio history show <file> <op-id|sha> [--format=text|json] [--technical]
swcstudio history checkout <file> <op-id|sha> [-o <path>]
swcstudio history branch <file> [<name>] [--from=<op-id|sha>]
swcstudio history switch <file> <name>
swcstudio history tag <file> [<name>] [<op-id|sha>]
swcstudio history checkpoint <file> <op-id|sha> --label <label>
swcstudio history reproduce <file> <op-id|sha> [-o reproduce.yaml]
swcstudio history reindex <file>
swcstudio history verify <file>
swcstudio history gc <file> [--dry-run]    # remove unreachable blobs
swcstudio history export-crate <file> -o <dir>   # RO-Crate bundle
swcstudio history init <file>
```

| Command | Purpose |
|---|---|
| `log` | Chronological operation list by default; `--technical` shows commit/version IDs. |
| `show` | Detailed view of one operation by default; `--technical` shows commit/version details. |
| `checkout` | Materialize a past operation/state as a read-only `.swc`. HEAD untouched. |
| `branch` | Create a named branch at a chosen operation/state (default HEAD). |
| `switch` | Set HEAD to an existing branch. |
| `tag` | Bookmark an operation/state with an immutable name. |
| `checkpoint` | Materialize a past operation/state as a labeled `.swc` next to the source SWC. |
| `reproduce` | Emit a `reproduce.yaml` so a colleague can recompute an AI result. |
| `reindex` | Rebuild `index.sqlite` from `events.jsonl`. |
| `verify` | Hash-check every blob; recompute every commit id and check that its parent is an earlier commit; check that branches and tags point into the log, that every blob they reach is present, and that the files match `checksums.json`. Exits non-zero on any problem. |
| `gc` | Remove unreferenced blobs from `objects/`. |
| `export-crate` | Bundle file + history + AI metadata as an RO-Crate directory. |
| `init` | Initialize history explicitly and migrate a legacy output sidecar when present. |

## 14. GUI surface (v1)

The GUI presents Operation History first. Exact commit/SHA-based version
details are kept in the Commit History tab for debugging and
reproducibility.

- **Timeline panel** — chronological list of commits on the active
  branch. Each row shows ts, actor, op kinds, summary counts. Click
  for detail.
- **Branch picker** — dropdown of branches; "create branch from this
  selected operation/state.
- **Detail view** — for a selected commit: ops, sub-op params, diff
  summary; for AI ops, env + reproduce.yaml export.
- **Operation History** — expandable operation rows. Operation IDs are
  per-file, chronological labels (`op-1`, `op-2`, `op-3`, ...), even when
  files are processed together in a batch. Each row summarizes time,
  actor, operation type, parameters, and changed-node count; its child
  rows show node-level old/new values. Restore operations include the
  operation/version they restored from. This is the default user-facing view.

GUI v1 does **not** include: in-app three-way diff viewer, merge UI,
visual DAG renderer. Those are v2.

## 15. Replacement of GUI session text logs

GUI morphology sessions no longer write `_session_log_*.txt` files by
default. Operation summaries and node-level old/new values are read from
the encrypted history archive and displayed in the History Browser.

Mutating GUI batch tools commit each source SWC to its own history.
Validation/report-only exports can still write normal text reports
through `swcstudio.core.reporting`; those are separate from GUI history.

## 16. Migration from existing output dirs

On first encounter with an existing `*_swc_studio_output/` directory:

1. Initialize `.history/` with a single synthetic `import` commit
   whose `output_sha` is the canonical hash of the most recent
   `_closed_*.swc` (if any), or of the original SWC otherwise.
2. The synthetic commit's message: `"Imported from pre-history sidecar
   at <ts>"`.
3. Old `_closed_*.swc` and text reports are **left in place**, not
   deleted, but excluded from future writes.
4. The user is told once, in CLI/GUI, that history is now tracked.

## 17. Format versioning

Two complementary mechanisms — directory-level and per-event:

**Directory-level** — `.history/version` contains a single integer.
v1 is `1`. On open:

- If the file is missing → treat as fresh; create with `1`.
- If the integer equals the current build's max supported version →
  open read-write.
- If the integer is **higher** than the current build's max → open
  **read-only** and surface a clear message: *"This history was
  written by a newer SWC-Studio (format v{N}); please upgrade."*
  Never silently downgrade or overwrite.
- If the integer is **lower** than the current build's max → open
  read-write at the lower version's semantics; future writes use the
  current build's compatible additions only.

**Per-event** — every JSONL event, every blob, and the SQLite `meta`
table carry `schema_version`. Readers must:

- Process events with `schema_version` ≤ the build's max.
- **Skip** events with `schema_version` > the build's max, preserving
  them on rewrite. Unknown future event kinds therefore round-trip
  through old readers without loss.

**Strict v1 freeze policy** — v1 is frozen on first release. Allowed
changes within v1.x:

- Adding **new optional fields** to existing event/blob shapes
  (readers ignore unknown fields).
- Adding **new event kinds** (older readers skip them).

Forbidden within v1.x:

- Renaming or removing existing fields.
- Changing the meaning, type, or canonicalization rules of any
  existing field.
- Changing the canonical SWC rules (§2) or the hash algorithm.

Anything forbidden requires a v2 bump: a parallel `events.v2.jsonl`
file is created alongside `events.jsonl`, plus a one-shot migration
tool (`swcstudio history migrate v1 v2`) that produces the v2 store
from the v1 store. The v1 store is kept untouched as the source of
truth until the user opts to discard it.

## 18. Garbage collection

`swcstudio history gc`:

1. Walks all branch and tag tips, marks reachable commits.
2. From each commit, marks reachable blobs (diff_ref, ai_run_ref,
   env_hash, snapshot blobs, AI artifacts).
3. Removes unreferenced blobs from `objects/`.
4. Reports bytes reclaimed.

Never automatic in v1.

## 19. Cross-file lineage

Whenever a new SWC is created from an existing one through a
lineage-aware SWC-Studio path, such as batch `split`, the new file's
first commit records its source as a parent:

```json
"derived_from": {
  "root_sha":   "sha256:<source root>",
  "commit_sha": "sha256:<source state at time of derivation>",
  "path":       "neuron_001.swc"
}
```

The new file gets its own `root_sha` and its own encrypted history
archive. The `derived_from` field links the two histories. The project-level index
joins on `derived_from.root_sha` to walk inter-file lineage:
"everything that came from `neuron_001.swc`".

If a user copies an SWC outside of SWC-Studio (`cp`, drag-and-drop,
email attachment), lineage is lost — the new file appears as a fresh
root on first edit. This is unavoidable: the file system gives us no
hook to capture the copy. If the user wants lineage preserved, they
must use `swcstudio history checkout -o new.swc` (or its GUI
equivalent) instead.

## 20. Privacy and export

`swcstudio history export-crate` produces an
[RO-Crate](https://www.researchobject.org/ro-crate/) directory:

```
<crate>/
    ro-crate-metadata.json   # JSON-LD provenance graph (PROV-O vocab)
    data/<files>.swc
    history/<events.jsonl, blobs>
    reproduce/<reproduce.yaml per AI run>
```

By default the export replaces every OS username with a pseudonym,
`agent-1`, `agent-2`, ... numbered in order of first appearance in
the log, so re-exporting the same history gives the same pseudonyms.
The replacement covers the crate metadata, the copied `events.jsonl`
and `index.sqlite`, and the `actor=` field of the copied SWC's `@PROV`
header; `history/checksums.json` is recomputed to match. Commit ids do
not hash the actor (§4), so the exported log still verifies.
`--with-pii` keeps the real usernames.

Hostnames are never recorded. The one filesystem path the history can
hold is a model folder chosen with `--model-dir`. It is stored with the
home directory written as `~` (model lookup expands it again), so a
folder under the home directory does not reveal the username. The path
is part of the operation's parameters, which the commit id hashes, so
the export cannot rewrite it; a folder outside the home directory is
exported as given.

---

## Open items deferred to implementation

- **Compression level** — start at zstd level 3, revisit after measuring.
- **Snapshots** — not written in v1; `checkout` replays inverse diffs (§9).
- **GC policy** — manual only in v1; auto-gc deferred.
- **RNG seed handling** — capture-only in v1; enforced-set deferred.
- **`lab_user`** — reserved field, not collected in v1.
