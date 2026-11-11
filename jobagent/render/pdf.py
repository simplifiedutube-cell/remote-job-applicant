"""docx -> pdf -- M4.

LibreOffice is not installed on this machine, so this is written as a Protocol with two
implementations rather than a hard dependency. `NullConverter` returns the .docx and
marks `meta.pdf_status="unavailable"`; Greenhouse, Lever and Ashby all accept .docx
uploads, so the apply lane degrades rather than blocks, and escalates only where PDF is
genuinely mandatory.

`docx2pdf` is deliberately not used: it requires Microsoft Word.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from jobagent.config import get_config

__all__ = ["NullConverter", "PdfConverter", "SofficeConverter", "get_converter"]


class PdfConverter(Protocol):
    available: bool

    def convert(self, docx_path: Path) -> Path | None:
        """Return the pdf path, or None when conversion is unavailable."""
        ...


class SofficeConverter:
    available = True

    def __init__(self, binary: Path) -> None:
        self.binary = binary

    def convert(self, docx_path: Path) -> Path | None:
        raise NotImplementedError("M4: render.pdf.SofficeConverter.convert")


class NullConverter:
    """No LibreOffice. Degrade to .docx rather than failing the job."""

    available = False

    def convert(self, docx_path: Path) -> Path | None:
        return None


def get_converter() -> PdfConverter:
    cfg = get_config()
    binary = shutil.which("soffice") or (str(cfg.soffice_bin) if cfg.soffice_bin.exists() else None)
    return SofficeConverter(Path(binary)) if binary else NullConverter()


_UNUSED = subprocess  # SofficeConverter.convert will use it in M4
