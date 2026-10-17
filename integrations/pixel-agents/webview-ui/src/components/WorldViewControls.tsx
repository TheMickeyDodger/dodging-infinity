import { useState } from 'react';

import { WORLD_VIEW_CONTROLS_BOTTOM_GAP_PX, WORLD_VIEW_CONTROLS_TOP_PX } from '../constants.js';
import type { WorldViewKind } from '../office/layout/worldViews.js';
import { Button } from './ui/Button.js';

/** Subway stations are areas named "... Station": listed apart from the districts. */
const STATION_LABEL = /Station$/;

const LABELS: Record<WorldViewKind, { label: string; title: string }> = {
  office: { label: 'Office', title: 'Camera: frame the office (view only)' },
  deck: {
    label: 'Deck',
    title: 'Camera: frame the observation deck and the view north (view only)',
  },
  woods: {
    label: 'Woods',
    title:
      'Camera: the woodland around the palace at the smallest pixel-perfect zoom (view only). The whole world is the Map.',
  },
};

interface WorldViewControlsProps {
  /** The views this world has (a world without a deck offers no Deck view). */
  views: readonly WorldViewKind[];
  /** The view the camera is holding, or null once the user has panned or zoomed by hand. */
  active: WorldViewKind | null;
  onSelect: (view: WorldViewKind) => void;
  /** Whether the whole-world Map overlay is open. */
  mapOpen: boolean;
  /** False in the layout editor: the Map is never open over an editing session. */
  mapAvailable: boolean;
  onToggleMap: () => void;
  /** The world's named districts (layout.areas, as data); empty = no list. */
  districts: readonly string[];
  onSelectDistrict: (label: string) => void;
  /** What the last district jump did (announced politely), or null. */
  districtStatus: string | null;
}

/**
 * CAMERA PRESETS for a large world — presentation only: each button moves the local camera and
 * nothing else (no message is sent, nothing is saved). Below the zoom buttons, top-left, and
 * stacked ABOVE the Map overlay (z-30 over its z-20) so Map can close itself. Arrow
 * keys pan (outside the layout editor); manual pan and zoom always win until a view is chosen.
 */
export function WorldViewControls({
  views,
  active,
  onSelect,
  mapOpen,
  mapAvailable,
  onToggleMap,
  districts,
  onSelectDistrict,
  districtStatus,
}: WorldViewControlsProps) {
  const [listOpen, setListOpen] = useState(false);
  return (
    <div
      className="absolute left-8 z-30 flex flex-col gap-4"
      // BOUNDED to the main area: a long district list scrolls INSIDE itself and never overflows the
      // office wrapper (an overflowing list let focus scroll the wrapper and lift the canvas off the
      // bottom of the view: the white lower band)
      style={{ top: WORLD_VIEW_CONTROLS_TOP_PX, maxHeight: `calc(100% - ${WORLD_VIEW_CONTROLS_TOP_PX + WORLD_VIEW_CONTROLS_BOTTOM_GAP_PX}px)` }}
      role="group"
      aria-label="Camera views"
      data-testid="world-view-controls"
    >
      {views.map((view) => (
        <Button
          key={view}
          size="sm"
          variant={active === view ? 'active' : 'default'}
          aria-pressed={active === view}
          onClick={() => onSelect(view)}
          title={LABELS[view].title}
          className="shadow-pixel"
          data-testid={`world-view-${view}`}
        >
          {LABELS[view].label}
        </Button>
      ))}
      {mapAvailable && (
        <Button
          size="sm"
          variant={mapOpen ? 'active' : 'default'}
          aria-pressed={mapOpen}
          onClick={onToggleMap}
          title="Map: the whole world, scaled to fit (view only). Click a spot to go there (at the office view's legible zoom); Esc closes."
          className="shadow-pixel"
          data-testid="world-view-map"
        >
          Map
        </Button>
      )}
      {districts.length > 0 && (
        <Button
          size="sm"
          variant={listOpen ? 'active' : 'default'}
          aria-expanded={listOpen}
          aria-controls="world-district-list"
          onClick={() => setListOpen((open) => !open)}
          title="Districts: go to a named place (view only). The list alternative to the Map."
          className="shadow-pixel"
          data-testid="world-view-districts"
        >
          Districts
        </Button>
      )}
      {listOpen && districts.length > 0 && (
        <nav
          id="world-district-list"
          aria-label="Districts and stations"
          className="pixel-panel p-4 flex flex-col gap-2 min-h-0 overflow-y-auto"
          data-testid="world-district-list"
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.stopPropagation();
              setListOpen(false);
              return;
            }
            // KEYBOARD: arrows (and Home/End) move between entries; Enter or Space jumps
            const items = [...e.currentTarget.querySelectorAll<HTMLButtonElement>('button[data-district]')];
            const at = items.indexOf(document.activeElement as HTMLButtonElement);
            const step = e.key === 'ArrowDown' ? 1 : e.key === 'ArrowUp' ? -1 : 0;
            let to = -1;
            if (step) to = at < 0 ? 0 : (at + step + items.length) % items.length;
            else if (e.key === 'Home') to = 0;
            else if (e.key === 'End') to = items.length - 1;
            if (to >= 0) {
              e.preventDefault();
              e.stopPropagation();
              items[to]?.focus();
            }
          }}
        >
          {[
            { heading: 'Districts', labels: districts.filter((l) => !STATION_LABEL.test(l)) },
            { heading: 'Stations', labels: districts.filter((l) => STATION_LABEL.test(l)) },
          ]
            .filter((g) => g.labels.length > 0)
            .map((g) => (
              <div key={g.heading} className="flex flex-col gap-2">
                <div className="text-xs" data-testid="world-district-heading">
                  {g.heading}
                </div>
                <ul className="flex flex-col gap-2" role="list" aria-label={g.heading}>
                  {g.labels.map((label) => (
                    <li key={label}>
                      <Button
                        size="sm"
                        className="w-full text-left"
                        onClick={() => onSelectDistrict(label)}
                        title={`Camera: go to ${label} (instant camera travel, view only; not a ride)`}
                        data-testid="world-district"
                        data-district={label}
                      >
                        {label}
                      </Button>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
        </nav>
      )}
      {/* width 0 + min-width 100%: the status never sizes the control column (a long status
          once widened every button over the world); it wraps inside the buttons' width instead */}
      <div
        aria-live="polite"
        className="text-xs"
        style={{ width: 0, minWidth: '100%', overflowWrap: 'anywhere' }}
        data-testid="world-district-status"
      >
        {districtStatus}
      </div>
    </div>
  );
}
