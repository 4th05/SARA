# Firmware integration point

Place the light Arduino sketch under `lights/lights.ino` and the pump sketch
under `pump/pump.ino`. Each sketch folder should include its pin assignments,
board/core/library versions, wiring diagram, command format, and local operating
limits. The firmware teammate owns these programs.

Coordinate the BLE service/characteristic UUIDs and command/notification encoding
with the gateway adapter described in `contracts/README.md`. Local hardware
limits and fault reporting belong in firmware. The current test application uses
the explicit simulated device adapter in `gateway/devices/mock.py`.

The lights sketch will control three outputs named `bathroom`, `kitchen`, and
`bedroom`. The gateway sends a command ID, output name, and boolean value. The
Python simulator uses a `pins` dictionary as its virtual GPIO outputs and applies
the same on/off request only on success or warning. Error and timeout scenarios
preserve previous output values. Real microphone input, Whisper, Ollama,
Node-RED orchestration, and spoken WAV feedback run normally around this adapter.
