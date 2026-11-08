"""The single owner of requested actions, confirmed device state, and events."""
from collections import deque
from copy import deepcopy
import time

from gateway.contracts import Decision, DeviceCommand, Feedback, InputEvent


class SystemState:
    def __init__(self, timeout_seconds=4):
        self.timeout_seconds = timeout_seconds
        self.devices = {name: {"confirmed": None, "desired": None, "pending": None,
                               "status": "unknown", "message": "Simulator ready; state unconfirmed."}
                        for name in ("lights", "pump")}
        self.commands = {}
        self.events = deque(maxlen=60)
        self.sequence = 0

    def event(self, kind, message, **details):
        self.sequence += 1
        record = {"sequence": self.sequence, "time": time.time(), "kind": kind,
                  "message": message, **details}
        self.events.append(record)
        return record

    def snapshot(self):
        return deepcopy({"mode": "simulated", "devices": self.devices, "events": list(self.events),
                         "commands": list(self.commands.values())[-30:]})

    def begin(self, event: InputEvent, decision: Decision):
        existing = self.commands.get(event.request_id)
        if existing:
            if existing["device"] != decision.device or existing["value"] != decision.value:
                raise ValueError("This command ID was already used for a different action.")
            return existing.copy()
        device = self.devices[decision.device]
        if device["pending"]:
            raise ValueError(f"{decision.device} already has a pending command. Please wait for feedback.")
        # Bound memory while keeping every pending command available.
        if len(self.commands) >= 200:
            old = next((key for key, item in self.commands.items() if item["status"] != "pending"), None)
            if old is not None:
                del self.commands[old]
        command = DeviceCommand(command_id=event.request_id, device=decision.device, value=decision.value)
        item = {**command.model_dump(), "status": "pending", "created_at": time.time(),
                "deadline": time.monotonic() + self.timeout_seconds}
        self.commands[event.request_id] = item
        device.update(desired=decision.value, pending=event.request_id, status="pending",
                      message="Waiting for simulated MCU feedback.")
        self.event("pending", f"Requested {decision.device} {'on' if decision.value else 'off'}.",
                   command_id=event.request_id, source=event.source)
        return item.copy()

    def feedback(self, feedback: Feedback):
        command = self.commands.get(feedback.command_id)
        if command is None or command["device"] != feedback.device:
            raise ValueError("Feedback does not match a known command and device.")
        if command["status"] != "pending":
            return command.copy()  # Duplicate or late feedback cannot overwrite a result.
        if feedback.outcome == "success" and feedback.reported_value != command["value"]:
            raise ValueError("Successful feedback must confirm the requested value.")
        device = self.devices[feedback.device]
        if feedback.reported_value is not None:
            device["confirmed"] = feedback.reported_value
        device.update(pending=None, status=feedback.outcome, message=feedback.message)
        command.update(status=feedback.outcome, feedback=feedback.model_dump(), completed_at=time.time())
        self.event(feedback.outcome, feedback.message, command_id=feedback.command_id, code=feedback.code)
        return command.copy()

    def expire(self):
        for command in list(self.commands.values()):
            if command["status"] == "pending" and time.monotonic() >= command["deadline"]:
                self.feedback(Feedback(command_id=command["command_id"], device=command["device"],
                                       outcome="timeout", reported_value=None, code="NO_CONFIRMATION",
                                       message=f"{command['device']} did not confirm the command before the timeout."))

    def fail(self, command_id, message):
        command = self.commands.get(command_id)
        if command and command["status"] != "pending":
            return command.copy()
        if command and command["status"] == "pending":
            return self.feedback(Feedback(command_id=command_id, device=command["device"], outcome="error",
                                          reported_value=None, code="WORKFLOW_ERROR", message=message))
        self.event("error", message, command_id=command_id)
        return None
