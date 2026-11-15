"""Validated messages exchanged between recognition, Node-RED, and devices."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOMS = ("bathroom", "kitchen", "bedroom")
DEVICES = (*ROOMS, "pump")
Device = Literal["bathroom", "kitchen", "bedroom", "lights", "pump"]
Outcome = Literal["success", "warning", "error", "timeout"]


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InputEvent(Message):
    request_id: str = Field(min_length=1, max_length=80)
    conversation_id: str = Field(default="demo", min_length=1, max_length=80)
    source: Literal["text", "voice", "gesture"] = "text"
    text: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(default=1.0, ge=0, le=1)
    language: Literal["en", "pt"] | None = None


class Action(Message):
    device: Device
    value: bool


class IntentPlan(Message):
    kind: Literal["command", "clarify", "status", "reply"]
    actions: list[Action] = Field(max_length=4)
    reply: str = Field(min_length=1, max_length=500)
    language: Literal["en", "pt"]


class Decision(IntentPlan):
    actions: list[Action] = Field(default_factory=list, max_length=4)
    language: Literal["en", "pt"] = "en"
    # Accept the original single-device contract used by existing adapters.
    device: Device | None = None
    value: bool | None = None

    @model_validator(mode="after")
    def command_has_target(self):
        if self.kind == "command":
            if not self.actions and self.device is not None and self.value is not None:
                self.actions = [Action(device=self.device, value=self.value)]
            if not self.actions:
                raise ValueError("A command requires at least one device action.")
            targets = [action.device for action in self.actions]
            if len(set(targets)) != len(targets):
                raise ValueError("Each device may appear only once in a command.")
            if "lights" in targets and any(room in targets for room in ROOMS):
                raise ValueError("Use individual rooms or the legacy lights group, not both.")
        elif self.actions:
            raise ValueError("Only command decisions may contain actions.")
        return self


class DeviceCommand(Message):
    command_id: str = Field(min_length=1, max_length=120)
    device: Device
    value: bool


class Feedback(Message):
    command_id: str
    device: Device
    outcome: Outcome
    reported_value: bool | None
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)


class MockSetting(Message):
    device: Device
    outcome: Outcome
    delay_ms: int = Field(default=500, ge=0, le=3000)


class RecognitionSetting(Message):
    voice_mode: Literal["manual", "automatic"] = "manual"
    voice_enabled: bool = True
    gesture_enabled: bool = False


class CommandBatch(Message):
    request_id: str = Field(min_length=1, max_length=80)
    commands: list[DeviceCommand] = Field(min_length=1, max_length=4)


class FeedbackBatch(Message):
    request_id: str = Field(min_length=1, max_length=80)
    feedback: list[Feedback] = Field(min_length=1, max_length=4)


class SpokenReply(Message):
    reply: str = Field(min_length=1, max_length=1000)
    language: Literal["en", "pt"]


class LightPlan(Message):
    bathroom: bool | None
    kitchen: bool | None
    bedroom: bool | None
    response_kind: Literal["clarify", "status", "reply"]
    reply: str = Field(min_length=1, max_length=500)
    language: Literal["en", "pt"]
