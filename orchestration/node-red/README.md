# Node-RED workspace

This workspace installs Node-RED 5.0.9 using Node.js 24. The editor runs at
http://localhost:1880/ on the gateway computer.

## Start on this Mac

From the IoE project folder:

```sh
./tools/start_node_red.sh
```

Keep that terminal running. Ctrl+C stops Node-RED. The launcher selects the
Homebrew Node.js 24 installation on this Mac.

## Install on another computer or Raspberry Pi

Install Node.js 24 and npm, then clone this repository. From the IoE folder:

```sh
npm --prefix orchestration/node-red ci
./tools/start_node_red.sh
```

`npm ci` restores the dependency versions recorded in `package-lock.json`.
It installs packages for the target computer, including Linux ARM64 on the Pi.
On the Pi itself, open http://localhost:1880/ in Firefox. The editor is bound to
the gateway's loopback address. Remote editing can use an SSH tunnel:

```sh
ssh -L 1880:127.0.0.1:1880 USER@PI_ADDRESS
```

Then open http://localhost:1880/ on the computer running the SSH client.

## Files saved with Git

- `flows.json` contains the workflow saved by the editor's Deploy button.
- `settings.js` contains shared runtime settings.
- `package.json` and `package-lock.json` record Node-RED and its dependencies.
- `.nvmrc` records the Node.js major version.

The Node-RED user directory is this workspace, so dependencies added through the
palette are recorded in its tracked `package.json` and lockfile. Installed
`node_modules/`, credential files, `.config*` runtime files, and persisted context
are ignored by Git. The complete IoE repository uses ordinary Git for version control.
The editor starts with an empty workspace for the implementation stage.

References:
- https://nodered.org/docs/getting-started/local
- https://nodered.org/docs/faq/node-versions
