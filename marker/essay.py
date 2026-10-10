"""Read essays from ``.txt``, ``.md`` and ``.docx`` files.

Essays are untrusted input, so parsing uses only the standard library: there is
no third-party document parser to audit. ``.docx`` files are read as ZIP
archives and only ``word/document.xml`` is parsed, with a size cap. Every essay
gets a SHA-256 of its normalised text so results can be tied to the exact text marked.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

#: File types that :func:`load_essay` accepts.
SUPPORTED_SUFFIXES = {".txt", ".md", ".docx"}

#: WordprocessingML XML namespace, in ElementTree's ``{uri}`` form.
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

#: Upper limit on the uncompressed size of ``document.xml`` (guards against zip bombs).
_MAX_DOCX_XML_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class Essay:
    """An essay ready for marking.

    Attributes:
        id: Essay identifier: the file name without its extension. It is used for
            output file names and to match human marks in ``marker evaluate``.
        path: Source path as given.
        text: Normalised text (NFC Unicode, ``\\n`` line endings, stripped,
            Markdown comments removed).
        word_count: Approximate word count, used for word-limit flags.
        sha256: SHA-256 of ``text`` encoded as UTF-8.
    """

    id: str
    path: str
    text: str
    word_count: int
    sha256: str


def load_essay(path: str | Path) -> Essay:
    """Load and normalise one essay file.

    For ``.md`` files, HTML comments (``<!-- ... -->``) are removed. They aren't
    part of the rendered essay and are a convenient place for provenance notes
    the model shouldn't see.

    Args:
        path: Path to a ``.txt``, ``.md`` or ``.docx`` file.

    Returns:
        The loaded :class:`Essay`.

    Raises:
        ValueError: Unsupported file type, empty essay, or invalid ``.docx`` contents.
        OSError: The file can't be read.
        KeyError: A ``.docx`` has no ``word/document.xml``.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"{p}: unsupported file type {suffix!r} (use .txt, .md or .docx)")
    # utf-8-sig drops a byte-order mark, which Windows editors often add.
    text = _read_docx(p) if suffix == ".docx" else p.read_text(encoding="utf-8-sig")
    if suffix == ".md":
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    # NFC makes visually identical text hash identically, e.g. "é" precomposed vs combining.
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").strip()
    if not text:
        raise ValueError(f"{p}: essay is empty")
    return Essay(
        id=p.stem,
        path=str(p),
        text=text,
        word_count=count_words(text),
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def expand_paths(paths: list[str]) -> list[Path]:
    """Expand directories into their supported essay files.

    Files given directly are kept as they are, even unsupported ones, so
    :func:`load_essay` can report a clear error. Directory contents are sorted
    (not recursive), so marking order is stable between runs. Inside
    directories, README files and hidden files are skipped: they document the
    folder and aren't essays.

    Args:
        paths: File and/or directory paths.

    Returns:
        Files to mark, in order.
    """
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out.extend(
                sorted(
                    f
                    for f in p.iterdir()
                    if f.is_file() and f.suffix.lower() in SUPPORTED_SUFFIXES and not _is_ignored(f)
                )
            )
        else:
            out.append(p)
    return out


def _is_ignored(path: Path) -> bool:
    """True for files in an essay directory that aren't essays: READMEs and hidden files."""
    return path.name.startswith(".") or path.stem.lower() == "readme"


def count_words(text: str) -> int:
    """Count words: runs of letters and digits, allowing internal apostrophes and hyphens.

    This is an approximation, close to what word processors report. It is used
    only for word-limit flags, never to change a mark.
    """
    return len(re.findall(r"\b[\w'’-]+\b", text))


def _read_docx(path: Path) -> str:
    """Extract plain text from a ``.docx`` file, one line per paragraph.

    Text runs (``w:t``) are joined, tabs become ``\\t`` and line breaks become
    ``\\n``. Headers, footers, footnotes and comments are not included.
    """
    with zipfile.ZipFile(path) as z:
        info = z.getinfo("word/document.xml")
        if info.file_size > _MAX_DOCX_XML_BYTES:
            raise ValueError(f"{path}: document.xml is too large")
        try:
            # ElementTree (expat) does not fetch external entities, so XXE is not possible here.
            root = ElementTree.fromstring(z.read(info))
        except ElementTree.ParseError as e:
            raise ValueError(f"{path}: invalid document.xml: {e}") from e
    paragraphs: list[str] = []
    for para in root.iter(f"{_W}p"):
        parts: list[str] = []
        for node in para.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)
