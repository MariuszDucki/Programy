"""Serwer HTTP + WebSocket monitoringu produkcji."""
from __future__ import annotations

import asyncio
import csv
import io
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import Engine
from .simulator import Simulator

ROOT = Path(__file__).resolve().parent.parent
CONFIG = os.environ.get("CONFIG", str(ROOT / "config.json"))
DB_PATH = os.environ.get("DB_PATH", str(ROOT / "produkcja.db"))
SIMULATOR = os.environ.get("SIMULATOR", "1") == "1"
API_KEY = os.environ.get("API_KEY")  # jeśli ustawiony – wymagany przy zapisie danych z PLC

engine = Engine(CONFIG, DB_PATH)


async def _background():
    sim = Simulator(engine) if SIMULATOR else None
    n = 0
    while True:
        try:
            if sim:
                sim.tick(1.0)
            if n % 5 == 0:
                engine.evaluate_alarms()
        except Exception as exc:  # nie zabijaj pętli z powodu pojedynczego błędu
            print("background error:", exc)
        n += 1
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(_background())
    yield
    task.cancel()


app = FastAPI(title="Monitor Produkcji", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")


def _check_key(key: str | None):
    if API_KEY and key != API_KEY:
        raise HTTPException(401, "Nieprawidłowy klucz API")


def _line(line_id: str):
    if line_id not in engine.lines:
        raise HTTPException(404, f"Nie ma linii {line_id}")


class StateIn(BaseModel):
    state: str
    reason: str | None = None
    api_key: str | None = None


class CountIn(BaseModel):
    good: int = Field(0, ge=0)
    scrap: int = Field(0, ge=0)
    api_key: str | None = None


class ReasonIn(BaseModel):
    reason: str
    comment: str | None = None


class AckIn(BaseModel):
    user: str = "operator"


@app.get("/")
def index():
    return FileResponse(ROOT / "app" / "static" / "index.html")


@app.get("/api/config")
def config():
    c = engine.cfg
    return {"plant_name": c.get("plant_name"), "lines": c["lines"], "states": c["states"],
            "stop_reasons": c["stop_reasons"], "shifts": c["shifts"]}


@app.get("/api/snapshot")
def snapshot():
    return engine.snapshot()


@app.get("/api/lines/{line_id}")
def line_detail(line_id: str):
    _line(line_id)
    return engine.line_detail(line_id)


# ---- wejście danych z maszyn / PLC -------------------------------------
@app.post("/api/lines/{line_id}/state")
def post_state(line_id: str, body: StateIn):
    _check_key(body.api_key)
    _line(line_id)
    try:
        return {"id": engine.set_state(line_id, body.state, body.reason)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/lines/{line_id}/count")
def post_count(line_id: str, body: CountIn):
    _check_key(body.api_key)
    _line(line_id)
    engine.add_count(line_id, body.good, body.scrap)
    return {"ok": True}


# ---- operator ---------------------------------------------------------
@app.put("/api/stops/{stop_id}/reason")
def put_reason(stop_id: int, body: ReasonIn):
    engine.db.set_reason(stop_id, body.reason, body.comment)
    return {"ok": True}


@app.get("/api/alarms")
def alarms(active: bool = False):
    return engine.alarms(active_only=active)


@app.post("/api/alarms/{alarm_id}/ack")
def ack(alarm_id: int, body: AckIn):
    engine.ack_alarm(alarm_id, body.user)
    return {"ok": True}


# ---- raporty ----------------------------------------------------------
@app.get("/api/report")
def report(date: str | None = None, shift: str | None = None):
    if not date or not shift:
        name, s, _ = engine.shift_window()
        date, shift = datetime.fromtimestamp(s).strftime("%Y-%m-%d"), name
    try:
        return engine.shift_report(date, shift)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, f"Błędne parametry: {e}")


@app.get("/api/report.csv", response_class=PlainTextResponse)
def report_csv(date: str | None = None, shift: str | None = None):
    r = report(date, shift)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Data", "Zmiana", "Linia", "OEE", "Dostępność", "Wydajność", "Jakość",
                "Dobre", "Braki", "Praca [min]", "Postoje nieplan. [min]", "Postoje plan. [min]",
                "Główna przyczyna postojów"])
    for l in r["lines"]:
        k = l["kpi"]
        top = l["stop_pareto"][0][0] if l["stop_pareto"] else ""
        w.writerow([r["date"], r["shift"], l["name"], f"{k['oee']:.3f}", f"{k['availability']:.3f}",
                    f"{k['performance']:.3f}", f"{k['quality']:.3f}", k["good"], k["scrap"],
                    round(k["run_s"] / 60, 1), round(k["unplanned_stop_s"] / 60, 1),
                    round(k["planned_stop_s"] / 60, 1), top])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition":
                                      f'attachment; filename="raport_{r["date"]}_{r["shift"]}.csv"'})


# ---- WebSocket: push na żywo ------------------------------------------
@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    try:
        while True:
            await sock.send_json(engine.snapshot())
            await asyncio.sleep(2)
    except (WebSocketDisconnect, RuntimeError):
        pass
