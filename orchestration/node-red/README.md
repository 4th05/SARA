# IoE workflow

Run the complete application from the repository root with:

```sh
./tools/start_system.sh
```

Open http://localhost:1880/ to see the **IoE workflow** graph. The web monitor at
http://localhost:8080/?view=monitor contains pairing, simulator settings, typed
requests, recognition controls, device state, and the event log.

The graph receives events, invokes Python interpretation, routes commands or
clarification, registers pending state, calls the mock adapter, records feedback,
and responds. Catch nodes route failures to Python state. A Python watchdog
handles missing feedback. Inject buttons provide reference gesture and wording
tests; choose the simulator outcomes in the monitor before using them.

`flows.json`, `settings.js`, `package.json`, and its lockfile are versioned. The
Node-RED user directory is this folder, so palette dependencies are recorded here.
Generated `.config*`, credential files, installed packages, and context data are
ignored. Use ordinary Git for the complete repository.

Service HTTP endpoints require the private token from `.local/workflow-token`.
The editor is bound to loopback on port 1880. Python state stays authoritative.
For a standalone editor, use `./tools/start_node_red.sh`; running the workflow
also requires the gateway, so the full launcher is the normal entry point.

Installation, phone HTTPS, model setup, and Pi instructions are in the root README.
