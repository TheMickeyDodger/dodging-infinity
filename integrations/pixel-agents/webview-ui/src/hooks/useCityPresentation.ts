/**
 * The viewer's CITY PRESENTATION (living city 6–9, item 18): lighting and traffic, and the season and regional weather. React state for
 * the controls, mirrored into the OfficeState that draws them, loaded from the server in the handshake and saved through the closed
 * `saveCityPresentation` message as a contract-v2 record (core/src/cityPresentation.ts).
 *
 * LOADING: the record the server sends is checked and, if it is a v1 record, migrated by the ONE shared function (`migrateCityPresentation`),
 * then handed to the OfficeState, which continues the season and the weather schedule from the time left they were saved with: no
 * catch-up. A record that is neither v2 nor v1 is ignored whole (the defaults stand) and nothing else is touched.
 *
 * SAVING: a viewer's own choice is saved at once; a transition (a season change, an automatic weather draw), a running clock's checkpoint
 * (only if a clock ACTUALLY ADVANCED since the last save: a hidden view never rewrites an unchanged record) and the hiding of the view are
 * saved by `CitySaver` at a bounded rate (never per frame), and nothing is saved before the saved record has been loaded (so a slow
 * handshake can never overwrite it with the defaults).
 *
 * Local decoration only: nothing here reads or writes a Mission, an agent or anything canonical.
 *
 * Call this BEFORE useExtensionMessages in the App: that hook sends `webviewReady` in its mount effect,
 * and the server answers with `cityPresentationLoaded`, so this subscription must already be in place.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import type { CityPresentation } from '../../../core/src/cityPresentation.js';
import {
  checkCityPresentation,
  DEFAULT_CITY_PRESENTATION,
  migrateCityPresentation,
} from '../../../core/src/cityPresentation.js';
import { CitySaver, startCitySaving } from '../office/city/cityPersist.js';
import type { WeatherMode } from '../office/city/cityRegionWeather.js';
import type { Season } from '../office/city/citySeason.js';
import type { OfficeState } from '../office/engine/officeState.js';
import { transport } from '../transport/index.js';

export type CityChoice = Partial<Omit<CityPresentation, 'v' | 'weather'>>;

/** A season or weather choice of the City panel (item 18). Saved at once, like the other choices. */
export type AtmosphereChoice =
  | { season: Season }
  | { automaticSeasons: true }
  | { seasonsPaused: boolean }
  | { weather: WeatherMode };

export interface CityPresentationControl {
  presentation: CityPresentation;
  /** Apply a lighting or traffic change, show it at once and save it. */
  choose: (change: CityChoice) => void;
  /** Apply a season or weather choice, show it at once and save it. */
  chooseAtmosphere: (change: AtmosphereChoice) => void;
}

export function useCityPresentation(getOfficeState: () => OfficeState): CityPresentationControl {
  const [presentation, setPresentation] = useState<CityPresentation>({
    ...DEFAULT_CITY_PRESENTATION,
  });
  const current = useRef(presentation);
  /** True once the server's answer (a saved record or none) has arrived: nothing is saved by the saver before that. */
  const ready = useRef(false);
  /** Every decision about WHEN to save is the saver's (cityPersist.ts); this hook only wires timers and events to it. */
  const saver = useRef<CitySaver | null>(null);
  saver.current ??= new CitySaver(() =>
    transport.send({ type: 'saveCityPresentation', state: getOfficeState().cityRecord() }),
  );

  const markSaved = useCallback(() => {
    saver.current?.loaded(Date.now(), getOfficeState().city);
  }, [getOfficeState]);

  /** Send the whole city as one v2 record (a viewer's choice). */
  const save = useCallback(() => {
    saver.current?.save(Date.now(), getOfficeState().city);
  }, [getOfficeState]);

  useEffect(() => {
    return transport.onMessage((msg) => {
      if (msg.type !== 'cityPresentationLoaded') return;
      // null = nothing saved; a v1 record is migrated and anything else is refused whole (the defaults stand)
      if (msg.state !== null) {
        const migration = migrateCityPresentation(msg.state);
        if (migration.ok) {
          const next: CityPresentation = {
            ...DEFAULT_CITY_PRESENTATION,
            lighting: migration.state.lighting,
            traffic: migration.state.traffic,
          };
          current.current = next;
          setPresentation(next);
          getOfficeState().loadCityRecord(migration.state);
        } else {
          console.warn(`[Webview] Ignoring the saved city presentation: ${migration.reason}`);
        }
      }
      ready.current = true;
      markSaved(); // what was just loaded is what is saved: nothing is due
    });
  }, [getOfficeState, markSaved]);

  // transitions, running-clock checkpoints (bounded rate) and the hiding of the view; never per frame (cityPersist.ts owns the timers and
  // the listeners, so a test drives the same wiring under fake timers)
  useEffect(() => {
    const current = saver.current;
    if (current === null) return;
    return startCitySaving(
      current,
      () => getOfficeState().city,
      () => ready.current,
      {
        doc: document,
        win: window,
      },
    );
  }, [getOfficeState]);

  const choose = useCallback(
    (change: CityChoice) => {
      const next: CityPresentation = { ...current.current, ...change };
      if (checkCityPresentation(next) !== null) return;
      current.current = next;
      setPresentation(next);
      getOfficeState().setCityPresentation(next);
      save();
    },
    [getOfficeState, save],
  );

  const chooseAtmosphere = useCallback(
    (change: AtmosphereChoice) => {
      const os = getOfficeState();
      if ('season' in change) os.chooseCitySeason(change.season);
      else if ('automaticSeasons' in change) os.chooseCityAutomaticSeasons();
      else if ('seasonsPaused' in change) {
        if (change.seasonsPaused) os.pauseCitySeasons();
        else os.resumeCitySeasons();
      } else os.chooseCityWeather(change.weather);
      save();
    },
    [getOfficeState, save],
  );

  return { presentation, choose, chooseAtmosphere };
}
