import { useEffect, useRef, useState } from 'react';

import type {
  CityLighting,
  CityPresentation,
  CityTraffic,
} from '../../../core/src/cityPresentation.js';
import { CITY_STATUS_REFRESH_MS, TRAFFIC_CAP_LOW, TRAFFIC_MAX_VEHICLES } from '../constants.js';
import type { AtmosphereChoice, CityChoice } from '../hooks/useCityPresentation.js';
import type { CityAtmosphere } from '../office/city/cityEffects.js';
import type { WeatherMode } from '../office/city/cityRegionWeather.js';
import { weatherStatus } from '../office/city/cityRegionWeather.js';
import type { Season } from '../office/city/citySeason.js';
import { seasonName, SEASONS, seasonStatus } from '../office/city/citySeason.js';
import { Button } from './ui/Button.js';

const LIGHTING: ReadonlyArray<{ id: CityLighting; label: string; title: string }> = [
  { id: 'day', label: 'Day', title: 'Day: the city as it always looked (no lighting layer).' },
  { id: 'dusk', label: 'Dusk', title: 'Dusk: a still evening stage you choose.' },
  {
    id: 'night',
    label: 'Night',
    title: 'Night: a still night stage. Offices stay lit; agents and street lamps glow.',
  },
  {
    id: 'clock',
    label: 'Clock',
    title:
      "Clock: follows this computer's local time of day while this view is open. A function of the clock, not a simulation: nothing runs while the view is closed or asleep.",
  },
];

const SEASON_TITLES: Readonly<Record<Season, string>> = {
  spring: 'Spring: fresher greens outdoors. Holds until you choose Automatic.',
  summer: 'Summer: the city as it has always looked. Holds until you choose Automatic.',
  autumn: 'Autumn: golden foliage and ground outdoors. Holds until you choose Automatic.',
  winter:
    'Winter: snow-covered ground and frosted foliage outdoors. Holds until you choose Automatic.',
};

const WEATHER: ReadonlyArray<{ id: WeatherMode; label: string; title: string }> = [
  {
    id: 'auto',
    label: 'Automatic',
    title:
      'Automatic: each region of the world changes its weather on its own, every two to five minutes, to suit the season.',
  },
  { id: 'clear', label: 'Clear', title: 'Clear skies everywhere.' },
  {
    id: 'rain',
    label: 'Rain',
    title: 'Rain over the outdoors everywhere (decoration; not a forecast).',
  },
  {
    id: 'light-snow',
    label: 'Light snow',
    title: 'Light snow over the outdoors everywhere (decoration; not a forecast).',
  },
  {
    id: 'heavy-snow',
    label: 'Heavy snow',
    title: 'Heavy snow over the outdoors everywhere, in any season (decoration; not a forecast).',
  },
];

const TRAFFIC: ReadonlyArray<{ id: CityTraffic; label: string; title: string }> = [
  { id: 'off', label: 'Off', title: 'No moving traffic: the parked vehicles stay where they are.' },
  { id: 'low', label: 'Low', title: `Up to ${TRAFFIC_CAP_LOW} of the street's vehicles drive.` },
  {
    id: 'normal',
    label: 'Normal',
    title: `Up to ${TRAFFIC_MAX_VEHICLES} vehicles drive: every vehicle parked on a street in this world.`,
  },
];

interface CityControlsProps {
  presentation: CityPresentation;
  onChoose: (change: CityChoice) => void;
  /** Season and weather choices (item 18). */
  onAtmosphere: (change: AtmosphereChoice) => void;
  /** The season and weather as they are now (re-read while the panel is open). */
  atmosphere: () => CityAtmosphere;
  onClose: () => void;
  /** The words for the current lighting (timing semantics included), re-read while the panel is open. */
  lightingStatus: () => string;
  reducedMotion: boolean;
}

/** How often the live status line re-reads the clock while the panel is open. */

/**
 * THE CITY CONTROLS: lighting and weather choices for the decorative city. Local decoration only — the
 * panel says so — saved as the viewer's own preference. Buttons are real toggle buttons (keyboard
 * operable, `aria-pressed`), the current stage is also written out in words, and Escape closes the panel.
 */
