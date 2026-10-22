import { spawn, type ChildProcess } from "node:child_process";
import { request } from "node:http";
import path from "node:path";
import { existsSync } from "node:fs";
import type { IncomingMessage, Server } from "node:http";
import type { Socket } from "node:net";
import type { Request, Response } from "express";
import { logger } from "./logger";

const PYTHON_PORT = Number(process.env["NEUROROUTE_API_PORT"] ?? 18081);
const PYTHON_HOST = "127.0.0.1";

export function startPythonBackend(): ChildProcess {
  const workspaceRoot = path.resolve(import.meta.dirname, "../../../");
  const backendDirectory = path.join(
    workspaceRoot,
    "artifacts",
    "neuroroute-sdn",
    "backend",
  );
  const managedPython = path.join(workspaceRoot, ".pythonlibs", "bin", "python");
  const executable =
    process.env["PYTHON_EXECUTABLE"] ??
    (existsSync(managedPython) ? managedPython : "python3");
  const child = spawn(
    executable,
    [
      "-m",
      "uvicorn",
      "neuroroute_api.main:app",
      "--host",
      PYTHON_HOST,
      "--port",
      String(PYTHON_PORT),
    ],
    {
      cwd: backendDirectory,
      env: {
        ...process.env,
        PYTHONPATH: backendDirectory,
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );

  child.stdout?.on("data", (chunk: Buffer) => {
    logger.info({ python: chunk.toString().trim() }, "NeuroRoute backend");
  });
  child.stderr?.on("data", (chunk: Buffer) => {
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

export async function proxyPythonRequest(
  req: Request,
  res: Response,
): Promise<void> {
  const upstreamUrl = `http://${PYTHON_HOST}:${PYTHON_PORT}${req.originalUrl}`;
  const headers: Record<string, string> = {};
  const contentType = req.header("content-type");
  if (contentType) headers["content-type"] = contentType;
  const accept = req.header("accept");
  if (accept) headers.accept = accept;

  const response = await fetch(upstreamUrl, {
    method: req.method,
    headers,
    signal: AbortSignal.timeout(5_000),
    body:
      req.method === "GET" || req.method === "HEAD"
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

export function proxyPythonWebSocket(server: Server): void {
  server.on("upgrade", (incoming: IncomingMessage, socket: Socket, head: Buffer) => {
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
    upstream.on(
      "upgrade",
      (response: IncomingMessage, upstreamSocket: Socket, upstreamHead: Buffer) => {
        const status = response.statusCode ?? 101;
        const statusText = response.statusMessage ?? "Switching Protocols";
        socket.write(`HTTP/${response.httpVersion} ${status} ${statusText}\r\n`);
        for (const [name, value] of Object.entries(response.headers)) {
          if (value !== undefined) {
            socket.write(
              `${name}: ${Array.isArray(value) ? value.join(", ") : value}\r\n`,
            );
          }
        }
        socket.write("\r\n");
        if (head.length > 0) upstreamSocket.write(head);
        if (upstreamHead.length > 0) socket.write(upstreamHead);
        upstreamSocket.pipe(socket);
        socket.pipe(upstreamSocket);
        socket.on("close", () => upstreamSocket.destroy());
        upstreamSocket.on("close", () => socket.destroy());
      },
    );
    upstream.on("response", (response: IncomingMessage) => {
      socket.end(
        `HTTP/${response.httpVersion} ${response.statusCode} ${response.statusMessage}\r\nConnection: close\r\n\r\n`,
      );
    });
    upstream.on("error", (err) => {
      logger.warn({ err }, "NeuroRoute WebSocket proxy unavailable");
      socket.destroy();
    });
    upstream.end();
  });
}
