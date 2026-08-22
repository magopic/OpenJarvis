"""Dependency-free minimal PDF writer for synthetic test fixtures only.

Builds a valid, real PDF with N pages of plain Helvetica text, computing
real xref byte offsets so pdfplumber/pdfminer parse it without needing
recovery-mode leniency. Never used outside tests/document_knowledge/.
"""
from __future__ import annotations

from pathlib import Path
from typing import List


def _escape_pdf_text(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def make_synthetic_pdf(pages_text: List[str], out_path: Path) -> None:
    n_pages = len(pages_text)
    catalog_num = 1
    pages_num = 2
    first_page_num = 3
    page_nums = [first_page_num + i for i in range(n_pages)]
    content_nums = [first_page_num + n_pages + i for i in range(n_pages)]
    font_num = first_page_num + 2 * n_pages

    objects: list[tuple[int, bytes]] = []
    kids = " ".join(f"{p} 0 R" for p in page_nums)
    objects.append((catalog_num, f"<< /Type /Catalog /Pages {pages_num} 0 R >>".encode()))
    objects.append((pages_num, f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode()))

    for i, text in enumerate(pages_text):
        page_num = page_nums[i]
        content_num = content_nums[i]
        objects.append((
            page_num,
            (
                f"<< /Type /Page /Parent {pages_num} 0 R "
                f"/Resources << /Font << /F1 {font_num} 0 R >> >> "
                f"/MediaBox [0 0 612 792] /Contents {content_num} 0 R >>"
            ).encode(),
        ))
        stream = f"BT /F1 18 Tf 72 700 Td ({_escape_pdf_text(text)}) Tj ET".encode()
        content_obj = f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        objects.append((content_num, content_obj))

    objects.append((font_num, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))
    objects.sort(key=lambda t: t[0])

    buf = bytearray()
    buf += b"%PDF-1.4\n"
    offsets: dict[int, int] = {}
    for num, body in objects:
        offsets[num] = len(buf)
        buf += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_start = len(buf)
    max_num = max(offsets) + 1
    buf += f"xref\n0 {max_num}\n".encode()
    buf += b"0000000000 65535 f \n"
    for num in range(1, max_num):
        off = offsets.get(num, 0)
        buf += f"{off:010d} 00000 n \n".encode()
    buf += f"trailer\n<< /Size {max_num} /Root {catalog_num} 0 R >>\nstartxref\n{xref_start}\n%%EOF".encode()

    out_path.write_bytes(bytes(buf))
