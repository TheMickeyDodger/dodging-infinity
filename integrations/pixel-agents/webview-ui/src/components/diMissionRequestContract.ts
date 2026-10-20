/**
 * Item 4, round 27 F1 — the proof rules a person approves.
 *
 * A proposal's `proof_contract` is the whole consequential contract of a Mission: what counts as done.
 * It sits INSIDE the proposal, so its digest covers it, and a revision that changes ONLY these rules is a
 * different proposal with a different digest. If the window dropped them, a person would approve rules they
 * were never shown, and those rules could change with nothing on the screen changing. So this module does
 * exactly one of two things with a proposal, never a third:
 *
 *  - it READS the whole contract under a CLOSED shape (every key the canonical contract has, nothing it does
 *    not) and says every rule in plain sentences, so the rules are displayed inside the offer that binds
 *    them; or
 *  - it reports the proposal as NOT REPRESENTED (an unknown key anywhere, a value of the wrong kind, a list
 *    over the canonical bound), and approval of it from this window is refused. Showing less than the
 *    contract and accepting anyway is the one outcome that must not exist.
 *
 * Everything here is pure. Text that came from a proposal is returned as data; the component draws it as
 * inert text.
 */

type Json = Record<string, unknown>;

const isObject = (v: unknown): v is Json =>
  typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);
const int = (v: unknown): number | null =>
  typeof v === 'number' && Number.isInteger(v) && v >= 0 ? v : null;

/** The proposal's own keys, exactly the canonical six plus the one optional contract. */
export const PROPOSAL_KEYS = [
  'objective',
  'target_context',
  'repository_url',
  'requested_scope',
  'requested_action_scope',
  'requested_delivery_target',
] as const;
export const PROPOSAL_OPTIONAL_KEYS = ['proof_contract'] as const;

const CONTRACT_KEYS = [
  'requirements',
  'required_artifacts',
  'required_dependencies',
  'required_resource_readiness',
  'degradation_policy',
  'continuation_budget',
] as const;
const REQUIREMENT_KEYS = [
  'key',
  'description',
  'evidence_kinds',
  'required_artifact_keys',
  'max_evidence_age_seconds',
] as const;
const ARTIFACT_KEYS = ['key', 'role', 'expected_content_digest_sha256'] as const;
const DEPENDENCY_KEYS = ['key', 'kind', 'target'] as const;
const TARGET_KEYS: Record<string, readonly string[]> = {
  exact_mission: ['form', 'mission_id', 'revision', 'proposal_digest_sha256'],
  exact_resource: ['form', 'resource_key'],
  eligibility: ['form', 'condition', 'proposal_digest_sha256'],
};
const READINESS_KEYS = ['resource_key', 'max_age_seconds'] as const;
const DEGRADATION_KEYS = ['permitted_blocker_keys'] as const;
const BUDGET_KEYS = ['max_attempts', 'max_checkpoints'] as const;

/** The canonical list bounds (Mission Core's own). A longer list is not the canonical contract. */
const MAX_REQUIREMENTS = 32;
const MAX_ARTIFACTS = 64;
const MAX_DEPENDENCIES = 32;
const MAX_READINESS = 16;
const MAX_BLOCKERS = 32;
const MAX_KEYS_PER_ITEM = 64;

export interface MrRequirement {
  key: string;
  description: string;
  evidenceKinds: string[];
  artifactKeys: string[];
  maxAgeSeconds: number;
}
export interface MrArtifactRule {
  key: string;
  role: string;
  digest: string;
}
export type MrTarget =
  | { form: 'exact_mission'; missionId: string; revision: number; digest: string }
  | { form: 'exact_resource'; resourceKey: string }
  | { form: 'eligibility'; condition: string; digest: string };
export interface MrDependency {
  key: string;
  kind: string;
  target: MrTarget;
}
export interface MrReadiness {
  resourceKey: string;
  maxAgeSeconds: number;
}
export interface MrContract {
  requirements: MrRequirement[];
  artifacts: MrArtifactRule[];
  dependencies: MrDependency[];
  readiness: MrReadiness[];
  permittedBlockers: string[];
  maxAttempts: number;
  maxCheckpoints: number;
}

/** What this window knows of a proposal's proof rules. */
export interface MrProofRules {
  /** Why part of the proposal cannot be shown, or null when everything bound is shown. */
  unrepresented: string | null;
  /** The contract, when there is one and it is fully read; null when none is recorded. */
  contract: MrContract | null;
}

class NotRepresented extends Error {}

const refuse = (where: string, why: string): never => {
  throw new NotRepresented(`${where} ${why}`);
};

function closed(value: unknown, keys: readonly string[], where: string): Json {
  if (!isObject(value)) return refuse(where, 'is not an object');
  for (const key of Object.keys(value)) {
    if (!keys.includes(key)) return refuse(where, `has a field this window does not know (${key})`);
  }
  for (const key of keys) {
    if (!(key in value)) return refuse(where, `is missing ${key}`);
  }
  return value;
}

