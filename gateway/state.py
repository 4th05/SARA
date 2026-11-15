"""The single owner of requested actions, confirmed device state, and events."""
from collections import deque
from copy import deepcopy
import time

from gateway.contracts import (CommandBatch, Decision, DeviceCommand, Feedback,
                               InputEvent, ROOMS, DEVICES)
from gateway.responses import acknowledge, describe_feedback


class SystemState:
    def __init__(self, timeout_seconds=4):
        self.timeout_seconds = timeout_seconds
        self.devices = {name: {"confirmed": None, "desired": None, "pending": None,
                               "status": "unknown", "message": "Simulator ready; state unconfirmed."}
                        for name in (*DEVICES, "lights")}
        self.commands = {}
        self.groups = {}
        self.events = deque(maxlen=60)
        self.sequence = 0
        self.owners = {}

    def event(self, kind, message, **details):
        request_id = details.get("request_id")
        if request_id in self.owners:
            details.setdefault("conversation_id", self.owners[request_id])
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
        targets = ROOMS if decision.device == "lights" else (decision.device,)
        if device["pending"] or any(self.devices[name]["pending"] for name in targets):
            raise ValueError(f"{decision.device} already has a pending command. Please wait for feedback.")
        if decision.device in ROOMS and self.devices["lights"]["pending"]:
            raise ValueError("A command for the lights group is still pending.")
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

    def begin_batch(self, event, decision):
        previous = self.groups.get(event.request_id)
        actions = [action.model_dump() for action in decision.actions]
        if previous:
            if previous["actions"] != actions:
                raise ValueError("This request ID already describes different actions.")
            return self.command_batch(event.request_id)
        # Reserve every target before making any change, so a busy room rejects the entire request.
        targets = {action.device for action in decision.actions}
        affected = (targets - {"lights"}) | set(ROOMS) if "lights" in targets else targets
        if any(self.devices[name]["pending"] for name in affected):
            raise ValueError("A requested device is busy. Please wait for its feedback.")
        if targets.intersection(ROOMS) and self.devices["lights"]["pending"]:
            raise ValueError("A command for the lights group is still pending.")
        identifiers = [event.request_id if len(actions) == 1 else f"{event.request_id}/{action.device}"
                       for action in decision.actions]
        if any(identifier in self.commands for identifier in identifiers):
            raise ValueError("A command identifier is already registered.")
        if len(self.groups) >= 200:
            old = next((key for key, group in self.groups.items() if "result" in group), None)
            if old is not None:
                del self.groups[old]
        self.groups[event.request_id] = {"actions": actions, "command_ids": identifiers,
                                         "language": decision.language}
        for action, identifier in zip(decision.actions, identifiers):
            child = event.model_copy(update={"request_id": identifier})
            single = Decision(kind="command", device=action.device, value=action.value,
                              reply=decision.reply, language=decision.language)
            self.begin(child, single)
        self.event("acknowledgement", decision.reply,
                   request_id=event.request_id, speak=True, language=decision.language)
        return self.command_batch(event.request_id)

    def command_batch(self, request_id):
        group = self.groups[request_id]
        commands = [DeviceCommand.model_validate({key: self.commands[identifier][key]
                    for key in ("command_id", "device", "value")}) for identifier in group["command_ids"]]
        return CommandBatch(request_id=request_id, commands=commands)

    def complete_batch(self, request_id, announce=True):
        group = self.groups[request_id]
        if "result" in group:
            return group["result"]
        commands = [self.commands[identifier] for identifier in group["command_ids"]]
        if any(command["status"] == "pending" for command in commands):
            raise ValueError("The request still has pending controller feedback.")
        feedback = [Feedback.model_validate(command["feedback"]) for command in commands]
        outcomes = {value.outcome for value in feedback}
        applied = any(value.reported_value is not None for value in feedback)
        if len(outcomes) == 1:
            status = feedback[0].outcome
        elif outcomes.issubset({"success", "warning"}):
            status = "warning"
        else:
            status = "partial" if applied else "error"
        spoken = feedback
        if {value.device for value in feedback} == set(ROOMS) and len(outcomes) == 1:
            if len({value.reported_value for value in feedback}) == 1:
                spoken = [feedback[0].model_copy(update={"device": "lights"})]
        reply = " ".join(describe_feedback(value, group["language"]) for value in spoken)
        result = {"request_id": request_id, "status": status, "reply": reply,
                  "device": feedback[0].device if len(feedback) == 1 else "lights",
                  "feedback": [value.model_dump() for value in feedback]}
        group["result"] = result
        if announce:
            self.announce_result(request_id)
        return result

    def announce_result(self, request_id, reply=None, language=None, source="controller"):
        group = self.groups[request_id]
        if group.get("announced"):
            return group["result"]
        result = group["result"]
        if reply:
            result["reply"] = reply
        group["announced"] = True
        self.event(result["status"], result["reply"], request_id=request_id, speak=True,
                   language=language or group["language"], reply_source=source)
        return result

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
        if feedback.device == "lights":
            for room in ROOMS:
                if feedback.reported_value is not None:
                    self.devices[room]["confirmed"] = feedback.reported_value
                self.devices[room].update(status=feedback.outcome, message=feedback.message)
        elif feedback.device in ROOMS:
            values = [self.devices[room]["confirmed"] for room in ROOMS]
            self.devices["lights"]["confirmed"] = values[0] if len(set(values)) == 1 else None
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
