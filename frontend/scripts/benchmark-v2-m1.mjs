#!/usr/bin/env node
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import { mkdir, open, readFile, rename } from "node:fs/promises";
import { dirname, isAbsolute, join } from "node:path";
import process from "node:process";

function fail(message) {
  throw new Error(message);
}

function argument(name) {
  const position = process.argv.indexOf(name);
  if (position < 0 || !process.argv[position + 1]) fail(`Missing ${name}`);
  return process.argv[position + 1];
}

function loadPlaywright() {
  const require = createRequire(import.meta.url);
  const candidates = [
    "playwright",
    process.env.CODEX_PLAYWRIGHT_PATH,
    process.env.USERPROFILE && join(
      process.env.USERPROFILE,
      ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "node", "node_modules", "playwright",
    ),
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      return { playwright: require(candidate), packagePath: require.resolve(join(candidate, "package.json")) };
    } catch { /* try the next verified local package location */ }
  }
  fail("Playwright is not installed locally; refusing to download a browser or package during the gate");
}

async function exclusiveJson(path, value) {
  await mkdir(dirname(path), { recursive: true });
  const temp = `${path}.tmp`;
  const handle = await open(temp, "wx");
  try {
    await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8");
  } finally {
    await handle.close();
  }
  await rename(temp, path);
}

async function installInstrumentation(page) {
  await page.evaluate(() => {
    window.__v2M1 = { target: null, inputAtMs: null, painted: null };
    const state = window.__v2M1;
    document.addEventListener("input", (event) => {
      const element = event.target;
      if (element instanceof HTMLInputElement && element.dataset.testid === "annotation-frame-input") {
        const index = Number(element.value);
        if (state.target === index) state.inputAtMs = performance.now();
      }
    }, true);
    document.addEventListener("keydown", (event) => {
      if ((event.key === "ArrowLeft" || event.key === "ArrowRight") && state.target !== null) {
        state.inputAtMs = performance.now();
      }
    }, true);
    document.addEventListener("load", (event) => {
      const element = event.target;
      if (!(element instanceof HTMLImageElement) || element.dataset.testid !== "exact-frame") return;
      const index = Number(element.dataset.index);
      if (state.target !== index) return;
      requestAnimationFrame(() => {
        state.painted = {
          paintedAtMs: performance.now(),
          displayedIndex: index,
          clipId: element.dataset.clipId,
          sourceHash: element.dataset.sourceHash,
          generation: Number(element.dataset.generation),
        };
      });
    }, true);
  });
}

async function rgbHash(image) {
  return image.evaluate(async (element) => {
    if (!(element instanceof HTMLImageElement) || !element.complete || !element.naturalWidth) {
      throw new Error("exact frame image is not fully loaded");
    }
    const canvas = document.createElement("canvas");
    canvas.width = element.naturalWidth;
    canvas.height = element.naturalHeight;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) throw new Error("cannot create browser RGB hash canvas");
    context.drawImage(element, 0, 0);
    const rgba = context.getImageData(0, 0, canvas.width, canvas.height).data;
    const rgb = new Uint8Array(canvas.width * canvas.height * 3);
    for (let source = 0, target = 0; source < rgba.length; source += 4) {
      rgb[target++] = rgba[source];
      rgb[target++] = rgba[source + 1];
      rgb[target++] = rgba[source + 2];
    }
    const digest = await crypto.subtle.digest("SHA-256", rgb);
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  });
}

