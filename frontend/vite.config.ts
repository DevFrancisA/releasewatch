import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs separately (docker compose or uvicorn on :8000).
// In production FastAPI serves the built files, so /api is same-origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": process.env.VITE_API_URL ?? "http://localhost:8000",
      "/healthz": process.env.VITE_API_URL ?? "http://localhost:8000",
    },
  },
});
