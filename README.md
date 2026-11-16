# SARA — Smart Assistive Residential Assistant

SARA is an LLM-powered home assistant designed to support independence for people
with disabilities through voice and gesture control. It connects everyday devices
to a local assistant that interprets requests and provides spoken feedback.

A phone browser sends camera/microphone media to a local Python gateway. Whisper
transcribes speech, LangChain/Ollama interprets requests, and Node-RED coordinates
commands and feedback. One Python `SystemState` owns device state.

Only the Arduino controllers are simulated. The light controller has separate
**bathroom, kitchen, and bedroom** outputs. The existing water-pump output is also available. Their
success, warning, error, and timeout behavior is selectable in the web interface.

## Install on Linux

The reference instructions use **Ubuntu 24.04 LTS, 64-bit**, on an x86-64 PC or
an ARM64 machine such as a Raspberry Pi 5 running Ubuntu. Other glibc-based Linux
distributions can use the same project commands after installing the prerequisite
versions and equivalent native libraries. Python wheel resolution has been checked
for both Linux architectures. A full installation on physical Ubuntu/Pi hardware
is still pending validation.

### Install system prerequisites

On Ubuntu 24.04, install Git, Make, Python 3.12 with venv support, and the native
libraries used by OpenCV and PortAudio.

```sh
sudo apt update
sudo apt install -y git make curl ca-certificates zstd \
  python3.12 python3.12-venv libgl1 libglib2.0-0t64 libportaudio2
```

Use **Python 3.12** for the project environment. If your Linux distribution uses
another default Python version, install 3.12 with your package manager or uv.
The following optional commands install uv and a separate Python 3.12 executable.

```sh
curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh
sh /tmp/uv-install.sh
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.12
```