function text(value: unknown, where: string): string {
  const s = str(value);
  return s === null ? refuse(where, 'is not text') : s;
}

function count(value: unknown, where: string): number {
  const n = int(value);
  return n === null ? refuse(where, 'is not a whole number') : n;
}

function list(value: unknown, where: string, max: number): unknown[] {
  if (!Array.isArray(value)) return refuse(where, 'is not a list');
  if (value.length > max) return refuse(where, `has more than the ${max} items a contract may hold`);
  return value;
}

function textList(value: unknown, where: string, max: number): string[] {
  return list(value, where, max).map((v, i) => text(v, `${where}[${i}]`));
}

function parseTarget(value: unknown, where: string): MrTarget {
  if (!isObject(value)) return refuse(where, 'is not an object');
  const form = str(value.form);
  if (form === null || !(form in TARGET_KEYS)) return refuse(where, 'names a kind of target this window does not know');
  const t = closed(value, TARGET_KEYS[form], where);
  if (form === 'exact_mission') {
    return {
      form,
      missionId: text(t.mission_id, `${where}.mission_id`),
      revision: count(t.revision, `${where}.revision`),
      digest: text(t.proposal_digest_sha256, `${where}.proposal_digest_sha256`),
    };
  }
  if (form === 'exact_resource') return { form, resourceKey: text(t.resource_key, `${where}.resource_key`) };
  return {
    form: 'eligibility',
    condition: text(t.condition, `${where}.condition`),
    digest: text(t.proposal_digest_sha256, `${where}.proposal_digest_sha256`),
  };
}

function parseContract(value: unknown): MrContract {
  const c = closed(value, CONTRACT_KEYS, 'the proof contract');
  const requirements = list(c.requirements, 'the proof requirements', MAX_REQUIREMENTS).map((r, i) => {
    const w = `proof requirement ${i + 1}`;
    const o = closed(r, REQUIREMENT_KEYS, w);
    return {
      key: text(o.key, `${w} key`),
      description: text(o.description, `${w} description`),
      evidenceKinds: textList(o.evidence_kinds, `${w} evidence kinds`, MAX_KEYS_PER_ITEM),
      artifactKeys: textList(o.required_artifact_keys, `${w} artifact keys`, MAX_KEYS_PER_ITEM),
      maxAgeSeconds: count(o.max_evidence_age_seconds, `${w} evidence age`),
    };
  });
  const artifacts = list(c.required_artifacts, 'the required artifacts', MAX_ARTIFACTS).map((a, i) => {
    const w = `required artifact ${i + 1}`;
    const o = closed(a, ARTIFACT_KEYS, w);
    return {
      key: text(o.key, `${w} key`),
      role: text(o.role, `${w} role`),
      digest: text(o.expected_content_digest_sha256, `${w} digest`),
    };
  });
  const dependencies = list(c.required_dependencies, 'the required prerequisites', MAX_DEPENDENCIES).map(
    (d, i) => {
      const w = `prerequisite ${i + 1}`;
      const o = closed(d, DEPENDENCY_KEYS, w);
      return {
        key: text(o.key, `${w} key`),
        kind: text(o.kind, `${w} kind`),
        target: parseTarget(o.target, `${w} target`),
      };
    },
  );
  const readiness = list(c.required_resource_readiness, 'the required resource readiness', MAX_READINESS).map(
    (r, i) => {
      const w = `resource readiness ${i + 1}`;
      const o = closed(r, READINESS_KEYS, w);
      return {
        resourceKey: text(o.resource_key, `${w} resource`),
        maxAgeSeconds: count(o.max_age_seconds, `${w} age`),
      };
    },
  );
  const degradation = closed(c.degradation_policy, DEGRADATION_KEYS, 'the degradation policy');
  const budget = closed(c.continuation_budget, BUDGET_KEYS, 'the continuation budget');
  return {
    requirements,
    artifacts,
    dependencies,
    readiness,
    permittedBlockers: textList(degradation.permitted_blocker_keys, 'the permitted blockers', MAX_BLOCKERS),
    maxAttempts: count(budget.max_attempts, 'the continuation attempts'),
    maxCheckpoints: count(budget.max_checkpoints, 'the continuation checkpoints'),
  };
}

/**
 * Read a proposal's proof rules under the closed shape. `proposal` is the whole proposal object as the
 * panel sent it: an unknown proposal key is as much a rule this window cannot show as an unknown contract
 * key. Never throws.
 */
