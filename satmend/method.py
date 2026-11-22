"""Cross-version repair sessions for declarative maintenance experiments."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Callable, Optional, Tuple

from .scenario import Application, Scenario


@dataclass(frozen=True)
class Action:
    """A state-recovery command or a code candidate submitted for delivery."""

    at_s: float
    kind: str
    targets: tuple[str, ...]
    component: Optional[str] = None
    version: Optional[str] = None
    candidate_id: Optional[str] = None
    package_bytes: int = 0
    validated: bool = True
    verification_passes: bool = True


@dataclass(frozen=True)
class Assessment:
    """Modeled ground validation and target verification for a candidate.

    Set duration_s to override the default ground validation duration for this
    version. The simulator applies verification_passes at the target after
    delivery and activation.
    """

    validated: bool = True
    verification_passes: bool = True
    duration_s: Optional[float] = None


AssessmentCallback = Callable[[str, str, Tuple[Application, ...]], Assessment]


def _check_duration(name: str, seconds: float) -> None:
    if not isfinite(seconds) or seconds < 0:
        raise ValueError(f"{name} must be finite and nonnegative")


def plan_sessions(
    scenario: Scenario,
    *,
    slots: int = 2,
    diagnosis_s: float = 2.0,
    reuse_diagnosis_s: float = 1.0,
    validation_s: float = 1.0,
    state_recovery_s: float = 0.5,
    assess: Optional[AssessmentCallback] = None,
) -> tuple[Action, ...]:
    """Construct a deterministic plan from the scenario's maintenance cases.

    Groups are formed by component and assigned intact, in descending number
    of versions, to the slot with the fewest assigned versions. The gateway's
    group and version are handled first on their assigned slot. Each slot runs
    on its own clock. Within a component, the first code version uses the full
    diagnosis budget and later versions use the reuse budget. Each version
    receives a separate assessment and emits an action at its ready time so
    that delivery can overlap preparation of the next version.

    An optional assess(component, version, targets) callback returns an
    Assessment with simulated check outcomes. Targets are the affected
    applications of that code version. State anomalies emit individual
    modeled recovery commands. Candidate validation and delivery are distinct
    stages, so failed validations are also reported to the simulator.
    """
    if isinstance(slots, bool) or not isinstance(slots, int) or slots < 1:
        raise ValueError("slots must be a positive integer")
    for name, seconds in (
        ("diagnosis_s", diagnosis_s),
        ("reuse_diagnosis_s", reuse_diagnosis_s),
        ("validation_s", validation_s),
        ("state_recovery_s", state_recovery_s),
    ):
        _check_duration(name, seconds)

    groups: dict[str, dict[str, list[Application]]] = {}
    gateway_key = None
    for app in scenario.applications:
        if app.fault == "healthy":
            continue
        groups.setdefault(app.component, {}).setdefault(app.version, []).append(app)
        if app.id == scenario.gateway_app:
            gateway_key = (app.component, app.version)

    loads = [0] * slots
    assignments: list[list[str]] = [[] for _ in range(slots)]
    for component in sorted(groups, key=lambda key: (-len(groups[key]), key)):
        slot = min(range(slots), key=loads.__getitem__)
        assignments[slot].append(component)
        loads[slot] += len(groups[component])

    actions: list[Action] = []
    for components in assignments:
        elapsed = 0.0
        if gateway_key and gateway_key[0] in components:
            components.remove(gateway_key[0])
            components.insert(0, gateway_key[0])
        for component in components:
            versions = groups[component]
            has_diagnosed = False
            ordered_versions = sorted(
                versions, key=lambda version: ((component, version) != gateway_key, version)
            )
            for version in ordered_versions:
                apps = sorted(
                    versions[version], key=lambda app: (app.id != scenario.gateway_app, app.id)
                )
                for app in apps:
                    if app.fault == "state":
                        elapsed += state_recovery_s
                        actions.append(Action(elapsed, "state", (app.id,)))

                targets = tuple(app for app in apps if app.fault == "code")
                if not targets:
                    continue
                assessment = assess(component, version, targets) if assess else Assessment()
                duration = assessment.duration_s
                if duration is None:
                    duration = validation_s
                _check_duration("assessment.duration_s", duration)
                elapsed += (reuse_diagnosis_s if has_diagnosed else diagnosis_s) + duration
                has_diagnosed = True
                # Reserve one package large enough for the whole replica cohort.
                package_bytes = max(app.package_bytes for app in targets)
                actions.append(
                    Action(
                        at_s=elapsed,
                        kind="code",
                        targets=tuple(app.id for app in targets),
                        component=component,
                        version=version,
                        candidate_id=f"candidate-{len(actions) + 1}",
                        package_bytes=package_bytes,
                        validated=assessment.validated,
                        verification_passes=assessment.verification_passes,
                    )
                )
    return tuple(sorted(actions, key=lambda action: action.at_s))
