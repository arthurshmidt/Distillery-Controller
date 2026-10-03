"""The HTTP API (FastAPI) the web GUI talks to. See PLAN.md for the endpoint
table. `/openapi.json` is the contract handed to the front end.

The web GUI (`static/`) is served at `/` without a token.

Every `/api` route requires the shared token, since the API can move valves.
Send it as `Authorization: Bearer <token>`; the SSE stream also accepts a
`?token=` query parameter because browser `EventSource` cannot set headers.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import secrets
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import AsyncIterator, Dict, Iterator, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from . import __version__
from .controller import Controller, ControllerState, Mode
from .config import AppConfig
from .hardware.base import VALVE_NAMES
from .runner import ControlLoop
from .store import HistoryPoint

STREAM_INTERVAL_S = 1.0
STATIC_DIR = Path(__file__).parent / "static"


# -- response / request models ---------------------------------------------


class TemperaturesModel(BaseModel):
    deph_supply: float
    deph_return: float
    cond_supply: float
    cond_return: float


class PidGainsModel(BaseModel):
    p: float
    i: float
    d: float


class PidTermsModel(BaseModel):
    p: float
    i: float
    d: float


class StateModel(BaseModel):
    mode: Mode
    profile: str
    setpoint_f: float
    temps_f: Optional[TemperaturesModel] = Field(None, description="null until the first reading")
    valves_pct: Dict[str, float] = Field(
        description="dephlegmator, condenser and supply; supply is absent until it is first commanded"
    )
    pid_terms: PidTermsModel
    pid_gains: PidGainsModel
    supply_setpoint_f: float = Field(description="supply (city water) valve loop setpoint")
    supply_pid_terms: PidTermsModel
    supply_pid_gains: PidGainsModel
    output_limits: List[float] = Field(description="[min, max] percent the dephlegmator PID may command; history points report 0-100 because limits are not logged")
    supply_output_limits: List[float] = Field(description="[min, max] percent the supply PID may command")
    fault: Optional[str] = Field(None, description="reason the failsafe is active, else null")


class HistoryPointModel(BaseModel):
    timestamp: float = Field(description="unix time, seconds")
    state: StateModel


class EventModel(BaseModel):
    timestamp: float
    kind: str = Field(description="mode, profile, setpoint, fault or log")
    text: str
    tone: Optional[str] = Field(None, description="amber for a move away from auto, red for a fault, else null")


class SegmentModel(BaseModel):
    start: float
    end: float
    kind: str = Field(description="auto, manual, off or fault (a fault wins over the mode)")


class EventsModel(BaseModel):
    events: List[EventModel] = Field(description="oldest first")
    segments: List[SegmentModel] = Field(description="timeline of mode and faults; stretches where nothing was logged are left out")


CSV_HEADER = [
    "timestamp", "time_utc", "mode", "profile", "dephlegmator_setpoint_f",
    "deph_supply_f", "deph_return_f", "cond_supply_f", "cond_return_f",
    "dephlegmator_valve_pct", "condenser_valve_pct", "supply_valve_pct", "supply_setpoint_f", "fault",
]  # fmt: skip


def _csv_line(values) -> str:
    out = io.StringIO()
    csv.writer(out, lineterminator="\r\n").writerow(values)
    return out.getvalue()


class SavedModel(BaseModel):
    setpoint_f: float
    pid: PidGainsModel
    output_limits: List[float]


class ProfileModel(BaseModel):
    name: str
    setpoint_f: float = Field(description="default from still.yaml")
    pid: PidGainsModel = Field(description="default from still.yaml")
    output_limits: List[float] = Field(description="default from still.yaml")
    saved: SavedModel = Field(description="what the profile uses now: defaults with saved changes on top")


class SupplyLoopModel(SavedModel):
    saved: SavedModel = Field(description="what the supply loop uses now")


class ProfilesModel(BaseModel):
    active: str
    profiles: List[ProfileModel]
    supply: SupplyLoopModel = Field(description="the shared supply loop; fields are the still.yaml defaults")


class ThermistorModel(BaseModel):
    r_fixed: float
    beta: float
    adc_max: float
    calibration_factor: float


class ChannelsModel(BaseModel):
    ai: Dict[str, int]
    ao: Dict[str, int]


class InfoModel(BaseModel):
    version: str
    hardware: str = Field(description="simulated or widgetlords")
    interval_s: float = Field(description="control loop period")
    retention_days: float = Field(description="how long history is kept")
    default_profile: str
    startup_mode: str = Field(description="the mode the daemon starts in; the mode is never saved")
    thermistor: ThermistorModel
    channels: ChannelsModel


class ProfileRequest(BaseModel):
    name: str


class SetpointRequest(BaseModel):
    setpoint_f: float = Field(ge=-40, le=300)


class ModeRequest(BaseModel):
    mode: Mode


class ValveRequest(BaseModel):
    percent: float = Field(ge=0, le=100)


class PidRequest(PidGainsModel):
    pass


class OutputLimitsRequest(BaseModel):
    min: float = Field(ge=0, le=100)
    max: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _ordered(self) -> "OutputLimitsRequest":
        if self.min >= self.max:
            raise ValueError("min must be below max")
        return self


def state_model(s: ControllerState) -> StateModel:
    return StateModel(
        mode=s.mode,
        profile=s.profile,
        setpoint_f=s.setpoint_f,
        temps_f=TemperaturesModel(**vars(s.temps_f)) if s.temps_f is not None else None,
        valves_pct=s.valves_pct,
        pid_terms=PidTermsModel(p=s.pid_terms[0], i=s.pid_terms[1], d=s.pid_terms[2]),
        pid_gains=PidGainsModel(p=s.pid_gains[0], i=s.pid_gains[1], d=s.pid_gains[2]),
        supply_setpoint_f=s.supply_setpoint_f,
        supply_pid_terms=PidTermsModel(
            p=s.supply_pid_terms[0], i=s.supply_pid_terms[1], d=s.supply_pid_terms[2]
        ),
        supply_pid_gains=PidGainsModel(
            p=s.supply_pid_gains[0], i=s.supply_pid_gains[1], d=s.supply_pid_gains[2]
        ),
        output_limits=list(s.output_limits),
        supply_output_limits=list(s.supply_output_limits),
        fault=s.fault,
    )


def history_model(p: HistoryPoint) -> HistoryPointModel:
    return HistoryPointModel(timestamp=p.timestamp, state=state_model(p.state))


# -- app ----------------------------------------------------------------------


def create_app(
    controller: Controller,
    loop: ControlLoop,
    config: AppConfig,
    token: str,
) -> FastAPI:
    """Build the API. Starting and stopping `loop` is the caller's job."""

    def check_token(request: Request, token_q: Optional[str] = Query(None, alias="token")) -> None:
        header = request.headers.get("authorization", "")
        supplied = header[7:] if header.lower().startswith("bearer ") else token_q
        if supplied is None or not secrets.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(401, "missing or invalid token", headers={"WWW-Authenticate": "Bearer"})

    try:
        version = metadata.version("still-controller")
    except metadata.PackageNotFoundError:  # running from a source tree that was never installed
        version = __version__
    app = FastAPI(title="Still Controller", version=version)
    api = APIRouter(prefix="/api", dependencies=[Depends(check_token)])

    @api.get("/state", response_model=StateModel)
    def get_state() -> StateModel:
        return state_model(controller.state())

    @api.get("/stream")
    async def stream(request: Request) -> StreamingResponse:
        """Server-sent events: the full state, once a second."""

        async def events() -> AsyncIterator[str]:
            while not await request.is_disconnected():
                yield f"data: {state_model(controller.state()).model_dump_json()}\n\n"
                await asyncio.sleep(STREAM_INTERVAL_S)

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @api.get("/history", response_model=List[HistoryPointModel])
    def get_history(
        since: Optional[float] = Query(None, description="only points after this unix time"),
        until: Optional[float] = Query(None, description="only points at or before this unix time"),
        step: Optional[float] = Query(
            None, gt=0, description="seconds; at most one point per step-second bucket (the earliest in it)"
        ),
        limit: int = Query(7200, ge=0, le=100000, description="at most this many, newest kept"),
    ) -> List[HistoryPointModel]:
        return [history_model(p) for p in loop.history(since=since, limit=limit, until=until, step=step)]

    @api.get("/events", response_model=EventsModel)
    def get_events(
        since: Optional[float] = Query(None, description="unix time, inclusive"),
        until: Optional[float] = Query(None, description="unix time, inclusive"),
    ) -> EventsModel:
        """Mode, profile, setpoint and fault changes and logging gaps, found in the
        full-resolution rows (thinning in /history does not hide them), plus the
        mode/fault segments for the timeline."""
        events, segments = loop.events(since=since, until=until)
        return EventsModel(events=events, segments=segments)

    @api.get("/history.csv")
    def get_history_csv(
        since: Optional[float] = Query(None, description="unix time, inclusive"),
        until: Optional[float] = Query(None, description="unix time, inclusive"),
    ) -> StreamingResponse:
        """Every logged row in the window, full resolution. Send the token in the
        Authorization header; a plain link cannot."""

        def lines() -> Iterator[str]:
            yield _csv_line(CSV_HEADER)
            for r in loop.export_rows(since=since, until=until):
                t = datetime.fromtimestamp(r[0], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                yield _csv_line([f"{r[0]:.3f}", t, *["" if v is None else v for v in r[1:]]])

        return StreamingResponse(
            lines(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="still-history.csv"'},
        )

    @api.get("/profiles", response_model=ProfilesModel)
    def get_profiles() -> ProfilesModel:
        sup = config.supply
        return ProfilesModel(
            active=controller.state().profile,
            profiles=[
                ProfileModel(
                    name=name,
                    setpoint_f=p.setpoint_f,
                    pid=PidGainsModel(p=p.pid.p, i=p.pid.i, d=p.pid.d),
                    output_limits=list(p.output_limits),
                    saved=SavedModel(**controller.saved_profile(name)),
                )
                for name, p in config.profiles.items()
            ],
            supply=SupplyLoopModel(
                setpoint_f=sup.setpoint_f,
                pid=PidGainsModel(p=sup.pid.p, i=sup.pid.i, d=sup.pid.d),
                output_limits=list(sup.output_limits),
                saved=SavedModel(**controller.saved_supply()),
            ),
        )

    @api.get("/info", response_model=InfoModel)
    def get_info() -> InfoModel:
        t = config.thermistor
        return InfoModel(
            version=version,
            hardware=controller.hardware_kind,
            interval_s=loop.interval_s,
            retention_days=loop.retention_days,
            default_profile=config.default_profile,
            startup_mode=Mode.AUTO.value,
            thermistor=ThermistorModel(
                r_fixed=t.r_fixed, beta=t.beta, adc_max=t.adc_max, calibration_factor=t.calibration_factor
            ),
            channels=ChannelsModel(ai=dict(config.channels.ai), ao=dict(config.channels.ao)),
        )

    @api.put("/profiles/{name}/setpoint", response_model=StateModel)
    def put_profile_setpoint(name: str, body: SetpointRequest) -> StateModel:
        """Save a profile's setpoint, whether or not it is the active one."""
        if name not in config.profiles:
            raise HTTPException(404, f"unknown profile {name!r}")
        controller.set_profile_setpoint(name, body.setpoint_f)
        return state_model(controller.state())

    @api.delete("/profiles/{name}/overrides", response_model=StateModel)
    def delete_profile_overrides(name: str) -> StateModel:
        """Forget a profile's saved setpoint, gains and limits (back to still.yaml)."""
        if name not in config.profiles:
            raise HTTPException(404, f"unknown profile {name!r}")
        controller.reset_profile(name)
        return state_model(controller.state())

    @api.delete("/supply/overrides", response_model=StateModel)
    def delete_supply_overrides() -> StateModel:
        controller.reset_supply()
        return state_model(controller.state())

    @api.put("/profile", response_model=StateModel)
    def put_profile(body: ProfileRequest) -> StateModel:
        if body.name not in config.profiles:
            raise HTTPException(404, f"unknown profile {body.name!r}")
        controller.set_profile(body.name)
        return state_model(controller.state())

    @api.put("/setpoint", response_model=StateModel)
    def put_setpoint(body: SetpointRequest) -> StateModel:
        controller.set_setpoint(body.setpoint_f)
        return state_model(controller.state())

    @api.put("/mode", response_model=StateModel)
    def put_mode(body: ModeRequest) -> StateModel:
        controller.set_mode(body.mode)
        return state_model(controller.state())

    @api.put("/valves/{name}", response_model=StateModel)
    def put_valve(name: str, body: ValveRequest) -> StateModel:
        if name not in VALVE_NAMES:
            raise HTTPException(404, f"unknown valve {name!r}")
        try:
            controller.set_manual_valve(name, body.percent)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return state_model(controller.state())

    @api.put("/pid", response_model=StateModel)
    def put_pid(body: PidRequest) -> StateModel:
        controller.set_pid_gains(body.p, body.i, body.d)
        return state_model(controller.state())

    @api.put("/output-limits", response_model=StateModel)
    def put_output_limits(body: OutputLimitsRequest) -> StateModel:
        """Limits for the active profile's dephlegmator PID. Manual moves are not limited."""
        controller.set_output_limits(body.min, body.max)
        return state_model(controller.state())

    @api.put("/supply/output-limits", response_model=StateModel)
    def put_supply_output_limits(body: OutputLimitsRequest) -> StateModel:
        controller.set_supply_output_limits(body.min, body.max)
        return state_model(controller.state())

    @api.put("/supply/setpoint", response_model=StateModel)
    def put_supply_setpoint(body: SetpointRequest) -> StateModel:
        controller.set_supply_setpoint(body.setpoint_f)
        return state_model(controller.state())

    @api.put("/supply/pid", response_model=StateModel)
    def put_supply_pid(body: PidRequest) -> StateModel:
        controller.set_supply_pid_gains(body.p, body.i, body.d)
        return state_model(controller.state())

    app.include_router(api)
    # The web GUI. Unauthenticated so the login screen can load; only /api is protected.
    # Mounted last so it never shadows /api, /docs or /openapi.json.
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
