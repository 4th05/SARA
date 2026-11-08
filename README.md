# Adaptive Assistive IoE

A phone browser sends camera/microphone media to a local Python gateway. Whisper
transcribes speech, LangChain/Ollama interprets requests, and Node-RED coordinates
commands and feedback. One Python `SystemState` owns device state.

The current light and water-pump controllers are **simulated MCUs**. Their
success, warning, error, and timeout behavior is selectable in the web interface.

## Start on this Mac

From the IoE folder:

```sh
./tools/start_system.sh
```

The launcher starts/reuses local Ollama and Node-RED, starts the gateway, and
prints a fresh six-digit pairing code plus the phone address.

- Monitor and test controls: http://localhost:8080/?view=monitor
- Node-RED workflow editor: http://localhost:1880/
- Phone on this Wi-Fi: https://192.168.1.163:8443/

Enter the code in the page and select **Connect controls**. On the phone select
camera, microphone, or both and **Start sharing**. Microphone commands are
interpreted automatically after a short pause in speech. The audio meter, heard
transcript, processing status, event list, and device cards show each stage.
Ctrl+C closes the gateway and services started by the launcher.

## Test without hardware

1. Connect the controls using the pairing code.
2. Select the desired feedback for the light or pump.
3. Type a request such as “Could you switch on the lamp?” and select **Send through LLM**.
4. Watch pending state, device feedback, and the confirmed result.

| Simulator outcome | Behavior |
|---|---|
| Success | Applies the command and confirms its value. |
| Warning | Applies the command and reports a simulated supply warning. |
| Error | Rejects the command and preserves the last confirmed value. |
| Timeout | Sends no timely acknowledgement; Python records a timeout. |

“Turn it off” without a clear target/context asks for clarification. Follow-up
questions share context within a browser or phone-media session. The state log
provides actual outcomes to subsequent LLM requests, including reported errors.
**Read replies aloud** uses voices available in that browser and pauses that
page's microphone transmission while speaking.

**Send test gesture** injects a recognition event through the same workflow.
Optional live camera recognition uses MediaPipe. Enable **Detect camera gestures**
for the reference mapping of open palm to light on and closed fist to light off.
The gesture model requires three stable detections at >= 80% confidence and
suppresses repeated events from a held gesture. The ML teammate can replace this
adapter and agree the intended gesture mappings.

## Workflow

Open the **IoE workflow** tab in Node-RED. Its saved graph contains:

1. Recognition/text input.
2. Python LangChain/Ollama interpretation.
3. A branch between a command and clarification/conversation.
4. Registration of a pending command in Python state.
5. The selected simulated MCU response.
6. Matching feedback and confirmed-state update.
7. A response to the interface and a Debug output.

The Catch path records workflow errors. A Python watchdog expires pending commands
if feedback never arrives. Duplicate request IDs reuse their existing result;
conflicting reuse is rejected. Commands for a busy device are rejected until its
pending request finishes. The gateway's original WebRTC/JPEG/WAV APIs remain
available for notebook experiments.

## Configuration and local AI

Tracked defaults live in `config/settings.json`. Optional machine-specific
values go in `.local/settings.json`. Models, credentials, certificates, and
runtime caches live under ignored local paths.

- LLM: `qwen3.5:0.8b-q8_0`, with thinking disabled and bounded context/output.
- Speech: faster-whisper `tiny`, CPU int8.
- Orchestration: Node-RED 5.0.9, Node.js 24.
- State/device/message handling: Python 3.12 and Pydantic.
- Optional reference gestures: MediaPipe 1.2.0.

The explicit GGUF model tag supports schema-constrained output on the tested Mac
and avoids its Homebrew MLX packaging error. Python validates every decision and
checks command targets against wording/context. Invalid inference or unavailable
services produce errors/clarification. Model interpretation still requires
application-specific accuracy and latency evaluation before physical deployment.
The model and speech/gesture services share CPU/RAM on the Pi; Pi performance
has yet to be measured.

## Install on another Mac or Ubuntu Raspberry Pi

Install Python 3.12 with venv support, Node.js 24/npm, Git, and Ollama from its
official installer. Clone the repository, then from its root:

```sh
./tools/setup.sh
```

This installs pinned Python and Node-RED dependencies and downloads local speech
and LLM models. To include reference camera gesture detection:

```sh
./tools/setup.sh --with-gesture
```

For localhost-only testing:

```sh
./tools/start_system.sh --http-only
```

Use Firefox on the Pi to open the local monitor/editor addresses above.
Node-RED and Ollama bind to loopback; the phone uses the gateway's HTTPS port.
After testing, commit Node-RED edits with ordinary Git. The editor's **Deploy**
button saves `orchestration/node-red/flows.json`.

## Phone HTTPS

Phones need a trusted HTTPS origin for camera/microphone access. Generate a local
development certificate for the gateway's current Wi-Fi IP:

```sh
.venv/bin/python gateway/make_certificate.py --host YOUR_GATEWAY_IP
```

Trust the generated public `.local/tls/development-ca.crt` on the test phone using
its certificate settings, then open `https://YOUR_GATEWAY_IP:8443/`. Keep the
private `gateway.key` local. The generator refuses to overwrite existing files.
For a changed IP, archive the old `.local/tls` directory and generate a new one.
Development certificates expire after 30 days. A deployed system can use a
publicly trusted HTTPS name. Both devices need a network permitting local traffic.

## Team boundaries

- `gateway/recognition/` holds runtime inference adapters; `notebooks/` contains experiments.
- `gateway/devices/mock.py` is the current device adapter. Firmware belongs in `firmware/`.
- `gateway/state.py` owns state; `gateway/runtime.py` binds service workers and APIs.
- `orchestration/node-red/flows.json` defines the application workflow.
- `contracts/` documents recognition, command, and feedback messages.
- `tools/` contains setup/start commands; `.github/workflows/ci.yml` checks integration.

See `contracts/README.md` for the input/output boundaries agreed across the team.
The previous Arduino camera experiment, toolchain, and VS Code configuration
were archived under `.local/archive/before-workflow-*` for recovery.

## Validation

```sh
.venv/bin/python -m unittest discover -s gateway -p 'test_*.py' -v
```

The tests negotiate actual synthetic WebRTC media and run the saved Node-RED
workflow in an isolated process. They cover feedback scenarios, timeouts,
clarification, internal API authentication, duplicate requests, state integrity,
and the audio-worker connection using deterministic test recognition. They require
installed Node-RED dependencies and use synthetic input rather than a microphone.
Live model and generated-speech-through-WebRTC checks are performed separately.

References:
- [Node-RED flow organization](https://nodered.org/docs/developing-flows/flow-structure)
- [LangChain ChatOllama](https://docs.langchain.com/oss/python/integrations/chat/ollama)
- [Ollama structured output](https://docs.ollama.com/capabilities/structured-outputs)
- [Model tags](https://ollama.com/library/qwen3.5/tags)
- [Homebrew MLX structured-output issue](https://github.com/ollama/ollama/issues/18567)
