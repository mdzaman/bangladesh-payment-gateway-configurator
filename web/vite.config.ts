import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the API runs on :8000 (`make dev`); everything that is not the
// single-page app is proxied to it so cookies and redirects behave as in production.
const API = process.env.BDPAY_CLOUD_API ?? "http://127.0.0.1:8000";
const proxied = ["/api", "/v1", "/bdpay", "/p", "/checkout", "/pay", "/simulator", "/static", "/healthz", "/readyz",
  "/docs", "/openapi.json"];

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(proxied.map((path) => [path, { target: API, changeOrigin: false }])),
  },
  build: { outDir: "dist", sourcemap: true, target: "es2022" },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
