# Component interfaces

`gateway/contracts.py` defines the validated Python messages. The adjacent JSON
schemas describe those messages for teammates working in other languages.

## Recognition input

POST `/api/input` to the gateway without a user password. The gateway associates
requests with an anonymous HttpOnly browser cookie. Node-RED receives the same input through its private
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
Gesture input contains the recognized label. MediaPipe recognizes `Thumb_Up`
or `Thumb_Down` across three consecutive frames with confidence >= 0.7. One
gesture is accepted per capture. Ollama interprets thumbs-up as all three lights
on and thumbs-down as all three lights off through the same workflow as speech.

## Interpreted actions and request batches

Ollama returns a nullable boolean for each room, a response kind, reply, and
language (`en` or `pt`). Python validates this plan and produces a `Decision`
with `kind`, `actions`, `reply`, and `language`. A mixed request becomes:

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
`language` and the exact spoken text in `message`. Using the same browser session, fetch `GET /api/speech/<sequence>.wav` for real local TTS audio. The web
app queues these in sequence and provides an audio player for replay. The event
history and audio cache are bounded and never persisted. `POST /api/speech/test`
creates a short voice-output check. Browser audio recordings are transcribed by
Whisper and go through the Ollama/Node-RED workflow. WebRTC supplies live media
to the admin monitor and notebook.

## Hold-to-capture and admin access

The browser records audio while the capture button is held. On release it stops
all local media tracks and sends the audio Blob to `POST /api/recording` with its
actual audio content type (WebM/Opus or MP4/AAC). The gateway decodes the recording
to mono 16 kHz PCM and queues the existing Whisper/Ollama workflow. Accepted
recordings continue processing after the WebRTC preview connection closes.
`GET /api/user` returns the current room states, user processing status, and only
that browser user's spoken feedback events. Speech WAV requests use the same
cookie and require event ownership or an authenticated admin session.

`POST /api/admin/login` accepts `{"password":"admin"}` and establishes an
HttpOnly admin session cookie. The password comes from `ADMIN_PASSWORD` in `.env`.
`/api/system`, `/api/mock`, `/api/recognition`, raw media preview, and monitoring
APIs require admin login. `POST /api/admin/logout` revokes the session.
The existing private Node-RED service token remains required for `/internal/`.

The earlier session-based `POST /api/voice` recording interface remains available
for adapters. New browser clients use `/api/recording` so no audio is lost during
WebRTC negotiation. `POST /api/video/complete` accepts the camera session ID and
finishes classification of queued frames. Accepted gestures continue processing
after the camera connection closes. Unrecognized video produces a spoken retry
message. Feedback and audio remain associated with the same browser user.
