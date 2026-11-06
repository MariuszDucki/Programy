"""Obliczenia OEE (Overall Equipment Effectiveness).

OEE = Dostępność × Wydajność × Jakość

* Dostępność = czas pracy / czas planowany
  (czas planowany = długość okna − postoje planowane, np. CIP)
* Wydajność  = (sztuki ogółem / prędkość idealna) / czas pracy
* Jakość     = sztuki dobre / sztuki ogółem
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class OEEResult:
    window_s: float
    planned_s: float
    run_s: float
    unplanned_stop_s: float
    planned_stop_s: float
    good: int
    scrap: int
    availability: float
    performance: float
    quality: float
    oee: float

    def as_dict(self) -> dict:
        return asdict(self)


def clip_intervals(intervals, start: float, end: float):
    """Przycina interwały (state, category, s, e) do okna [start, end]."""
    out = []
    for state, category, s, e in intervals:
        e = end if e is None else e
        s2, e2 = max(s, start), min(e, end)
        if e2 > s2:
            out.append((state, category, s2, e2))
    return out


def compute_oee(intervals, good: int, scrap: int, ideal_rate_per_min: float,
                start: float, end: float) -> OEEResult:
    """intervals: lista (state, category, start_ts, end_ts|None),
    category ∈ {"run", "planned", "unplanned"}."""
    clipped = clip_intervals(intervals, start, end)
    run_s = sum(e - s for _, c, s, e in clipped if c == "run")
    planned_stop_s = sum(e - s for _, c, s, e in clipped if c == "planned")
    unplanned_s = sum(e - s for _, c, s, e in clipped if c == "unplanned")
    window_s = max(0.0, end - start)
    # Czas bez żadnego znanego stanu traktujemy jako postój nieplanowany
    # (np. brak komunikacji ze sterownikiem) – to konserwatywne podejście.
    unknown_s = max(0.0, window_s - run_s - planned_stop_s - unplanned_s)
    unplanned_s += unknown_s
    planned_s = max(0.0, window_s - planned_stop_s)

    total = good + scrap
    availability = run_s / planned_s if planned_s > 0 else 0.0
    ideal_rate_per_s = ideal_rate_per_min / 60.0
    if run_s > 0 and ideal_rate_per_s > 0:
        performance = min(1.0, (total / ideal_rate_per_s) / run_s)
    else:
        performance = 0.0
    quality = good / total if total > 0 else (1.0 if run_s == 0 else 0.0)
    oee = availability * performance * quality
    return OEEResult(window_s, planned_s, run_s, unplanned_s, planned_stop_s,
                     good, scrap, round(availability, 4), round(performance, 4),
                     round(quality, 4), round(oee, 4))
