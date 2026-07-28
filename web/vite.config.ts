import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

declare const process: {
  env: Record<string, string | undefined>;
};

export default defineConfig({
  build: {
    outDir: "dist/client",
  },
  optimizeDeps: {
    include: ["react", "react-dom/client"],
  },
  server: {
    host: "127.0.0.1",
    allowedHosts: ["terminal.local"],
    proxy: {
      "/api":
        process.env.GRANDQUIZ_API_ORIGIN ?? "http://127.0.0.1:8000",
    },
    warmup: {
      clientFiles: ["./src/main.tsx"],
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: true,
    include: ["src/**/*.test.{ts,tsx}"],
  },
  plugins: [react()],
});
