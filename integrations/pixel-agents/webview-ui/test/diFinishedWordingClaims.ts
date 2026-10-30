/**
 * The claim families a refusal sentence in the finished-Mission review must not make, shared by the tests that read those sentences (so the set cannot drift between them).
 *
 * Five axes on which a sentence can claim more than its data, each found by a rejected wording in this one small surface. Enumerate them BEFORE a sentence ships:
 *   read history  whether, or what, was read (a refusal arises both before and after the read, and the answer carries no phase);
 *   absence       what EXISTS (a tie that cannot be established is not a tie that does not exist);
 *   attribution   who acted (a timestamp records that an action happened, not who did it);
 *   completeness  how much of the whole the data covers (a list cut off at a limit is a floor, not a total);
 *   referent      what the sentence is ABOUT. A sentence shown in a composite view makes claims about everything visible around it, not only about the thing that produced it:
 *                 a notice printed beside a retained overview is read with that overview present.
 *
 * Only the referent set lives here: the read-history and absence sets sit beside the test that first needed them.
 */

/**
 * Claims whose REFERENT is the review as a whole or the view: they describe the SCREEN or the REVIEW, not the request that was refused. In a composite view (a successful overview retained beside a later refusal) they
 * are false while any retained content is drawn. The set can fail: diFinishedRefusalComposite.test.ts shows it flagging the sentence rejected in round 30.
 */
export const OVERBROAD_REFERENT: RegExp[] = [
  /\bnothing\b[^.]*\bis shown\b/i, // "... and nothing from one is shown": a claim about what the view displays
  /\bnothing\b[^.]*\b(is|are) (displayed|drawn|listed)\b/i,
  /\bthis review\b/i, // "This review could not establish ...": the whole review, whose overview just succeeded
  /\b(the|this) (view|screen|page|pane)\b[^.]*\b(shows?|has|contains?|lists?)\b[^.]*\bnothing\b/i,
];
