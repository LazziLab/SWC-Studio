"""Unit tests for SWC reading and writing (swcstudio.core.swc_io)."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from swcstudio.core.swc_io import (
    SWC_HEADER_ATTR,
    decode_swc_bytes,
    encode_swc_text,
    parse_swc_text_preserve_tokens,
    rewrite_swc_fields,
    split_swc_lines,
    swc_data_rows_from_text,
    write_swc_to_bytes_preserve_tokens,
)


HEADER = [
    "# ORIGINAL_SOURCE test tracer",
    "# SCALE 1.0 1.0 1.0",
    "# id type x y z radius parent",
]
DATA = [
    "1 1 0.000 0.0 0 5.50 -1",
    "2 3 1.250 -0.5 2.0e+00 1.000 1",
    "3 3 2.5000000000000e+00 -1.00 4 0.75 2",
    "4 2 -3.1415926535 0.0 0.0 2.5390625000000e-01 1",
]


def _text(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


def _data_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


class SwcReadTests(unittest.TestCase):
    def test_parses_the_seven_columns(self) -> None:
        df = parse_swc_text_preserve_tokens(_text(HEADER + DATA))
        self.assertEqual(df["id"].tolist(), [1, 2, 3, 4])
        self.assertEqual(df["type"].tolist(), [1, 3, 3, 2])
        self.assertEqual(df["parent"].tolist(), [-1, 1, 2, 1])
        self.assertEqual(df["x"].tolist(), [0.0, 1.25, 2.5, -3.1415926535])
        self.assertEqual(df["z"].tolist(), [0.0, 2.0, 4.0, 0.0])
        self.assertEqual(df["radius"].tolist(), [5.5, 1.0, 0.75, 0.25390625])

    def test_header_lines_are_kept_on_the_frame(self) -> None:
        df = parse_swc_text_preserve_tokens(_text(HEADER + DATA))
        self.assertEqual(df.attrs[SWC_HEADER_ATTR], HEADER)

    def test_whitespace_variants_parse(self) -> None:
        text = (
            "1\t1\t0\t0\t0\t5\t-1\n"
            "2  3   1.5  0    0  1 1\n"
            "   3 3 3.0 0 0 1 2   \n"
            "4 \t 3 \t 4.5 \t 0 \t 0 \t 1 \t 3\n"
        )
        df = parse_swc_text_preserve_tokens(text)
        self.assertEqual(df["id"].tolist(), [1, 2, 3, 4])
        self.assertEqual(df["x"].tolist(), [0.0, 1.5, 3.0, 4.5])
        self.assertEqual(df["parent"].tolist(), [-1, 1, 2, 3])

    def test_crlf_line_endings_parse(self) -> None:
        df = parse_swc_text_preserve_tokens("1 1 0 0 0 5 -1\r\n2 3 1 0 0 1 1\r\n")
        self.assertEqual(df["id"].tolist(), [1, 2])
        self.assertEqual(df["parent"].tolist(), [-1, 1])

    def test_lines_with_fewer_than_seven_fields_are_dropped(self) -> None:
        text = _text([
            "1 1 0 0 0 5 -1",
            "2 3 1 0 0 1",
            "3 3",
            "",
            "4 3 2 0 0 1 1",
        ])
        df = parse_swc_text_preserve_tokens(text)
        self.assertEqual(df["id"].tolist(), [1, 4])

    def test_extra_columns_are_ignored(self) -> None:
        df = parse_swc_text_preserve_tokens("1 1 0 0 0 5 -1 extra 42\n")
        self.assertEqual(df["id"].tolist(), [1])
        self.assertEqual(df["parent"].tolist(), [-1])

    def test_non_numeric_radius_becomes_nan(self) -> None:
        df = parse_swc_text_preserve_tokens("1 1 0 0 0 abc -1\n")
        self.assertTrue(math.isnan(float(df["radius"].iloc[0])))

    def test_empty_text_gives_empty_frame(self) -> None:
        df = parse_swc_text_preserve_tokens("# only a comment\n")
        self.assertEqual(len(df), 0)
        self.assertEqual(df.attrs[SWC_HEADER_ATTR], ["# only a comment"])

    def test_byte_order_mark_before_a_header_keeps_the_header(self) -> None:
        df = parse_swc_text_preserve_tokens("﻿" + _text(HEADER + DATA))
        self.assertEqual(df.attrs[SWC_HEADER_ATTR], HEADER)
        self.assertEqual(df["id"].tolist(), [1, 2, 3, 4])

    def test_byte_order_mark_before_the_first_node_keeps_the_node(self) -> None:
        df = parse_swc_text_preserve_tokens("﻿" + _text(DATA))
        self.assertEqual(df["id"].tolist(), [1, 2, 3, 4])
        self.assertEqual(df["type"].tolist(), [1, 3, 3, 2])


class SwcFieldRewriteTests(unittest.TestCase):
    """``rewrite_swc_fields`` changes the named fields and nothing else."""

    MESSY = (
        "﻿# header\r\n"
        "1 1 0 0 0 5.0 -1 extra1 extra2\r\n"
        "# a note between nodes\r\n"
        "2\t3.0\t1.5\t0\t0\t1.000\t1   # inline comment\r\n"
        "abc 3 1 1 1 1 1\r\n"
        "3  3  2.5  0  0  0.0  2\r\n"
        "4 3 3.5 0 0 0.75 3"
    )

    def test_only_the_type_field_changes(self) -> None:
        out = rewrite_swc_fields(self.MESSY, {2: 4, 3: 2})
        expected = (self.MESSY.replace("2\t3.0\t1.5", "2\t4\t1.5")
                    .replace("3  3  2.5", "3  2  2.5"))
        self.assertEqual(out, expected)

    def test_an_unchanged_value_keeps_its_text(self) -> None:
        self.assertEqual(rewrite_swc_fields(self.MESSY, {2: 3, 1: 1}), self.MESSY)

    def test_radius_is_rewritten_only_when_asked(self) -> None:
        out = rewrite_swc_fields(self.MESSY, {}, {3: 1.0})
        self.assertEqual(out, self.MESSY.replace("3  3  2.5  0  0  0.0  2", "3  3  2.5  0  0  1.0  2"))
        self.assertEqual(rewrite_swc_fields(self.MESSY, {}, {2: 1.0}), self.MESSY)

    def test_duplicate_or_missing_ids_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            rewrite_swc_fields("1 1 0 0 0 1 -1\n1 2 1 0 0 1 1\n", {1: 3})
        with self.assertRaises(ValueError):
            rewrite_swc_fields(self.MESSY, {99: 3})

    def test_line_breaks_round_trip(self) -> None:
        for text in ("a\r\nb\rc\nd", "a\n", "", "\n\n"):
            self.assertEqual("".join(c + b for c, b in split_swc_lines(text)), text)

    def test_node_rows_skip_comments_and_unparsable_lines(self) -> None:
        headers, rows = swc_data_rows_from_text(self.MESSY)
        self.assertEqual([r["id"] for r in rows], [1, 2, 3, 4])
        self.assertEqual(headers, ["# header", "# a note between nodes"])

    def test_bytes_that_are_not_utf8_survive(self) -> None:
        data = b"# caf\xe9\n1 1 0 0 0 1 -1\n2 3 1 0 0 1 1\n"
        text = decode_swc_bytes(data)
        self.assertEqual(encode_swc_text(rewrite_swc_fields(text, {2: 4})),
                         data.replace(b"2 3 1", b"2 4 1"))


class SwcWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="swcstudio-io-")
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_load_save_without_edits_is_byte_identical(self) -> None:
        text = _text(HEADER + DATA)
        out = write_swc_to_bytes_preserve_tokens(parse_swc_text_preserve_tokens(text))
        self.assertEqual(out.decode("utf-8"), text)

    def test_round_trip_through_a_file(self) -> None:
        src = self.tmp / "cell.swc"
        src.write_bytes(_text(HEADER + DATA).encode("utf-8"))
        df = parse_swc_text_preserve_tokens(src.read_bytes().decode("utf-8"))
        dst = self.tmp / "copy.swc"
        dst.write_bytes(write_swc_to_bytes_preserve_tokens(df))
        self.assertEqual(dst.read_bytes(), src.read_bytes())

    def test_number_text_is_preserved_when_whitespace_differs(self) -> None:
        text = "1\t1\t0.000\t0.0\t0\t5.50\t-1\n2  3  1.250  -0.5  2.0e+00  1.000  1\n"
        out = write_swc_to_bytes_preserve_tokens(parse_swc_text_preserve_tokens(text)).decode("utf-8")
        self.assertEqual(
            _data_lines(out),
            ["1 1 0.000 0.0 0 5.50 -1", "2 3 1.250 -0.5 2.0e+00 1.000 1"],
        )

    def test_file_without_header_gets_default_swc_plus_header(self) -> None:
        out = write_swc_to_bytes_preserve_tokens(parse_swc_text_preserve_tokens(_text(DATA))).decode("utf-8")
        lines = out.splitlines()
        self.assertEqual(lines[0], "# SWC plus (SWC+) format specification")
        self.assertIn('# <SWCplus version="0.12">', lines)
        header = [ln for ln in lines if ln.startswith("#")]
        self.assertEqual(header[-1], "# </SWCplus>")
        self.assertEqual(lines[len(header):], DATA)

    def test_edited_values_are_rewritten_and_others_kept(self) -> None:
        df = parse_swc_text_preserve_tokens(_text(HEADER + DATA))
        df.loc[df["id"] == 2, "radius"] = 0.6
        df.loc[df["id"] == 3, "x"] = 7.0
        out = write_swc_to_bytes_preserve_tokens(df).decode("utf-8")
        lines = _data_lines(out)
        self.assertEqual(lines[0], DATA[0])
        self.assertEqual(lines[1], "2 3 1.250 -0.5 2.0e+00 0.6 1")
        self.assertEqual(lines[2], "3 3 7.0 -1.00 4 0.75 2")
        self.assertEqual(lines[3], DATA[3])

    def test_comment_lines_between_data_move_into_header(self) -> None:
        text = _text(["# top", DATA[0], "# middle note", DATA[1]])
        out = write_swc_to_bytes_preserve_tokens(parse_swc_text_preserve_tokens(text)).decode("utf-8")
        self.assertEqual(out, _text(["# top", "# middle note", DATA[0], DATA[1]]))

    def test_written_bytes_parse_back_to_same_values(self) -> None:
        df = parse_swc_text_preserve_tokens(_text(HEADER + DATA))
        df.loc[df["id"] == 4, "radius"] = 0.3333333333
        again = parse_swc_text_preserve_tokens(write_swc_to_bytes_preserve_tokens(df).decode("utf-8"))
        for col in ("id", "type", "x", "y", "z", "radius", "parent"):
            self.assertEqual(again[col].tolist(), df[col].tolist(), col)


if __name__ == "__main__":
    unittest.main()
