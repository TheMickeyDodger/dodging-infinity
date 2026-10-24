/** The window's inner width in CSS px, kept current on resize (and on zoom, which changes the CSS width of the window). */
import { useEffect, useState } from 'react';

export function useViewportWidth(): number {
  const [width, setWidth] = useState(() => window.innerWidth);
  useEffect(() => {
    const onResize = (): void => setWidth(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return width;
}
