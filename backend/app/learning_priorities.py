"""Workspace study preferences; never repertoire-selection probabilities."""
from dataclasses import dataclass
import hashlib
import json
import math

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .card_deletion import SYSTEM_REPERTOIRE_IDS


class LearningPriorityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    learning_priority_weight: float = Field(ge=0, allow_inf_nan=False, strict=True)
    learning_priority_active: bool = Field(strict=True)
    expected_revision: int = Field(ge=1, strict=True)

    @field_validator("learning_priority_weight", mode="before")
    @classmethod
    def validate_weight(cls, value):
        if type(value) not in (int, float):
            raise ValueError("Weight must be a finite nonnegative JSON number")
        try:
            weight = float(value)
        except (OverflowError, ValueError) as error:
            raise ValueError("Weight must be finite") from error
        if not math.isfinite(weight) or weight < 0:
            raise ValueError("Weight must be finite and nonnegative")
        return 0.0 if weight == 0 else weight


@dataclass(frozen=True, slots=True)
class PolicyLearningPriority:
    policy_id: str
    learning_priority_weight: float
    learning_priority_active: bool
    revision: int

    def document(self) -> dict:
        return {"policy_id": self.policy_id,
                "learning_priority_weight": self.learning_priority_weight,
                "learning_priority_active": self.learning_priority_active,
                "revision": self.revision}


@dataclass(frozen=True, slots=True)
class LearningPrioritySnapshot:
    policies: tuple[PolicyLearningPriority, ...]
    schema_version: int = 1

    @property
    def snapshot_revision(self) -> str:
        serialized = json.dumps({"schema_version": self.schema_version,
                                 "policies": [policy.document() for policy in self.policies]},
                                sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def document(self) -> dict:
        return {"schema_version": self.schema_version,
                "snapshot_revision": self.snapshot_revision,
                "policies": [policy.document() for policy in self.policies]}


def _policy_from_row(row) -> PolicyLearningPriority:
    # Stored configuration is authoritative. Corruption must never become a neutral default.
    weight = float(row["learning_priority_weight"])
    active = row["learning_priority_active"]
    revision = int(row["learning_priority_revision"])
    if not math.isfinite(weight) or weight < 0 or active not in (0, 1) or revision < 1:
        raise HTTPException(503, "Learning priorities are invalid; restore valid repertoire settings before retrying")
    return PolicyLearningPriority(row["id"], 0.0 if weight == 0 else weight, bool(active), revision)


def read_learning_priority_snapshot(database) -> LearningPrioritySnapshot:
    placeholders = ",".join("?" for _ in SYSTEM_REPERTOIRE_IDS)
    rows = database.execute(
        "SELECT id,learning_priority_weight,learning_priority_active,learning_priority_revision "
        f"FROM repertoires WHERE id NOT IN ({placeholders}) ORDER BY id",
        SYSTEM_REPERTOIRE_IDS,
    ).fetchall()
    return LearningPrioritySnapshot(tuple(_policy_from_row(row) for row in rows))


def update_learning_priority(database, policy_id: str, request: LearningPriorityUpdate, *, postgres: bool) -> dict:
    if policy_id in SYSTEM_REPERTOIRE_IDS:
        raise HTTPException(404, "Repertoire policy not found")
    row = database.execute(
        "SELECT id,learning_priority_weight,learning_priority_active,learning_priority_revision "
        "FROM repertoires WHERE id=?" + (" FOR UPDATE" if postgres else ""), (policy_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Repertoire policy not found")
    previous = _policy_from_row(row)
    if previous.revision != request.expected_revision:
        raise HTTPException(409, {"code": "stale_learning_priority_revision",
                                  "message": "Learning priority changed; read current settings before retrying",
                                  "current_revision": previous.revision})
    if (previous.learning_priority_weight == request.learning_priority_weight
            and previous.learning_priority_active == request.learning_priority_active):
        return previous.document()
    database.execute(
        "UPDATE repertoires SET learning_priority_weight=?,learning_priority_active=?,"
        "learning_priority_revision=learning_priority_revision+1 WHERE id=?",
        (request.learning_priority_weight, int(request.learning_priority_active), policy_id),
    )
    return PolicyLearningPriority(policy_id, request.learning_priority_weight,
                                  request.learning_priority_active, previous.revision + 1).document()
