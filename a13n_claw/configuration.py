"""File configuration values and their transactional execution projection."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, JsonValue, ValidationError, model_validator

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
    """Launch settings take effect at the next startup, not during reload."""

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
    retired: tuple[str, ...] = ()

    def definitions(self) -> dict[tuple[str, str], dict[str, JsonValue]]:
        data = self.model_dump(mode="json")
        result = {}
        for group, kind in GROUPS.items():
            entries = data[group]
            assert isinstance(entries, dict)
            for identity, content in entries.items():
                assert isinstance(content, dict)
                result[kind, identity] = content
        if self.defaults is not None:
            result["defaults", "instance"] = self.defaults.model_dump(mode="json")
        return result

    @model_validator(mode="after")
    def retirement_keys(self) -> Configuration:
        if set(self.retired) - {f"{kind}/{identity}" for kind, identity in self.definitions()}:
            raise ValueError("Retirement keys must name definitions in this document")
        return self


class ConfigurationWrite(Value):
    expected_revision: str = Field(min_length=1, max_length=100)
    text: str = Field(max_length=2_000_000)


class ConfigurationReload(Value):
    expected_revision: str = Field(min_length=1, max_length=100)


class ConfigurationPreview(Value):
    text: str = Field(max_length=2_000_000)


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate keys rather than silently accepting different configuration."""

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ClawError(
                    "configuration_yaml",
                    f"YAML keys must be unique strings (line {key_node.start_mark.line + 1})",
                    422,
                )
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def parse_yaml(text: str) -> object:
    try:
        return yaml.load(text, Loader=UniqueLoader)
    except (yaml.YAMLError, RecursionError) as error:
        # Do not include source lines or submitted values in diagnostics.
        raise ClawError("configuration_yaml", "Invalid YAML document", 422) from error


def read_yaml(path: Path) -> object:
    return parse_yaml(path.read_text(encoding="utf-8"))


def parse_configuration(text: str) -> Configuration:
    if len(text.encode("utf-8")) > 2_000_000:
        raise ClawError("configuration_size", "Configuration exceeds 2 MB", 422)
    return Configuration.model_validate(parse_yaml(text))


def load_configuration(path: Path) -> Configuration:
    return parse_configuration(path.read_text(encoding="utf-8"))


def dump_configuration(configuration: Configuration) -> str:
    data = configuration.model_dump(mode="json", exclude_defaults=True)
    return yaml.safe_dump({"format": 1, **data}, sort_keys=False, allow_unicode=True)


def configuration_issues(error: ClawError | ValidationError) -> list[JsonValue]:
    if isinstance(error, ClawError):
        return [{"path": "configuration", "code": error.code, "message": str(error)}]
    return [
        {
            "path": ".".join(map(str, item["loc"])),
            "code": item["type"],
            "message": "Check this field",
        }
        for item in error.errors(include_input=False, include_context=False, include_url=False)
    ]


def export_configuration(store: Store, actor_id: str) -> Configuration:
    """Explicit migration of a pre-file Instance; ordinary exports read the file."""
    with store._transaction() as db:
        store._principal(db, actor_id).require("admin")
        data: dict = {group: {} for group in GROUPS}
        data.update(retired=[], channels={})
        groups = {kind: group for group, kind in GROUPS.items()}
        for row in db.execute("SELECT kind,id FROM resources ORDER BY kind,id").fetchall():
            resource = store._resource(db, row[0], row[1])
            if resource.kind == "defaults":
                data["defaults"] = resource.content
            else:
                data[groups[resource.kind]][resource.id] = resource.content
            if resource.retired:
                data["retired"].append(f"{resource.kind}/{resource.id}")
        for row in db.execute("SELECT * FROM channels WHERE active=1 ORDER BY id"):
            data["channels"][row["id"]] = json.loads(row["policy"])
        return Configuration.model_validate(data)


def project_configuration(
    store: Store,
    db: sqlite3.Connection,
    actor_id: str,
    document: Configuration,
) -> list[JsonValue]:
    """Load a complete desired catalog. The caller owns the transaction/rollback."""
    actor = store._principal(db, actor_id)
    actor.require("admin")
    desired = document.definitions()
    rows = db.execute("SELECT kind,id FROM resources ORDER BY kind,id").fetchall()
    current = {(row[0], row[1]): store._resource(db, row[0], row[1]) for row in rows}
    changes = []
    summary: list[JsonValue] = []
    for (kind, identity), content in desired.items():
        prior = current.get((kind, identity))
        retired = f"{kind}/{identity}" in document.retired
        if prior and prior.content == content and prior.retired == retired:
            continue
        changes.append(
            ResourceChange(
                kind=kind,
                id=identity,
                content=content,
                expected_version=prior.version if prior else 0,
                retired=retired,
            )
        )
        summary.append(
            {"resource": f"{kind}/{identity}", "action": "update" if prior else "create"}
        )
    for (kind, identity), prior in current.items():
        if (kind, identity) not in desired and not prior.retired:
            changes.append(
                ResourceChange(
                    kind=kind,
                    id=identity,
                    content=prior.content,
                    expected_version=prior.version,
                    retired=True,
                )
            )
            summary.append({"resource": f"{kind}/{identity}", "action": "retire"})
    store._import_resources(db, actor_id, changes)
    for row in db.execute("SELECT id FROM resources WHERE kind='profile' AND retired=0").fetchall():
        store._capture(db, actor, row[0], "")
    if (
        document.defaults
        and "defaults/instance" not in document.retired
        and document.defaults.profile_id
    ):
        store._capture(db, actor, document.defaults.profile_id, "")
    messaging = Messaging(store)
    for identity, policy in document.channels.items():
        if policy.enabled and policy.profile_id:
            store._capture(db, actor, policy.profile_id, "")
        prior = db.execute("SELECT * FROM channels WHERE id=?", (identity,)).fetchone()
        if (
            prior
            and prior["active"]
            and json.loads(prior["policy"]) == policy.model_dump(mode="json")
        ):
            continue
        messaging._save_channel(db, actor_id, identity, policy, prior["version"] if prior else 0)
        summary.append(
            {"resource": f"channel/{identity}", "action": "update" if prior else "create"}
        )
    for row in db.execute("SELECT * FROM channels WHERE active=1").fetchall():
        if row["id"] not in document.channels:
            policy = ChannelPolicy.model_validate_json(row["policy"])
            if policy.enabled:
                disabled = policy.model_copy(update={"enabled": False})
                db.execute(
                    "UPDATE channels SET policy=?,version=version+1 WHERE id=?",
                    (disabled.model_dump_json(), row["id"]),
                )
                store._audit(db, "channel_disabled", row["id"], {"actor": actor_id})
                summary.append({"resource": f"channel/{row['id']}", "action": "disable"})
    return summary


def load_projection(
    store: Store,
    actor_id: str,
    document: Configuration,
    path: Path,
    revision: str,
    *,
    validate_only: bool = False,
) -> list[JsonValue]:
    with store._transaction() as db:
        db.execute("SAVEPOINT configuration_validation")
        changes = project_configuration(store, db, actor_id, document)
        if validate_only:
            db.execute("ROLLBACK TO configuration_validation")
        else:
            db.execute(
                "INSERT INTO configuration_state VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "path=excluded.path,revision=excluded.revision,document=excluded.document",
                (str(path), revision, canonical(document.model_dump(mode="json"))),
            )
        db.execute("RELEASE configuration_validation")
        return changes
