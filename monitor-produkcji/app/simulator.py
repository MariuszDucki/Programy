"""Symulator linii produkcyjnych – generuje realistyczne dane do demo i testów.

W produkcji wyłącz go (SIMULATOR=0) i zasilaj system przez REST API
z bramki PLC (OPC UA / Modbus / MQTT → HTTP, np. Node-RED).
"""
from __future__ import annotations

import random
import time

from .engine import Engine

UNPLANNED = [("STOP", 0.55), ("STARVED", 0.2), ("BLOCKED", 0.15), ("CHANGEOVER", 0.1)]


class Simulator:
    def __init__(self, engine: Engine, seed: int | None = None):
        self.e = engine
        self.rnd = random.Random(seed)
        self.carry = {lid: 0.0 for lid in engine.lines}
        self.until = {lid: 0.0 for lid in engine.lines}
        self.health = {lid: self.rnd.uniform(0.85, 0.98) for lid in engine.lines}

    def _pick(self, choices):
        r, acc = self.rnd.random(), 0.0
        for v, p in choices:
            acc += p
            if r <= acc:
                return v
        return choices[-1][0]

    def tick(self, dt: float = 1.0, now: float | None = None):
        now = now or time.time()
        for lid, line in self.e.lines.items():
            st = self.e.db.open_state(lid)["state"]
            if now >= self.until[lid]:
                if st == "RUN":
                    # średnio kilka postojów na zmianę
                    if self.rnd.random() < 0.15:
                        nxt = "CIP" if self.rnd.random() < 0.08 else self._pick(UNPLANNED)
                        reason = None
                        if nxt == "STOP" and self.rnd.random() < 0.4:
                            reason = self.rnd.choice(self.e.cfg["stop_reasons"])
                        self.e.set_state(lid, nxt, reason, now)
                        dur = {"CIP": (900, 1800), "CHANGEOVER": (300, 900)}.get(nxt, (60, 480))
                        self.until[lid] = now + self.rnd.uniform(*dur)
                    else:
                        self.until[lid] = now + self.rnd.uniform(120, 600)
                else:
                    self.e.set_state(lid, "RUN", None, now)
                    self.until[lid] = now + self.rnd.uniform(300, 1500)
                    self.health[lid] = min(0.99, max(0.7, self.health[lid] + self.rnd.uniform(-0.05, 0.05)))
                st = self.e.db.open_state(lid)["state"]

            if st == "RUN":
                rate_s = line["ideal_rate_per_min"] / 60 * self.health[lid] * self.rnd.uniform(0.95, 1.02)
                self.carry[lid] += rate_s * dt
                n = int(self.carry[lid])
                self.carry[lid] -= n
                if n:
                    scrap = sum(1 for _ in range(n) if self.rnd.random() < 0.012)
                    self.e.add_count(lid, n - scrap, scrap, now)


def backfill(engine: Engine, start: float, end: float, seed: int | None = 1):
    """Wypełnia bazę danymi historycznymi (do demo) – symulacja krok 5 s."""
    # stany startowe utworzone "teraz" przesuwamy na początek historii
    engine.db.x("UPDATE state_log SET start_ts=? WHERE end_ts IS NULL AND start_ts>?", (start, start))
    sim = Simulator(engine, seed)
    t = start
    while t < end:
        sim.tick(5.0, t)
        t += 5.0
    return sim


if __name__ == "__main__":
    import argparse
    import os
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description="Wypełnij bazę danymi demo")
    p.add_argument("--hours", type=float, default=6)
    p.add_argument("--db", default=os.environ.get("DB_PATH", str(root / "produkcja.db")))
    a = p.parse_args()
    e = Engine(root / "config.json", a.db)
    now = time.time()
    backfill(e, now - a.hours * 3600, now)
    e.evaluate_alarms(now)
    print(f"Wypełniono {a.hours} h danych w {a.db}")
