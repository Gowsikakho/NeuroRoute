---
name: Replit internal ports
description: Avoid conflicts when a project API server launches a local sidecar service.
---

Do not assume an available-looking loopback port is free in the Replit workspace. Check the actual listeners before choosing a port, and keep proxy and sidecar configuration in sync.

**Why:** A local FastAPI child could not bind `127.0.0.1:18080` because the Replit environment was already listening there.

**How to apply:** Use a verified free loopback port for child services. Do not stop or kill environment-owned listeners to make room.
