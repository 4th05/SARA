# Firmware integration point

Place the light Arduino sketch under `lights/lights.ino` and the pump sketch
under `pump/pump.ino`. Each sketch folder should include its pin assignments,
board/core/library versions, wiring diagram, command format, and local operating
limits. The firmware teammate owns these programs.

Coordinate the BLE service/characteristic UUIDs and command/notification encoding
with the gateway adapter described in `contracts/README.md`. Local hardware
limits and fault reporting belong in firmware. The current test application uses
the explicit simulated device adapter in `gateway/devices/mock.py`.
