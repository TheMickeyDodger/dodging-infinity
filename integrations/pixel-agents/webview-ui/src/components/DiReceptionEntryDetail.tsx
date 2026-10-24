/**
 * The exact record behind one reception entry, one labelled disclosure away from its plain card:
 * identity, source, lifecycle, every cause with its details, and the route to act as text. Nothing here
 * decides or performs anything; it shows the record's values and identifiers verbatim. A cause's label is the
 * office's own explanatory wording, shown in plain punctuation (`plainServerWording`, AC7).
 */
import type { DiReceptionEntry } from '../../../core/src/messages.js';
import { DI_FOCUS_RING } from './diNavigation.js';
import { formatLive } from './diOfficeFormat.js';
import { plainServerWording } from './diReceptionPlain.js';

export function EntryDetail({ entry, fixture }: { entry: DiReceptionEntry; fixture: boolean }) {
  return (
    <div className="flex flex-col gap-1 pl-8">
      <div className="text-text-muted break-all">
        {`${entry.missionRef} · source ${entry.sourceId} (${entry.sourceKind}) · native ${entry.nativeId} · producer (declared) ${entry.producerDeclared}`}
      </div>
      <div className="text-text-muted break-all">
        {`${entry.lifecycleModel} · ${entry.standing} / ${entry.lifecycleState} · binding ${entry.bindingState} (${entry.bindingReason}) · ${formatLive(entry.live, entry.notLiveReason, fixture)}`}
      </div>
      <ul className="flex flex-col gap-1 pl-8">
        {entry.causes.map((cause) => (
          <li key={cause.cause} className="break-words">
            {plainServerWording(cause.label)}
            {cause.details.map((detail, i) => (
              <div key={i} className="pl-8 text-text-muted break-all">
                {`${detail.label}: ${detail.value ?? 'not recorded'}`}
              </div>
            ))}
          </li>
        ))}
      </ul>
      {entry.alsoUnknown.length > 0 && (
        <div className="text-text-muted break-all">
          {`also not known for this Mission: ${entry.alsoUnknown.join('; ')}`}
        </div>
      )}
      <details>
        <summary className={`cursor-pointer text-text-muted ${DI_FOCUS_RING}`}>
          Route to act (text only; this desk performs nothing)
        </summary>
        <div className="pl-8 break-words">{entry.route}</div>
        {entry.routeIdentifiers.map((id) => (
          <div key={id.label} className="pl-8 text-text-muted break-all">
            {`${id.label}: ${id.value ?? 'not recorded'}`}
          </div>
        ))}
      </details>
    </div>
  );
}
