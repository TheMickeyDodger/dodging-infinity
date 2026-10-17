#!/usr/bin/env node
/**
 * Renders LABELLED visual-QA contact sheets for every extracted atlas crop — for a human (and the
 * Reviewer) to verify identity, completeness, edges and alignment BY EYE. Counts agreeing with the
 * prompt grid prove nothing about identity; these sheets are the evidence.
 *
 *   node scripts/render-sprite-qa-sheets.mjs --review <review dir> --atlases <dir> --out <dir>
 *        [--verdicts scripts/atlas-sprite-qa.json] [--no-png]
 *
 * <review dir> is what `extract-atlas-sprites.mjs --review-dir` wrote (review.json + sprites/).
 * Writes one <pack>.html per pack (and, unless --no-png, a full-page <pack>.png screenshot taken in
 * a headless browser with a throwaway profile). Each card shows: id, display name, the source
 * crop from the atlas (alpha visible on a checkerboard), the output sprite at an integer zoom over
 * a magenta checkerboard with the FOOTPRINT box and tile grid, the BASELINE, and the bottom SOLID
 * collision rows tinted red (the rows above are walk-behind); the source atlas + rect + scale; any
 * merge (with gap, flagged when gutter-like), attached or dropped fragments; the alpha/edge QA
 * numbers; and the recorded verdict.
 */
import fs from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';

const args = process.argv.slice(2);
const flag = (n) => (args.includes(n) ? args[args.indexOf(n) + 1] : undefined);
const reviewDir = flag('--review');
const atlasDir = flag('--atlases');
const outDir = flag('--out');
const verdictFile = flag('--verdicts');
if (!reviewDir || !atlasDir || !outDir) {
  console.error('usage: render-sprite-qa-sheets.mjs --review <dir> --atlases <dir> --out <dir> [--verdicts f] [--no-png]');
  process.exit(2);
}
const review = JSON.parse(fs.readFileSync(path.join(reviewDir, 'review.json'), 'utf-8'));
const verdicts = verdictFile ? JSON.parse(fs.readFileSync(verdictFile, 'utf-8')).verdicts : {};
fs.mkdirSync(outDir, { recursive: true });
const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
const rel = (to) => path.relative(outDir, to).split(path.sep).join('/');

/** Collision as the runtime will apply it: per-column for split arches (green = opening,
 *  red = solid leg rows), a blue walk-over deck for spans, else the solid bottom rows. */
function passageOverlay(c, W, H, tile, solidPx) {
  const pg = c.passage ?? { kind: 'solid' };
  if (pg.kind === 'span')
    return `<div class="span" style="top:0;width:${W}px;height:${H}px">walk-over deck · axis ${pg.axis}</div>`;
  if (pg.kind === 'opening' && c.runtimeTypes.length > 1)
    return pg.columns
      .map((col) =>
        col.passable
          ? `<div class="open" style="left:${col.col * tile}px;top:0;width:${tile}px;height:${H}px"></div>`
          : `<div class="solid" style="left:${col.col * tile}px;top:${H - solidPx}px;width:${tile}px;height:${solidPx}px"></div>`,
      )
      .join('');
  return `<div class="solid" style="top:${H - solidPx}px;width:${W}px;height:${solidPx}px"></div>`;
}

