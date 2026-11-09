import type { ReactNode } from 'react';
import { useId, useLayoutEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

import { useDiModal } from '../diModal.js';
import { Button } from './Button.js';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  /** z-index for backdrop (modal gets +1). Default 49 */
  zIndex?: number;
  className?: string;
}

/**
 * The shared modal (Settings, the changelog, the hooks notice, paste import). D1b: it OWNS its keys through the same mechanics every office dialog uses
 * (diModal.ts / diModalStack.ts): it is drawn outside #root, #root is inert while it is in front, Tab stays in it, Escape closes it, no key reaches the
 * surface beneath it, and on close focus goes back to the control that opened it (never the page body). Its look is unchanged.
 */
export function Modal({ isOpen, ...rest }: ModalProps) {
  if (!isOpen) return null;
  return <OpenModal {...rest} />;
}

function OpenModal({
  onClose,
  title,
  children,
  zIndex = 50,
  className = '',
}: Omit<ModalProps, 'isOpen'>) {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  // the control that opened it: read before the dialog takes focus (this layout effect runs before useDiModal's, which reads it once, on mount)
  const opener = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  }, []);
  const onKeyDown = useDiModal(dialogRef, onClose, opener);
  const titleId = useId();
  const closeWordId = useId();

  return createPortal(
    <>
      <div className="fixed inset-0 bg-black/50" style={{ zIndex }} onClick={onClose} />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={onKeyDown}
        onKeyUp={(e) => e.stopPropagation()}
        className={`fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-bg border-2 border-border rounded-none shadow-pixel p-4 min-w-xs outline-none ${className}`}
        style={{ zIndex: zIndex + 1 }}
      >
        <div className="flex items-center justify-between py-4 px-10 border-b border-border mb-4">
          <span id={titleId} className="text-accent-bright text-2xl">
            {title}
          </span>
          {/* pre-review 5: named for what it does and for WHICH dialog it closes ("Close Settings"), from the word and this dialog's own title; the
              visible x is unchanged and is not part of the name */}
          <Button variant="ghost" size="icon" onClick={onClose} aria-labelledby={`${closeWordId} ${titleId}`}>
            <span id={closeWordId} className="sr-only">
              Close
            </span>
            <span aria-hidden="true">x</span>
          </Button>
        </div>
        {children}
      </div>
    </>,
    document.body,
  );
}
