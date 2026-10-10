import io
import math
import os
import re
from collections.abc import Iterable, Mapping

import numpy as np
import pandas as pd

# Keep a compact SWC I/O helper set similar to the project's previous swc_io.py

SWC_COLS = ["id", "type", "x", "y", "z", "radius", "parent"]
SWC_HEADER_ATTR = "swc_header_lines"

# A UTF-8 byte order mark, as text. Some editors write one before the first
# line; it is not whitespace to ``str.strip``/``str.split``, so a parser that
# does not drop it reads the first line as garbage.
BOM = "﻿"

# Line terminators as text-mode ``open()`` (universal newlines) splits them,
# which is how the labeler and the auto-label row parse read a file.
_LINE_BREAK = re.compile(r"(\r\n|\r|\n)")
# The first field (id), the whitespace after it, the type field, the whitespace
# after it, then x, y, z, the radius field.
_FIELDS_TO_RADIUS = re.compile(r"^(\s*\S+\s+)(\S+)(\s+\S+\s+\S+\s+\S+\s+)(\S+)")


def strip_line_bom(line: str) -> str:
    """``line`` without a byte order mark at its start.

    A BOM belongs at the start of a file, but the ``# @PROV`` header lines are
    spliced in above whatever the file starts with, so after an in-place edit
    it can sit at the start of a later line; it is dropped there too.
    """
    return line[1:] if line.startswith(BOM) else line


