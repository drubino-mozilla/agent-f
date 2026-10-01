/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

// Makes the toolbar fedora hop while an agent is working.

// Height of the hat above its resting place in each frame, in pixels of the 32-pixel icon.
const HOP_LIFT = [0, 1, 3, 4, 4, 3, 1, 0, 0, 0, 0, 0, 0];
const HOP_FRAME_MS = 50;
const HOP_ACTIVE_MS = 10000;
const HOP_SIZES = [16, 32];
// theme_icons naming: "light" icons are drawn for dark toolbars.
const HOP_VARIANTS = { onDark: "fedora-light", onLight: "fedora" };

const darkToolbar = window.matchMedia("(prefers-color-scheme: dark)");
let hopFrames = null;
let hopTimer = null;
let hopIndex = 0;
let hopUntil = 0;
let hopShown = null;

function loadIcon(name) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`can't load ${name}`));
    img.src = browser.runtime.getURL(`icons/${name}`);
  });
}

async function renderHopFrames() {
  const frames = {};
  for (const [variant, prefix] of Object.entries(HOP_VARIANTS)) {
    frames[variant] = {};
    for (const size of HOP_SIZES) {
      const img = await loadIcon(`${prefix}-${size}.png`);
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = size;
      const ctx = canvas.getContext("2d");
      for (const lift of new Set(HOP_LIFT)) {
        ctx.clearRect(0, 0, size, size);
        ctx.drawImage(img, 0, -Math.round((lift * size) / 32));
        frames[variant][lift] ??= {};
        frames[variant][lift][size] = ctx.getImageData(0, 0, size, size);
      }
    }
  }
  hopFrames = frames;
}

function stopHopping() {
  clearInterval(hopTimer);
  hopTimer = null;
  hopShown = null;
  // An empty icon falls back to the manifest's, including its theme_icons.
  browser.browserAction.setIcon({});
}

function hopTick() {
  if (hopIndex === 0 && Date.now() > hopUntil) {
    stopHopping();
    return;
  }
  const lift = HOP_LIFT[hopIndex];
  const variant = darkToolbar.matches ? "onDark" : "onLight";
  const key = `${variant}:${lift}`;
  if (key !== hopShown) {
    hopShown = key;
    browser.browserAction.setIcon({ imageData: hopFrames[variant][lift] });
  }
  hopIndex = (hopIndex + 1) % HOP_LIFT.length;
}

/** Keeps the fedora hopping until HOP_ACTIVE_MS after the latest call. */
function hop() {
  hopUntil = Date.now() + HOP_ACTIVE_MS;
  if (hopTimer || !hopFrames) {
    return;
  }
  hopIndex = 0;
  hopTimer = setInterval(hopTick, HOP_FRAME_MS);
}

renderHopFrames().catch(err => console.error("Agent F: toolbar animation unavailable:", err));
