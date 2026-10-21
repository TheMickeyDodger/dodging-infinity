/**
 * The viewer's CITY PRESENTATION choices (living city 6–9): lighting, weather and traffic. React state for
 * the controls, mirrored into the OfficeState that draws them, loaded from the server in the handshake and
 * saved on every change through the closed `saveCityPresentation` message.
 *
 * Local decoration only: nothing here reads or writes a Mission, an agent or anything canonical. A saved
 * record that fails the shared contract is ignored (the defaults stand), never partly applied.
 *
 * Call this BEFORE useExtensionMessages in the App: that hook sends `webviewReady` in its mount effect,
 * and the server answers with `cityPresentationLoaded`, so this subscription must already be in place.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import type { CityPresentation } from '../../../core/src/cityPresentation.js';
import {
  checkCityPresentation,
  DEFAULT_CITY_PRESENTATION,
} from '../../../core/src/cityPresentation.js';
import type { OfficeState } from '../office/engine/officeState.js';
import { transport } from '../transport/index.js';

export type CityChoice = Partial<Omit<CityPresentation, 'v'>>;

export interface CityPresentationControl {
  presentation: CityPresentation;
  /** Apply a change, show it at once and save it. */
  choose: (change: CityChoice) => void;
}

export function useCityPresentation(getOfficeState: () => OfficeState): CityPresentationControl {
  const [presentation, setPresentation] = useState<CityPresentation>({
    ...DEFAULT_CITY_PRESENTATION,
  });
  const current = useRef(presentation);

  const apply = useCallback(
    (next: CityPresentation) => {
      current.current = next;
      setPresentation(next);
      getOfficeState().setCityPresentation(next);
    },
    [getOfficeState],
  );

  useEffect(() => {
    return transport.onMessage((msg) => {
      if (msg.type !== 'cityPresentationLoaded') return;
      // null = nothing saved; a record that fails the contract is refused whole
      if (msg.state === null || checkCityPresentation(msg.state) !== null) return;
      apply(msg.state as unknown as CityPresentation);
    });
  }, [apply]);

  const choose = useCallback(
    (change: CityChoice) => {
      const next: CityPresentation = { ...current.current, ...change };
      if (checkCityPresentation(next) !== null) return;
      apply(next);
      transport.send({ type: 'saveCityPresentation', state: next });
    },
    [apply],
  );

  return { presentation, choose };
}
