"""A small XLSX writer (no third-party dependency) for the AEAT books workbook.

Cells hold text, whole numbers, decimals (shown with two decimals) or dates (shown dd/mm/yyyy).
Header rows get a bold wrapped style; the first rows can be frozen so the headers stay visible.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple, Union
from xml.sax.saxutils import escape

Cell = Union[None, str, int, Decimal, date]

_STYLE_DEFAULT, _STYLE_HEADER, _STYLE_NUMBER, _STYLE_DATE = 0, 1, 2, 3
_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
_EPOCH = date(1899, 12, 30)


@dataclass
class Sheet:
    """One worksheet: rows of cells, the number of leading header rows, merged ranges, column widths."""
    name: str
    rows: List[Sequence[Cell]] = field(default_factory=list)
    header_rows: int = 0
    merges: Tuple[str, ...] = ()
    widths: Optional[Sequence[float]] = None


def column_letters(index: int) -> str:
    """1 -> 'A', 27 -> 'AA'."""
    letters = ""
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _text(value: str) -> str:
    return escape(_ILLEGAL.sub("", value))


class _Strings:
    def __init__(self) -> None:
        self.items: List[str] = []
        self._index: Dict[str, int] = {}

    def add(self, value: str) -> int:
        if value not in self._index:
            self._index[value] = len(self.items)
            self.items.append(value)
        return self._index[value]


def _cell_xml(ref: str, value: Cell, header: bool, strings: _Strings) -> str:
    if value is None or value == "":
        return f'<c r="{ref}" s="{_STYLE_HEADER}"/>' if header else ""
    if isinstance(value, str):
        style = f' s="{_STYLE_HEADER}"' if header else ""
        return f'<c r="{ref}"{style} t="s"><v>{strings.add(value)}</v></c>'
    if isinstance(value, bool):  # a bool is an int; it has no place in these books
        raise TypeError("booleans are not supported")
    if isinstance(value, int):
        return f'<c r="{ref}"><v>{value}</v></c>'
    if isinstance(value, Decimal):
        return f'<c r="{ref}" s="{_STYLE_NUMBER}"><v>{value.quantize(Decimal("0.01"))}</v></c>'
    if isinstance(value, date):
        return f'<c r="{ref}" s="{_STYLE_DATE}"><v>{(value - _EPOCH).days}</v></c>'
    raise TypeError(f"unsupported cell value {value!r}")


def _sheet_xml(sheet: Sheet, strings: _Strings) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
    if sheet.header_rows:
        out.append('<sheetViews><sheetView workbookViewId="0"><pane ySplit="%d" topLeftCell="A%d" '
                   'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>' % (sheet.header_rows, sheet.header_rows + 1))
    out.append('<sheetFormatPr defaultRowHeight="15"/>')
    if sheet.widths:
        out.append("<cols>" + "".join(
            f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(sheet.widths, 1)) + "</cols>")
    out.append("<sheetData>")
    for r, row in enumerate(sheet.rows, 1):
        header = r <= sheet.header_rows
        cells = "".join(_cell_xml(f"{column_letters(c)}{r}", v, header, strings) for c, v in enumerate(row, 1))
        out.append(f'<row r="{r}">{cells}</row>')
    out.append("</sheetData>")
    if sheet.merges:
        out.append(f'<mergeCells count="{len(sheet.merges)}">' + "".join(f'<mergeCell ref="{m}"/>' for m in sheet.merges)
                   + "</mergeCells>")
    out.append("</worksheet>")
    return "".join(out)


_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="dd/mm/yyyy"/></numFmts>'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFD9E1F2"/></patternFill></fill></fills>'
    '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>'
    '<border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="4">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">'
    '<alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
    '<xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


def write_xlsx(sheets: Sequence[Sheet]) -> bytes:
    """The workbook as bytes."""
    strings = _Strings()
    sheet_parts = [_sheet_xml(s, strings) for s in sheets]
    shared = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(strings.items)}" '
              f'uniqueCount="{len(strings.items)}">'
              + "".join(f'<si><t xml:space="preserve">{_text(s)}</t></si>' for s in strings.items) + "</sst>")
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                + "".join(f'<sheet name="{_text(s.name)}" sheetId="{i}" r:id="rId{i}"/>' for i, s in enumerate(sheets, 1))
                + "</sheets></workbook>")
    n = len(sheets)
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                      f'Target="worksheets/sheet{i}.xml"/>' for i in range(1, n + 1))
            + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
              f'Target="styles.xml"/>'
              f'<Relationship Id="rId{n + 2}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
              f'Target="sharedStrings.xml"/></Relationships>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                               'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                               for i in range(1, n + 1))
                     + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                     '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
                     '</Types>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                 'Target="xl/workbook.xml"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/styles.xml", _STYLES)
        z.writestr("xl/sharedStrings.xml", shared)
        for i, part in enumerate(sheet_parts, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", part)
    return buf.getvalue()
