/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

// Makes the toolbar fedora lift, wobble and settle while an agent is working.

// One entry per frame: [height above the resting place in pixels of the 32-pixel icon, tilt in degrees].
const HOP_FRAMES = [
  [1, 0], [3, 0], [5, 0], [5, 0],
  [5, -10], [5, -7], [5, 0], [5, 7], [5, 10], [5, 7], [5, 0],
  [5, -6], [5, -8], [5, -5], [5, 0], [5, 4], [5, 5], [5, 3], [5, 0],
  [4, 0], [2, 0], [0, 0],
  [0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0],
];
// Where the hat tilts around, as a fraction of the icon: the middle of the crown's base.
const HOP_PIVOT = [0.5, 0.62];
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

function frameKey([lift, angle]) {
  return `${lift}:${angle}`;
}

async function renderHopFrames() {
  const frames = {};
  for (const [variant, prefix] of Object.entries(HOP_VARIANTS)) {
    frames[variant] = {};
    const large = await loadIcon(`${prefix}-64.png`);
    for (const size of HOP_SIZES) {
      const exact = await loadIcon(`${prefix}-${size}.png`);
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = size;
      const ctx = canvas.getContext("2d");
      ctx.imageSmoothingQuality = "high";
      for (const frame of HOP_FRAMES) {
        const [lift, angle] = frame;
        ctx.clearRect(0, 0, size, size);
        ctx.save();
        ctx.translate(0, -Math.round((lift * size) / 32));
        if (angle) {
          // Tilted frames come from the large icon; upright ones use the crisp one drawn for this size.
          const [px, py] = [HOP_PIVOT[0] * size, HOP_PIVOT[1] * size];
          ctx.translate(px, py);
          ctx.rotate((angle * Math.PI) / 180);
          ctx.translate(-px, -py);
          ctx.drawImage(large, 0, 0, size, size);
        } else {
          ctx.drawImage(exact, 0, 0);
        }
        ctx.restore();
        frames[variant][frameKey(frame)] ??= {};
        frames[variant][frameKey(frame)][size] = ctx.getImageData(0, 0, size, size);
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
  const frame = frameKey(HOP_FRAMES[hopIndex]);
  const variant = darkToolbar.matches ? "onDark" : "onLight";
  const key = `${variant}:${frame}`;
  if (key !== hopShown) {
    hopShown = key;
    browser.browserAction.setIcon({ imageData: hopFrames[variant][frame] });
  }
  hopIndex = (hopIndex + 1) % HOP_FRAMES.length;
}

/** Keeps the fedora lifting and wobbling until HOP_ACTIVE_MS after the latest call. */
function hop() {
  hopUntil = Date.now() + HOP_ACTIVE_MS;
  if (hopTimer || !hopFrames) {
    return;
  }
  hopIndex = 0;
  hopTimer = setInterval(hopTick, HOP_FRAME_MS);
}

renderHopFrames().catch(err => console.error("Agent F: toolbar animation unavailable:", err));
