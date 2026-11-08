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
  "text": "Could you turn on the light?",
  "confidence": 1.0
}
```

Sources are `text`, `voice`, and `gesture`. Voice input contains the transcript.
Gesture input contains the recognized label. The reference mapping is
`Open_Palm` → lights on and `Closed_Fist` → lights off, with confidence >= 0.8.
Camera recognition requires three consecutive detections and sends one event
per held gesture. These mappings are provisional and should be agreed with the
ML teammate and intended users.

## Device command and feedback

The device adapter receives:

```json
{"command_id":"unique-request-id","device":"lights","value":true}
```

It returns feedback:

```json
{
  "command_id": "unique-request-id",
  "device": "lights",
  "outcome": "success",
  "reported_value": true,
  "code": "OK",
  "message": "Light controller confirmed on."
}
```

Devices are `lights` and `pump`. Outcomes are `success`, `warning`, `error`, and
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
