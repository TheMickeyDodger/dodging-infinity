/**
 * C1 on the request panel's overview (webview side): the question fields a conversation may carry.
 * The fields are ADDITIVE — a panel that predates them sends none, which reads "not reported", never
 * "no questions" — and bounded exactly as the panel's journal bounds them. A value that breaks a
 * bound is refused as unreadable with nothing awaited; it is never truncated or repaired.
 */
import { describe, expect, it } from 'vitest';

import {
  MR_MAX_QUESTION_CHARS,
  MR_MAX_QUESTIONS,
  parseOverview,
} from '../src/components/diMissionRequestModel.ts';

const CV = `cv-${'a'.repeat(32)}`;
const CT = `ct-${'b'.repeat(32)}`;

function overviewOf(conversation: Record<string, unknown>) {
  return parseOverview({
    server_now: 1000,
    requests_truncated: false,
    capabilities: {
      run_available: false,
      delivery_authority: 'none',
      decision: { transport: 't', proof: 'p', reply: 'approve', approval_valid_seconds: 900 },
      scope_meanings: {},
    },
    requests: [],
    conversations: [
      { conversation_id: CV, turns: 2, last_activity: 9, first_words: 'make it stable', ...conversation },
    ],
  })!.conversations[0];
}

const awaiting = (over: Record<string, unknown> = {}) => ({
  awaiting_answer: true,
  questions_reporting: 'read',
  questions_problem: null,
  awaiting_turn_id: CT,
  awaiting_questions: ['Which branch?', 'Which tests?'],
  latest_turn_state: 'COMPLETED',
  ...over,
});

describe('the bounds are the panel journal’s own, pinned by exact value', () => {
  it('names three questions of at most 280 characters', () => {
    expect(MR_MAX_QUESTIONS).toBe(3);
    expect(MR_MAX_QUESTION_CHARS).toBe(280);
  });
});

describe('a conversation’s question fields', () => {
  it('a panel that predates the fields reads NOT REPORTED — never "no questions"', () => {
    const c = overviewOf({});
    expect(c).toMatchObject({
      awaitingAnswer: false,
      questionsReporting: 'not_reported',
      awaitingQuestions: [],
      awaitingTurnId: null,
    });
  });

  it('reads a conversation awaiting an answer', () => {
    expect(overviewOf(awaiting())).toMatchObject({
      awaitingAnswer: true,
      questionsReporting: 'read',
      awaitingTurnId: CT,
      awaitingQuestions: ['Which branch?', 'Which tests?'],
      latestTurnState: 'COMPLETED',
    });
  });

  it('reads “read; none declared” and “not reported by this panel version” as different things', () => {
    const none = overviewOf(
      awaiting({ awaiting_answer: false, awaiting_turn_id: null, awaiting_questions: [] }),
    );
    expect(none).toMatchObject({ questionsReporting: 'read', awaitingAnswer: false });
    const legacy = overviewOf(
      awaiting({
        awaiting_answer: false,
        awaiting_turn_id: null,
        awaiting_questions: [],
        questions_reporting: 'not_reported_by_this_panel_version',
      }),
    );
    expect(legacy).toMatchObject({ questionsReporting: 'not_reported', awaitingAnswer: false });
    expect(none.questionsReporting).not.toBe(legacy.questionsReporting);
    expect(
      overviewOf(
        awaiting({
          awaiting_answer: false,
          awaiting_turn_id: null,
          awaiting_questions: [],
          questions_reporting: 'not_applicable',
        }),
      ).questionsReporting,
    ).toBe('not_applicable');
  });

  it('keeps a recorded problem with a read reply and no awaited questions', () => {
    const c = overviewOf(
      awaiting({
        awaiting_answer: false,
        awaiting_turn_id: null,
        awaiting_questions: [],
        questions_problem: 'the questions block repeats a question',
      }),
    );
    expect(c).toMatchObject({
      questionsReporting: 'read',
      awaitingAnswer: false,
      questionsProblem: 'the questions block repeats a question',
    });
  });

  const unreadable: Array<[string, Record<string, unknown>]> = [
    ['four questions', awaiting({ awaiting_questions: ['a?', 'b?', 'c?', 'd?'] })],
    ['a question over 280 characters', awaiting({ awaiting_questions: ['x'.repeat(281)] })],
    ['an empty question', awaiting({ awaiting_questions: ['   '] })],
    ['a control character in a question', awaiting({ awaiting_questions: ['a\u0007?'] })],
    ['a newline in a question', awaiting({ awaiting_questions: ['a?\nb?'] })],
    ['a repeated question', awaiting({ awaiting_questions: ['a?', 'a?'] })],
    ['a non-string question', awaiting({ awaiting_questions: [7] })],
    ['questions that are not a list', awaiting({ awaiting_questions: 'a?' })],
    ['awaiting with no questions', awaiting({ awaiting_questions: [] })],
    ['awaiting with no turn', awaiting({ awaiting_turn_id: null })],
    ['awaiting with a malformed turn id', awaiting({ awaiting_turn_id: 'ct-1' })],
    ['awaiting from a reply that was not read', awaiting({ questions_reporting: 'not_applicable' })],
    ['questions while not awaiting', awaiting({ awaiting_answer: false })],
    ['an unknown reporting value', awaiting({ questions_reporting: 'maybe' })],
    ['a non-boolean awaiting flag', awaiting({ awaiting_answer: 'yes' })],
    [
      'a missing reporting field',
      Object.fromEntries(Object.entries(awaiting()).filter(([key]) => key !== 'questions_reporting')),
    ],
  ];
  for (const [label, wire] of unreadable) {
    it(`refuses ${label}: unreadable, nothing awaited, nothing repaired`, () => {
      const c = overviewOf(wire);
      expect(c.awaitingAnswer).toBe(false);
      expect(c.awaitingQuestions).toEqual([]);
      expect(c.awaitingTurnId).toBeNull();
      expect(['unreadable', 'not_reported']).toContain(c.questionsReporting);
    });
  }

  it('accepts exactly the bound: three questions of 280 characters', () => {
    const q = ['a'.repeat(280), 'b'.repeat(280), 'c'.repeat(280)];
    expect(overviewOf(awaiting({ awaiting_questions: q })).awaitingQuestions).toEqual(q);
  });
});
