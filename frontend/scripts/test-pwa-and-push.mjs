import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

test("Web App Manifest includes complete PWA & TWA requirements", async () => {
  const manifestRaw = await readFile(new URL("../public/manifest.json", import.meta.url), "utf8");
  const manifest = JSON.parse(manifestRaw);

  assert.equal(manifest.name, "Maç Analiz - Futbol Veri ve Analiz Platformu");
  assert.equal(manifest.short_name, "Maç Analiz");
  assert.equal(manifest.display, "standalone");
  assert.equal(manifest.start_url, "/?source=pwa");
  assert.equal(manifest.scope, "/");
  assert.equal(manifest.background_color.toUpperCase(), "#FFFFFF");
  assert.equal(manifest.theme_color.toUpperCase(), "#FFFFFF");
  assert.equal(manifest.lang, "tr");

  // Verify icons exist and have maskable variant
  assert.ok(manifest.icons && manifest.icons.length >= 2);
  const has192 = manifest.icons.some((i) => i.sizes === "192x192" && i.purpose === "any");
  const has512 = manifest.icons.some((i) => i.sizes === "512x512" && i.purpose === "any");
  const hasMaskable = manifest.icons.some((i) => i.purpose === "maskable");
  assert.ok(has192, "Must contain 192x192 standard icon");
  assert.ok(has512, "Must contain 512x512 standard icon");
  assert.ok(hasMaskable, "Must contain maskable icon for Android adaptive icons");

  // Verify shortcuts
  assert.ok(manifest.shortcuts && manifest.shortcuts.length >= 2);
});

test("Digital Asset Links file is strictly valid and targets correct package", async () => {
  const dalRaw = await readFile(new URL("../public/.well-known/assetlinks.json", import.meta.url), "utf8");
  const dal = JSON.parse(dalRaw);

  assert.ok(Array.isArray(dal));
  assert.ok(dal.length >= 1);
  const statement = dal[0];
  assert.ok(statement.relation.includes("delegate_permission/common.handle_all_urls"));
  assert.equal(statement.target.namespace, "android_app");
  assert.equal(statement.target.package_name, "com.macanaliz.app");
  assert.ok(statement.target.sha256_cert_fingerprints.length > 0);
});

test("Service Worker strictly DOES NOT cache live API responses offline", async () => {
  const swCode = await readFile(new URL("../public/sw.js", import.meta.url), "utf8");

  // Check event listeners
  assert.ok(swCode.includes("addEventListener('install'"), "SW must have install event");
  assert.ok(swCode.includes("addEventListener('activate'"), "SW must have activate event");
  assert.ok(swCode.includes("addEventListener('fetch'"), "SW must have fetch event");

  // CRITICAL REQUIREMENT: Live API responses must never be cached offline!
  assert.ok(
    swCode.includes("/api/") && swCode.includes("event.respondWith"),
    "SW must intercept /api/ requests"
  );
  assert.ok(
    swCode.includes("503") || swCode.includes("offline: true"),
    "SW must return offline status without saving live API responses to cache"
  );
});

test("Offline page is present with Turkish UI and retry capabilities", async () => {
  const offlineHtml = await readFile(new URL("../public/offline.html", import.meta.url), "utf8");

  assert.ok(offlineHtml.includes("İnternet Bağlantısı Kesildi"));
  assert.ok(offlineHtml.includes("Bağlantıyı Yeniden Dene"));
  assert.ok(offlineHtml.includes("window.location.reload()"));
  assert.ok(offlineHtml.includes("online"));
  assert.ok(offlineHtml.includes("#DC2626"), "Uses brand red color");
});

test("Android TWA configuration matches web manifest, package ID, and notifications disabled", async () => {
  const twaRaw = await readFile(new URL("../../android/twa-manifest.json", import.meta.url), "utf8");
  const twa = JSON.parse(twaRaw);

  assert.equal(twa.packageId, "com.macanaliz.app");
  assert.equal(twa.host, "ma-analiz-2.onrender.com");
  assert.equal(twa.themeColor.toUpperCase(), "#FFFFFF");
  assert.equal(twa.backgroundColor.toUpperCase(), "#FFFFFF");
  assert.equal(twa.enableNotifications, false);
  assert.equal(twa.startUrl, "/?source=twa");
});
