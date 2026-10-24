import { useEffect, useState } from 'react';

import type { OfficeState } from '../office/engine/officeState.js';
import { ambientLegendText } from './ambientLegendText.js';

/** How often the legend re-reads the roster (it changes only with the world). */
const LEGEND_REFRESH_MS = 1_000;

/**
 * DI mode: a concise, always-visible note that the walking residents are Ambient and decorative,
 * with their count (counts by job in the tooltip). Each resident's own hover label says the same.
 */
export function AmbientLegend({ officeState }: { officeState: OfficeState }) {
  const [summary, setSummary] = useState(() => officeState.ambient?.summary() ?? null);
  useEffect(() => {
    const id = setInterval(
      () => setSummary(officeState.ambient?.summary() ?? null),
      LEGEND_REFRESH_MS,
    );
    return () => clearInterval(id);
  }, [officeState]);
  if (!summary || summary.total === 0) return null;
  const jobs =
    summary.byJob.map((j) => `${j.label} ${j.count}`).join(', ') +
    (summary.offDuty > 0
      ? `. ${summary.offDuty} are off duty (resting in the working office, none removed) and take their turn.`
      : '');
  return (
    <div
      className="order-2 shrink-0 max-w-full pixel-panel px-8 py-4 text-xs pointer-events-none select-none"
      role="note"
      data-testid="ambient-legend"
      title={jobs}
    >
      {ambientLegendText(summary)}
    </div>
  );
}
