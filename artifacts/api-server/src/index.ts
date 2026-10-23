import app from "./app";
import { logger } from "./lib/logger";
import { startPythonBackend, proxyPythonWebSocket } from "./lib/python-backend";

// PORT is normally provided by the hosting environment; default to the Express
// convention so `pnpm --filter @workspace/api-server run dev` also works on a
// machine that does not set it.
const rawPort = process.env["PORT"] ?? "3000";

const port = Number(rawPort);

if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

const pythonBackend = startPythonBackend();
const server = app.listen(port, (err) => {
  if (err) {
    logger.error({ err }, "Error listening on port");
    pythonBackend.kill();
    process.exit(1);
  }

  logger.info({ port }, "Server listening");
});
proxyPythonWebSocket(server);

const shutdown = () => {
  pythonBackend.kill();
  server.close();
};
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
