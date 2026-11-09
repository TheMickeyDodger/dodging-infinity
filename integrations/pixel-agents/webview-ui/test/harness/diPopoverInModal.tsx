/**
 * TEST-ONLY SURFACES (B2c, the Lead's ruling (A)): an anchored popover inside a modal dialog. No product surface does this; these pages prove the
 * MECHANISM only (diPopover.ts: a focus-returning close whose trigger cannot take focus returns focus to the popover's own modal), not that it is
 * wired anywhere a person can reach.
 *  - `?surface=modal` (the default): the app's own Modal, built on useDiModal, which ALSO retakes focus on its own (its focusout handler). In Chrome
 *    that retake already rescues this path (b2c-A-inmodal-BEFORE-chrome.log), so this surface is an INTEGRATION check and cannot isolate (A).
 *  - `?surface=bare`: a bare aria-modal dialog with a MODAL claim on the shared front stack (#root inert) and NO retake of its own: the surface that
 *    isolates (A).
 * The trigger is made unable to take focus (hidden) BEFORE the popover closes (`window.__makeTriggerUnavailable`); hiding it in the same commit
 * as the close would hide it only after the focus decision (the BEFORE run's item case).
 */
import '../../src/index.css';

import type { ReactNode } from 'react';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { createRoot } from 'react-dom/client';

import { claimFront, releaseFront } from '../../src/components/diModalStack.ts';
import { AnchoredPopover, PopoverItem } from '../../src/components/ui/AnchoredPopover.tsx';
import { Modal } from '../../src/components/ui/Modal.tsx';

declare global {
  interface Window {
    /** Make the popover's trigger unable to take focus (hidden), the way a re-render could, while the popover stays open. */
    __makeTriggerUnavailable?: () => void;
  }
}

/** A modal dialog with NO focus handling of its own: a modal claim (so #root is inert) and nothing else. */
export function BareDialog({ children }: { children: ReactNode }) {
  const owner = useRef<object>({});
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const me = owner.current;
    claimFront(me);
    ref.current?.focus();
    return () => {
      releaseFront(me);
    };
  }, []);
  return createPortal(
    <div ref={ref} role="dialog" aria-modal="true" aria-label="Bare test-only dialog" tabIndex={-1} data-testid="h-bare-dialog">
      {children}
    </div>,
    document.body,
  );
}

export function Harness() {
  const bare = new URLSearchParams(window.location.search).get('surface') === 'bare';
  const [open, setOpen] = useState(true);
  const [triggerGone, setTriggerGone] = useState(false);
  useEffect(() => {
    window.__makeTriggerUnavailable = () => setTriggerGone(true);
  }, []);
  const body = (
    <div data-testid="h-body" data-trigger-gone={triggerGone ? 'true' : undefined}>
      <p>A popover inside a modal (test only).</p>
      <AnchoredPopover label="Options" dialogLabel="Options" testid="h-pop">
        {(dismiss) => <PopoverItem label="Close from an item" testid="h-item" onSelect={() => dismiss('to-trigger')} />}
      </AnchoredPopover>
    </div>
  );
  return (
    <>
      <p>TEST-ONLY SURFACE: an anchored popover inside a modal dialog ({bare ? 'bare dialog, no retake' : "the app's Modal"}).</p>
      {bare ? (
        <BareDialog>{body}</BareDialog>
      ) : (
        <Modal isOpen={open} onClose={() => setOpen(false)} title="Test-only dialog">
          {body}
        </Modal>
      )}
    </>
  );
}

createRoot(document.getElementById('root')!).render(<Harness />);
