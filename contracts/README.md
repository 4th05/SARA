# Component interfaces

`gateway/contracts.py` defines the validated Python messages. The adjacent JSON
schemas describe those messages for teammates working in other languages.

## Recognition input

POST `/api/input` to the gateway using the pairing code in the Authorization
header (`Bearer CODE`). Node-RED receives the same input through its private
`/ioe/event` endpoint. Example:

```json
{
  "request_id": "unique-request-id",
  "conversation_id": "user-session-id",
  "source": "text",
  "text": "Could you turn on the kitchen light?",
  "confidence": 1.0
}
```

Sources are `text`, `voice`, and `gesture`. Voice input contains the transcript.
Gesture input contains the recognized label. The reference mapping is
`Open_Palm` → all three lights on and `Closed_Fist` → all three lights off, with confidence >= 0.8.
Camera recognition requires three consecutive detections and sends one event
per held gesture. These mappings are provisional and should be agreed with the
ML teammate and intended users.

## Interpreted actions and request batches

Ollama returns `kind`, an `actions` list, `reply`, and `language` (`en` or `pt`).
For example, a mixed request becomes:

```json
{"kind":"command","actions":[{"device":"kitchen","value":true},{"device":"bedroom","value":false}],"reply":"I will request those changes.","language":"en"}
```

“All lights” expands into bathroom, kitchen, and bedroom. The complete batch is
reserved before execution. Node-RED Split/Join nodes carry independent child
command IDs and collect results into the original request ID. Successes are
recorded as soon as they arrive, even while another room is waiting or timing out.
The overall result may be `partial`; each feedback entry retains its actual outcome.
Legacy single-device decisions and the `lights` group remain accepted for existing callers.

## Device command and feedback

The device adapter receives:

```json
{"command_id":"unique-request-id","device":"kitchen","value":true}
```

It returns feedback:

```json
{
  "command_id": "unique-request-id",
  "device": "kitchen",
  "outcome": "success",
  "reported_value": true,
  "code": "OK",
  "message": "Light controller confirmed on."
}
```

Devices are `bathroom`, `kitchen`, `bedroom`, and `pump`. The legacy `lights` group addresses all three rooms. Outcomes are `success`, `warning`, `error`, and
`timeout`. A successful response must confirm the requested value. A warning
can report an applied value. Errors/timeouts use null when the device has no
new state to report. Python preserves the last confirmed value in that case.
An acknowledgement describes controller execution; physical lamp/pump health
requires appropriate sensor feedback and fault codes.

The current adapter is `gateway/devices/mock.py`. The future BLE adapter should
provide its `execute(DeviceCommand) -> Feedback` boundary and preserve command
IDs for duplicate protection. Agree GATT UUIDs, payload encoding/framing, maximum
payload sizes, notification format, and firmware-side duplicate handling with
the firmware teammate before implementing physical BLE communication.

Python owns central state. Node-RED coordinates service calls. Pending commands
expire after the configured timeout. Late/duplicate feedback cannot overwrite
a terminal result. State and recent events are held in memory and start fresh
when the gateway restarts.

## Spoken replies

`GET /api/system` includes ordered events. Events marked `speak: true` contain
`language` and the exact spoken text in `message`. With the pairing-code bearer
header, fetch `GET /api/speech/<sequence>.wav` for real local TTS audio. The web
app queues these in sequence and provides an audio player for replay. The event
history and audio cache are bounded and never persisted. `POST /api/speech/test`
creates a short voice-output check. Voice input still arrives through WebRTC,
is transcribed by Whisper, and goes through the actual Ollama/Node-RED workflow.
