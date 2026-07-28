import { defineConfig } from "@playwright/test";

declare const process: {
  env: Record<string, string | undefined>;
};

const systemChrome =
  process.env.GRANDQUIZ_SYSTEM_CHROME === "1"
    ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    : undefined;

export default defineConfig({
  testDir: "./e2e",
  globalTeardown: "./e2e/global-teardown.mjs",
  reporter: [["list"], ["html", { open: "never" }]],
  // The fixture deliberately owns one SQLite database, matching the local single-user runtime.
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:14173",
    ...(systemChrome === undefined
      ? {}
      : { launchOptions: { executablePath: systemChrome } }),
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
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
      command: "GRANDQUIZ_FIXTURE_PORT=18000 npm run dev:fixture",
      port: 18000,
      reuseExistingServer: false,
    },
    {
      command:
        "GRANDQUIZ_API_ORIGIN=http://127.0.0.1:18000 npm run dev -- --host 127.0.0.1 --port 14173 --strictPort",
      port: 14173,
      reuseExistingServer: false,
    },
  ],
});
