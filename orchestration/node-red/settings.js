const path = require("node:path");
const fs = require("node:fs");
const crypto = require("node:crypto");

const localDir = path.resolve(__dirname, "../../.local");
const tokenFile = path.join(localDir, "workflow-token");
fs.mkdirSync(localDir, { recursive: true });
try {
  fs.writeFileSync(tokenFile, crypto.randomBytes(32).toString("hex"), { flag: "wx", mode: 0o600 });
} catch (error) {
  if (error.code !== "EEXIST") throw error;
}
const token = fs.readFileSync(tokenFile, "utf8").trim();
process.env.IOE_INTERNAL_TOKEN = token;

module.exports = {
  uiHost: "127.0.0.1",
  uiPort: 1880,
  userDir: __dirname,
  flowFile: path.join(__dirname, "flows.json"),
  flowFilePretty: true,
  httpNodeMiddleware(req, res, next) {
    const supplied = Buffer.from(req.headers.authorization || "");
    const expected = Buffer.from("Bearer " + token);
    if (supplied.length !== expected.length || !crypto.timingSafeEqual(supplied, expected)) {
      res.status(401).json({ error: "Workflow service token required." });
      return;
    }
    next();
  },
};