function card(c) {
  const m = c.manifest;
  const zoom = Math.max(1, Math.min(8, Math.floor(160 / Math.max(m.width, m.height))));
  const srcScale = Math.min(1, 160 / Math.max(c.sourceRect.w, c.sourceRect.h));
  const atlasUrl = rel(path.join(atlasDir, c.sourceAtlas));
  const spriteUrl = rel(path.join(reviewDir, 'sprites', `${c.id}.png`));
  const W = m.width * zoom;
  const H = m.height * zoom;
  const tile = 16 * zoom;
  const solidPx = c.footprint.solidRows * tile;
  const v = verdicts[c.id];
  const flagged = c.reviewFlags.length > 0;
  const gridLines = [];
  for (let x = tile; x < W; x += tile) gridLines.push(`<div class="gv" style="left:${x}px;height:${H}px"></div>`);
  for (let y = tile; y < H; y += tile) gridLines.push(`<div class="gh" style="top:${y}px;width:${W}px"></div>`);
  return `<div class="card${flagged ? ' flagged' : ''}${v?.verdict === 'quarantine' ? ' quarantine' : ''}">
  <div class="title"><b>${esc(c.id)}</b> — ${esc(c.name)} <span class="cell">r${c.sourceGridCell.row} c${c.sourceGridCell.col}</span></div>
  <div class="pair">
    <div class="src checker" style="width:${Math.round(c.sourceRect.w * srcScale)}px;height:${Math.round(c.sourceRect.h * srcScale)}px">
      <div style="width:${c.sourceRect.w}px;height:${c.sourceRect.h}px;transform:scale(${srcScale});transform-origin:0 0;background:url('${atlasUrl}') -${c.sourceRect.x}px -${c.sourceRect.y}px no-repeat"></div>
    </div>
    <div class="out magenta" title="plain: no overlay" style="width:${W}px;height:${H}px">
      <img src="${spriteUrl}" style="width:${W}px;height:${H}px">
    </div>
    <div class="out magenta" title="overlays" style="width:${W}px;height:${H}px">
      <img src="${spriteUrl}" style="width:${W}px;height:${H}px">
      ${gridLines.join('')}
      ${passageOverlay(c, W, H, tile, solidPx)}
      <div class="base" style="top:${(c.drawnRect.y + c.drawnRect.h) * zoom - 1}px;width:${W}px"></div>
      <div class="box" style="width:${W}px;height:${H}px"></div>
    </div>
  </div>
  <div class="meta">
    source <b>${esc(c.sourceAtlas)}</b> rect ${c.sourceRect.x},${c.sourceRect.y} ${c.sourceRect.w}x${c.sourceRect.h} · scale ${c.scale} · zoom ${zoom}x<br>
    sprite ${m.width}x${m.height}px · footprint ${m.footprintW}x${m.footprintH} tiles · <span class="r">solid ${c.footprint.solidRows}</span> / walk-behind ${c.footprint.backgroundTiles}${m.canPlaceOnSurfaces ? ' · surface item' : ''}<br>
    QA: opaque ${c.qa.opaquePixels} · isolated removed ${c.qa.isolatedRemoved} · holes filled ${c.qa.holesFilled} · src hidden-RGB@α0 ${c.qa.sourceHiddenRgbUnderAlpha0} · src semi-α ${c.qa.sourceSemiTransparent} · foreign px excluded ${c.qa.foreignPixelsExcludedFromRect}<br>
    passage: <b>${esc((c.passage ?? { kind: 'solid' }).kind)}</b>${c.passage?.rule ? ' — ' + esc(c.passage.rule) : ''}${c.runtimeTypes && c.runtimeTypes.length > 1 ? ' · runtime types ' + c.runtimeTypes.map((t) => esc(t.id) + (t.passable ? ' (open)' : '')).join(', ') : ''} · rim ${c.qa.rim} (mean luma ${c.qa.meanLuma})<br>
    ${c.merges.length ? `merge: ${c.merges.map((g) => `gap ${g.gapPx}px cols ${g.partGridColumns.join('/')}${g.gutterLike ? ' <b class="r">GUTTER-LIKE</b>' : ''}`).join(', ')} (${c.merges.length + 1} pieces)` : 'merge: none'} ·
    attached: ${c.attachedFragments.length ? c.attachedFragments.map((f) => `${f.pieces}pc ${f.area}px@${f.distPx} row ${f.row} rgb(${f.meanRgb.join(',')}) colour-dist ${f.colourDist}${f.crossRowWithinReach.length ? ' <b class="r">cross-row excluded</b>' : ''}`).join('; ') : 'none'} ·
    dropped nearby: ${c.droppedFragmentsNearby.length ? c.droppedFragmentsNearby.map((f) => `${f.area}px (${esc(f.reason)})`).join(', ') : 'none'} ·
    grid column ${c.gridColumn}${c.gridColumnMatchesOrdinal ? '' : ' <b class="r">≠ ordinal: verify identity</b>'}
  </div>
  <div class="verdict">${v ? `${v.verdict.toUpperCase()}${v.reason ? ' — ' + esc(v.reason) : ''}${v.note ? ' · ' + esc(v.note) : ''}` : flagged ? 'FLAGGED: review required' : 'verdict: (none recorded)'}</div>
</div>`;
}

