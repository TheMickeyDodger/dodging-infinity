import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';

import { toMajorMinor } from '../changelogData.ts';
import { WHATS_NEW_AUTO_CLOSE_MS, WHATS_NEW_FADE_MS } from '../constants.ts';
import { Button } from './ui/Button.js';

interface VersionIndicatorProps {
  currentVersion: string;
  lastSeenVersion: string;
  onDismiss: () => void;
  onOpenChangelog: () => void;
  /**
   * Where the version LABEL (and its hover tooltip) is drawn when it must not share space with the strip's scrolling content: the world strip's pinned footer, in a narrow world
   * (WorldLeftRail). Null: the label sits where it always did, in the world's corner. The label is a control and a tab stop (a real button), and in a narrow world it used to sit in the
   * top-right band, under whatever the strip drew there (the ambient legend at 320 x 225) and under an invisible wrapper that took the pointer; the footer is a flex sibling of the
   * scrolling strip, so nothing is drawn over it and no focus scroll can carry it away.
   */
  labelHost?: HTMLElement | null;
  /** A popover is open over the world: the label, which then lives outside the controls gate that would make it inert, is made inert here (it stays drawn). */
  suspended?: boolean;
}

export function VersionIndicator({
  currentVersion,
  lastSeenVersion,
  onDismiss,
  onOpenChangelog,
  labelHost = null,
  suspended = false,
}: VersionIndicatorProps) {
  const [dismissed, setDismissed] = useState(false);
  const [fading, setFading] = useState(false);
  const [labelHovered, setLabelHovered] = useState(false);

  const currentMajorMinor = toMajorMinor(currentVersion);
  const isUnseen = currentMajorMinor !== lastSeenVersion;
  const showUpdateNotice = isUnseen && !dismissed;

  // Start fade-out after the auto-close delay, then fully dismiss after the transition. Tab resets the
  // delay: a keyboard pass through a wide office is longer than the idle timeout, and the dismiss has to
  // still be in the tab sequence when the traversal reaches it. Idle time with no Tab still closes it.
  useEffect(() => {
    if (!showUpdateNotice || fading) return;
    let fadeTimer = window.setTimeout(() => setFading(true), WHATS_NEW_AUTO_CLOSE_MS);
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return;
      window.clearTimeout(fadeTimer);
      fadeTimer = window.setTimeout(() => setFading(true), WHATS_NEW_AUTO_CLOSE_MS);
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.clearTimeout(fadeTimer);
      window.removeEventListener('keydown', onKey);
    };
  }, [showUpdateNotice, fading]);

  useEffect(() => {
    if (!fading) return;
    const removeTimer = setTimeout(() => {
      setDismissed(true);
      onDismiss();
    }, WHATS_NEW_FADE_MS);
    return () => clearTimeout(removeTimer);
  }, [fading, onDismiss]);

  const handleDismiss = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      setDismissed(true);
      onDismiss();
    },
    [onDismiss],
  );

  const handleOpenChangelog = useCallback(() => {
    setDismissed(true);
    onOpenChangelog();
  }, [onOpenChangelog]);

  if (!currentVersion) return null;

  // Hover tooltip: "See what's new" appears on label hover after the notice is gone. A pointer convenience for the version label's own action: the label is the keyboard-operable control, so this is a
  // real button (the same action) that is not a second tab stop that appears only while the pointer is over the label.
  const tooltip = (hosted: boolean) =>
    !showUpdateNotice && labelHovered ? (
      <button
        type="button"
        tabIndex={-1}
        onClick={handleOpenChangelog}
        className={
          hosted
            ? 'absolute bottom-full right-0 mb-4 z-20 pixel-panel py-6 px-12 cursor-pointer text-sm whitespace-nowrap pointer-events-auto'
            : 'absolute bottom-42 right-28 z-20 pixel-panel py-6 px-12 cursor-pointer text-sm whitespace-nowrap'
        }
      >
        See what's new!
      </button>
    ) : null;
  // Version label: always visible; a real button (the same action), with a name that carries its visible text. Hosted in the pinned footer it is in the flow (right-aligned) and takes the pointer back.
  const label = (hosted: boolean) => (
    <button
      type="button"
      onMouseEnter={() => setLabelHovered(true)}
      onMouseLeave={() => setLabelHovered(false)}
      onClick={handleOpenChangelog}
      aria-label={`v${currentMajorMinor}, see what's new`}
      className={
        hosted
          ? 'di-version-label ml-auto text-lg cursor-pointer select-none pr-2 transition-opacity duration-200 pointer-events-auto'
          : 'di-version-label absolute bottom-8 right-28 z-20 text-lg cursor-pointer select-none pr-2 transition-opacity duration-200'
      }
      style={{ opacity: labelHovered ? 0.8 : 0.4 }}
      inert={hosted && suspended}
    >
      v{currentMajorMinor}
    </button>
  );

  return (
    <>
      {/* Update notice — shown once per version until dismissed or auto-closed. The card's face is ONE real button (it opens the changelog, as the whole card always did) and the
          dismiss is its SIBLING, not nested in it (a button inside a button is invalid and breaks keyboard and screen reader use); they sit side by side where the title and the x
          always were, so nothing overlaps and each is its own target. */}
      {showUpdateNotice && (
        <div
          className="di-version-notice absolute bottom-[76px] right-28 z-20 pixel-panel flex items-start max-w-[min(288px,calc(100%-48px))]"
          style={{
            opacity: fading ? 0 : 1,
            transition: `opacity ${WHATS_NEW_FADE_MS / 1000}s ease-out`,
          }}
        >
          <button
            type="button"
            onClick={handleOpenChangelog}
            className="flex flex-col gap-8 pl-10 pt-8 pb-9 pr-10 text-left cursor-pointer"
          >
            <span className="text-lg text-accent-bright leading-none">
              Updated to v{currentMajorMinor}!
            </span>
            <span className="text-sm whitespace-nowrap">See what's new</span>
          </button>
          <Button
            variant="ghost"
            size="icon"
            onClick={handleDismiss}
            className="leading-none mt-[13px] mr-10"
            aria-label="Dismiss the update notice"
            title="Dismiss the update notice"
            data-testid="version-notice-dismiss"
          >
            x
          </Button>
        </div>
      )}
      {labelHost === null ? (
        <>
          {tooltip(false)}
          {label(false)}
        </>
      ) : (
        createPortal(
          <>
            {tooltip(true)}
            {label(true)}
          </>,
          labelHost,
        )
      )}
    </>
  );
}
