/**
 * THE ONE PROCESS-STARTING MODULE OF THE DI OFFICE ENTRY: the real Node APIs the Herdr launch route (herdrLaunch.ts) is built from.
 *
 * The office otherwise starts no process at all (diClientMessages.test.ts pins that no other module in the entry's closure imports a process-starting module, and that THIS file is the one exception,
 * named there by its path). It exists so the route itself imports nothing: herdrLaunch.ts is given these APIs by the composition, and every one of its tests injects stand-ins instead.
 *
 * What it can start, through the route and only through the route: Herdr's own commands (`agent list`, `workspace focus`, `workspace get`) with no shell, bounded in time and size; and the macOS
 * opener (`/usr/bin/open`), detached with its output ignored. It sends nothing to any terminal. It exports no function that takes a command from a caller: the file and arguments are chosen by
 * herdrLaunch.ts after its admission checks, and this module only carries them out.
 */
import { execFile, spawn } from 'child_process';
import { randomBytes } from 'crypto';
import { access, writeFile } from 'fs/promises';

import type { NodeLaunchApis } from './herdrLaunch.js';

/** The real APIs, over this process's own environment. */
export function realLaunchApis(env: Readonly<Record<string, string | undefined>> = process.env): NodeLaunchApis {
  return {
    execFile: (file, args, options, callback) => execFile(file, [...args], options, callback),
    spawn: (file, args, options) => spawn(file, [...args], options),
    writeFile: (filePath, text, options) => writeFile(filePath, text, options),
    randomBytes: (size) => randomBytes(size),
    env,
    pathExists: (path) =>
      access(path).then(
        () => true,
        () => false,
      ),
  };
}