const css = `body{font:12px/1.35 -apple-system,Helvetica,Arial,sans-serif;background:#1d1f24;color:#e8e8e8;margin:12px}
h1{font-size:18px;margin:4px 0 2px}.sub{color:#aab;margin-bottom:10px}
.grid{display:flex;flex-wrap:wrap;gap:10px}
.card{background:#2a2d34;border:2px solid #3b3f48;padding:8px;width:690px}
.card.flagged{border-color:#e0a030}.card.quarantine{border-color:#e04040;background:#3a2626}
.title{font-size:13px;margin-bottom:6px}.cell{color:#99a;float:right}
.pair{display:flex;gap:10px;align-items:flex-end;min-height:60px}
.checker{background-color:#fff;background-image:linear-gradient(45deg,#ccc 25%,transparent 25%),linear-gradient(-45deg,#ccc 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#ccc 75%),linear-gradient(-45deg,transparent 75%,#ccc 75%);background-size:12px 12px;background-position:0 0,0 6px,6px -6px,-6px 0;overflow:hidden;position:relative}
.magenta{background-color:#ff00ff;background-image:linear-gradient(45deg,#c000c0 25%,transparent 25%),linear-gradient(-45deg,#c000c0 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#c000c0 75%),linear-gradient(-45deg,transparent 75%,#c000c0 75%);background-size:16px 16px;background-position:0 0,0 8px,8px -8px,-8px 0;position:relative}
.out img{position:absolute;left:0;top:0;image-rendering:pixelated}
.gv{position:absolute;top:0;border-left:1px dashed rgba(255,255,255,.55)}.gh{position:absolute;left:0;border-top:1px dashed rgba(255,255,255,.55)}
.solid{position:absolute;box-sizing:border-box;background:rgba(255,40,40,.12);border:2px solid #ff3030}
.open{position:absolute;box-sizing:border-box;background:rgba(40,255,90,.18);border:2px solid #30ff70}
.span{position:absolute;left:0;box-sizing:border-box;background:rgba(40,140,255,.16);border:2px solid #40a0ff;color:#cfe6ff;font-size:10px;padding:2px}
.base{position:absolute;left:0;border-top:2px solid #20ff60}
.box{position:absolute;left:0;top:0;box-sizing:border-box;border:2px solid #ffffff}
.meta{margin-top:6px;color:#cdd;font-size:11px}.r{color:#ff7070}
.verdict{margin-top:4px;font-weight:bold;color:#9fe39f}.quarantine .verdict{color:#ff8080}.flagged .verdict{color:#ffcf70}
.legend span{display:inline-block;margin-right:14px}`;