def split_swc_lines(text: str) -> list[tuple[str, str]]:
    """``text`` as ``(content, line_break)`` pairs; joined back they give ``text``.

    Lines break at ``\\r\\n``, ``\\r`` or ``\\n``, as text-mode ``open()`` reads
    them. The last pair's line break is ``""`` when the text does not end with one.
    """
    parts = _LINE_BREAK.split(text)
    out = [(parts[i], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]
    if parts[-1]:
        out.append((parts[-1], ""))
    return out


def parse_swc_data_line(content: str) -> tuple[int, int, float, float, float, float, int] | None:
    """The seven fields of one SWC node line, or ``None`` for any other line.

    A node line has at least seven whitespace-separated fields that all parse
    as numbers (id, type and parent as whole numbers written as integers or
    floats); comment lines, blank lines, short lines and lines with a field
    that does not parse are not node lines. Fields after the seventh are
    ignored. This is the rule the labeler's parser and auto-labeling's row
    parse use, so the node lines found here are the nodes that get labeled.
    """
    s = strip_line_bom(content).strip()
    if not s or s.startswith("#"):
        return None
    parts = s.split()
    if len(parts) < 7:
        return None
    try:
        return (int(float(parts[0])), int(float(parts[1])), float(parts[2]), float(parts[3]),
                float(parts[4]), float(parts[5]), int(float(parts[6])))
    except (ValueError, OverflowError):
        return None


def swc_data_rows_from_text(text: str) -> tuple[list[str], list[dict]]:
    """``(comment_lines, rows)`` of an SWC text, node lines in file order.

    Each row is a dict with ``id``, ``type``, ``x``, ``y``, ``z``, ``radius``
    and ``parent``; see :func:`parse_swc_data_line` for which lines count.
    """
    headers: list[str] = []
    rows: list[dict] = []
    for content, _brk in split_swc_lines(text):
        stripped = strip_line_bom(content).strip()
        if stripped.startswith("#"):
            headers.append(strip_line_bom(content))
            continue
        fields = parse_swc_data_line(content)
        if fields is None:
            continue
        rid, rtype, x, y, z, radius, parent = fields
        rows.append({"id": rid, "type": rtype, "x": x, "y": y, "z": z, "radius": radius, "parent": parent})
    return headers, rows


def _same_number(token: str, value: float) -> bool:
    try:
        old = float(token)
    except (ValueError, OverflowError):
        return False
    new = float(value)
    return old == new or (math.isnan(old) and math.isnan(new))


def _radius_token(value: float | str) -> str:
    if isinstance(value, str):
        return value
    return repr(float(value))


def rewrite_swc_fields(
    text: str,
    types: Mapping[int, int],
    radii: Mapping[int, float | str] | None = None,
) -> str:
    """``text`` with only the type (and radius) field of the given nodes rewritten.

    ``types`` maps a node id to its new type; ``radii``, when given, maps a
    node id to its new radius (a number, or the exact text to write). Every
    other byte is kept: line endings, a byte order mark, comment lines where
    they stand, text after a ``#`` on a node line, fields after the seventh,
    and the spacing between fields. A field whose value does not change keeps
    its text (``3.0`` stays ``3.0`` when the new type is 3). Lines that are not
    node lines (:func:`parse_swc_data_line`) are copied unchanged.

    Raises ``ValueError`` when a node id appears on more than one node line, or
    when an id in ``types`` or ``radii`` has no node line, so a label is never
    written to the wrong line.
    """
    radii = dict(radii or {})
    wanted = set(types) | set(radii)
    seen: set[int] = set()
    out: list[str] = []
    for content, brk in split_swc_lines(text):
        fields = parse_swc_data_line(content)
        if fields is None:
            out.append(content + brk)
            continue
        node_id = fields[0]
        if node_id in seen:
            raise ValueError(f"node id {node_id} appears on more than one line")
        seen.add(node_id)
        if node_id not in wanted:
            out.append(content + brk)
            continue
        bom = BOM if content.startswith(BOM) else ""
        body = content[len(bom):]
        match = _FIELDS_TO_RADIUS.match(body)
        if match is None:  # pragma: no cover - a node line always has these fields
            raise ValueError(f"cannot locate the fields of node {node_id}")
        lead, type_tok, mid, radius_tok = match.groups()
        if node_id in types and int(types[node_id]) != fields[1]:
            type_tok = str(int(types[node_id]))
        if node_id in radii and not _same_number(radius_tok, float(radii[node_id])):
            radius_tok = _radius_token(radii[node_id])
        out.append(bom + lead + type_tok + mid + radius_tok + body[match.end():] + brk)
    missing = wanted - seen
    if missing:
        raise ValueError("no node line for node id(s) " + ", ".join(str(i) for i in sorted(missing)[:10]))
    return "".join(out)


def decode_swc_bytes(data: bytes) -> str:
    """SWC bytes as text, every byte kept: invalid UTF-8 is carried through as
    surrogate escapes, so :func:`encode_swc_text` gives the same bytes back."""
    return data.decode("utf-8", errors="surrogateescape")


def encode_swc_text(text: str) -> bytes:
    """The inverse of :func:`decode_swc_bytes`."""
    return text.encode("utf-8", errors="surrogateescape")


def is_swc_data_file(path: str | os.PathLike, extensions: Iterable[str] = (".swc",)) -> bool:
    """True for a regular, visible file with an SWC extension.

    Folder scans use this so they skip hidden files, in particular the
    ``._name.swc`` AppleDouble files macOS writes next to every file it
    copies to a USB drive or network share: they hold Finder metadata,
    not SWC rows, and would otherwise fail as unreadable SWC files.
    """
    name = os.path.basename(os.fspath(path))
    suffix = os.path.splitext(name)[1].lower()
    exts = {str(e).lower() for e in extensions}
    return suffix in exts and not name.startswith(".") and os.path.isfile(path)


def _default_swc_plus_header() -> list[str]:
    return [
        "# SWC plus (SWC+) format specification",
        '# <SWCplus version="0.12">',
        "#   <MetaData>",
        '#     <FileHistory originalFormat="SWC">',
        '#       <Modification software="SWC-Studio" command="save" summary="Saved by SWC-Studio"/>',
        "#     </FileHistory>",
        "#   </MetaData>",
        '#   <CustomTypes version="0.12">',
        "#     <!-- No custom types defined for this file. Custom types (TypeIDs >= 16) would be listed here. -->",
        "#   </CustomTypes>",
        "# </SWCplus>",
    ]


def parse_swc_text_preserve_tokens(text: str) -> pd.DataFrame:
    rows = []
    header_lines: list[str] = []
    for raw in text.splitlines():
        # A byte order mark is not data (see ``strip_line_bom``).
        raw = strip_line_bom(raw)
        s = raw.strip()
        if s.startswith("#"):
            header_lines.append(raw.rstrip("\r\n"))
            continue
        if not s:
            continue
        parts = s.split()
        if len(parts) < 7:
            continue

        id_tok, type_tok, x_tok, y_tok, z_tok, radius_tok, parent_tok = parts[:7]

        def _to_int(tok, default):
            try:
                return int(float(tok))
            except Exception:
                return default

        def _to_float(tok):
            try:
                return float(tok)
            except Exception:
                return np.nan

        rows.append({
            "id": _to_int(id_tok, -1),
            "type": _to_int(type_tok, 0),
            "x": _to_float(x_tok),
            "y": _to_float(y_tok),
            "z": _to_float(z_tok),
            "radius": _to_float(radius_tok),
            "parent": _to_int(parent_tok, -1),
            "x_str": x_tok,
            "y_str": y_tok,
            "z_str": z_tok,
            "radius_str": radius_tok,
            "id_str": id_tok,
            "parent_str": parent_tok,
        })

    df = pd.DataFrame(rows, columns=[
        "id","type","x","y","z","radius","parent",
        "x_str","y_str","z_str","radius_str","id_str","parent_str"
    ])
    df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(-1).astype(int)
    df["type"] = pd.to_numeric(df["type"], errors="coerce").fillna(0).astype(int)
    df["parent"] = pd.to_numeric(df["parent"], errors="coerce").fillna(-1).astype(int)
    df.attrs[SWC_HEADER_ATTR] = header_lines
    return df


def write_swc_to_bytes_preserve_tokens(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    header_lines = list(getattr(df, "attrs", {}).get(SWC_HEADER_ATTR) or [])
    if not header_lines:
        header_lines = _default_swc_plus_header()
    for line in header_lines:
        text = str(line).rstrip("\r\n")
        if text:
            buf.write(text + "\n")

    has_tok = all(c in df.columns for c in ["x_str","y_str","z_str","radius_str","id_str","parent_str"])

    def _same_int_value(token, value) -> bool:
        try:
            return int(float(token)) == int(value)
        except Exception:
            return False

    def _same_float_value(token, value) -> bool:
        try:
            return float(token) == float(value)
        except Exception:
            return False

    for _, row in df.iterrows():
        id_out = (
            row["id_str"]
            if has_tok and isinstance(row["id_str"], str) and _same_int_value(row["id_str"], row["id"])
            else str(int(row["id"]))
        )
        parent_out = (
            row["parent_str"]
            if has_tok and isinstance(row["parent_str"], str) and _same_int_value(row["parent_str"], row["parent"])
            else str(int(row["parent"]))
        )
        type_out = str(int(row["type"]))

        x_out = (
            row["x_str"]
            if has_tok and isinstance(row["x_str"], str) and _same_float_value(row["x_str"], row["x"])
            else f"{row['x']}"
        )
        y_out = (
            row["y_str"]
            if has_tok and isinstance(row["y_str"], str) and _same_float_value(row["y_str"], row["y"])
            else f"{row['y']}"
        )
        z_out = (
            row["z_str"]
            if has_tok and isinstance(row["z_str"], str) and _same_float_value(row["z_str"], row["z"])
            else f"{row['z']}"
        )
        radius_out = (
            row["radius_str"]
            if has_tok and isinstance(row["radius_str"], str) and _same_float_value(row["radius_str"], row["radius"])
            else f"{row['radius']}"
        )

        buf.write(f"{id_out} {type_out} {x_out} {y_out} {z_out} {radius_out} {parent_out}\n")
    return buf.getvalue().encode("utf-8")
