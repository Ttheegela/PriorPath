import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const api = { "/api": "http://127.0.0.1:8000" };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "../public", emptyOutDir: true },
  server: { proxy: api },
  preview: { proxy: api },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
