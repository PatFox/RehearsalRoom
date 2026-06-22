"""Render tab tracks to printable forms — ASCII text and engraved PDF.

Spacing model: a bar has `ts_num * _SUBDIV` columns; each note onset sits on a
column. Per bar we collapse to the *minimal* grid (gcd of the onset columns) so
empty 16th-subdivisions don't waste space, while still preserving rhythm. In
"combined" multi-track output, bars at the same index across tracks share one
grid (via lcm of their column counts) so they line up vertically.

No new dependency: the PDF is drawn with QtGui.QPdfWriter + QPainter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

_SUBDIV = 4   # editing-grid columns per beat — must match ui/tab_editor.py


@dataclass
class ExportOpts:
    mode: str = "combined"      # "combined" | "sections" | "separate"
    fmt: str = "txt"            # "txt" | "pdf"
    spacing: int = 2            # 1..5 compactness (cell size)
    bars_per_line: int = 4
    show_bar_numbers: bool = True
    show_time_sig: bool = True
    title: str = ""
    artist: str = ""


def _ts_str(bar) -> str:
    return f"{bar.ts_num}/{bar.ts_den}"


# --------------------------------------------------------------------------- #
# Grid reconstruction
# --------------------------------------------------------------------------- #

def _bar_onsets(bar) -> tuple[int, dict]:
    """(ncols, {col: {string: (fret, techniques)}}) for a bar at full 16th grid."""
    ncols = max(1, bar.ts_num * _SUBDIV)
    cols: dict[int, dict] = {}
    for beat in bar.beats:
        col = max(0, min(ncols - 1, round(beat.pos * ncols)))
        cell = cols.setdefault(col, {})
        for n in beat.notes:
            cell[n.string] = (n.fret, list(n.techniques))
    return ncols, cols


def align_bars(bars: list) -> tuple[int, list[dict]]:
    """Reduce a set of same-index bars (one per track, None allowed) to a shared
    minimal grid. Returns (cells, [per-track {cell_index: {string:(fret,techs)}}]).
    Onsets are normalised to the lcm of the bars' column counts so differing time
    signatures still align."""
    grids = [(_bar_onsets(b) if b is not None else (_SUBDIV, {})) for b in bars]
    ncols_list = [g[0] for g in grids]
    common = math.lcm(*ncols_list) if ncols_list else _SUBDIV

    normalised: list[dict] = []
    onsets: list[int] = []
    for ncols, cols in grids:
        scale = common // ncols
        nm = {col * scale: cell for col, cell in cols.items()}
        normalised.append(nm)
        onsets.extend(nm.keys())

    g = math.gcd(common, *onsets) if onsets else _SUBDIV
    g = g or _SUBDIV
    cells = max(1, common // g)
    reduced = [{col // g: cell for col, cell in nm.items()} for nm in normalised]
    return cells, reduced


def _chunks(n: int, size: int):
    for start in range(0, max(n, 1), max(1, size)):
        yield list(range(start, min(start + size, n)))


def _string_label(track, row: int) -> str:
    """Tuning letter for display row *row* (0 = top line = highest string)."""
    tuning = track.tuning or []
    # tuning is low→high; the top line is the highest-pitched string.
    idx = track.strings - 1 - row
    if 0 <= idx < len(tuning):
        return tuning[idx]
    return ""


# --------------------------------------------------------------------------- #
# Text (ASCII) rendering
# --------------------------------------------------------------------------- #

def _cell_chars(opts: ExportOpts) -> int:
    return max(2, opts.spacing + 1)


def _system_grids(tracks: list, bar_indices: list[int]):
    """For a system (span of bar indices), return per-track per-bar (cells, grid),
    aligned across tracks so each bar index has one shared cell count."""
    per_track = [[] for _ in tracks]
    for i in bar_indices:
        bars_i = [t.bars[i] if i < len(t.bars) else None for t in tracks]
        cells, reduced = align_bars(bars_i)
        for k in range(len(tracks)):
            per_track[k].append((cells, reduced[k]))
    return per_track


def _string_line(track, row: int, bars, cell_chars: int) -> str:
    label = _string_label(track, row)
    string = row + 1
    parts = [f"{label:>2}|"]
    for cells, grid in bars:
        out = []
        for c in range(cells):
            note = grid.get(c, {}).get(string)
            if note:
                fret, techs = note
                txt = str(fret) + (techs[0] if techs else "")
            else:
                txt = ""
            out.append(txt.ljust(cell_chars, "-") if txt else "-" * cell_chars)
        parts.append("".join(out) + "|")
    return "".join(parts)


def _track_header(track) -> str:
    tuning = " ".join(track.tuning or [])
    capo = f"  Capo {track.capo}" if track.capo else ""
    return f"{track.name}   [{tuning}]{capo}"


def _marker_line(fields) -> str:
    """A header line aligned to the staff: 3-char prefix (matches the string
    label + barline), then each (width, text) field left-justified."""
    s = " " * 3
    for w, txt in fields:
        s += (str(txt)[:max(0, w)]).ljust(w)
    return s.rstrip()


def _bar_widths(grids, cell_chars: int) -> list[int]:
    return [cells * cell_chars + 1 for cells, _g in grids]


def render_text(tracks: list, opts: ExportOpts) -> str:
    body = _render_body(tracks, opts)
    if not (opts.title or opts.artist):
        return body
    width = max([len(l) for l in body.splitlines()] + [24])
    head = []
    if opts.title:
        head.append(opts.title.center(width))
    if opts.artist:
        head.append(opts.artist.center(width))
    return "\n".join(head) + "\n\n" + body


def _render_body(tracks: list, opts: ExportOpts) -> str:
    if not tracks:
        return ""
    cc = _cell_chars(opts)
    combined = opts.mode == "combined" and len(tracks) > 1
    lines: list[str] = []

    if combined:
        maxbars = max((len(t.bars) for t in tracks), default=0)
        lines.append("  ".join(t.name for t in tracks))
        lines.append("")
        prev_ts = {k: None for k in range(len(tracks))}
        for system in _chunks(maxbars, opts.bars_per_line):
            if not system:
                break
            per_track = _system_grids(tracks, system)
            widths = _bar_widths(per_track[0], cc)
            if opts.show_bar_numbers:
                lines.append(_marker_line([(w, i + 1) for w, i in zip(widths, system)]))
            for k, t in enumerate(tracks):
                lines.append(_track_header(t))
                if opts.show_time_sig:
                    lines.append(_marker_line(_ts_fields(t, system, widths, prev_ts, k)))
                for row in range(t.strings):
                    lines.append(_string_line(t, row, per_track[k], cc))
                lines.append("")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    # sections / single: each track in full, one after another
    blocks = []
    for t in tracks:
        b = [_track_header(t), ""]
        prev_ts = {0: None}
        for system in _chunks(len(t.bars), opts.bars_per_line):
            if not system:
                break
            per_track = _system_grids([t], system)
            widths = _bar_widths(per_track[0], cc)
            if opts.show_bar_numbers:
                b.append(_marker_line([(w, i + 1) for w, i in zip(widths, system)]))
            if opts.show_time_sig:
                b.append(_marker_line(_ts_fields(t, system, widths, prev_ts, 0)))
            for row in range(t.strings):
                b.append(_string_line(t, row, per_track[0], cc))
            b.append("")
        blocks.append("\n".join(b).rstrip())
    sep = "\n\n" + "=" * 48 + "\n\n"
    return sep.join(blocks) + "\n"


def _ts_fields(track, span, widths, prev_ts, k):
    """Time-signature header fields for one track over a system; shows the ts
    only where it changes from the previous bar."""
    fields = []
    for w, bi in zip(widths, span):
        if bi < len(track.bars):
            ts = _ts_str(track.bars[bi])
            txt = ts if ts != prev_ts[k] else ""
            prev_ts[k] = ts
        else:
            txt = ""
        fields.append((w, txt))
    return fields


# --------------------------------------------------------------------------- #
# Graphical (engraved) rendering — shared by PDF export and the preview widget
# --------------------------------------------------------------------------- #

def _geom(opts: ExportOpts) -> dict:
    return dict(cell_w=8 + opts.spacing * 5, row_h=9, header_h=14,
                track_gap=16, system_gap=20, label_w=22, barline_gap=7)


def _groups(tracks: list, opts: ExportOpts):
    """Yield (subset, span, sys_grids, page_break_before) painting units."""
    if opts.mode == "combined" and len(tracks) > 1:
        maxbars = max((len(t.bars) for t in tracks), default=0)
        for span in _chunks(maxbars, opts.bars_per_line):
            if span:
                yield (tracks, span, _system_grids(tracks, span), False)
    else:
        first_track = True
        for t in tracks:
            first_sys = True
            spans = [s for s in _chunks(len(t.bars), opts.bars_per_line) if s]
            for span in spans:
                yield ([t], span, _system_grids([t], span), first_sys and not first_track)
                first_sys = False
            first_track = False


def _header_height(opts: ExportOpts) -> float:
    h = (22 if opts.title else 0) + (18 if opts.artist else 0)
    return h + 10 if h else 0


def _paint_header(p, opts: ExportOpts, width: float, y0: float) -> float:
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QPen, QFont
    center = int(0x0004 | 0x0080)   # AlignHCenter | AlignVCenter
    y = y0
    if opts.title:
        p.setPen(QPen(QColor("#111111"), 1))
        p.setFont(QFont("Helvetica", 14, QFont.Weight.Bold))
        p.drawText(QRectF(0, y, width, 20), center, opts.title)
        y += 22
    if opts.artist:
        p.setPen(QPen(QColor("#555555"), 1))
        p.setFont(QFont("Helvetica", 10))
        p.drawText(QRectF(0, y, width, 16), center, opts.artist)
        y += 18
    return _header_height(opts)


def _system_height(subset: list, geom: dict) -> float:
    return sum(geom["header_h"] + (t.strings - 1) * geom["row_h"] + geom["track_gap"]
               for t in subset) + geom["system_gap"]


def _paint_system(p, subset, span, sys_grids, x0, y0, width, geom, opts, prev_ts):
    from PySide6.QtCore import QRectF, QPointF
    from PySide6.QtGui import QColor, QPen, QFont

    ink = QColor("#111111")
    muted = QColor("#777777")
    bar_cells = [c for c, _g in sys_grids[0]] if sys_grids and sys_grids[0] else []
    total_cells = sum(bar_cells)
    nbars = len(bar_cells)
    avail = width - geom["label_w"]
    cell_w = geom["cell_w"]
    needed = total_cells * cell_w + nbars * geom["barline_gap"]
    if total_cells and needed > avail:
        cell_w = max(4.0, (avail - nbars * geom["barline_gap"]) / total_cells)

    staff_x0 = x0 + geom["label_w"]
    y = y0
    for k, t in enumerate(subset):
        grids_t = sys_grids[k]
        p.setPen(QPen(ink, 1))
        p.setFont(QFont("Helvetica", 8, QFont.Weight.Bold))
        p.drawText(QPointF(x0, y + 9), t.name)

        top = y + geom["header_h"]
        bottom = top + (t.strings - 1) * geom["row_h"]

        # bar x-offsets
        bar_x = []
        x = staff_x0
        for cells, _g in grids_t:
            bar_x.append(x)
            x += cells * cell_w + geom["barline_gap"]
        staff_end = x - geom["barline_gap"]

        # bar numbers (top track only) + time signatures (per track, on change)
        for bi in range(len(grids_t)):
            global_i = span[bi] if bi < len(span) else bi
            if opts.show_bar_numbers and k == 0:
                p.setPen(QPen(muted, 1))
                p.setFont(QFont("Helvetica", 6))
                p.drawText(QPointF(bar_x[bi] + 1, y + 6), str(global_i + 1))
            if opts.show_time_sig and global_i < len(t.bars):
                ts = _ts_str(t.bars[global_i])
                if ts != prev_ts.get(id(t)):
                    prev_ts[id(t)] = ts
                    p.setPen(QPen(ink, 1))
                    p.setFont(QFont("Helvetica", 7, QFont.Weight.Bold))
                    p.drawText(QPointF(bar_x[bi] + 1, y + 13), ts)

        # string lines + tuning labels
        p.setFont(QFont("Helvetica", 7))
        for row in range(t.strings):
            yl = top + row * geom["row_h"]
            p.setPen(QPen(QColor("#888888"), 0.6))
            p.drawLine(QPointF(staff_x0, yl), QPointF(staff_end, yl))
            p.setPen(QPen(ink, 1))
            p.drawText(QPointF(x0, yl + 2.5), _string_label(t, row))

        # barlines (start + each bar end)
        p.setPen(QPen(ink, 0.8))
        p.drawLine(QPointF(staff_x0, top), QPointF(staff_x0, bottom))
        xx = staff_x0
        for cells, _g in grids_t:
            xx += cells * cell_w + geom["barline_gap"]
            bxe = xx - geom["barline_gap"]
            p.drawLine(QPointF(bxe, top), QPointF(bxe, bottom))

        # fret numbers + technique markers
        for bi, (cells, grid) in enumerate(grids_t):
            for c in range(cells):
                cell = grid.get(c)
                if not cell:
                    continue
                cx = bar_x[bi] + c * cell_w + cell_w / 2
                for s, (fret, techs) in cell.items():
                    cy = top + (s - 1) * geom["row_h"]
                    txt = str(fret)
                    p.setFont(QFont("Helvetica", 8, QFont.Weight.Bold))
                    fm = p.fontMetrics()
                    tw = fm.horizontalAdvance(txt)
                    p.fillRect(QRectF(cx - tw / 2 - 1, cy - 4.5, tw + 2, 9),
                               QColor("#FFFFFF"))
                    p.setPen(QPen(ink, 1))
                    p.drawText(QRectF(cx - tw / 2 - 1, cy - 5, tw + 2, 10),
                               int(0x84), txt)   # AlignHCenter|AlignVCenter
                    if techs:
                        p.setFont(QFont("Helvetica", 6))
                        p.drawText(QPointF(cx + tw / 2, cy - 4), techs[0])
        y = bottom + geom["track_gap"]
    return (y - y0) + geom["system_gap"]


def paint_flow(painter, tracks: list, opts: ExportOpts, width: float,
               page_h: float | None = None, new_page=None) -> float:
    """Paint all systems top-to-bottom. With page_h + new_page, break across
    pages (PDF). Without, paint continuously and return the total height
    (preview). Returns the final y (total height when continuous)."""
    geom = _geom(opts)
    prev_ts: dict = {}
    y = 0.0
    if opts.title or opts.artist:
        y += _paint_header(painter, opts, width, y)   # document title, first page only
    for subset, span, sys_grids, brk in _groups(tracks, opts):
        if brk and new_page:
            new_page(); y = 0.0
        h = _system_height(subset, geom)
        if page_h and y > 0 and y + h > page_h:
            new_page(); y = 0.0
        _paint_system(painter, subset, span, sys_grids, 0.0, y, width, geom, opts, prev_ts)
        y += h
    return y


def measure_flow(tracks: list, opts: ExportOpts) -> float:
    """Total continuous height (no page breaks) — for sizing the preview."""
    geom = _geom(opts)
    body = sum(_system_height(subset, geom) for subset, _sp, _g, _b in _groups(tracks, opts))
    return body + _header_height(opts)


def render_pdf(tracks: list, opts: ExportOpts, dest) -> None:
    from PySide6.QtGui import QPdfWriter, QPainter, QPageSize, QPageLayout
    from PySide6.QtCore import QMarginsF
    writer = QPdfWriter(str(dest))
    writer.setResolution(72)                       # 1 unit = 1 point
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(40, 40, 40, 40), QPageLayout.Unit.Point)
    painter = QPainter(writer)
    try:
        paint_flow(painter, tracks, opts, writer.width(), writer.height(),
                   new_page=writer.newPage)
    finally:
        painter.end()

