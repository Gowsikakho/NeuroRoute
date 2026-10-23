import { spawn } from "node:child_process";
import { request } from "node:http";
import path from "node:path";
import { existsSync } from "node:fs";
import { logger } from "./logger";
const PYTHON_PORT = Number(process.env["NEUROROUTE_API_PORT"] ?? 18081);
const PYTHON_HOST = "127.0.0.1";
const PYTHON_REQUEST_TIMEOUT_MS = Number(process.env["NEUROROUTE_PROXY_TIMEOUT_MS"] ?? 5_000);
// These endpoints run whole simulations synchronously before answering, so they
// take far longer than an ordinary API call: the dashboard's default
// comparison (120s x 2 algorithms) measures ~105 seconds wall clock. They get a
// separate, configurable budget instead of the normal fast-fail timeout.
const LONG_RUNNING_TIMEOUT_MS = Number(process.env["NEUROROUTE_LONG_PROXY_TIMEOUT_MS"] ?? 600_000);
const LONG_RUNNING_PATHS = new Set(["/api/experiments/run"]);
export function startPythonBackend() {
    const workspaceRoot = path.resolve(import.meta.dirname, "../../../");
    const backendDirectory = path.join(workspaceRoot, "artifacts", "neuroroute-sdn", "backend");
    const managedPython = path.join(workspaceRoot, ".pythonlibs", "bin", "python");
    const executable = process.env["PYTHON_EXECUTABLE"] ??
        (existsSync(managedPython) ? managedPython : "python3");
    const child = spawn(executable, [
        "-m",
        "uvicorn",
        "neuroroute_api.main:app",
        "--host",
        PYTHON_HOST,
        "--port",
        String(PYTHON_PORT),
    ], {
        cwd: backendDirectory,
        env: {
            ...process.env,
            PYTHONPATH: backendDirectory,
        },
        stdio: ["ignore", "pipe", "pipe"],
    });
    child.stdout?.on("data", (chunk) => {
        logger.info({ python: chunk.toString().trim() }, "NeuroRoute backend");
    });
    child.stderr?.on("data", (chunk) => {
        logger.warn({ python: chunk.toString().trim() }, "NeuroRoute backend");
    });
    child.on("error", (err) => {
        logger.error({ err }, "Could not start NeuroRoute Python backend");
    });
    child.on("exit", (code, signal) => {
        if (code !== 0 && code !== null) {
            logger.error({ code, signal }, "NeuroRoute Python backend exited");
        }
    });
    return child;
}
export async function proxyPythonRequest(req, res) {
    const upstreamUrl = `http://${PYTHON_HOST}:${PYTHON_PORT}${req.originalUrl}`;
    const requestPath = req.originalUrl.split("?")[0] ?? "";
    const timeoutMs = LONG_RUNNING_PATHS.has(requestPath)
        ? LONG_RUNNING_TIMEOUT_MS
        : PYTHON_REQUEST_TIMEOUT_MS;
    const headers = {};
    const contentType = req.header("content-type");
    if (contentType)
        headers["content-type"] = contentType;
    const accept = req.header("accept");
    if (accept)
        headers.accept = accept;
    try {
        const response = await fetch(upstreamUrl, {
            method: req.method,
            headers,
            signal: AbortSignal.timeout(timeoutMs),
            body: req.method === "GET" || req.method === "HEAD"
                ? undefined
                : JSON.stringify(req.body ?? {}),
        });
        res.status(response.status);
        response.headers.forEach((value, key) => {
            if (key !== "content-encoding" && key !== "transfer-encoding") {
                res.setHeader(key, value);
            }
        });
        res.send(Buffer.from(await response.arrayBuffer()));
    }
    catch (error) {
        const details = error !== null && typeof error === "object"
            ? error
            : {};
        const cause = details.name === "TimeoutError" || details.cause?.name === "TimeoutError"
            ? `timed out after ${timeoutMs / 1000} seconds`
            : details.cause?.code === "ECONNREFUSED"
                ? "connection was refused"
                : "could not be reached";
        throw new Error(`NeuroRoute simulator backend ${cause} at ${PYTHON_HOST}:${PYTHON_PORT}. Check that the Python API is running and that this port is free.`, { cause: error });
    }
}
export function proxyPythonWebSocket(server) {
    server.on("upgrade", (incoming, socket, head) => {
        if (!incoming.url?.startsWith("/api/ws/")) {
            socket.destroy();
            return;
        }
        const upstream = request({
            host: PYTHON_HOST,
            port: PYTHON_PORT,
            path: incoming.url,
            method: "GET",
            headers: {
                ...incoming.headers,
                host: `${PYTHON_HOST}:${PYTHON_PORT}`,
            },
        });
        upstream.on("upgrade", (response, upstreamSocket, upstreamHead) => {
            const status = response.statusCode ?? 101;
            const statusText = response.statusMessage ?? "Switching Protocols";
            socket.write(`HTTP/${response.httpVersion} ${status} ${statusText}\r\n`);
            for (const [name, value] of Object.entries(response.headers)) {
                if (value !== undefined) {
                    socket.write(`${name}: ${Array.isArray(value) ? value.join(", ") : value}\r\n`);
                }
            }
            socket.write("\r\n");
            if (head.length > 0)
                upstreamSocket.write(head);
            if (upstreamHead.length > 0)
                socket.write(upstreamHead);
            upstreamSocket.pipe(socket);
            socket.pipe(upstreamSocket);
            socket.on("close", () => upstreamSocket.destroy());
            upstreamSocket.on("close", () => socket.destroy());
        });
        upstream.on("response", (response) => {
            socket.end(`HTTP/${response.httpVersion} ${response.statusCode} ${response.statusMessage}\r\nConnection: close\r\n\r\n`);
        });
        upstream.on("error", (err) => {
            logger.warn({ err }, "NeuroRoute WebSocket proxy unavailable");
            socket.destroy();
        });
        upstream.end();
    });
}
