from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Float, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = DATA_DIR / "neuroroute.sqlite3"
engine = create_engine(
    f"sqlite:///{DATABASE_PATH.as_posix()}",
    connect_args={"check_same_thread": False},
)


class Base(DeclarativeBase):
    pass


class QueueSample(Base):
    __tablename__ = "queue_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[float] = mapped_column(Float, index=True)
    elapsed_seconds: Mapped[float] = mapped_column(Float)
    switch_id: Mapped[str] = mapped_column(String(32), index=True)
    ls_occupancy: Mapped[float] = mapped_column(Float)
    ts_occupancy: Mapped[float] = mapped_column(Float)
    capacity: Mapped[float] = mapped_column(Float)


class SavedExperiment(Base):
    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    topology_id: Mapped[str] = mapped_column(String(32))
    topology_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    algorithms_json: Mapped[str] = mapped_column(Text)
    duration_seconds: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40))
    completed_at: Mapped[str] = mapped_column(String(40))
    results_json: Mapped[str] = mapped_column(Text)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "topologyId": self.topology_id,
            "topologyName": self.topology_name,
            "status": self.status,
            "algorithms": json.loads(self.algorithms_json),
            "durationSeconds": self.duration_seconds,
            "createdAt": self.created_at,
            "completedAt": self.completed_at,
            "results": json.loads(self.results_json),
        }


def initialize_storage() -> None:
    Base.metadata.create_all(engine)


def save_queue_samples(points: list[dict]) -> None:
    if not points:
        return
    with Session(engine) as session:
        session.add_all(
            [
                QueueSample(
                    timestamp=point["timestamp"],
                    elapsed_seconds=point["elapsedSeconds"],
                    switch_id=point["switchId"],
                    ls_occupancy=point["lsOccupancy"],
                    ts_occupancy=point["tsOccupancy"],
                    capacity=point["capacity"],
                )
                for point in points
            ]
        )
        session.commit()


def load_queue_samples(window_seconds: int) -> list[dict]:
    cutoff = time.time() - window_seconds
    with Session(engine) as session:
        samples = session.scalars(
            select(QueueSample)
            .where(QueueSample.timestamp >= cutoff)
            .order_by(QueueSample.timestamp, QueueSample.id)
        ).all()
        return [
            {
                "timestamp": sample.timestamp,
                "elapsedSeconds": sample.elapsed_seconds,
                "switchId": sample.switch_id,
                "lsOccupancy": sample.ls_occupancy,
                "tsOccupancy": sample.ts_occupancy,
                "capacity": sample.capacity,
            }
            for sample in samples
        ]


def load_experiments() -> list[dict]:
    with Session(engine) as session:
        records = session.scalars(
            select(SavedExperiment).order_by(SavedExperiment.created_at.desc())
        ).all()
        return [record.as_dict() for record in records]


def save_experiment(experiment: dict) -> None:
    with Session(engine) as session:
        session.add(
            SavedExperiment(
                id=experiment["id"],
                topology_id=experiment["topologyId"],
                topology_name=experiment["topologyName"],
                status=experiment["status"],
                algorithms_json=json.dumps(experiment["algorithms"]),
                duration_seconds=experiment["durationSeconds"],
                created_at=experiment["createdAt"],
                completed_at=experiment["completedAt"]
                or datetime.now(timezone.utc).isoformat(),
                results_json=json.dumps(experiment["results"]),
            )
        )
        session.commit()
