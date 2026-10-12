"""Reviewable configuration documents and atomic publication to application state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import Field, JsonValue, model_validator

from a13n_claw.attention import ChannelPolicy, ConversationMode
from a13n_claw.domain import (
    ClawError,
    EnvironmentDefinition,
    InstanceDefaults,
    MCPDefinition,
    ModelDefinition,
    ProfileDefinition,
    ResourceChange,
    ResourceId,
    SkillDefinition,
    Value,
    canonical,
    digest,
)
from a13n_claw.messaging import Messaging
from a13n_claw.storage import Store

GROUPS = {
    "models": "model",
    "environments": "environment",
    "profiles": "profile",
    "skills": "skill",
    "mcp": "mcp",
}


class ServerSettings(Value):
    """Local launch settings, never published by configuration apply."""

    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    data_root: str | None = None
    workspace: str | None = None
    concurrency: int = Field(default=4, ge=1, le=64)
    mode: ConversationMode | None = None
    console: bool = True


class Configuration(Value):
    format: Literal[1] = 1
    server: ServerSettings = Field(default_factory=ServerSettings)
    models: dict[ResourceId, ModelDefinition] = Field(default_factory=dict)
    environments: dict[ResourceId, EnvironmentDefinition] = Field(default_factory=dict)
    profiles: dict[ResourceId, ProfileDefinition] = Field(default_factory=dict)
    skills: dict[ResourceId, SkillDefinition] = Field(default_factory=dict)
    mcp: dict[ResourceId, MCPDefinition] = Field(default_factory=dict)
    defaults: InstanceDefaults | None = None
    channels: dict[ResourceId, ChannelPolicy] = Field(default_factory=dict)
    versions: dict[str, Annotated[int, Field(ge=0)]] = Field(default_factory=dict)
    retired: tuple[str, ...] = ()

    def changes(self) -> list[ResourceChange]:
        data = self.model_dump(mode="json")
        result = []
        for group, kind in GROUPS.items():
            entries = data[group]
            assert isinstance(entries, dict)
            for identity, content in entries.items():
                assert isinstance(content, dict)
                key = f"{kind}/{identity}"
                result.append(
                    ResourceChange(
                        kind=kind,
                        id=identity,
                        content=content,
                        expected_version=self.versions.get(key, 0),
                        retired=key in self.retired,
                    )
                )
        if self.defaults is not None:
            result.append(
                ResourceChange(
                    kind="defaults",
                    id="instance",
                    content=self.defaults.model_dump(mode="json"),
                    expected_version=self.versions.get("defaults/instance", 0),
                    retired="defaults/instance" in self.retired,
                )
            )
        return result

    @model_validator(mode="after")
    def version_keys(self) -> Configuration:
        resources = {f"{item.kind}/{item.id}" for item in self.changes()}
        keys = resources | {f"channel/{identity}" for identity in self.channels}
        if self.versions.keys() - keys or set(self.retired) - resources:
            raise ValueError("Version and retirement keys must name definitions in this document")
        return self

    def publication(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", exclude={"server"})


class ConfigurationApply(Value):
    request_id: str = Field(min_length=1, max_length=200)
    configuration: Configuration


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate keys instead of silently accepting a different configuration."""

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ClawError("configuration_yaml", "YAML keys must be unique strings", 422)
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def read_yaml(path: Path) -> object:
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
    except (yaml.YAMLError, UnicodeError, RecursionError) as error:
        # Parser exceptions may include submitted secrets and source lines.
        raise ClawError("configuration_yaml", "Invalid UTF-8 YAML document", 422) from error


def load_configuration(path: Path) -> Configuration:
    return Configuration.model_validate(read_yaml(path))


def dump_configuration(configuration: Configuration) -> str:
    data = configuration.model_dump(mode="json", exclude_defaults=True)
    return yaml.safe_dump({"format": 1, **data}, sort_keys=False, allow_unicode=True)


def export_configuration(store: Store, actor_id: str) -> Configuration:
    # Resources and Channel versions must belong to one coherent read.
    with store._transaction() as db:
        store._principal(db, actor_id).require("admin")
        data: dict = {group: {} for group in GROUPS}
        data.update(versions={}, retired=[], channels={})
        groups = {kind: group for group, kind in GROUPS.items()}
        for row in db.execute("SELECT kind,id FROM resources ORDER BY kind,id").fetchall():
            resource = store._resource(db, row[0], row[1])
            key = f"{resource.kind}/{resource.id}"
            if resource.kind == "defaults":
                data["defaults"] = resource.content
            else:
                data[groups[resource.kind]][resource.id] = resource.content
            data["versions"][key] = resource.version
            if resource.retired:
                data["retired"].append(key)
        for row in db.execute("SELECT * FROM channels WHERE active=1 ORDER BY id"):
            data["channels"][row["id"]] = json.loads(row["policy"])
            data["versions"][f"channel/{row['id']}"] = row["version"]
        return Configuration.model_validate(data)


def apply_configuration(
    store: Store,
    actor_id: str,
    request: ConfigurationApply,
    *,
    validate_only: bool = False,
) -> dict[str, JsonValue]:
    document = request.configuration
    fingerprint = digest(document.publication())
    with store._transaction() as db:
        actor = store._principal(db, actor_id)
        actor.require("admin")
        prior = db.execute(
            "SELECT fingerprint,result FROM configuration_applications "
            "WHERE actor_id=? AND request_id=?",
            (actor_id, request.request_id),
        ).fetchone()
        if prior and not validate_only:
            if prior["fingerprint"] != fingerprint:
                raise ClawError("request_conflict", "Configuration request identity was reused")
            return json.loads(prior["result"])
        # Roll back a successful dry run as well as any failed validation.
        db.execute("SAVEPOINT configuration_validation")
        resources = store._import_resources(db, actor_id, document.changes())
        for row in db.execute(
            "SELECT id FROM resources WHERE kind='profile' AND retired=0"
        ).fetchall():
            store._capture(db, actor, row[0], "")
        defaults = db.execute(
            "SELECT content FROM resources WHERE kind='defaults' AND id='instance' AND retired=0"
        ).fetchone()
        if defaults:
            profile_id = InstanceDefaults.model_validate_json(defaults[0]).profile_id
            if profile_id:
                store._capture(db, actor, profile_id, "")
        messaging = Messaging(store)
        channels = [
            messaging._save_channel(
                db,
                actor_id,
                identity,
                policy,
                document.versions.get(f"channel/{identity}", 0),
            )
            for identity, policy in document.channels.items()
        ]
        result: dict[str, JsonValue] = {
            "request_id": request.request_id,
            "versions": {f"{item.kind}/{item.id}": item.version for item in resources}
            | {f"channel/{item['id']}": item["version"] for item in channels},
            "validated": True,
            "applied": not validate_only,
        }
        if validate_only:
            db.execute("ROLLBACK TO configuration_validation")
        else:
            db.execute(
                "INSERT INTO configuration_applications VALUES (?,?,?,?)",
                (
                    actor_id,
                    request.request_id,
                    fingerprint,
                    canonical(result),
                ),
            )
        db.execute("RELEASE configuration_validation")
        return result
