/**
 * AC3: the minimized-surfaces tray, provided by App (see DiMinimizedTray.tsx). Null where no tray is mounted.
 */
import { createContext } from 'react';

export const DiMinimizedTrayContext = createContext<HTMLElement | null>(null);
