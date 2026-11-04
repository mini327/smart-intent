"""Strict public contracts. Identity and filesystem permissions are never tool arguments."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

Name = Annotated[str, Field(min_length=1, max_length=200)]
TaskId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Origin(Model):
    kind: Literal["user_message", "tool_result", "assumption"]
    ref: Name


class ContextField(Model):
    value: JsonValue
    confirmation: Literal["user_explicit", "tool_verified", "assumed"]
    origin: Origin
    expires_at: datetime | None = None


CONTEXT_FIELDS = {
    "target",
    "action",
    "time_range",
    "filters",
    "output_preference",
    "pending_items",
    "last_result",
}


def context_keys(fields: dict[str, ContextField]) -> dict[str, ContextField]:
    if set(fields) - CONTEXT_FIELDS:
        raise ValueError("Unknown context field")
    return fields


class CreateContext(Model):
    fields: dict[str, ContextField] = Field(default_factory=dict, max_length=7)
    label: str = Field(default="", max_length=200)
    _keys = field_validator("fields")(context_keys)


class GetContext(Model):
    task_id: TaskId


class UpdateContext(GetContext):
    expected_version: int = Field(ge=1, strict=True)
    patch: dict[str, ContextField] = Field(default_factory=dict, max_length=7)
    remove_fields: list[Name] = Field(default_factory=list, max_length=7)
    _keys = field_validator("patch")(context_keys)

    @model_validator(mode="after")
    def check_patch(self):
        if set(self.remove_fields) - CONTEXT_FIELDS:
            raise ValueError("Unknown field to remove")
        if set(self.patch) & set(self.remove_fields):
            raise ValueError("Cannot replace and remove the same field")
        if not self.patch and not self.remove_fields:
            raise ValueError("Empty update")
        return self


class ClearContext(GetContext):
    expected_version: int = Field(ge=1, strict=True)


class Target(Model):
    mode: Literal["single", "explicit_set", "all"]
    target_ids: list[Name] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def check_mode(self):
        if self.mode == "all" and self.target_ids:
            raise ValueError("all cannot contain target_ids")
        if len(set(self.target_ids)) != len(self.target_ids):
            raise ValueError("Duplicate target IDs")
        return self


class TimeRange(Model):
    expression: str | None = Field(default=None, min_length=1, max_length=100)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    start: datetime | None = None
    end: datetime | None = None
    reference_at: datetime | None = None


class Aggregation(Model):
    count_by: Literal["category", "status"]
    top_k: int = Field(default=5, ge=1, le=100, strict=True)


class Plan(Model):
    source_id: Name
    action: str = Field(min_length=1, max_length=100)
    target: Target | None = None
    time: TimeRange | None = None
    filters: dict[str, list[Name]] = Field(default_factory=dict, max_length=5)
    aggregation: Aggregation | None = None

    @field_validator("filters")
    @classmethod
    def filter_lengths(cls, filters):
        if any(not values or len(values) > 200 for values in filters.values()):
            raise ValueError("Filter values must contain between 1 and 200 values")
        return filters


class ValidatePlan(Model):
    plan: Plan
    task_id: TaskId | None = None


class QueryRecords(ValidatePlan):
    page_size: int | None = Field(default=None, ge=1, le=200, strict=True)
    cursor: str | None = Field(default=None, min_length=1, max_length=4096)


class ResolveTarget(Model):
    source_id: Name
    target_type: Literal["record_target"] = "record_target"
    query: str = Field(min_length=1, max_length=200)
    limit: int | None = Field(default=None, ge=1, le=20, strict=True)

    @field_validator("query")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Target query must not be blank")
        return value


class ErrorInfo(Model):
    code: str
    message: str
    retryable: bool = False
    field_errors: dict[str, str] = Field(default_factory=dict)


class Envelope(Model):
    schema_version: Literal["1.0"] = "1.0"
    request_id: str
    status: str
    data: dict[str, JsonValue] = Field(default_factory=dict)
    sources: list[dict[str, JsonValue]] = Field(default_factory=list)
    applied_scope: dict[str, JsonValue] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None
