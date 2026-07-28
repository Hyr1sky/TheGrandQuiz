import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

export default function globalTeardown() {
  const locationFile = path.resolve("test-results/runtime-location.txt");
  if (!existsSync(locationFile)) {
    return;
  }
  execFileSync(
    path.resolve("../.venv/bin/python"),
    [
      path.resolve("../scripts/audit_web_fixture.py"),
      locationFile,
    ],
    { cwd: process.cwd(), stdio: "inherit" },
  );
}
