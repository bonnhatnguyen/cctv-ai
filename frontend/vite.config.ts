import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
const apiTarget = process.env.V1_BACKEND_PORT
  ? `http://127.0.0.1:${process.env.V1_BACKEND_PORT}`
  : "http://127.0.0.1:8000";
const instanceId = process.env.V1_TRACKING_INSTANCE_ID ?? "unmanaged";
const backendUrl = process.env.V1_BACKEND_URL ?? apiTarget;
const backendPort = Number(new URL(backendUrl).port);

const runtimeIdentity = {
  name: "v1-runtime-identity",
  configureServer(server: any) {
    server.middlewares.use((request: any, response: any, next: any) => {
      if ((request.url ?? "").split("?", 1)[0] !== "/__v1_identity") return next();
      response.statusCode = 200;
      response.setHeader("Content-Type", "application/json; charset=utf-8");
      response.end(JSON.stringify({
        service: "v1-person-tracking-ui",
        version: "1",
        network: "loopback-only",
        instance_id: instanceId,
        backend_url: backendUrl,
        backend_port: backendPort,
      }));
    });
  },
};

export default defineConfig({
  plugins: [runtimeIdentity, react()],
  server: { proxy: { "/api": apiTarget } },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
  },
});
