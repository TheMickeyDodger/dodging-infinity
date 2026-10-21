import { useEffect, useState } from 'react';

import type {
  CityLighting,
  CityPresentation,
  CityTraffic,
  CityWeather,
} from '../../../core/src/cityPresentation.js';
import { CITY_STATUS_REFRESH_MS, TRAFFIC_CAP_LOW, TRAFFIC_MAX_VEHICLES } from '../constants.js';
import type { CityChoice } from '../hooks/useCityPresentation.js';
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

const WEATHER: ReadonlyArray<{ id: CityWeather; label: string; title: string }> = [
  { id: 'off', label: 'Clear', title: 'No weather.' },
  { id: 'rain', label: 'Rain', title: 'Rain over the outdoors (decoration; not a forecast).' },
  { id: 'snow', label: 'Snow', title: 'Snow over the outdoors (decoration; not a forecast).' },
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

  return (
    <div
      id="city-controls"
      // sits ABOVE the always-visible Ambient legend (fixed bottom-56 left-10), which would otherwise cover its last line
      className="absolute bottom-[104px] left-10 z-30 pixel-panel p-6 flex flex-col gap-6 max-w-320"
      role="group"
      aria-label="City lighting and weather"
      data-testid="city-controls"
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          onClose();
        }
      }}
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
      <div role="group" aria-label="Weather" className="flex flex-col gap-2">
        <span className="text-xs">Weather</span>
        <div className="flex gap-4">
          {WEATHER.map((m) => (
            <Button
              key={m.id}
              size="sm"
              variant={presentation.weather === m.id ? 'active' : 'default'}
              aria-pressed={presentation.weather === m.id}
              title={m.title}
              onClick={() => onChoose({ weather: m.id })}
              data-testid={`city-weather-${m.id}`}
            >
              {m.label}
            </Button>
          ))}
        </div>
        <span className="text-xs text-text-muted" data-testid="city-weather-status">
          {presentation.weather === 'off'
            ? 'Clear skies.'
            : `${presentation.weather === 'rain' ? 'Rain' : 'Snow'} over the outdoors only; indoors stays dry.${reducedMotion ? ' Held still: reduced motion is on.' : ''}`}
        </span>
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
  );
}
