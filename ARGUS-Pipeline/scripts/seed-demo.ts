import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const directory = dirname(fileURLToPath(import.meta.url));
const python = process.env.PYTHON ?? "python3";
const result = spawnSync(python, [resolve(directory, "seed_demo.py")], {
  cwd: resolve(directory, ".."),
  stdio: "inherit",
});

if (result.error) throw result.error;
process.exit(result.status ?? 1);
