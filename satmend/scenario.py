"""Read and validate scenario data for the SatMend simulator."""

from __future__ import annotations

import csv
import json
import math
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union


@dataclass(frozen=True)
class Contact:
    start_s: float
    end_s: float


@dataclass(frozen=True)
class Application:
    id: str
    satellite: str
    component: str
    version: str
    fault: str
    dependencies: tuple[str, ...]
    package_bytes: int
    activation_s: float
    verification_s: float


@dataclass(frozen=True)
class Scenario:
    deadline_s: float
    gateway_satellite: str
    gateway_app: str
    ground_contacts: tuple[Contact, ...]
    uplink_bytes_per_s: float
    downlink_bytes_per_s: float
    crosslink_bytes_per_s: float
    command_bytes: int
    receipt_bytes: int
    crosslink_contacts: dict[str, tuple[Contact, ...]]
    applications: tuple[Application, ...]
    title: str = ""


def _name(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field} must be a nonempty string without surrounding whitespace")
    return value


def _mapping(value: object, field: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _list(value: object, field: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return value


def _nonnegative_number(value: object, field: str) -> float:
    message = f"{field} must be a finite nonnegative number"
    if type(value) not in (int, float):
        raise ValueError(message)
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(message) from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(message)
    return number


def _positive_number(value: object, field: str) -> float:
    number = _nonnegative_number(value, field)
    if number == 0:
        raise ValueError(f"{field} must be greater than zero")
    return number


def _byte_count(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def _normalize_contacts(rows: list, field: str) -> tuple[Contact, ...]:
    contacts = []
    for index, raw in enumerate(rows):
        label = f"{field}[{index}]"
        if not isinstance(raw, list) or len(raw) != 2:
            raise ValueError(f"{label} must contain a start and an end time")
        start = _nonnegative_number(raw[0], f"{label}.start_s")
        end = _nonnegative_number(raw[1], f"{label}.end_s")
        if start >= end:
            raise ValueError(f"{label} must end after it starts")
        contacts.append(Contact(start, end))

    merged: list[Contact] = []
    for contact in sorted(contacts, key=lambda item: (item.start_s, item.end_s)):
        if merged and contact.start_s <= merged[-1].end_s:
            previous = merged[-1]
            merged[-1] = Contact(previous.start_s, max(previous.end_s, contact.end_s))
        else:
            merged.append(contact)
    return tuple(merged)


def _read_contact_csv(path: Path) -> list[list[float]]:
    rows = []
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            if reader.fieldnames is None or not {"start_s", "end_s"} <= set(reader.fieldnames):
                raise ValueError("Ground contact CSV requires start_s and end_s columns")
            for row in reader:
                try:
                    rows.append([float(row["start_s"]), float(row["end_s"])])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Invalid contact time at CSV line {reader.line_num}") from exc
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValueError(f"Could not read ground contact CSV: {exc}") from exc
    return rows


def _parse_application(raw: object, index: int) -> Application:
    field = f"applications[{index}]"
    values = _mapping(raw, field)
    fault = values.get("fault", "healthy")
    if fault not in ("healthy", "code", "state"):
        raise ValueError(f"{field}.fault must be healthy, code, or state")
    dependencies = tuple(
        _name(dependency, f"{field}.dependencies")
        for dependency in _list(values.get("dependencies", []), f"{field}.dependencies")
    )
    if len(dependencies) != len(set(dependencies)):
        raise ValueError(f"{field}.dependencies contains a repeated application ID")
    package_bytes = _byte_count(values.get("package_bytes", 0), f"{field}.package_bytes")
    if fault == "code" and package_bytes == 0:
        raise ValueError(f"{field}.package_bytes must be positive for a code fault")
    return Application(
        id=_name(values.get("id"), f"{field}.id"),
        satellite=_name(values.get("satellite"), f"{field}.satellite"),
        component=_name(values.get("component"), f"{field}.component"),
        version=_name(values.get("version"), f"{field}.version"),
        fault=fault,
        dependencies=dependencies,
        package_bytes=package_bytes,
        activation_s=_nonnegative_number(values.get("activation_s", 0), f"{field}.activation_s"),
        verification_s=_nonnegative_number(values.get("verification_s", 0), f"{field}.verification_s"),
    )


def _validate_dependencies(apps: dict[str, Application]) -> None:
    dependents: dict[str, list[str]] = {app_id: [] for app_id in apps}
    pending = {app.id: len(app.dependencies) for app in apps.values()}
    for app in apps.values():
        for dependency in app.dependencies:
            if dependency not in apps:
                raise ValueError(f"Application {app.id!r} depends on unknown application {dependency!r}")
            dependents[dependency].append(app.id)

    ready = deque(app_id for app_id, count in pending.items() if count == 0)
    visited = 0
    while ready:
        app_id = ready.popleft()
        visited += 1
        for dependent in dependents[app_id]:
            pending[dependent] -= 1
            if pending[dependent] == 0:
                ready.append(dependent)
    if visited != len(apps):
        raise ValueError("Application dependencies contain a cycle")


def load_scenario(json_path: Union[str, Path], contacts_csv: Optional[Union[str, Path]] = None) -> Scenario:
    """Load a scenario, optionally using a CSV in place of its ground contact array."""
    try:
        with Path(json_path).open(encoding="utf-8") as handle:
            values = _mapping(json.load(handle), "scenario")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read scenario JSON: {exc}") from exc

    app_rows = _list(values.get("applications"), "applications")
    applications = tuple(_parse_application(raw, index) for index, raw in enumerate(app_rows))
    apps = {app.id: app for app in applications}
    if len(apps) != len(applications):
        raise ValueError("Application IDs must be unique")
    gateway_satellite = _name(values.get("gateway_satellite"), "gateway_satellite")
    gateway_app = _name(values.get("gateway_app"), "gateway_app")
    if gateway_app not in apps or apps[gateway_app].satellite != gateway_satellite:
        raise ValueError("gateway_app must identify an application on gateway_satellite")
    _validate_dependencies(apps)

    if contacts_csv is None:
        ground_rows = _list(values.get("ground_contacts"), "ground_contacts")
    else:
        ground_rows = _read_contact_csv(Path(contacts_csv))
    ground_contacts = _normalize_contacts(ground_rows, "ground_contacts")
    remote_satellites = {app.satellite for app in applications} - {gateway_satellite}
    crosslink_contacts = {}
    for satellite, rows in _mapping(values.get("crosslink_contacts", {}), "crosslink_contacts").items():
        field = f"crosslink_contacts.{satellite}"
        if satellite not in remote_satellites:
            raise ValueError(f"{field} must refer to a known satellite other than the gateway")
        crosslink_contacts[satellite] = _normalize_contacts(_list(rows, field), field)

    title = values.get("title", "")
    if not isinstance(title, str):
        raise ValueError("title must be a string")
    return Scenario(
        deadline_s=_positive_number(values.get("deadline_s"), "deadline_s"),
        gateway_satellite=gateway_satellite,
        gateway_app=gateway_app,
        ground_contacts=ground_contacts,
        uplink_bytes_per_s=_positive_number(values.get("uplink_bytes_per_s"), "uplink_bytes_per_s"),
        downlink_bytes_per_s=_positive_number(values.get("downlink_bytes_per_s"), "downlink_bytes_per_s"),
        crosslink_bytes_per_s=_positive_number(values.get("crosslink_bytes_per_s"), "crosslink_bytes_per_s"),
        command_bytes=_byte_count(values.get("command_bytes", 20), "command_bytes"),
        receipt_bytes=_byte_count(values.get("receipt_bytes", 12), "receipt_bytes"),
        crosslink_contacts=crosslink_contacts,
        applications=applications,
        title=title,
    )


def scenario_to_dict(scenario: Scenario) -> dict:
    """Return the validated scenario in its normalized JSON representation."""
    return {
        "title": scenario.title,
        "deadline_s": scenario.deadline_s,
        "gateway_satellite": scenario.gateway_satellite,
        "gateway_app": scenario.gateway_app,
        "ground_contacts": [[contact.start_s, contact.end_s] for contact in scenario.ground_contacts],
        "uplink_bytes_per_s": scenario.uplink_bytes_per_s,
        "downlink_bytes_per_s": scenario.downlink_bytes_per_s,
        "crosslink_bytes_per_s": scenario.crosslink_bytes_per_s,
        "command_bytes": scenario.command_bytes,
        "receipt_bytes": scenario.receipt_bytes,
        "crosslink_contacts": {
            satellite: [[contact.start_s, contact.end_s] for contact in contacts]
            for satellite, contacts in sorted(scenario.crosslink_contacts.items())
        },
        "applications": [
            {
                "id": app.id,
                "satellite": app.satellite,
                "component": app.component,
                "version": app.version,
                "fault": app.fault,
                "dependencies": list(app.dependencies),
                "package_bytes": app.package_bytes,
                "activation_s": app.activation_s,
                "verification_s": app.verification_s,
            }
            for app in scenario.applications
        ],
    }
