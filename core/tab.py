"""Guitar/bass tablature data model + audio-timeline timing helpers.

Tab is anchored to a *fixed recording*: each bar carries explicit millisecond
anchors (start_ms = downbeat, end_ms = next downbeat). Real time for any beat is
interpolated piecewise-linearly between its bar's anchors, so the tab stays
aligned to the audio even when the performance drifts in tempo. Musical fields
(time signature, durations) drive layout/engraving — not wall-clock time.

Serialised inside the .stems manifest under a "tabs" list (see core/project.py).
Every dataclass mirrors the SavedLoop style: to_dict() / from_dict() with
`.get()` defaults so older files (no tabs) load unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Tier-1 technique tokens the MVP editor exposes.
TECHNIQUES = ("h", "p", "/", "\\", "b", "~", "PM", "x", "ring", "staccato", "accent")

# Common tuning presets (low → high string), used for sensible defaults.
TUNINGS = {
    6: ["E", "A", "D", "G", "B", "E"],   # standard guitar
    4: ["E", "A", "D", "G"],             # standard bass
    5: ["B", "E", "A", "D", "G"],
    7: ["B", "E", "A", "D", "G", "B", "E"],
}


def default_tuning(strings: int) -> list[str]:
    return list(TUNINGS.get(strings, ["E"] * strings))


@dataclass
class Note:
    string: int               # 1 = highest-pitched line (top of the tab)
    fret: int = 0
    techniques: list[str] = field(default_factory=list)
    bend: list[int] = field(default_factory=list)   # quarter-tone points (advanced)

    def to_dict(self) -> dict:
        d = {"string": self.string, "fret": self.fret}
        if self.techniques:
            d["techniques"] = list(self.techniques)
        if self.bend:
            d["bend"] = list(self.bend)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Note":
        return cls(
            string=int(d.get("string", 1)),
            fret=int(d.get("fret", 0)),
            techniques=list(d.get("techniques", [])),
            bend=[int(x) for x in d.get("bend", [])],
        )


@dataclass
class Beat:
    pos: float = 0.0          # fractional position within the bar, [0, 1)
    dur: str = "4"            # "1" | "2" | "4" | "8" | "16"
    dotted: bool = False
    tuplet: int = 0           # 0 = none, 3 = triplet, … (advanced)
    rest: bool = False
    notes: list[Note] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {"pos": round(self.pos, 6), "dur": self.dur}
        if self.dotted:
            d["dotted"] = True
        if self.tuplet:
            d["tuplet"] = self.tuplet
        if self.rest:
            d["rest"] = True
        if self.notes:
            d["notes"] = [n.to_dict() for n in self.notes]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Beat":
        return cls(
            pos=float(d.get("pos", 0.0)),
            dur=str(d.get("dur", "4")),
            dotted=bool(d.get("dotted", False)),
            tuplet=int(d.get("tuplet", 0)),
            rest=bool(d.get("rest", False)),
            notes=[Note.from_dict(n) for n in d.get("notes", [])],
        )


@dataclass
class Bar:
    ts_num: int = 4
    ts_den: int = 4
    start_ms: int = 0
    end_ms: int = 0
    beats: list[Beat] = field(default_factory=list)

    @property
    def length_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    def to_dict(self) -> dict:
        return {
            "ts_num": self.ts_num,
            "ts_den": self.ts_den,
            "start_ms": int(self.start_ms),
            "end_ms": int(self.end_ms),
            "beats": [b.to_dict() for b in self.beats],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Bar":
        return cls(
            ts_num=int(d.get("ts_num", 4)),
            ts_den=int(d.get("ts_den", 4)),
            start_ms=int(d.get("start_ms", 0)),
            end_ms=int(d.get("end_ms", 0)),
            beats=[Beat.from_dict(b) for b in d.get("beats", [])],
        )


@dataclass
class TabTrack:
    id: str = "tab1"
    stem_id: str = ""         # lane this tab is attached to ("bass", "other", …)
    name: str = "Guitar"
    strings: int = 6
    tuning: list[str] = field(default_factory=lambda: default_tuning(6))
    capo: int = 0
    def_ts_num: int = 4       # default time signature for new bars
    def_ts_den: int = 4
    bars: list[Bar] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "stem_id": self.stem_id,
            "name": self.name,
            "strings": self.strings,
            "tuning": list(self.tuning),
            "capo": self.capo,
            "def_ts_num": self.def_ts_num,
            "def_ts_den": self.def_ts_den,
            "bars": [b.to_dict() for b in self.bars],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TabTrack":
        strings = int(d.get("strings", 6))
        return cls(
            id=str(d.get("id", "tab1")),
            stem_id=str(d.get("stem_id", "")),
            name=str(d.get("name", "Guitar")),
            strings=strings,
            tuning=list(d.get("tuning") or default_tuning(strings)),
            capo=int(d.get("capo", 0)),
            def_ts_num=int(d.get("def_ts_num", 4)),
            def_ts_den=int(d.get("def_ts_den", 4)),
            bars=[Bar.from_dict(b) for b in d.get("bars", [])],
        )


# --------------------------------------------------------------------------- #
# Timing helpers — anchor-based, audio is the clock.
# --------------------------------------------------------------------------- #

def beat_ms(bar: Bar, beat: Beat) -> float:
    """Absolute ms of *beat* via linear interpolation between the bar anchors."""
    return bar.start_ms + beat.pos * bar.length_ms


def find_bar(track: TabTrack, ms: float) -> int:
    """Index of the bar containing *ms*, or -1 if outside every bar."""
    for i, bar in enumerate(track.bars):
        if bar.start_ms <= ms < bar.end_ms:
            return i
    return -1


def active_bar_beat(track: TabTrack, ms: float) -> tuple[int, int]:
    """(bar_index, beat_index) active at *ms*; either may be -1 if none."""
    bi = find_bar(track, ms)
    if bi < 0:
        return -1, -1
    bar = track.bars[bi]
    best = -1
    best_ms = -1.0
    for j, beat in enumerate(bar.beats):
        bms = beat_ms(bar, beat)
        if bms <= ms and bms >= best_ms:
            best_ms = bms
            best = j
    return bi, best
