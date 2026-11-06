"""Silnik monitoringu: zmiany, stany linii, KPI na żywo, alarmy, raporty."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from .db import DB
from .oee import compute_oee


def _parse_hm(s: str):
    h, m = s.split(":")
    return int(h), int(m)


class Engine:
    def __init__(self, config_path: str | Path, db_path: str):
        self.cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
        self.db = DB(db_path)
        self.lines = {l["id"]: l for l in self.cfg["lines"]}
        self.states = self.cfg["states"]
        # Linie bez otwartego stanu startują jako "Brak zlecenia".
        for lid in self.lines:
            if not self.db.open_state(lid):
                self.db.change_state(lid, "NO_ORDER", "planned")

    # ------------------------------------------------------------ zmiany
    def shift_window(self, ts: float | None = None, date: str | None = None,
                     shift_name: str | None = None):
        """Zwraca (nazwa, start_ts, end_ts) zmiany trwającej w chwili ts
        albo wskazanej zmiany z danego dnia (date=YYYY-MM-DD)."""
        if date and shift_name:
            day = datetime.strptime(date, "%Y-%m-%d")
            for sh in self.cfg["shifts"]:
                if sh["name"] == shift_name:
                    return self._window_for(sh, day)
            raise KeyError(shift_name)
        now = datetime.fromtimestamp(ts or time.time())
        for day in (now, now - timedelta(days=1)):
            for sh in self.cfg["shifts"]:
                name, s, e = self._window_for(sh, day)
                if s <= now.timestamp() < e:
                    return name, s, e
        raise RuntimeError("Konfiguracja zmian nie pokrywa całej doby")

    @staticmethod
    def _window_for(sh, day: datetime):
        sh_h, sh_m = _parse_hm(sh["start"])
        eh, em = _parse_hm(sh["end"])
        start = day.replace(hour=sh_h, minute=sh_m, second=0, microsecond=0)
        end = day.replace(hour=eh, minute=em, second=0, microsecond=0)
        if end <= start:
            end += timedelta(days=1)
        return sh["name"], start.timestamp(), end.timestamp()

    # ------------------------------------------------------------ wejście danych
    def set_state(self, line_id: str, state: str, reason: str | None = None, ts=None):
        if line_id not in self.lines:
            raise KeyError(line_id)
        if state not in self.states:
            raise ValueError(f"Nieznany stan: {state}")
        cur = self.db.open_state(line_id)
        if cur and cur["state"] == state:
            return cur["id"]
        return self.db.change_state(line_id, state, self.states[state]["category"], reason, ts)

    def add_count(self, line_id: str, good: int = 0, scrap: int = 0, ts=None):
        if line_id not in self.lines:
            raise KeyError(line_id)
        if good < 0 or scrap < 0:
            raise ValueError("Liczniki muszą być nieujemne")
        self.db.add_counts(line_id, good, scrap, ts)

    # ------------------------------------------------------------ KPI
    def _intervals(self, line_id, start, end):
        return [(r["state"], r["category"], r["start_ts"], r["end_ts"])
                for r in self.db.intervals(line_id, start, end)]

    def line_kpi(self, line_id: str, start: float, end: float, now: float | None = None):
        now = now or time.time()
        line = self.lines[line_id]
        upto = min(end, now)
        good, scrap = self.db.sum_counts(line_id, start, upto)
        res = compute_oee(self._intervals(line_id, start, upto), good, scrap,
                          line["ideal_rate_per_min"], start, upto)
        return res

    def recent_rate(self, line_id: str, minutes: int, now=None):
        """Średnia prędkość [szt/min] i udział braków z ostatnich N pełnych minut."""
        now = now or time.time()
        end = int(now // 60 * 60)
        g, s = self.db.sum_counts(line_id, end - minutes * 60, end)
        total = g + s
        return total / minutes, (s / total if total else 0.0)

    def snapshot(self, now: float | None = None):
        now = now or time.time()
        shift, s, e = self.shift_window(now)
        out = []
        for lid, line in self.lines.items():
            st = self.db.open_state(lid)
            k = self.line_kpi(lid, s, e, now)
            speed, _ = self.recent_rate(lid, 1, now)
            elapsed = max(1.0, now - s)
            target_units = line["ideal_rate_per_min"] * line.get("target_oee", 0.75) * (e - s) / 60
            out.append({
                "id": lid, "name": line["name"], "product": line["product"],
                "ideal_rate_per_min": line["ideal_rate_per_min"],
                "target_oee": line.get("target_oee", 0.75),
                "state": st["state"], "state_label": self.states[st["state"]]["label"],
                "category": st["category"], "state_since": st["start_ts"],
                "state_id": st["id"], "reason": st["reason"],
                "speed_per_min": round(speed, 1),
                "good": k.good, "scrap": k.scrap,
                "availability": k.availability, "performance": k.performance,
                "quality": k.quality, "oee": k.oee,
                "target_units_shift": round(target_units),
                "expected_units_now": round(target_units * min(1.0, elapsed / (e - s))),
            })
        return {"ts": now, "shift": {"name": shift, "start": s, "end": e},
                "plant": self.cfg.get("plant_name", ""), "lines": out,
                "alarms": self.alarms(active_only=True)}

    def line_detail(self, line_id: str, now=None):
        now = now or time.time()
        shift, s, e = self.shift_window(now)
        rows = self.db.intervals(line_id, s, min(e, now))
        for r in rows:
            r["label"] = self.states[r["state"]]["label"]
            r["duration_s"] = (r["end_ts"] or now) - r["start_ts"]
        hourly = self.db.counts_by_hour(line_id, s, min(e, now))
        stops = [r for r in rows if r["category"] != "run"]
        return {"line": self.lines[line_id], "shift": {"name": shift, "start": s, "end": e},
                "now": now, "timeline": rows, "hourly": hourly,
                "stops": sorted(stops, key=lambda r: -r["start_ts"]),
                "kpi": self.line_kpi(line_id, s, e, now).as_dict()}

    # ------------------------------------------------------------ alarmy
    def evaluate_alarms(self, now: float | None = None):
        now = now or time.time()
        a = self.cfg["alarms"]
        for lid, line in self.lines.items():
            st = self.db.open_state(lid)
            dur_min = (now - st["start_ts"]) / 60
            self._alarm(lid, "LONG_STOP",
                        st["category"] == "unplanned" and dur_min >= a["unplanned_stop_minutes"],
                        f"{line['name']}: {self.states[st['state']]['label']} trwa "
                        f"{int(dur_min)} min", now)

            win = a["low_speed_window_minutes"]
            speed, _ = self.recent_rate(lid, win, now)
            running_long = st["state"] == "RUN" and (now - st["start_ts"]) >= (win + 1) * 60
            self._alarm(lid, "LOW_SPEED",
                        running_long and speed < a["low_speed_ratio"] * line["ideal_rate_per_min"],
                        f"{line['name']}: prędkość {speed:.0f}/min "
                        f"(< {int(a['low_speed_ratio'] * 100)}% nominału)", now)

            _, scrap_rate = self.recent_rate(lid, a["scrap_window_minutes"], now)
            self._alarm(lid, "HIGH_SCRAP", scrap_rate > a["scrap_rate_max"],
                        f"{line['name']}: braki {scrap_rate * 100:.1f}% "
                        f"(limit {a['scrap_rate_max'] * 100:.1f}%)", now)

    def _alarm(self, line_id, kind, active: bool, msg: str, now):
        cur = self.db.active_alarm(line_id, kind)
        if active and not cur:
            self.db.x("INSERT INTO alarms(line_id,kind,message,raised_ts) VALUES (?,?,?,?)",
                      (line_id, kind, msg, now))
        elif active and cur:
            self.db.x("UPDATE alarms SET message=? WHERE id=?", (msg, cur["id"]))
        elif not active and cur:
            self.db.x("UPDATE alarms SET cleared_ts=? WHERE id=?", (now, cur["id"]))

    def alarms(self, active_only=False, limit=100):
        if active_only:
            return self.db.q("SELECT * FROM alarms WHERE cleared_ts IS NULL ORDER BY raised_ts DESC")
        return self.db.q("SELECT * FROM alarms ORDER BY raised_ts DESC LIMIT ?", (limit,))

    def ack_alarm(self, alarm_id: int, user: str = "operator"):
        self.db.x("UPDATE alarms SET acked_ts=?, acked_by=? WHERE id=?",
                  (time.time(), user, alarm_id))

    # ------------------------------------------------------------ raport zmianowy
    def shift_report(self, date: str, shift_name: str, now=None):
        now = now or time.time()
        name, s, e = self.shift_window(date=date, shift_name=shift_name)
        lines = []
        for lid, line in self.lines.items():
            k = self.line_kpi(lid, s, e, now)
            rows = self.db.intervals(lid, s, min(e, now))
            pareto: dict[str, float] = {}
            for r in rows:
                if r["category"] == "unplanned":
                    rs, re_ = max(r["start_ts"], s), min(r["end_ts"] or now, e)
                    key = r["reason"] or f"(nieprzypisane) {self.states[r['state']]['label']}"
                    pareto[key] = pareto.get(key, 0) + max(0, re_ - rs)
            lines.append({"id": lid, "name": line["name"], "kpi": k.as_dict(),
                          "stop_pareto": sorted(([k_, round(v / 60, 1)] for k_, v in pareto.items()),
                                                key=lambda x: -x[1])})
        return {"date": date, "shift": name, "start": s, "end": e, "lines": lines}
