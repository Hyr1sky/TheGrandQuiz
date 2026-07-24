import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: {
    baseURL: "http://127.0.0.1:4173",
  },
  projects: [
    {
      name: "desktop",
      use: { viewport: { width: 1440, height: 1024 } },
    },
    {
      name: "mobile",
      use: { viewport: { width: 390, height: 844 } },
    },
  ],
  webServer: [
    {
      command: "npm run dev:fixture",
      port: 8000,
      reuseExistingServer: true,
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 4173 --strictPort",
      port: 4173,
      reuseExistingServer: true,
    },
  ],
});
