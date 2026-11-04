// Copies the pinned browser libraries from node_modules into the app's static folder,
// so they are served by the app itself (no CDN at runtime).
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const target = join(root, "src", "hourplan", "static", "vendor");

const files = {
  "htmx.min.js": "node_modules/htmx.org/dist/htmx.min.js",
  "alpine-csp.min.js": "node_modules/@alpinejs/csp/dist/cdn.min.js",
};

mkdirSync(target, { recursive: true });
for (const [name, source] of Object.entries(files)) {
  copyFileSync(join(root, source), join(target, name));
  console.log(`vendor: ${name}`);
}
