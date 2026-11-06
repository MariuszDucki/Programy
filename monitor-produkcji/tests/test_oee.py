import time

from app.engine import Engine
from app.oee import compute_oee
from pathlib import Path

CFG = Path(__file__).resolve().parent.parent / "config.json"


def test_oee_basic():
    # 8h okno, 30 min CIP (planowany), 60 min awarii, reszta praca
    start, end = 0, 8 * 3600
    iv = [("RUN", "run", 0, 3600 * 3), ("STOP", "unplanned", 3600 * 3, 3600 * 4),
          ("CIP", "planned", 3600 * 4, 3600 * 4.5), ("RUN", "run", 3600 * 4.5, None)]
    run_s = 3 * 3600 + 3.5 * 3600
    ideal = 100  # szt/min
    total = int(run_s / 60 * ideal * 0.9)
    r = compute_oee(iv, total - 100, 100, ideal, start, end)
    assert abs(r.availability - run_s / (7.5 * 3600)) < 1e-3
    assert abs(r.performance - 0.9) < 1e-3
    assert abs(r.quality - (total - 100) / total) < 1e-3
    assert abs(r.oee - r.availability * r.performance * r.quality) < 1e-3


def test_unknown_time_counts_as_unplanned():
    r = compute_oee([("RUN", "run", 3600, None)], 0, 0, 60, 0, 7200)
    assert r.unplanned_stop_s == 3600
    assert r.availability == 0.5


def test_engine_flow(tmp_path):
    e = Engine(CFG, str(tmp_path / "t.db"))
    _, s, _ = e.shift_window()
    t0 = max(s, time.time() - 600)
    e.set_state("L1", "RUN", ts=t0)
    e.add_count("L1", 1000, 10, ts=t0 + 60)
    e.set_state("L1", "STOP", ts=t0 + 300)
    snap = e.snapshot(now=t0 + 400)
    l1 = next(l for l in snap["lines"] if l["id"] == "L1")
    assert l1["state"] == "STOP" and l1["good"] == 1000 and l1["scrap"] == 10
    det = e.line_detail("L1", now=t0 + 400)
    stop = det["stops"][0]
    e.db.set_reason(stop["id"], "Awaria mechaniczna")
    assert e.db.open_state("L1")["reason"] == "Awaria mechaniczna"


def test_long_stop_alarm(tmp_path):
    e = Engine(CFG, str(tmp_path / "t.db"))
    now = time.time()
    e.set_state("L2", "STOP", ts=now - 10 * 60)
    e.evaluate_alarms(now)
    kinds = {(a["line_id"], a["kind"]) for a in e.alarms(active_only=True)}
    assert ("L2", "LONG_STOP") in kinds
    e.set_state("L2", "RUN", ts=now)
    e.evaluate_alarms(now + 1)
    kinds = {(a["line_id"], a["kind"]) for a in e.alarms(active_only=True)}
    assert ("L2", "LONG_STOP") not in kinds
