"""Discrete-event execution of maintenance actions on constellation scenarios."""

from __future__ import annotations

from dataclasses import dataclass, field
from heapq import heappop, heappush
from itertools import count
from math import isfinite
from typing import Callable, Optional

from .method import Action
from .scenario import Scenario


@dataclass(eq=False)
class _Transfer:
    kind: str
    remaining: float
    allowed: Callable[[], bool]
    complete: Callable[[], None]
    details: dict
    started: bool = False


@dataclass
class _Link:
    rate: float
    queue: list[_Transfer] = field(default_factory=list)
    active: Optional[_Transfer] = None


def simulate(scenario: Scenario, actions: tuple[Action, ...]) -> dict:
    """Run maintenance up to the scenario deadline and return a JSON-ready report.

    Links are fluid, FIFO among eligible transfers, and independently paced.
    Code and state actions schedule target activation and a configured check.
    Successful application recovery and ground receipt are timestamped separately.
    """
    return _Simulation(scenario).run(actions)


class _Simulation:
    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.now = 0.0
        self.callbacks = []
        self.sequence = count()
        self.events = []
        self.apps = {app.id: app for app in scenario.applications}
        self.healthy = {app.id: app.fault == "healthy" for app in scenario.applications}
        self.service_since = {}
        self.recovery_time = {}
        self.receipt_time = {}
        self.jobs = {}
        self.candidates = {}
        self.cached_candidates = set()
        self.inflight_candidates = set()
        self.links = {
            "uplink": _Link(scenario.uplink_bytes_per_s),
            "crosslink": _Link(scenario.crosslink_bytes_per_s),
            "downlink": _Link(scenario.downlink_bytes_per_s),
        }
        self.refresh_services(initial=True)
        self.affected_services = set(self.apps) - self.service_since.keys()

    def emit(self, event, **details):
        self.events.append({"time_s": round(self.now, 9), "event": event, **details})

    def schedule(self, at, callback):
        heappush(self.callbacks, (at, next(self.sequence), callback))

    def refresh_services(self, *, initial=False):
        changed = True
        while changed:
            changed = False
            for app in self.apps.values():
                if app.id in self.service_since or not self.healthy[app.id]:
                    continue
                if all(dep in self.service_since for dep in app.dependencies):
                    self.service_since[app.id] = self.now
                    changed = True
                    if not initial:
                        self.emit("service_restored", target=app.id)

    def ground_contact(self):
        return any(window.start_s <= self.now < window.end_s
                   for window in self.scenario.ground_contacts)

    def crosslink_contact(self, target):
        if not self.healthy[self.scenario.gateway_app]:
            return False
        windows = self.scenario.crosslink_contacts.get(self.apps[target].satellite)
        return windows is None or any(window.start_s <= self.now < window.end_s for window in windows)

    def enqueue(self, link, kind, size, allowed, complete, **details):
        self.links[link].queue.append(_Transfer(kind, float(size), allowed, complete, details))

    def submit(self, action):
        if action.kind == "code":
            if not action.validated:
                for target in action.targets:
                    self.emit("action_rejected", target=target, reason="ground_validation_failed")
                return
            signature = (action.component, action.version, action.package_bytes)
            previous = self.candidates.get(action.candidate_id)
            if previous is not None and previous != signature:
                for target in action.targets:
                    self.emit("action_rejected", target=target, reason="candidate_id_conflict")
                return

        targets = []
        for target in action.targets:
            app = self.apps.get(target)
            if app is None:
                reason = "unknown_target"
            elif self.healthy[target]:
                reason = "target_already_healthy"
            elif app.fault != action.kind:
                reason = "action_fault_mismatch"
            elif action.kind == "code" and (app.component, app.version) != (action.component, action.version):
                reason = "incompatible_version"
            elif action.kind == "code" and (action.candidate_id, target) in self.inflight_candidates:
                reason = "candidate_already_in_flight"
            else:
                targets.append(target)
                continue
            self.emit("action_rejected", target=target, reason=reason)

        if not targets:
            return
        if action.kind == "code" and action.candidate_id not in self.candidates:
            self.candidates[action.candidate_id] = signature
            self.enqueue("uplink", "package", action.package_bytes, self.ground_contact,
                         lambda: self.cache(action.candidate_id), candidate_id=action.candidate_id)
        for target in targets:
            job = len(self.jobs) + 1
            if action.kind == "code":
                self.inflight_candidates.add((action.candidate_id, target))
            self.jobs[job] = {
                "target": target, "kind": action.kind, "candidate_id": action.candidate_id,
                "stage": "awaiting_ground_upload", "submitted_s": self.now,
            }
            self.emit("action_submitted", job=job, target=target, kind=action.kind,
                      candidate_id=action.candidate_id)
            self.enqueue(
                "uplink", "command", self.scenario.command_bytes, self.ground_contact,
                lambda job=job, action=action: self.stage(job, action.verification_passes),
                job=job, target=target,
            )

    def cache(self, candidate):
        self.cached_candidates.add(candidate)
        self.emit("package_cached", candidate_id=candidate)
        for job, data in self.jobs.items():
            if data["stage"] == "awaiting_package" and data["candidate_id"] == candidate:
                self.dispatch(job, data.pop("verification_passes"))

    def stage(self, job, passes):
        data = self.jobs[job]
        candidate = data["candidate_id"]
        if candidate is not None and candidate not in self.cached_candidates:
            data["stage"] = "awaiting_package"
            data["verification_passes"] = passes
            return
        self.dispatch(job, passes)

    def dispatch(self, job, passes):
        data = self.jobs[job]
        target = data["target"]
        app = self.apps[target]
        data["staged_at_gateway_s"] = self.now
        self.emit("command_staged", job=job, target=target)
        if app.satellite == self.scenario.gateway_satellite:
            self.activate(job, passes)
            return

        size = self.scenario.command_bytes
        if data["candidate_id"] is not None:
            size += self.candidates[data["candidate_id"]][2]
        data["stage"] = "awaiting_crosslink"
        self.enqueue("crosslink", "delivery", size, lambda: self.crosslink_contact(target),
                     lambda: self.activate(job, passes), job=job, target=target)

    def activate(self, job, passes):
        data = self.jobs[job]
        target = data["target"]
        app = self.apps[target]
        data["stage"] = "activating"
        self.emit("activation_started", job=job, target=target)
        self.schedule(self.now + app.activation_s, lambda: self.start_check(job, passes))

    def start_check(self, job, passes):
        data = self.jobs[job]
        target = data["target"]
        data["stage"] = "verifying"
        self.emit("verification_started", job=job, target=target)
        self.schedule(self.now + self.apps[target].verification_s, lambda: self.verify(job, passes))

    def verify(self, job, passes):
        data = self.jobs[job]
        target = data["target"]
        data["stage"] = "verified" if passes else "verification_failed"
        data["verified_at_target_s"] = self.now
        self.inflight_candidates.discard((data["candidate_id"], target))
        self.emit("target_verified", job=job, target=target, passed=passes)
        first_success = passes and not self.healthy[target]
        if first_success:
            self.healthy[target] = True
            self.recovery_time[target] = self.now
            self.refresh_services()

        def receive():
            data["receipt_at_ground_s"] = self.now
            if passes:
                self.receipt_time.setdefault(target, self.now)
            self.emit("ground_receipt", job=job, target=target, passed=passes)

        def return_to_ground():
            self.enqueue("downlink", "receipt", self.scenario.receipt_bytes,
                         self.ground_contact, receive, job=job, target=target)

        if self.apps[target].satellite == self.scenario.gateway_satellite:
            return_to_ground()
        else:
            self.enqueue("crosslink", "receipt", self.scenario.receipt_bytes,
                         lambda: self.crosslink_contact(target), return_to_ground,
                         job=job, target=target)

    def select_transfers(self):
        for name, link in self.links.items():
            selected = next((transfer for transfer in link.queue if transfer.allowed()), None)
            previous = link.active
            if selected is previous:
                continue
            if previous is not None:
                self.emit("transfer_paused", link=name, kind=previous.kind,
                          bytes_remaining=round(previous.remaining, 9), **previous.details)
            link.active = selected
            if selected is not None:
                event = "transfer_resumed" if selected.started else "transfer_started"
                self.emit(event, link=name, kind=selected.kind,
                          bytes_remaining=round(selected.remaining, 9), **selected.details)
                selected.started = True

    def process_completed_transfers(self):
        for name, link in self.links.items():
            finished = link.active
            if finished is None or finished.remaining > 0:
                continue
            link.queue.remove(finished)
            link.active = None
            self.emit("transfer_completed", link=name, kind=finished.kind, **finished.details)
            finished.complete()

    def run(self, actions):
        deadline = self.scenario.deadline_s
        for action in actions:
            _validate_action(action)
            if action.at_s <= deadline:
                self.schedule(action.at_s, lambda action=action: self.submit(action))
        # Events at contact boundaries change eligibility even when no job completes.
        calendars = (self.scenario.ground_contacts, *self.scenario.crosslink_contacts.values())
        for time in sorted({t for calendar in calendars for window in calendar
                            for t in (window.start_s, window.end_s) if 0 < t < deadline}):
            self.schedule(time, lambda: None)

        while True:
            while self.callbacks and self.callbacks[0][0] <= self.now:
                _, _, callback = heappop(self.callbacks)
                callback()
            if self.now >= deadline:
                break
            self.select_transfers()
            next_time = min(deadline, self.callbacks[0][0] if self.callbacks else deadline)
            for link in self.links.values():
                if link.active is not None:
                    next_time = min(next_time, self.now + link.active.remaining / link.rate)
            elapsed = next_time - self.now
            for link in self.links.values():
                if link.active is not None:
                    time_to_finish = link.active.remaining / link.rate
                    if time_to_finish <= elapsed or self.now + time_to_finish <= next_time:
                        link.active.remaining = 0.0
                    else:
                        link.active.remaining = max(0.0, link.active.remaining - elapsed * link.rate)
            self.now = next_time
            self.process_completed_transfers()

        return self.report()

    def report(self):
        deadline = self.scenario.deadline_s
        outages = [self.service_since.get(target, deadline) for target in sorted(self.affected_services)]
        return {
            "summary": {
                "deadline_s": deadline,
                "faulty_applications": sum(app.fault != "healthy" for app in self.apps.values()),
                "recovered_applications": len(self.recovery_time),
                "affected_services": len(self.affected_services),
                "restored_services": len(self.affected_services & self.service_since.keys()),
                "mean_service_outage_s": round(sum(outages) / len(outages), 9) if outages else 0.0,
            },
            "applications": {
                target: {
                    "initially_healthy": app.fault == "healthy",
                    "healthy_at_deadline": self.healthy[target],
                    "verified_recovery_s": self.recovery_time.get(target),
                    "first_ground_receipt_s": self.receipt_time.get(target),
                    "service_available_s": self.service_since.get(target),
                }
                for target, app in self.apps.items()
            },
            "jobs": self.jobs,
            "pending_transfers": [
                {"link": name, "kind": transfer.kind,
                 "bytes_remaining": round(transfer.remaining, 9), **transfer.details}
                for name, link in self.links.items() for transfer in link.queue
            ],
            "events": self.events,
        }


def _validate_action(action):
    if (isinstance(action.at_s, bool) or not isinstance(action.at_s, (int, float))
            or not isfinite(action.at_s) or action.at_s < 0):
        raise ValueError("Action at_s must be finite and nonnegative")
    if action.kind not in {"code", "state"}:
        raise ValueError(f"Unknown action kind: {action.kind}")
    if (not isinstance(action.targets, (tuple, list)) or not action.targets
            or any(not isinstance(t, str) or not t for t in action.targets)
            or len(set(action.targets)) != len(action.targets)):
        raise ValueError("Action targets must contain unique, nonempty application IDs")
    if type(action.validated) is not bool or type(action.verification_passes) is not bool:
        raise ValueError("Action outcomes must be boolean")
    if action.kind == "code":
        if not all(isinstance(value, str) and value
                   for value in (action.component, action.version, action.candidate_id)):
            raise ValueError("Code actions require a component, version, and candidate_id")
        if type(action.package_bytes) is not int or action.package_bytes <= 0:
            raise ValueError("Code actions require a positive package_bytes value")
    elif action.candidate_id is not None or action.package_bytes != 0:
        raise ValueError("State actions carry commands only")
