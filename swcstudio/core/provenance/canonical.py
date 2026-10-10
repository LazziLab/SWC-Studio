"""Canonical SWC byte form + content hashes.

Implements the canonicalization rules from ``docs/PROVENANCE_SPEC.md``
section 2. Every other piece of the provenance system anchors on these
two functions, so they must be small, deterministic, and never change
their behavior across v1.x.

Rules (frozen for v1):

============================  =====================================
Aspect                         Rule
============================  =====================================
Line endings                   normalized to ``\\n``
Trailing whitespace            stripped per line
Float format                   pass-through (no rounding/reformatting)
Comment lines (``#``)          included in hash, **except** lines
                               starting with ``# @PROV``
Node ordering                  as-written (do not sort)
Hash algorithm                 SHA-256
Encoding                       UTF-8 strict
============================  =====================================

The exclusion of ``# @PROV`` lines is what makes the in-file
provenance header (spec §5) cycle-free: writing the header about a
state cannot recursively change that state's own hash.
"""

from __future__ import annotations

import hashlib

__all__ = [
    "canonical_swc",
    "root_sha",
    "sha256_hex",
]


# Sentinel prefix for the in-file provenance "business card" lines
# specified in PROVENANCE_SPEC §5. Any comment line beginning with
# this exact prefix is excluded from the canonical form (and therefore
# from the file's hash) so updating the @PROV tip line does not change
# the file identity it describes.
_PROV_PREFIX = "# @PROV"


def canonical_swc(data: bytes) -> bytes:
    """Return the canonical byte form of an SWC file.

    The returned bytes are what every SWC-Studio hash is computed
    over. Two SWC files with the same canonical form are, by
    definition, the same dataset for provenance purposes.

    Parameters
    ----------
    data:
        Raw bytes of an SWC file as read from disk.

    Returns
    -------
    bytes
        Canonicalized UTF-8 bytes. Always terminates with a single
        ``\\n``.

    Raises
    ------
    UnicodeDecodeError
        If ``data`` is not valid UTF-8. SWC is a text format; we
        refuse to silently coerce binary garbage into a "canonical"
        form.
    """
    # ``str.splitlines()`` handles every line terminator the platform
    # might have produced (``\n``, ``\r\n``, ``\r``, plus the more
    # exotic Unicode line separators) and yields the content without
    # the terminator. Re-joining with ``\n`` guarantees a single
    # consistent line ending in the output regardless of input OS.
    text = data.decode("utf-8")
    had_bom = text.startswith("\ufeff")

    out_lines: list[str] = []
    for line in text.splitlines():
        # Strip trailing whitespace only. Leading whitespace is
        # preserved because some tools indent comment blocks
        # intentionally and we promised "pass-through" semantics for
        # everything we don't explicitly normalize.
        line = line.rstrip()
        # A byte-order mark may precede the first @PROV line.
        if line.lstrip("\ufeff").startswith(_PROV_PREFIX):
            # See module docstring on why @PROV lines are excluded.
            continue
        out_lines.append(line)

    # Trailing newline is part of the canonical form. Without it,
    # adding or removing a final newline would change the hash, which
    # is exactly the kind of cosmetic difference canonicalization is
    # supposed to absorb.
    out = "\n".join(out_lines) + "\n"
    # Keep a leading byte-order mark even when it sat on a @PROV line, so
    # a file hashes the same whether its header is above or below the mark.
    if had_bom and not out.startswith("\ufeff"):
        out = "\ufeff" + out
    return out.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """SHA-256 of ``data`` as a 64-character lowercase hex string.

    Wrapped here (rather than inlining ``hashlib``) so every
    provenance hash in the codebase routes through one place. If the
    hash algorithm ever changes (it would require a v2 format bump
    per spec §17), there is one symbol to revisit.
    """
    return hashlib.sha256(data).hexdigest()


def root_sha(data: bytes) -> str:
    """Compute the dataset root SHA for raw SWC bytes.

    This is the value spec §2 calls ``root_sha``: the stable
    identity of an SWC dataset, derived from the canonical form so
    cosmetic differences (line endings, trailing whitespace, in-file
    ``@PROV`` headers) do not produce a different identity.

    Equivalent to ``sha256_hex(canonical_swc(data))`` and exposed as
    a named function so call sites read intentionally.
    """
    return sha256_hex(canonical_swc(data))
