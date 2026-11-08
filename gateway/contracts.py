"""Validated messages exchanged between recognition, Node-RED, and devices."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Device = Literal["lights", "pump"]
Outcome = Literal["success", "warning", "error", "timeout"]


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InputEvent(Message):
    request_id: str = Field(min_length=1, max_length=80)
    conversation_id: str = Field(default="demo", min_length=1, max_length=80)
    source: Literal["text", "voice", "gesture"] = "text"
    text: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(default=1.0, ge=0, le=1)


class Decision(Message):
    kind: Literal["command", "clarify", "status", "reply"]
    device: Device | None
    value: bool | None
    reply: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def command_has_target(self):
        if self.kind == "command" and (self.device is None or self.value is None):
            raise ValueError("A command requires a supported device and boolean value.")
        return self


class DeviceCommand(Message):
    command_id: str = Field(min_length=1, max_length=80)
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
    voice_enabled: bool = True
    gesture_enabled: bool = False