async function sample(page, input, kind, index, action) {
  await page.evaluate((target) => {
    window.__v2M1.target = target;
    window.__v2M1.inputAtMs = null;
    window.__v2M1.painted = null;
  }, index);
  await action();
  await page.waitForFunction((target) => {
    const state = window.__v2M1;
    return state.target === target && state.inputAtMs !== null && state.painted?.displayedIndex === target;
  }, index, { timeout: 10_000 });
  const timing = await page.evaluate(() => ({
    inputAtMs: window.__v2M1.inputAtMs,
    ...window.__v2M1.painted,
  }));
  const image = page.getByTestId("exact-frame");
  await image.waitFor({ state: "visible" });
  const hash = await rgbHash(image);
  return {
    kind,
    requestedIndex: index,
    displayedIndex: timing.displayedIndex,
    clipId: timing.clipId,
    sourceHash: timing.sourceHash,
    generation: timing.generation,
    inputAtMs: timing.inputAtMs,
    paintedAtMs: timing.paintedAtMs,
    latencyMs: timing.paintedAtMs - timing.inputAtMs,
    displayed_rgb_sha256: hash,
  };
}

async function main() {
  const configPath = argument("--config");
  if (!isAbsolute(configPath)) fail("--config must be an absolute path");
  const config = JSON.parse(await readFile(configPath, "utf8"));
  const runDir = join(config.annotation_dir, "acceptance", config.run_id);
  const schedulePath = join(runDir, "schedule.json");
  const benchmarkPath = join(runDir, "benchmark.json");
  const scheduleBytes = await readFile(schedulePath);
  const schedule = JSON.parse(scheduleBytes.toString("utf8"));
  const scheduleSha256 = createHash("sha256").update(scheduleBytes).digest("hex");
  if (schedule.run_id !== config.run_id || schedule.clip_id !== config.clip_id) {
    fail("schedule/config identity mismatch");
  }
  const { playwright, packagePath } = loadPlaywright();
  const packageVersion = JSON.parse(await readFile(packagePath, "utf8")).version;
  const browser = await playwright.chromium.launch({
    executablePath: config.browser_executable,
    headless: true,
  });
  const samples = [];
  const failures = [];
  let browserVersion = "unknown";
  let stage = "launch";
  try {
    browserVersion = browser.version();
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    stage = "open frontend";
    await page.goto(config.frontend_url, { waitUntil: "networkidle" });
    stage = "open annotation workspace";
    await page.getByRole("button", { name: "Danh sách clip" }).click();
    stage = "open configured clip";
    await page.locator(`[data-clip-id="${config.clip_id}"]`).getByRole("button", { name: /^Mở/ }).click();
    const input = page.getByTestId("annotation-frame-input");
    stage = "warm exact frame";
    await input.fill(String(schedule.initial_index));
    await page.getByTestId("exact-frame").waitFor({ state: "visible" });
    await page.waitForFunction((index) => document.querySelector('[data-testid="exact-frame"]')?.dataset.index === String(index), schedule.initial_index);
    await installInstrumentation(page);
    for (const index of schedule.adjacent) {
      stage = `adjacent frame ${index}`;
      const current = Number(await input.inputValue());
      const key = index > current ? "ArrowRight" : "ArrowLeft";
      samples.push(await sample(page, input, "adjacent", index, async () => {
        await input.evaluate((element) => element.blur());
        await page.keyboard.press(key);
      }));
    }
    for (const index of schedule.far) {
      stage = `far frame ${index}`;
      samples.push(await sample(page, input, "far", index, async () => {
        await input.fill(String(index));
      }));
    }
    await context.close();
  } catch (error) {
    failures.push(`${stage}: ${error instanceof Error ? error.message : String(error)}`);
  } finally {
    await browser.close();
  }
  const report = {
    schema_version: 1,
    run_id: config.run_id,
    schedule_sha256: scheduleSha256,
    browser_version: browserVersion,
    playwright_version: packageVersion,
    cache_policy: schedule.cache_policy,
    samples,
    failures,
  };
  await exclusiveJson(benchmarkPath, report);
  if (failures.length || samples.length !== 70) fail(`benchmark failed: ${failures[0] ?? `${samples.length}/70 samples`}`);
  console.log(benchmarkPath);
}

main().catch((error) => {
  console.error(`FAIL: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
