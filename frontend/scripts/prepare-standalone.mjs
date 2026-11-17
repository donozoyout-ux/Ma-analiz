import { cp, mkdir, rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const standaloneRoot = path.join(frontendRoot, ".next", "standalone");
const staticSource = path.join(frontendRoot, ".next", "static");
const staticTarget = path.join(standaloneRoot, ".next", "static");

await rm(staticTarget, { recursive: true, force: true });
await mkdir(path.dirname(staticTarget), { recursive: true });
await cp(staticSource, staticTarget, { recursive: true });

const publicSource = path.join(frontendRoot, "public");
if (existsSync(publicSource)) {
  await cp(publicSource, path.join(standaloneRoot, "public"), { recursive: true, force: true });
}
