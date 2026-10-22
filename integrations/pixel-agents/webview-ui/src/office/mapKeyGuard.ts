/**
 * The Map's keyboard isolation. Registered in the CAPTURE phase on the window — the first
 * listener any key event reaches — it stops EVERY keydown from propagating while the Map is open,
 * so no other handler (the layout editor's Delete/Backspace/R/T/Esc/Ctrl+Z/Y, arrow-key pan, the
 * intro) can act on what sits behind the overlay. Escape closes the Map and is consumed too.
 * Returns the disposer.
 *
 * A modal dialog opened OVER the Map (the reception inbox from the sidebar, a Mission's details, the code
 * review) is the foreground surface and owns the keyboard: while `yieldTo()` says one is open, the guard
 * lets every key through untouched, so Escape and the arrows reach the dialog and Escape closes only it.
 * The Map's isolation resumes the moment the dialog closes.
 */
export function installMapKeyGuard(
  target: EventTarget,
  onClose: () => void,
  yieldTo: () => boolean = () => false,
): () => void {
  const guard = (event: Event) => {
    if (yieldTo()) return;
    event.stopImmediatePropagation();
    if ((event as Event & { key?: string }).key === 'Escape') {
      event.preventDefault();
      onClose();
    }
  };
  target.addEventListener('keydown', guard, { capture: true });
  return () => target.removeEventListener('keydown', guard, { capture: true });
}
