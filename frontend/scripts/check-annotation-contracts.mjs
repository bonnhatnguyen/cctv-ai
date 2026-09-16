import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const root = resolve(frontend, "..");
const candidates = process.platform === "win32"
  ? [join(root, ".venv", "Scripts", "python.exe")]
  : [join(root, ".venv", "bin", "python")];
const python = candidates.find(existsSync);

if (!python) {
  console.error("V2 Python environment is missing. Create .venv at the repository root.");
  process.exit(1);
}

const completed = spawnSync(
  python,
  [join(root, "scripts", "generate-annotation-types.py"), "--check"],
  { cwd: root, shell: false, stdio: "inherit" },
);
if (completed.error) {
  console.error(completed.error.message);
  process.exit(1);
}
process.exit(completed.status ?? 1);
