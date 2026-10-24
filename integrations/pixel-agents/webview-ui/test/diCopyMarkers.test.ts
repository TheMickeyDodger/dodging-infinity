/**
 * AC7, boundary A: the content marker (`data-copy="content"`) names ONLY what this project does not author, so the
 * copy guard keeps reading everything else as ours. A marker that is wider than the content it names would hide our
 * own wording from the guard. Explanatory wording that arrives over the wire (here a scope's meaning, written by the
 * request panel) is our copy; the scope key beside it is a record value.
 */
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { ScopeLine } from '../src/components/DiMissionRequestParts.tsx';

const render = (scope: string, meanings: Record<string, string>) =>
  renderToStaticMarkup(createElement(ScopeLine, { scope, meanings }));

describe('content markers are as narrow as the content they name', () => {
  it('a scope with a meaning: the meaning is OURS (unmarked), only the record key is marked as content', () => {
    expect(render('repository_read', { repository_read: 'read the repository' })).toBe(
      'read the repository <span class="di-mr-key" data-copy="content">(repository_read)</span>',
    );
  });

  it('a meaning that carried a long dash would be READ by the guard, not hidden behind the marker', () => {
    const html = render('repository_read', { repository_read: 'read the repository — all of it' });
    const marked = [...html.matchAll(/<[^>]*data-copy="content"[^>]*>([^<]*)</g)].map((m) => m[1]);
    expect(marked).toEqual(['(repository_read)']);
    expect(html).toContain('—');
    expect(marked.join(' ')).not.toMatch(/[–—]/);
  });

  it('a scope with no meaning shows only the key, which is a record value', () => {
    expect(render('some_scope', {})).toBe('<span data-copy="content">some_scope</span>');
  });
});
