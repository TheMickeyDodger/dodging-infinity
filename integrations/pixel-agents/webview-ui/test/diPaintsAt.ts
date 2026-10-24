/**
 * DOES THIS ELEMENT PAINT ANYTHING AT THIS POINT? The question a forced hit test cannot answer on its own.
 *
 * The probes (diTabTraversal.ts, diOcclusion.ts) make every element hit-testable (`pointer-events: auto !important`) so that an overlay that ignores the pointer (the fixture
 * banner, the ambient legend) is still seen covering content. The price: an element that paints NOTHING (a transparent layout container: the left strip's rail, grid and
 * first-row cell, all pointer-events: none, no background, no border) is returned too, and a stop under it looked "covered" although a person sees the stop and a real click
 * reaches it. A forced hit is not painted obstruction. So the probes walk the whole forced stack from the top and stop at the target, or at the FIRST element that really paints
 * something at the point; elements that paint nothing there are looked through.
 *
 * WHAT THE MODEL COMPUTES. A hit stack answers what would receive a pointer event at a point; that is NOT the same question as what is painted there, and the two diverge exactly where content
 * paints but takes no hits. This model makes the two known divergences hit-testable for the probe (pointer-events is forced on by a probe style, and `inert` content, which hit-testing excludes
 * whatever pointer-events says, is made hit-testable for the instant of sampling and restored: the camera controls drawn but suspended under the City panel are the case that showed it), and then
 * asks, of each element the stack returns, whether it paints something of its OWN at the point: its background colour with alpha, a background image, an inset shadow, a border whose band contains
 * the point, its own text at the point (the text's line boxes, which is coarser than the glyphs), a replaced element (image, svg, canvas, video, form control, iframe), or a pseudo-element with
 * content. A colour in a syntax it does not parse counts as opaque and a pseudo-element with any content counts as painting; that choice errs towards reporting FOR THOSE CASES ONLY. An element
 * that is visibility: hidden, display: none or opacity 0 paints nothing. A transparent container is NEVER permission to ignore its painted subtree: the children are separate entries in the stack
 * and are judged on their own paint.
 *
 * WHAT IT DOES NOT MODEL, so a FALSE NEGATIVE (something painted and not reported) is POSSIBLE for it: outlines; non-inset (outer) box shadows (an element whose shadow, not its box, lies over the
 * point is not in the stack at all); clip-path; mask; filters, blend modes and backdrop-filter; content-visibility; canvas contents; glyph-level text coverage; and any painted content that is absent
 * from the stack for a reason other than pointer-events and inert. This is NOT a universal paint oracle and is not claimed to be one. The INDEPENDENT measure of what is painted is DECODED
 * PIXELS (the sticky note's opacity, the City panel's paint invariant, the shared-space paint invariant) and it is kept: a hit-stack result never replaces it.
 *
 * Shipped as a function-expression source so both probes can build it inside the page (a page function cannot close over module code).
 */
export const PAINTS_AT_SOURCE = String.raw`(el, x, y) => {
  const s = getComputedStyle(el);
  if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) return false;
  const alpha = (c) => {
    if (c === 'transparent') return 0;
    const m = /^rgba?\(([^)]*)\)$/.exec(c);
    if (!m) return 1;
    const parts = m[1].split(/[,/]/).map((t) => t.trim()).filter((t) => t !== '');
    return parts.length >= 4 ? Number(parts[3]) : 1;
  };
  if (alpha(s.backgroundColor) > 0 || s.backgroundImage !== 'none') return true;
  if (/inset/.test(s.boxShadow)) return true;
  const b = el.getBoundingClientRect();
  const band = [
    [s.borderTopWidth, s.borderTopStyle, s.borderTopColor, y - b.top],
    [s.borderBottomWidth, s.borderBottomStyle, s.borderBottomColor, b.bottom - y],
    [s.borderLeftWidth, s.borderLeftStyle, s.borderLeftColor, x - b.left],
    [s.borderRightWidth, s.borderRightStyle, s.borderRightColor, b.right - x],
  ];
  for (const [w, st, col, d] of band) if (parseFloat(w) > 0 && st !== 'none' && alpha(col) > 0 && d <= parseFloat(w)) return true;
  if (alpha(s.color) > 0) {
    for (const n of Array.from(el.childNodes)) {
      if (n.nodeType !== 3 || !(n.textContent || '').trim()) continue;
      const r = document.createRange();
      r.selectNodeContents(n);
      for (const q of Array.from(r.getClientRects())) if (x >= q.left && x <= q.right && y >= q.top && y <= q.bottom) return true;
    }
  }
  if (['IMG', 'SVG', 'CANVAS', 'VIDEO', 'INPUT', 'TEXTAREA', 'SELECT', 'IFRAME'].includes(el.tagName.toUpperCase())) return true;
  for (const pseudo of ['::before', '::after']) {
    const ps = getComputedStyle(el, pseudo);
    if (ps.content !== 'none' && ps.content !== 'normal' && ps.display !== 'none') return true;
  }
  return false;
}`;
