// Copies the pinned browser libraries from node_modules into the app's static folder,
// so they are served by the app itself (no CDN at runtime), and builds the icon sprite.
import { copyFileSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const app = join(root, "src", "hourplan");
const target = join(app, "static", "vendor");

const files = {
  "htmx.min.js": "node_modules/htmx.org/dist/htmx.min.js",
  "alpine-csp.min.js": "node_modules/@alpinejs/csp/dist/cdn.min.js",
};

mkdirSync(target, { recursive: true });
for (const [name, source] of Object.entries(files)) {
  copyFileSync(join(root, source), join(target, name));
  console.log(`vendor: ${name}`);
}

// Icon sprite: only the Lucide icons the app names, as `icon("name")` in templates
// or `icon="name"` in Python. Any of the ~2000 Lucide icons can be used that way.
const ICON_CALL = /\bicon(?:\(\s*|=)["']([a-z0-9-]+)["']/g;
const names = new Set();
for (const entry of readdirSync(app, { recursive: true })) {
  if (!/\.(html|py)$/.test(entry) || entry.includes("__pycache__")) continue;
  for (const match of readFileSync(join(app, entry), "utf8").matchAll(ICON_CALL)) {
    names.add(match[1]);
  }
}
const icons = join(root, "node_modules", "lucide-static", "icons");
const symbols = [...names].sort().map((name) => {
  let svg;
  try {
    svg = readFileSync(join(icons, `${name}.svg`), "utf8");
  } catch {
    throw new Error(`icon "${name}" does not exist in lucide-static`);
  }
  const body = svg.slice(svg.indexOf(">", svg.indexOf("<svg")) + 1, svg.lastIndexOf("</svg>"));
  return `<symbol id="${name}" viewBox="0 0 24 24">${body.replace(/\s*\n\s*/g, "")}</symbol>`;
});
writeFileSync(
  join(target, "icons.svg"),
  `<svg xmlns="http://www.w3.org/2000/svg"><!-- Lucide icons, ISC license -->${symbols.join("")}</svg>\n`,
);
console.log(`vendor: icons.svg (${symbols.length} icons)`);
