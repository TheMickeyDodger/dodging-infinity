import { useRef, useState } from 'react';

import { Button } from './ui/Button.js';
import { Modal } from './ui/Modal.js';

/** What an import did: applied (with a summary) or refused (with the reason; nothing changed). */
export type PasteImportResult = { ok: true; message: string } | { ok: false; reason: string };

interface PasteImportModalProps {
  /** Which existing import this serves; null = closed. */
  kind: 'layout' | 'ambient' | null;
  /** The byte bound enforced before parsing (shown to the user). */
  maxBytes: number;
  onImport: (kind: 'layout' | 'ambient', text: string) => PasteImportResult;
  onClose: () => void;
}

const TITLES = { layout: 'Import Layout (paste JSON)', ambient: 'Import Ambient Snapshot (paste JSON)' } as const;

/**
 * DI office (browser): ONE small surface for the existing imports, by PASTED JSON. Pasted text goes
 * through exactly the route a file's text would (bounded before parse, the same validator, the same
 * atomic apply and save). Success and refusal are both shown; a refusal changes nothing.
 */
export function PasteImportModal({ kind, maxBytes, onImport, onClose }: PasteImportModalProps) {
  const textRef = useRef<HTMLTextAreaElement>(null);
  const [result, setResult] = useState<PasteImportResult | null>(null);
  if (kind === null) return null;
  const close = () => {
    setResult(null);
    onClose();
  };
  return (
    <Modal isOpen onClose={close} title={TITLES[kind]}>
      <div className="flex flex-col gap-4 py-4 px-10" style={{ width: 'min(560px, 80vw)' }} data-testid="paste-import">
        <div className="text-xs text-text-muted">
          Paste the whole JSON file here, then Import. Max {maxBytes.toLocaleString('en-US')} bytes (UTF-8), checked
          before parsing. A file that fails the checks is refused whole and nothing changes.
        </div>
        <textarea
          ref={textRef}
          data-testid="paste-import-text"
          aria-label={TITLES[kind]}
          rows={10}
          spellCheck={false}
          className="w-full font-mono text-xs bg-bg-dark text-text border-2 border-border rounded-none p-4"
          onChange={() => setResult(null)}
        />
        <div className="flex gap-4 items-center">
          <Button data-testid="paste-import-go" onClick={() => setResult(onImport(kind, textRef.current?.value ?? ''))}>
            Import
          </Button>
          <Button variant="ghost" onClick={close}>
            Close
          </Button>
        </div>
        {result && (
          <div role="status" data-testid="paste-import-result" data-ok={result.ok ? 'true' : 'false'} className="text-xs" style={{ overflowWrap: 'anywhere' }}>
            {result.ok ? `Imported: ${result.message}` : `Not imported: ${result.reason}. Nothing was changed.`}
          </div>
        )}
      </div>
    </Modal>
  );
}
