"""The HTTP API (FastAPI) the web GUI talks to. See PLAN.md for the endpoint
table. `/openapi.json` is the contract handed to the front end.

The web GUI (`static/`) is served at `/` without a token.

Every `/api` route requires the shared token, since the API can move valves.
Send it as `Authorization: Bearer <token>`; the SSE stream also accepts a
`?token=` query parameter because browser `EventSource` cannot set headers.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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
    fault: Optional[str] = Field(None, description="reason the failsafe is active, else null")


class HistoryPointModel(BaseModel):
    timestamp: float = Field(description="unix time, seconds")
    state: StateModel


class ProfileModel(BaseModel):
    name: str
    setpoint_f: float
    pid: PidGainsModel
    output_limits: List[float]


class ProfilesModel(BaseModel):
    active: str
    profiles: List[ProfileModel]


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

    app = FastAPI(title="Still Controller", version="0.1.0")
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
        limit: int = Query(7200, ge=0, le=100000, description="at most this many, newest kept"),
    ) -> List[HistoryPointModel]:
        return [history_model(p) for p in loop.history(since=since, limit=limit)]

    @api.get("/profiles", response_model=ProfilesModel)
    def get_profiles() -> ProfilesModel:
        return ProfilesModel(
            active=controller.state().profile,
            profiles=[
                ProfileModel(
                    name=name,
                    setpoint_f=p.setpoint_f,
                    pid=PidGainsModel(p=p.pid.p, i=p.pid.i, d=p.pid.d),
                    output_limits=list(p.output_limits),
                )
                for name, p in config.profiles.items()
            ],
        )

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
