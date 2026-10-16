"""Strict, structured authored input for Content's independent v1 boundary.

These models validate shape and references. Chess legality belongs to the
restartable domain validation steps, after database connections have closed.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from tempo_content_publication_contracts.v1 import ExecutableExercise


SourceKey = Annotated[str, Field(min_length=1, max_length=200)]
Title = Annotated[str, Field(min_length=1, max_length=2000)]
UciMove = Annotated[str, Field(pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$")]
# Reuse the accepted contract's complete discriminated specification type.
Specification = ExecutableExercise.model_fields["specification"].rebuild_annotation()


class SourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    @model_validator(mode='before')
    @classmethod
    def strict_version(cls,value):
        if isinstance(value,dict) and 'schema_version' in value and type(value['schema_version']) is not int:
            raise ValueError('schema_version must be an integer')
        return value


class SourceRepertoire(SourceModel):
    key: SourceKey
    title: Title
    learner_color: Literal["white", "black"]


class SourcePolicy(SourceModel):
    key: SourceKey
    repertoire_key: SourceKey


class SourceRoute(SourceModel):
    key: SourceKey
    starting_fen: Annotated[str, Field(min_length=1, max_length=120)]
    moves: list[UciMove] = Field(default_factory=list, max_length=1000)


class RouteMembership(SourceModel):
    repertoire_key: SourceKey
    policy_key: SourceKey
    route_key: SourceKey


class SourceExercise(SourceModel):
    key: SourceKey
    decision_key: SourceKey
    policy_key: SourceKey
    starting_fen: Annotated[str, Field(min_length=1, max_length=120)]
    prompt: Annotated[str, Field(min_length=1, max_length=2000)]
    specification: Specification
    supersedes_key: SourceKey | None = None

    @model_validator(mode="after")
    def distinct_supersession(self):
        if self.supersedes_key == self.key:
            raise ValueError("exercise cannot supersede itself")
        return self


class ExerciseBinding(SourceModel):
    route_key: SourceKey
    policy_key: SourceKey
    ply: Annotated[int, Field(ge=0, le=1000)]
    exercise_key: SourceKey


class SourceSnapshot(SourceModel):
    schema_version: Literal[1] = 1
    workspace_id: SourceKey
    source_key: SourceKey
    title: Title
    repertoires: list[SourceRepertoire] = Field(default_factory=list, max_length=10000)
    policies: list[SourcePolicy] = Field(default_factory=list, max_length=10000)
    routes: list[SourceRoute] = Field(default_factory=list, max_length=10000)
    memberships: list[RouteMembership] = Field(default_factory=list, max_length=100000)
    exercises: list[SourceExercise] = Field(default_factory=list, max_length=10000)
    bindings: list[ExerciseBinding] = Field(default_factory=list, max_length=100000)

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_schema_version(cls, value):
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def source_references(self):
        indexed = {}
        for collection_name in ("repertoires", "policies", "routes", "exercises"):
            collection = getattr(self, collection_name)
            keys = [item.key for item in collection]
            if len(set(keys)) != len(keys):
                raise ValueError(f"duplicate {collection_name} key")
            indexed[collection_name] = {item.key: item for item in collection}
        for policy in self.policies:
            if policy.repertoire_key not in indexed["repertoires"]:
                raise ValueError("policy references unknown repertoire")

        memberships = set()
        for membership in self.memberships:
            policy = indexed["policies"].get(membership.policy_key)
            if policy is None or membership.route_key not in indexed["routes"]:
                raise ValueError("membership references unknown policy or route")
            if policy.repertoire_key != membership.repertoire_key:
                raise ValueError("membership repertoire does not own its policy")
            identity = (membership.repertoire_key, membership.policy_key, membership.route_key)
            if identity in memberships:
                raise ValueError("duplicate route membership")
            memberships.add(identity)
        if set(indexed["routes"]) - {membership.route_key for membership in self.memberships}:
            raise ValueError("every authored route requires a repertoire policy membership")
        for exercise in self.exercises:
            if exercise.policy_key not in indexed["policies"]:
                raise ValueError("exercise references unknown policy")

        bindings = set()
        for binding in self.bindings:
            route = indexed["routes"].get(binding.route_key)
            policy = indexed["policies"].get(binding.policy_key)
            exercise = indexed["exercises"].get(binding.exercise_key)
            if route is None or policy is None or exercise is None:
                raise ValueError("binding references unknown route, policy or exercise")
            if exercise.policy_key != binding.policy_key:
                raise ValueError("binding policy does not own its exercise")
            if (policy.repertoire_key, binding.policy_key, binding.route_key) not in memberships:
                raise ValueError("binding requires a matching route membership")
            if binding.ply > len(route.moves):
                raise ValueError("binding ply exceeds its authored route")
            identity = (binding.route_key, binding.policy_key, binding.ply)
            if identity in bindings:
                raise ValueError("duplicate or ambiguous exercise binding at route/policy/ply")
            bindings.add(identity)
        return self


__all__ = [
    "SourceSnapshot", "SourceRepertoire", "SourcePolicy", "SourceRoute",
    "RouteMembership", "SourceExercise", "ExerciseBinding",
]
