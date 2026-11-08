"""Shared configuration for the gateway and local workflow."""
import json
import os
from pathlib import Path
import secrets

from pydantic import BaseModel, ConfigDict, Field

PROJECT = Path(__file__).resolve().parent.parent
LOCAL = PROJECT / ".local"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ollama_model: str = "qwen3.5:0.8b-q8_0"
    ollama_url: str = "http://127.0.0.1:11434"
    workflow_url: str = "http://127.0.0.1:1880/ioe/event"
    command_timeout_seconds: float = Field(default=4, ge=1, le=30)
    inference_timeout_seconds: float = Field(default=40, ge=5, le=120)
    speech_model: str = "tiny"
    speech_threshold: float = Field(default=0.012, gt=0, lt=1)


def load_settings():
    values = json.loads((PROJECT / "config/settings.json").read_text())
    local = LOCAL / "settings.json"
    if local.exists():
        values.update(json.loads(local.read_text()))
    return Settings.model_validate(values)


def control_token():
    """Private service token shared through a local file, never in flows.json."""
    LOCAL.mkdir(exist_ok=True)
    path = LOCAL / "workflow-token"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    token = secrets.token_urlsafe(32)
    with os.fdopen(descriptor, "w") as output:
        output.write(token)
    return token
