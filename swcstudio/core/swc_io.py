import io
import os
from collections.abc import Iterable

import numpy as np
import pandas as pd

# Keep a compact SWC I/O helper set similar to the project's previous swc_io.py

SWC_COLS = ["id", "type", "x", "y", "z", "radius", "parent"]
SWC_HEADER_ATTR = "swc_header_lines"


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