const packs = [...new Set(review.crops.map((c) => c.pack))];
const written = [];
for (const pack of packs) {
  const crops = review.crops.filter((c) => c.pack === pack);
  const atlas = review.atlases.find((a) => a.file === crops[0].sourceAtlas);
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>Sprite QA — ${esc(pack)}</title><style>${css}</style></head><body>
<h1>Sprite QA — ${esc(pack)} (${crops.length} crops)</h1>
<div class="sub">atlas ${esc(atlas.file)} sha256 ${atlas.sha256} · ${atlas.width}x${atlas.height} · pieces ${atlas.piecesDetected} · merges ${atlas.multiPartMerges} (gaps ${atlas.mergeGapsPx.join(', ') || 'none'}) · inter-motif gutter min ${atlas.gutterMinPx}px median ${atlas.gutterMedianPx}px · gutter-like merge threshold ${review.pipeline.mergeGutterLikeThresholdPx}px (global measured min gutter) · fragments attached ${atlas.fragmentsAttached}, dropped ${atlas.fragmentsDropped.length}</div>
<div class="sub legend"><span>left: source crop (checker = transparent)</span><span>middle: output sprite, plain, on magenta checker</span><span>right: the same with overlays</span><span style="color:#fff">white: footprint box + tile grid</span><span style="color:#20ff60">green: baseline</span><span style="color:#ff7070">red: SOLID collision cells</span><span style="color:#30ff70">green: drawn OPENING (passable)</span><span style="color:#40a0ff">blue: walk-over span (deck tiles carry it)</span><span style="color:#ffcf70">amber card: flagged for mandatory review</span><span style="color:#ff8080">red card: quarantined</span></div>
<div class="grid">${crops.map(card).join('\n')}</div></body></html>`;
  const file = path.join(outDir, `${pack}.html`);
  fs.writeFileSync(file, html);
  written.push(file);
}
// GAME-SCALE VISIBILITY: every small or dark crop at the ACTUAL in-world size — device zoom 2
// (the whole-woods framing on this Retina Mac) and device zoom 4 (the default office framing,
// round(2 x devicePixelRatio)) — on a LIGHT and two DARK grounds. Screenshotted at
// deviceScaleFactor 1, so one image pixel is one device pixel: what a viewer actually sees.
const GROUNDS = [
  ['light meadow', 'rgb(132,170,92)'],
  ['light pavement', 'rgb(196,192,180)'],
  ['dark forest floor', 'rgb(46,66,40)'],
  ['dark water', 'rgb(30,56,84)'],
];
const small = review.crops.filter(
  (c) =>
    c.drawnRect.h <= 24 ||
    c.qa.meanLuma < 0.2 ||
    ['pets-and-companions', 'farm-animals'].includes(c.pack),
);
const cell = (c, zoom, ground) => {
  const m = c.manifest;
  const url = rel(path.join(reviewDir, 'sprites', `${c.id}.png`));
  return `<td style="background:${ground};padding:6px"><img src="${url}" style="width:${m.width * zoom}px;height:${m.height * zoom}px;image-rendering:pixelated;display:block"></td>`;
};
const gsRows = small
  .map((c) => {
    const v = verdicts[c.id];
    return `<tr><td class="lab"><b>${esc(c.id)}</b><br>${esc(c.name)}<br>${c.drawnRect.w}x${c.drawnRect.h}px drawn · luma ${c.qa.meanLuma} · rim ${c.qa.rim}${v ? '<br><b>' + esc(v.verdict) + (v.reason ? ': ' + esc(v.reason) : '') + '</b>' : ''}</td>${[2, 4].map((z) => GROUNDS.map(([, g]) => cell(c, z, g)).join('')).join('')}</tr>`;
  })
  .join('\n');
const gsHtml = `<!doctype html><html><head><meta charset="utf-8"><title>Game-scale visibility</title><style>body{font:12px -apple-system,Helvetica,sans-serif;background:#1d1f24;color:#e8e8e8;margin:12px}table{border-collapse:collapse}td{border:1px solid #333;vertical-align:bottom}td.lab{width:230px;padding:4px;vertical-align:middle}th{font-weight:normal;color:#aab;padding:4px}</style></head><body>
<h1 style="font-size:17px">Game-scale visibility — ${small.length} small or dark crops at actual in-world pixel size</h1>
<div style="color:#aab;margin-bottom:8px">Selection: drawn height ≤ 24 px, or mean luminance &lt; 0.2, or any companion / farm animal. Image pixel = device pixel (deviceScaleFactor 1). Zoom 2 = whole-woods framing on a 2x display; zoom 4 = default office framing (round(2 × devicePixelRatio)). All sprites are STATIC single frames.</div>
<table><tr><th></th>${[2, 4].map((z) => GROUNDS.map(([n]) => `<th>zoom ${z}<br>${n}</th>`).join('')).join('')}</tr>${gsRows}</table></body></html>`;
const gsFile = path.join(outDir, 'game-scale-visibility.html');
fs.writeFileSync(gsFile, gsHtml);
written.push(gsFile);
console.log(`wrote ${written.length} sheets to ${outDir}`);

if (!args.includes('--no-png')) {
  const require = createRequire(import.meta.url);
  const { chromium } = require('playwright-core');
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1460, height: 900 }, deviceScaleFactor: 1 });
    for (const file of written) {
      await page.goto('file://' + file);
      await page.waitForLoadState('load');
      await page.screenshot({ path: file.replace(/\.html$/, '.png'), fullPage: true });
    }
  } finally {
    await browser.close();
  }
  console.log(`screenshotted ${written.length} sheets`);
}
