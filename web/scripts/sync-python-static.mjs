#!/usr/bin/env node
import {
  cpSync,
  existsSync,
  rmSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const repositoryRoot = path.resolve(webRoot, "..");
const source = path.join(webRoot, "dist", "client");
const target = path.join(
  repositoryRoot,
  "src",
  "grandquiz",
  "interfaces",
  "api",
  "static",
);

if (!existsSync(path.join(source, "index.html"))) {
  throw new Error("Missing web/dist/client/index.html; run npm run build first.");
}

rmSync(target, { force: true, recursive: true });
cpSync(source, target, { recursive: true });
console.log(`Synced production Web assets to ${target}`);