export function readProofRules(proposal: unknown): MrProofRules {
  try {
    if (!isObject(proposal)) return { unrepresented: 'the proposal is not an object', contract: null };
    for (const key of Object.keys(proposal)) {
      if (!(PROPOSAL_KEYS as readonly string[]).includes(key) && !(PROPOSAL_OPTIONAL_KEYS as readonly string[]).includes(key)) {
        return { unrepresented: `the proposal has a field this window does not know (${key})`, contract: null };
      }
    }
    const raw = proposal.proof_contract;
    if (raw === undefined || raw === null) return { unrepresented: null, contract: null };
    return { unrepresented: null, contract: parseContract(raw) };
  } catch (error) {
    if (error instanceof NotRepresented) return { unrepresented: error.message, contract: null };
    throw error;
  }
}

/** "1 hour (3600 s)": the unit a person reads and the exact number the contract carries. */
export function durationWords(seconds: number): string {
  const exact = `${seconds} s`;
  const unit = (n: number, one: string): string => `${n} ${one}${n === 1 ? '' : 's'}`;
  if (seconds > 0 && seconds % 86400 === 0) return `${unit(seconds / 86400, 'day')} (${exact})`;
  if (seconds > 0 && seconds % 3600 === 0) return `${unit(seconds / 3600, 'hour')} (${exact})`;
  if (seconds > 0 && seconds % 60 === 0) return `${unit(seconds / 60, 'minute')} (${exact})`;
  return unit(seconds, 'second');
}

export interface MrRuleSection {
  title: string;
  lines: string[];
}

const quoted = (s: string): string => `“${s}”`;

function targetWords(target: MrTarget): string {
  switch (target.form) {
    case 'exact_mission':
      return `Mission ${target.missionId} at revision ${target.revision} with proposal digest ${target.digest}`;
    case 'exact_resource':
      return `the resource ${quoted(target.resourceKey)}`;
    case 'eligibility':
      return `a Mission meeting the condition ${quoted(target.condition)} with proposal digest ${target.digest}`;
  }
}

/**
 * Every rule of the contract as plain sentences, in sections. Nothing is summarized away: every
 * requirement, artifact, prerequisite, readiness key, permitted blocker and budget appears, with the
 * exact numbers and digests, so two contracts that differ in any rule read differently.
 */
export function proofRuleSections(contract: MrContract): MrRuleSection[] {
  const sections: MrRuleSection[] = [];
  sections.push({
    title: 'Proof the team must provide',
    lines:
      contract.requirements.length === 0
        ? ['No proof requirement is recorded.']
        : contract.requirements.map((r) => {
            const kinds =
              r.evidenceKinds.length === 0
                ? 'evidence of any recorded kind'
                : `evidence of kind ${r.evidenceKinds.join(', ')}`;
            const artifacts =
              r.artifactKeys.length === 0
                ? 'needing no artifact'
                : `needing the artifact${r.artifactKeys.length === 1 ? '' : 's'} ${r.artifactKeys.map(quoted).join(', ')}`;
            return `${quoted(r.key)}: ${r.description} Satisfied by ${kinds}, ${artifacts}; evidence older than ${durationWords(r.maxAgeSeconds)} does not count.`;
          }),
  });
  sections.push({
    title: 'Artifacts that must match',
    lines:
      contract.artifacts.length === 0
        ? ['No artifact is required.']
        : contract.artifacts.map(
            (a) => `${quoted(a.key)} (role ${a.role}) must have the content digest ${a.digest}.`,
          ),
  });
  sections.push({
    title: 'Prerequisites that must be met first',
    lines:
      contract.dependencies.length === 0
        ? ['No prerequisite is required.']
        : contract.dependencies.map(
            (d) => `${quoted(d.key)} (${d.kind}): requires ${targetWords(d.target)}.`,
          ),
  });
  sections.push({
    title: 'Resources that must be ready',
    lines:
      contract.readiness.length === 0
        ? ['No resource readiness is required.']
        : contract.readiness.map(
            (r) =>
              `The resource ${quoted(r.resourceKey)} must have been confirmed ready within the last ${durationWords(r.maxAgeSeconds)}.`,
          ),
  });
  sections.push({
    title: 'Blockers that may remain',
    lines:
      contract.permittedBlockers.length === 0
        ? ['None. Any open blocker prevents completion.']
        : [
            `These blockers may remain without preventing completion: ${contract.permittedBlockers.map(quoted).join(', ')}.`,
          ],
  });
  sections.push({
    title: 'How long the team may keep trying',
    lines: [
      `At most ${contract.maxAttempts} attempt${contract.maxAttempts === 1 ? '' : 's'} and ${contract.maxCheckpoints} checkpoint${contract.maxCheckpoints === 1 ? '' : 's'}.`,
    ],
  });
  return sections;
}

/** The words shown where the rules cannot be: no contract recorded, or not fully representable. */
export function proofRulesAbsentWords(rules: MrProofRules): string {
  if (rules.unrepresented !== null) {
    return `This window cannot show everything this proposal binds: ${rules.unrepresented}. Approving it from here is refused, so nothing is approved on rules you were not shown.`;
  }
  return 'This proposal carries no proof rules: it states no requirement, artifact, prerequisite or limit beyond its objective, and approving it approves exactly that.';
}