export function CityControls({
  presentation,
  onChoose,
  onAtmosphere,
  atmosphere,
  onClose,
  lightingStatus,
  reducedMotion,
}: CityControlsProps) {
  const [status, setStatus] = useState(lightingStatus);
  useEffect(() => {
    setStatus(lightingStatus());
    const id = setInterval(() => setStatus(lightingStatus()), CITY_STATUS_REFRESH_MS);
    return () => clearInterval(id);
  }, [lightingStatus, presentation.lighting]);

  // the seasons and the weather move on their own, so their words are re-read while the panel is open, and at once after a choice
  const [atmo, setAtmo] = useState<CityAtmosphere>(atmosphere);
  useEffect(() => {
    setAtmo(atmosphere());
    const id = setInterval(() => setAtmo(atmosphere()), CITY_STATUS_REFRESH_MS);
    return () => clearInterval(id);
  }, [atmosphere]);
  const choose = (change: AtmosphereChoice) => {
    onAtmosphere(change);
    setAtmo(atmosphere());
  };
  const auto = atmo.season.mode === 'auto';

  // Escape and the Close button close the panel and hand focus back to what opened it (the City button), so a keyboard user is never
  // left on the page body. (Escape pressed while focus is on the City button itself is handled there: see BottomToolbar.)
  const opener = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const active = document.activeElement;
    opener.current = active instanceof HTMLElement && active !== document.body ? active : null;
  }, []);
  const close = () => {
    onClose();
    const back = opener.current?.isConnected
      ? opener.current
      : document.querySelector<HTMLElement>('[aria-controls="city-controls"]');
    back?.focus();
  };

  return (
    <div
      id="city-controls"
      // sits ABOVE the always-visible Ambient legend (fixed bottom-56 left-10), which would otherwise cover its last line. It grows
      // upward from there, so its height is BOUNDED by the viewport: the heading and the close button stay in view and the controls
      // scroll inside (the panel's own scroll region), at any window height.
      className="absolute bottom-[104px] left-10 z-30 pixel-panel flex flex-col max-w-320 max-h-[calc(100vh-120px)]"
      role="group"
      aria-label="City lighting and weather"
      data-testid="city-controls"
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          close();
        }
      }}
    >
      <div
        className="flex items-center justify-between gap-6 p-6 pb-2 shrink-0"
        data-testid="city-controls-header"
      >
        <h2 className="text-sm font-normal m-0" data-testid="city-controls-heading">
          City: lighting, seasons and weather
        </h2>
        <Button
          size="sm"
          aria-label="Close the City panel"
          title="Close the City panel (Escape also closes it)"
          onClick={close}
          data-testid="city-controls-close"
        >
          Close
        </Button>
      </div>
      <div
        className="flex-1 min-h-0 overflow-y-auto p-6 pt-2 flex flex-col gap-6"
        data-testid="city-controls-scroll"
      >
        <p className="text-xs text-text-muted" data-testid="city-controls-note">
          Decoration only. Lighting and weather are drawn on your screen; they change nothing about
          Missions, agents or their work, and use no weather service or location.
        </p>
        <div role="group" aria-label="Lighting" className="flex flex-col gap-2">
          <span className="text-xs">Lighting</span>
          <div className="flex gap-4">
            {LIGHTING.map((m) => (
              <Button
                key={m.id}
                size="sm"
                variant={presentation.lighting === m.id ? 'active' : 'default'}
                aria-pressed={presentation.lighting === m.id}
                title={m.title}
                onClick={() => onChoose({ lighting: m.id })}
                data-testid={`city-lighting-${m.id}`}
              >
                {m.label}
              </Button>
            ))}
          </div>
          <span
            className="text-xs text-text-muted"
            role="status"
            aria-live="polite"
            data-testid="city-lighting-status"
          >
            {status}
          </span>
        </div>
        <div role="group" aria-label="Season" className="flex flex-col gap-2">
          <span className="text-xs">Season</span>
          <div className="flex flex-wrap gap-4">
            <Button
              size="sm"
              variant={auto ? 'active' : 'default'}
              aria-pressed={auto}
              title="Automatic: the seasons turn on their own, thirty minutes each, starting from summer."
              onClick={() => choose({ automaticSeasons: true })}
              data-testid="city-season-auto"
            >
              Automatic
            </Button>
            {SEASONS.map((id) => {
              const selected = !auto && atmo.season.season === id;
              return (
                <Button
                  key={id}
                  size="sm"
                  variant={selected ? 'active' : 'default'}
                  aria-pressed={selected}
                  aria-current={auto && atmo.season.season === id ? 'true' : undefined}
                  title={SEASON_TITLES[id]}
                  onClick={() => choose({ season: id })}
                  data-testid={`city-season-${id}`}
                >
                  {seasonName(id)}
                </Button>
              );
            })}
            <Button
              size="sm"
              variant={atmo.season.paused && auto ? 'active' : 'default'}
              aria-pressed={atmo.season.paused && auto}
              disabled={!auto}
              title={
                auto
                  ? 'Pause the seasons where they are; press again to resume them. The weather carries on either way.'
                  : 'The season is held on your choice. Choose Automatic to pause or resume the seasons.'
              }
              onClick={() => choose({ seasonsPaused: !atmo.season.paused })}
              data-testid="city-season-pause"
            >
              Pause seasons
            </Button>
          </div>
          <span
            className="text-xs text-text-muted"
            role="status"
            aria-live="polite"
            data-testid="city-season-status"
          >
            {seasonStatus(atmo.season)}
            {reducedMotion ? ' Reduced motion is on: the season changes at once.' : ''}
          </span>
        </div>
        <div role="group" aria-label="Weather" className="flex flex-col gap-2">
          <span className="text-xs">Weather</span>
          <div className="flex flex-wrap gap-4">
            {WEATHER.map((m) => (
              <Button
                key={m.id}
                size="sm"
                variant={atmo.weatherMode === m.id ? 'active' : 'default'}
                aria-pressed={atmo.weatherMode === m.id}
                title={m.title}
                onClick={() => choose({ weather: m.id })}
                data-testid={`city-weather-${m.id}`}
              >
                {m.label}
              </Button>
            ))}
          </div>
          <span className="text-xs text-text-muted" data-testid="city-weather-status">
            {weatherStatus(atmo.weatherMode, atmo.regions)} Indoors stays dry.
            {reducedMotion ? ' Held still: reduced motion is on.' : ''}
          </span>
          <details data-testid="city-atmosphere-details">
            <summary
              className="text-xs cursor-pointer"
              data-testid="city-atmosphere-details-summary"
            >
              How seasons and weather work
            </summary>
            <p className="text-xs text-text-muted mt-2">
              Automatic seasons take thirty minutes of this view being open and running each, two
              hours for the year; nothing passes while it is closed, hidden or asleep. Pause keeps
              the time left. Choosing a season holds it until you choose Automatic, which then
              starts a fresh thirty minutes. Automatic weather gives each region its own weather and
              changes it every two to five minutes; heavy snow comes by itself only in winter, in
              the Woodland and Highlands. Seasons and weather are decoration: never a forecast, and
              never a sign of what an agent or a Mission is doing.
            </p>
          </details>
        </div>
        <div role="group" aria-label="Traffic" className="flex flex-col gap-2">
          <span className="text-xs">Traffic</span>
          <div className="flex gap-4">
            {TRAFFIC.map((m) => (
              <Button
                key={m.id}
                size="sm"
                variant={presentation.traffic === m.id ? 'active' : 'default'}
                aria-pressed={presentation.traffic === m.id}
                title={m.title}
                onClick={() => onChoose({ traffic: m.id })}
                data-testid={`city-traffic-${m.id}`}
              >
                {m.label}
              </Button>
            ))}
          </div>
          <span className="text-xs text-text-muted" data-testid="city-traffic-status">
            {presentation.traffic === 'off'
              ? 'No moving traffic: parked vehicles stay parked.'
              : `Vehicles drive the streets they are parked on, stop at crossings and give way to people.${reducedMotion ? ' Held still: reduced motion is on.' : ''}`}
          </span>
          <span className="text-xs text-text-muted" data-testid="city-traffic-note">
            A vehicle that looks like an ambulance or a police car is an ordinary car: none has any
            priority or meaning.
          </span>
        </div>
      </div>
    </div>
  );
}
