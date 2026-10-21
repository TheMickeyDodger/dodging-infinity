/** What the legend says. `total` is every simulated resident; `onDuty` is how many are drawn right now (a working
 *  office shows only some of its residents at a time; the rest are off duty, not removed). With nobody off
 *  duty the text is exactly what it always was. */
export function ambientLegendText(summary: {
  total: number;
  onDuty: number;
  offDuty: number;
}): string {
  return summary.offDuty > 0
    ? `Ambient residents · ${summary.onDuty} on duty of ${summary.total} · decorative`
    : `Ambient residents · ${summary.total} · decorative`;
}
