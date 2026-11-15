# Adaptive Assistive IoE

A phone browser sends camera/microphone media to a local Python gateway. Whisper
transcribes speech, LangChain/Ollama interprets requests, and Node-RED coordinates
commands and feedback. One Python `SystemState` owns device state.

Only the Arduino controllers are simulated. The light controller has separate
**bathroom, kitchen, and bedroom** outputs. The existing water-pump output is also available. Their
success, warning, error, and timeout behavior is selectable in the web interface.

## Start on this Mac

From the IoE folder:

```sh
./tools/start_system.sh
```

The launcher starts/reuses Ollama and Node-RED and prints the phone HTTPS URL.
Open that URL on the phone. User access is password-free.

Choose **Audio**, **Video**, or **Both**. Hold the large capture button and release
it when finished. Allow browser microphone/camera permission when prompted. If
permission completes after release, hold again to start the recording. Capture
is limited to 15 seconds. Audio is recorded in the browser and submitted on
release, so the beginning of a request is preserved while the live connection
starts. WebRTC provides live media to the monitor while capturing. All local
tracks stop on release.

After release, Whisper transcribes the recording, Ollama maps its meaning to
room actions, and Node-RED coordinates the simulated Arduino feedback. Ollama
then describes the actual result. The same text appears in **System feedback**
and is converted to Piper audio. The player supports replay and manual Play if
a browser blocks automatic audio. A factual controller reply is used if the
final LLM summary is unavailable. This fallback is marked in admin events.

The supported lights are **bathroom**, **kitchen**, and **bedroom**. Try “turn all
lights on”, “make the cooking area brighter and leave the bedroom dark”, or
mixed room commands. Interpretation uses the LLM prompt and structured output.
Ambiguous or contradictory requests should ask for clarification. Video capture
and preview are available; gesture classification remains deferred for this stage.

For monitoring, click **Admin** on the same page and log in with `admin`.
The password is configured with `ADMIN_PASSWORD` in the root `.env` file and is
loaded on startup. `.env` is ignored by Git; `.env.example` provides the default.
The admin monitor contains service health, live preview, transcripts, events,
and simulator outcome dropdowns. Direct monitor URLs and admin APIs require the
same login. Public users receive their own feedback log and audio.

| Simulator outcome | Behavior |
|---|---|
| Success | Applies the command and confirms the state. |
| Warning | Applies the command and reports a simulated supply warning. |
| Error | Rejects the command and preserves the last confirmed value. |
| Timeout | Reports missing confirmation and preserves the last confirmed value. |

Different rooms can return different outcomes. Partial results describe every
room's actual result. Physical MCU and Raspberry Pi testing remain outstanding.

Local desktop UI is at http://localhost:8080/ and the workflow editor is at
http://localhost:1880/. Phones use the HTTPS URL printed by the launcher.
Ctrl+C closes the gateway and services started by the launcher.

## Workflow

Open the **IoE workflow** tab in Node-RED. Its saved graph contains:

1. Recognition/text input.
2. Python LangChain/Ollama interpretation.
3. A branch between a command and clarification/conversation.
4. Atomic registration of the requested rooms in Python state and a spoken acknowledgement.
5. Native Split/Join nodes dispatching one command per room.
6. The Arduino simulator applying virtual output values and returning selected feedback.
7. Each room’s matching feedback updating its confirmed state immediately.
8. A combined spoken result, a response to the interface, and a Debug output.

The Catch path records workflow errors. A Python watchdog expires pending commands
if feedback never arrives. Duplicate request IDs reuse their existing result;
conflicting reuse is rejected. Commands for a busy device are rejected until its
pending request finishes. The gateway's original WebRTC/JPEG/WAV APIs remain
available for notebook experiments.

## Configuration and local AI

Tracked defaults live in `config/settings.json`. Optional machine-specific
values go in `.local/settings.json`. Models, credentials, certificates, and
runtime caches live under ignored local paths.

- LLM: `qwen3.5:2b-q4_K_M`, with thinking disabled and bounded context/output.
- Speech recognition: faster-whisper `tiny`, CPU int8.
- Speech output: Piper 1.8.0 neural voices, replayable WAV audio.
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
recognition, neural voice, and LLM models. To include reference camera gesture detection:

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
- `gateway/devices/mock.py` is the Arduino simulator. Its `pins` dictionary holds virtual output levels; only this adapter is simulated. Firmware belongs in `firmware/`.
- `gateway/speech_output.py` creates local speech audio and `gateway/responses.py` formats truthful acknowledgements/results.
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
multi-room partial results, atomic reservations, clarification, authenticated speech playback, duplicate requests, state integrity,
and the audio-worker connection using deterministic test recognition. They require
installed Node-RED dependencies and use synthetic input rather than a microphone.
Live model and generated-speech-through-WebRTC checks are performed separately.

References:
- [Node-RED flow organization](https://nodered.org/docs/developing-flows/flow-structure)
- [LangChain ChatOllama](https://docs.langchain.com/oss/python/integrations/chat/ollama)
- [Ollama structured output](https://docs.ollama.com/capabilities/structured-outputs)
- [Model tags](https://ollama.com/library/qwen3.5/tags)
- [Homebrew MLX structured-output issue](https://github.com/ollama/ollama/issues/18567)

- [Piper neural speech engine and Python API](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md)
- [English voice model card](https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/lessac/medium/MODEL_CARD)
- [Portuguese voice model card](https://huggingface.co/rhasspy/piper-voices/blob/main/pt/pt_BR/faber/medium/MODEL_CARD)
- [Browser voice availability](https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesis/getVoices)