The project creates its own `.venv` later. [Python downloads](https://www.python.org/downloads/),
[uv installation](https://docs.astral.sh/uv/getting-started/installation/),
[uv Python installation](https://docs.astral.sh/uv/guides/install-python/).

### Install Node.js 24 and npm

SARA uses **Node.js 24.15 or newer within version 24**. On Ubuntu/Debian, the
NodeSource repository provides the required Node.js branch, including npm.

```sh
curl -fsSL https://deb.nodesource.com/setup_24.x -o /tmp/nodesource_setup.sh
sudo bash /tmp/nodesource_setup.sh
sudo apt install -y nodejs
node --version
npm --version
```

For another Linux distribution, use its Node.js 24 package or the official
Linux x64/ARM64 downloads. [NodeSource instructions](https://github.com/nodesource/distributions/blob/master/DEV_README.md),
[Node.js downloads](https://nodejs.org/en/download),
[Node.js 24 release files](https://nodejs.org/dist/latest-v24.x/).

### Install and configure Ollama

Install the Ollama Linux executable using its official installer.

```sh
curl -fsSL https://ollama.com/install.sh -o /tmp/ollama-install.sh
sh /tmp/ollama-install.sh
command -v ollama
```

On Ubuntu with systemd, the installer creates an Ollama background service.
Disable that automatic service so SARA can own its Ollama process, model location,
and shutdown lifecycle.

```sh
sudo systemctl disable --now ollama
```

Run SARA's Make commands as your normal user. Its launcher sets `OLLAMA_MODELS`
to the repository's `.local/ollama/models` and binds Ollama to `127.0.0.1:11434`.
This lets `make stop_ui` close the services it uses. An already running server
uses the model storage configured for that server. [Ollama Linux installation](https://docs.ollama.com/linux),
[Ollama model storage configuration](https://docs.ollama.com/faq#where-are-models-stored).

### Download the project and install its dependencies

Clone the repository below. For an existing checkout, open its SARA directory
and run `make setup`.

```sh
git clone https://github.com/4th05/SARA.git SARA
cd SARA
make setup
```

`make setup` checks prerequisites, creates `.venv`, installs the pinned Python
packages, installs the project's Node-RED workspace with `npm ci`, copies
`.env.example` to `.env` if needed, and downloads all default AI models.
It temporarily starts Ollama when needed for the model download and closes that
temporary process afterwards. Internet access is used for package/model downloads.

| Component | Installation/download process | Local location |
|---|---|---|
| Python packages, including LangChain, aiortc, MediaPipe, faster-whisper, and Piper | `make setup` reads `gateway/requirements.txt` | `.venv/` |
| Node-RED 5.0.9 | `make setup` reads the npm lockfile | `orchestration/node-red/node_modules/` |
| Qwen LLM, `qwen3.5:2b-q4_K_M` | `make setup` runs `ollama pull` for the configured tag | `.local/ollama/models/` |
| Whisper `tiny` | `make setup` uses faster-whisper's model downloader | `.local/models/models--Systran--faster-whisper-tiny/` |
| MediaPipe Gesture Recognizer | `make setup` downloads `gesture_recognizer.task` | `.local/models/gesture_recognizer.task` |
| Piper English voice | `make setup` downloads `en_US-lessac-medium.onnx` and `en_US-lessac-medium.onnx.json` | `.local/models/piper/` |
| Piper Portuguese voice | `make setup` downloads `pt_BR-faber-medium.onnx` and `pt_BR-faber-medium.onnx.json` | `.local/models/piper/` |
| Notebook kernel and examples | `make notebooks` after setup | `.venv/` |

The selected LLM download is about 1.9 GB. Package and other model downloads
consume additional disk space. The model loaders use the files in the locations
above. To retry an interrupted model download after installing dependencies, run
`make models`.

The model and runtime sources are available from their publishers.

- [Node-RED installation](https://nodered.org/docs/getting-started/local).
- [Qwen model and exact Ollama tag](https://ollama.com/library/qwen3.5:2b-q4_K_M).
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and
  [Whisper tiny model files](https://huggingface.co/Systran/faster-whisper-tiny).
- [MediaPipe model documentation](https://developers.google.com/edge/mediapipe/solutions/vision/gesture_recognizer) and
  [gesture model file](https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task).
- [Piper installation](https://github.com/OHF-Voice/piper1-gpl),
  [English voice files](https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/lessac/medium), and
  [Portuguese voice files](https://huggingface.co/rhasspy/piper-voices/tree/main/pt/pt_BR/faber/medium).

### Configure the app

Default settings are in `config/settings.json`. Machine-specific overrides go
in `.local/settings.json`. The selected LLM tag is the `ollama_model` setting.
After changing that tag, run `make models` to download the selected model.

The admin password is `ADMIN_PASSWORD` in `.env`, initially `admin`.
Models, `.env`, credentials, and runtime caches are ignored by Git.
Node-RED's workflow is already included in `orchestration/node-red/flows.json`.
The launcher opens this workspace automatically.

### Configure phone HTTPS

The gateway serves the web app. A phone opens its HTTPS URL to provide the camera
and microphone. Find the Linux gateway's LAN IP with `hostname -I`, then use that
address for the certificate. Replace the example `192.168.1.50` below with your
actual address.

```sh
hostname -I
make cert GATEWAY_HOST=192.168.1.50
```

This creates the following files.

| File | Purpose |
|---|---|
| `.local/tls/development-ca.crt` | Public local CA certificate to transfer to the test phone |
| `.local/tls/gateway.crt` | Server certificate loaded by SARA |
| `.local/tls/gateway.key` | Private server key kept on the gateway |

Install the public CA certificate as a trusted CA on the phone. On iOS, enable
full trust for the installed certificate profile. Follow the device's certificate
settings and then open `https://YOUR_GATEWAY_IP:8443/`.
[Android certificate settings](https://support.google.com/pixelphone/answer/2844832),
[iOS certificate trust](https://support.apple.com/en-us/102390).

Certificates expire after 30 days. To regenerate them for expiry or a new IP,
archive the previous files and create a new certificate, then install the new
public CA on the phone.

```sh
mv .local/tls ".local/tls-$(date +%Y%m%d-%H%M%S)"
make cert GATEWAY_HOST=192.168.1.50
```

The phone and gateway need a LAN that allows traffic between them. SARA uses
TCP 8443 for phone HTTPS and negotiated UDP ports for WebRTC media. Permit these
between the phone and gateway if a firewall is enabled. The local UI uses TCP
8080, Node-RED TCP 1880, and Ollama TCP 11434 on loopback.

### Verify the installation

Start `make ui`, open `http://localhost:8080/` in Firefox or Chromium, and check
Admin for Node-RED, Ollama, and voice readiness. Open the printed HTTPS URL on
the phone, allow camera/microphone access, and test a short request.
For an initial localhost check before setting up phone HTTPS, use
`make ui UI_ARGS=--http-only`. Stop the system with `make stop_ui`.
[Firefox installation on Linux](https://support.mozilla.org/en-US/kb/install-firefox-linux).

| Setup problem | Action |
|---|---|
| Python version or venv error | Install Python 3.12 and its venv package, or use the uv steps above |
| Node.js version error | Install Node.js 24.15+ within version 24 |
| `libGL.so.1` or PortAudio import error | Install the Ubuntu native libraries listed above, or their distro equivalents |
| Ollama port 11434 remains occupied | Stop the systemd Ollama service with `sudo systemctl disable --now ollama`, then start SARA |
| Missing model | Run `make models` after setup |
| Phone camera/microphone unavailable | Check trusted HTTPS, the certificate's LAN IP, browser permissions, and LAN/firewall access |

The Node-RED workspace overrides `body-parser`, `multer`, and npm versions to
apply security updates. The 2026-11-16 scan found no known Python vulnerabilities
or high/critical Node dependency alerts. npm's bundled `glob` and `minimatch`
still produce 24 moderate alerts. [glob advisory](https://github.com/advisories/GHSA-3g3m-2fjr-6pw4),
[minimatch advisory](https://github.com/advisories/GHSA-g3hv-75wg-m249).

## Run and use SARA

From the SARA folder:

```sh
make ui
```

The launcher starts/reuses Ollama and Node-RED and prints the phone HTTPS URL.
Open that URL on the phone. User access is password-free.

Select **Audio**, **Video**, or both. Hold the capture button and release
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
Ambiguous or contradictory requests should ask for clarification.

For gestures, select **Video**, hold the capture button, and show a
**thumbs-up** to turn all three lights on or **thumbs-down** to turn them off.
Keep the hand visible and steady for about a second, then release. MediaPipe's
pretrained model must agree across three frames at 70% confidence or higher.
One gesture is accepted per recording. Its label goes through Ollama and the
same Node-RED/state/spoken-feedback workflow as speech. Releasing the camera
does not cancel an accepted request. With **Audio** also selected, speech is processed on release.

For monitoring, click **Admin** on the same page and log in with `admin`.
The password is configured with `ADMIN_PASSWORD` in the root `.env` file and is
loaded on startup. `.env` is ignored by Git; `.env.example` provides the default.
The admin monitor contains service health, live preview, transcripts, gestures, events,
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
The command stays open and displays service logs. To close the gateway, Node-RED,
Ollama, and their worker processes, run this in another terminal:

```sh
make stop_ui
```

This also stops project services left running after a previous launch. Ctrl+C
closes the gateway and services started by that launch.

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
- Gesture recognition uses MediaPipe 1.2.0 and its pretrained model. [Official model documentation](https://developers.google.com/edge/mediapipe/solutions/vision/gesture_recognizer).

Python validates decisions and controller feedback against the message contracts.
Invalid inference or unavailable services produce an error or clarification.
The Pi shares CPU/RAM between the models and gateway services. Its end-to-end
latency and physical-device integration still need hardware validation.

## Team boundaries

- `gateway/recognition/` holds runtime inference adapters; `notebooks/` contains experiments.
- `gateway/devices/mock.py` is the Arduino simulator. Its `pins` dictionary holds virtual output levels; only this adapter is simulated. Firmware belongs in `firmware/`.
- `gateway/speech_output.py` creates local speech audio and `gateway/responses.py` formats truthful acknowledgements/results.
- `gateway/state.py` owns state; `gateway/runtime.py` binds service workers and APIs.
- `orchestration/node-red/flows.json` defines the application workflow.
- `contracts/` documents recognition, command, and feedback messages.
- `tools/` contains setup/start commands; `.github/workflows/ci.yml` checks integration.

See `contracts/README.md` for the input/output boundaries agreed across the team.

## Notebook experiments

- [Images and gestures](notebooks/image_gesture.ipynb) collects PIL RGB images and
  provides an editable `predict_gesture(image)` example plus a live prediction loop.
- [Audio](notebooks/audio.ipynb) collects mono 16 kHz NumPy samples, plays the captured
  audio, and provides an editable `transcribe(audio)` example.

Run `make ui`, open the phone URL, and select Audio or Video. Open the matching
notebook on the gateway computer using the project's `.venv` kernel. Run Connect
and the capture cell, then hold the phone's capture button. Captured data stays
in notebook memory after release. The app's normal recognition continues during
these experiments.

Install the kernel and example dependencies with `make notebooks`. In VS Code,
select the project's `.venv` as the notebook kernel. The Python and Jupyter
extensions provide notebook editing and execution.
[VS Code Linux installation](https://code.visualstudio.com/docs/setup/linux),
[Python extension](https://marketplace.visualstudio.com/items?itemName=ms-python.python),
[Jupyter extension](https://marketplace.visualstudio.com/items?itemName=ms-toolsai.jupyter).

```sh
make notebooks
code --install-extension ms-python.python
code --install-extension ms-toolsai.jupyter
code .
```

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

- [Piper neural speech engine and Python API](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md)
- [English voice model card](https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/lessac/medium/MODEL_CARD)
- [Portuguese voice model card](https://huggingface.co/rhasspy/piper-voices/blob/main/pt/pt_BR/faber/medium/MODEL_CARD)
