from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from .simulation import ALGORITHM_CATALOG, AVAILABLE_ALGORITHMS, SimulationManager, simulation
from .storage import (
    initialize_storage,
    load_experiments,
    load_queue_samples,
    save_experiment,
)
from .topologies import TOPOLOGIES

app = FastAPI(
    title="NeuroRoute-SDN API",
    description="Windows-native SimPy SDN research simulator API.",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
api = APIRouter(prefix="/api")


class FailureScheduleInput(BaseModel):
    linkId: str
    downSeconds: float = Field(ge=0)
    upSeconds: float = Field(gt=0)


class SimulationStartInput(BaseModel):
    topologyId: str
    algorithm: str | None = None
    durationSeconds: int = Field(ge=10, le=3600)
    seed: int
    lsRate: float = Field(ge=0, le=100)
    tsRate: float = Field(ge=0, le=100)
    speedMode: Literal["fast", "realtime"] = "realtime"
    failureSchedule: FailureScheduleInput | None = None


class ExperimentInput(BaseModel):
    topologyId: str
    durationSeconds: int = Field(ge=10, le=3600)
    algorithms: list[str] = Field(min_length=1, max_length=8)
    seed: int


class QueueRateInput(BaseModel):
    switchId: str
    lsRatePercent: float = Field(ge=0, le=100)


class ModeInput(BaseModel):
    mode: str


class FlowRuleInput(BaseModel):
    switchId: str
    id: str | None = None
    match: dict
    actions: dict


@app.on_event("startup")
def startup() -> None:
    initialize_storage()


@api.get("/healthz")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@api.get("/topologies")
def get_topologies() -> list[dict]:
    return simulation.topologies()


@api.get("/algorithms")
def get_algorithms() -> list[dict]:
    return [dict(algorithm) for algorithm in ALGORITHM_CATALOG]


@api.get("/mode")
def get_mode() -> dict:
    return simulation.mode()


@api.post("/mode")
def set_mode(config: ModeInput) -> dict:
    try:
        return simulation.set_mode(config.mode)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@api.get("/simulation")
def get_simulation() -> dict:
    return simulation.snapshot()


@api.post("/simulation/start")
def start_simulation(config: SimulationStartInput) -> dict:
    if config.topologyId not in TOPOLOGIES:
        raise HTTPException(status_code=400, detail="Unknown topology.")
    algorithm = config.algorithm or simulation.mode()["mode"]
    if algorithm not in AVAILABLE_ALGORITHMS:
        raise HTTPException(
            status_code=400,
            detail="This algorithm is not available in the current simulator build.",
        )
    try:
        return simulation.start(
            config.topologyId,
            algorithm,
            config.durationSeconds,
            config.seed,
            config.lsRate,
            config.tsRate,
            config.speedMode,
            config.failureSchedule.model_dump() if config.failureSchedule else None,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@api.post("/simulation/stop")
def stop_simulation() -> dict:
    return simulation.stop()


@api.post("/simulation/reset")
def reset_simulation() -> dict:
    return simulation.reset()


@api.get("/metrics")
def get_queue_metrics(
    window_seconds: int = Query(120, alias="windowSeconds", ge=10, le=600),
) -> list[dict]:
    live_points = simulation.metrics(window_seconds)
    return live_points if live_points else load_queue_samples(window_seconds)


@api.get("/controller/state")
def get_controller_state() -> dict:
    return simulation.controller_state()


@api.post("/controller/queue-rates")
def install_queue_rate(config: QueueRateInput) -> dict:
    try:
        return simulation.install_queue_rate(
            config.switchId, config.lsRatePercent / 100
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@api.post("/controller/flow-rules")
def install_flow_rule(config: FlowRuleInput) -> dict:
    try:
        return simulation.install_flow_rule(
            config.switchId,
            {
                "id": config.id,
                "match": config.match,
                "actions": config.actions,
            },
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@api.get("/experiments")
def get_experiments() -> list[dict]:
    return load_experiments()


@api.post("/experiments/run")
async def run_experiment(config: ExperimentInput) -> dict:
    if config.topologyId not in TOPOLOGIES:
        raise HTTPException(status_code=400, detail="Unknown topology.")
    unavailable = [
        algorithm
        for algorithm in config.algorithms
        if algorithm not in AVAILABLE_ALGORITHMS
    ]
    if unavailable:
        raise HTTPException(
            status_code=400,
            detail=f"Unavailable algorithms: {', '.join(unavailable)}",
        )

    async def run_comparison() -> dict:
        results = []
        for offset, algorithm in enumerate(config.algorithms):
            results.append(
                await asyncio.to_thread(
                    SimulationManager.run_offline,
                    config.topologyId,
                    algorithm,
                    config.durationSeconds,
                    config.seed + offset,
                )
            )
        now = datetime.now(timezone.utc).isoformat()
        return {
            "id": str(uuid.uuid4()),
            "topologyId": config.topologyId,
            "topologyName": TOPOLOGIES[config.topologyId].name,
            "status": "completed",
            "algorithms": config.algorithms,
            "durationSeconds": config.durationSeconds,
            "createdAt": now,
            "completedAt": now,
            "results": results,
        }

    experiment = await run_comparison()
    await asyncio.to_thread(save_experiment, experiment)
    return experiment


app.include_router(api)


@app.websocket("/api/ws/simulation")
async def simulation_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(
                {
                    "state": simulation.snapshot(),
                    "metrics": simulation.metrics(120),
                }
            )
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        return

