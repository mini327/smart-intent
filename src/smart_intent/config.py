import tomllib
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from .models import Model, Name


class ContextConfig(Model):
    mode: Literal["ephemeral", "persistent"] = "ephemeral"
    ttl_seconds: int = Field(default=86400, ge=1, le=604800, strict=True)
    database_path: Path | None = None

    @model_validator(mode="after")
    def persistence(self):
        if self.mode == "persistent" and (
            self.database_path is None or not self.database_path.is_absolute()
        ):
            raise ValueError("persistent mode needs an absolute database_path")
        if self.mode == "ephemeral" and self.database_path is not None:
            raise ValueError("ephemeral mode must not specify a database_path")
        return self


class Limits(Model):
    page_size_default: int = Field(default=50, ge=1, le=200)
    page_size_max: int = Field(default=200, ge=1, le=200)
    candidate_limit: int = Field(default=5, ge=1, le=20)
    source_bytes_max: int = Field(default=20 * 1024 * 1024, ge=1, le=20 * 1024 * 1024)
    source_records_max: int = Field(default=100000, ge=1, le=100000)
    snapshot_cache_bytes_max: int = Field(default=50 * 1024 * 1024, ge=1)
    snapshot_ttl_seconds: int = Field(default=300, ge=1, le=3600)
    query_timeout_seconds: float = Field(default=10, gt=0, le=60)
    result_bytes_max: int = Field(default=262144, ge=2048, le=1048576)

    @model_validator(mode="after")
    def pages(self):
        if self.page_size_default > self.page_size_max:
            raise ValueError("default page size exceeds maximum")
        return self


class FieldMap(Model):
    record_id: Name = "id"
    target_id: Name = "target_id"
    target_name: Name = "target_name"
    timestamp: Name = "occurred_at"
    status: Name = "status"
    category: Name = "category"
    summary: Name = "summary"

    @model_validator(mode="after")
    def unique(self):
        values = list(self.model_dump().values())
        if len(set(values)) != len(values):
            raise ValueError("Source field mappings must be distinct")
        return self


class SourceConfig(Model):
    id: Name
    path: Path
    allowed_root: Path
    classification: Literal["real", "test"] = "real"
    fields: FieldMap = Field(default_factory=FieldMap)

    @field_validator("path", "allowed_root")
    @classmethod
    def absolute(cls, value):
        if not value.is_absolute():
            raise ValueError("Source paths must be absolute")
        return value


class Config(Model):
    schema_version: Literal[1] = 1
    principal_id: Name
    project_id: Name
    project_root: Path
    timezone: str = "Asia/Shanghai"
    context: ContextConfig = Field(default_factory=ContextConfig)
    limits: Limits = Field(default_factory=Limits)
    sources: list[SourceConfig] = Field(default_factory=list, max_length=20)

    @field_validator("timezone")
    @classmethod
    def zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown timezone") from exc
        return value

    @model_validator(mode="after")
    def scope(self):
        if not self.project_root.is_absolute():
            raise ValueError("project_root must be absolute")
        if len({s.id for s in self.sources}) != len(self.sources):
            raise ValueError("Duplicate source IDs")
        root = self.project_root.resolve()
        for source in self.sources:
            if not source.allowed_root.resolve().is_relative_to(root):
                raise ValueError("Source root must be inside project_root")
            if not source.path.resolve().is_relative_to(source.allowed_root.resolve()):
                raise ValueError("Source path must be inside allowed_root")
        return self


def load_config(path: Path) -> Config:
    if not path.is_absolute():
        raise ValueError("--config must be an absolute path")
    with path.open("rb") as stream:
        return Config.model_validate(tomllib.load(stream))
