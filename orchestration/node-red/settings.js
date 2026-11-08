const path = require("node:path");

module.exports = {
  uiHost: "127.0.0.1",
  uiPort: 1880,
  userDir: __dirname,
  flowFile: path.join(__dirname, "flows.json"),
  flowFilePretty: true,
};
