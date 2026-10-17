/**
 * The Map's keyboard isolation. Registered in the CAPTURE phase on the window — the first
 * listener any key event reaches — it stops EVERY keydown from propagating while the Map is open,
 * so no other handler (the layout editor's Delete/Backspace/R/T/Esc/Ctrl+Z/Y, arrow-key pan, the
 * intro) can act on what sits behind the overlay. Escape closes the Map and is consumed too.
 * Returns the disposer.
 */
export function installMapKeyGuard(target: EventTarget, onClose: () => void): () => void {
  const guard = (event: Event) => {
    event.stopImmediatePropagation();
    if ((event as Event & { key?: string }).key === 'Escape') {
      event.preventDefault();
      onClose();
    }
  };
  target.addEventListener('keydown', guard, { capture: true });
  return () => target.removeEventListener('keydown', guard, { capture: true });
}
