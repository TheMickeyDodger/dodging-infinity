/**
 * The ambient JOB catalogue. A job is a label and a routine flavour for a decorative resident:
 * it names what the resident appears to be doing, nothing more. No job performs, dispatches or
 * stands for any real service, Mission, commerce or emergency response.
 */

export type AmbientJobId =
  | 'bellhop'
  | 'police'
  | 'firefighter'
  | 'store-clerk'
  | 'barista'
  | 'courier'
  | 'street-sweeper'
  | 'gardener'
  | 'farmhand'
  | 'dock-worker'
  | 'angler'
  | 'aquarist'
  | 'ranger'
  | 'jogger'
  | 'tourist'
  | 'receptionist'
  | 'custodian'
  | 'office-visitor'
  | 'verger'
  | 'cathedral-visitor'
  | 'shopper'
  // the five show-inspired Easter-egg families (original fan homages; no affiliation)
  | 'egg-pocket-creature'
  | 'egg-shinobi'
  | 'egg-martial-artist'
  | 'egg-cul-de-sac-kid'
  | 'egg-trickster-hare';

export interface AmbientJob {
  id: AmbientJobId;
  /** Display name (always shown with the "Ambient" qualifier). */
  label: string;
  /** What the resident is shown doing at a stop. Decorative wording only. */
  activity: string;
  /** Character palette (0-5) the job prefers; residents also get a seeded hue shift. */
  palette: number;
  /** Routine flavour: posted jobs return to one post often; roaming jobs cover more ground. */
  style: 'posted' | 'roaming';
}

export const AMBIENT_JOBS: Readonly<Record<AmbientJobId, AmbientJob>> = {
  bellhop: { id: 'bellhop', label: 'Bellhop', activity: 'greeting guests', palette: 3, style: 'posted' },
  police: { id: 'police', label: 'Police officer', activity: 'walking a beat', palette: 1, style: 'roaming' },
  firefighter: { id: 'firefighter', label: 'Firefighter', activity: 'on a station round', palette: 4, style: 'posted' },
  'store-clerk': { id: 'store-clerk', label: 'Store clerk', activity: 'stocking shelves', palette: 2, style: 'posted' },
  barista: { id: 'barista', label: 'Barista', activity: 'pulling coffee', palette: 5, style: 'posted' },
  courier: { id: 'courier', label: 'Courier', activity: 'making rounds', palette: 0, style: 'roaming' },
  'street-sweeper': { id: 'street-sweeper', label: 'Street sweeper', activity: 'sweeping', palette: 2, style: 'roaming' },
  gardener: { id: 'gardener', label: 'Gardener', activity: 'tending plants', palette: 4, style: 'posted' },
  farmhand: { id: 'farmhand', label: 'Farmhand', activity: 'working the fields', palette: 0, style: 'posted' },
  'dock-worker': { id: 'dock-worker', label: 'Dock worker', activity: 'moving crates', palette: 1, style: 'posted' },
  angler: { id: 'angler', label: 'Angler', activity: 'fishing', palette: 3, style: 'posted' },
  aquarist: { id: 'aquarist', label: 'Aquarist', activity: 'checking the tanks', palette: 5, style: 'posted' },
  ranger: { id: 'ranger', label: 'Ranger', activity: 'walking the trails', palette: 2, style: 'roaming' },
  jogger: { id: 'jogger', label: 'Jogger', activity: 'out for a run', palette: 0, style: 'roaming' },
  tourist: { id: 'tourist', label: 'Tourist', activity: 'sightseeing', palette: 5, style: 'roaming' },
  receptionist: { id: 'receptionist', label: 'Receptionist', activity: 'at the front desk', palette: 3, style: 'posted' },
  custodian: { id: 'custodian', label: 'Custodian', activity: 'tidying up', palette: 2, style: 'roaming' },
  'office-visitor': { id: 'office-visitor', label: 'Visitor', activity: 'looking around', palette: 4, style: 'roaming' },
  verger: { id: 'verger', label: 'Verger', activity: 'tending the chapels', palette: 1, style: 'posted' },
  'cathedral-visitor': { id: 'cathedral-visitor', label: 'Cathedral visitor', activity: 'touring the nave', palette: 5, style: 'roaming' },
  shopper: { id: 'shopper', label: 'Shopper', activity: 'browsing the stores', palette: 0, style: 'roaming' },
  'egg-pocket-creature': { id: 'egg-pocket-creature', label: 'Pocket-creature homage', activity: 'rustling in the tall grass', palette: 2, style: 'posted' },
  'egg-shinobi': { id: 'egg-shinobi', label: 'Shinobi homage', activity: 'training among the pines', palette: 4, style: 'posted' },
  'egg-martial-artist': { id: 'egg-martial-artist', label: 'Martial-artist homage', activity: 'training at the lookout', palette: 1, style: 'posted' },
  'egg-cul-de-sac-kid': { id: 'egg-cul-de-sac-kid', label: 'Cul-de-sac-kid homage', activity: 'hatching a scheme', palette: 0, style: 'roaming' },
  'egg-trickster-hare': { id: 'egg-trickster-hare', label: 'Trickster-hare homage', activity: 'raiding the carrot rows', palette: 5, style: 'posted' },
};

/**
 * EASTER EGGS (M3 S6): one resident per family, homed where it belongs — the first place whose label
 * matches, in preference order. A family with no matching place is NOT placed (reported by the world
 * build), never forced somewhere incoherent. They are ordinary decorative residents otherwise.
 */
export const AMBIENT_EGGS: ReadonlyArray<{ job: AmbientJobId; homes: readonly RegExp[] }> = [
  { job: 'egg-pocket-creature', homes: [/wildwood/i, /wood|forest|wild/i, /botanical/i] },
  { job: 'egg-shinobi', homes: [/pine|highland/i, /wood|forest/i] },
  { job: 'egg-martial-artist', homes: [/observatory$/i, /observatory/i, /highland/i] },
  { job: 'egg-cul-de-sac-kid', homes: [/^market boulevard$/i, /market/i, /metropolis/i] },
  { job: 'egg-trickster-hare', homes: [/farm|field|valley/i, /botanical/i] },
];

/** Jobs the human explicitly asked for: every roster includes each at least once (when it has
 *  any district at all). */
export const REQUIRED_AMBIENT_JOBS: readonly AmbientJobId[] = [
  'bellhop',
  'police',
  'firefighter',
  'store-clerk',
  'barista',
];

/** District flavour by label keyword (district labels are layout DATA, so match loosely). */
const DISTRICT_JOBS: ReadonlyArray<{ match: RegExp; jobs: readonly AmbientJobId[] }> = [
  // The original palace and deck (C1): lobby, corridor and lounge atmosphere — never seated work.
  { match: /^palace$/i, jobs: ['receptionist', 'custodian', 'office-visitor', 'courier', 'office-visitor'] },
  { match: /^deck$/i, jobs: ['office-visitor', 'tourist'] },
  // the M3 S2 quarters (matched before the looser keywords below)
  { match: /atrium/i, jobs: ['receptionist', 'gardener', 'office-visitor', 'custodian', 'courier', 'office-visitor'] },
  { match: /observatory/i, jobs: ['receptionist', 'office-visitor', 'custodian', 'tourist', 'courier', 'office-visitor'] },
  { match: /cathedral/i, jobs: ['cathedral-visitor', 'verger', 'cathedral-visitor', 'tourist', 'cathedral-visitor'] },
  { match: /market|target|walmart/i, jobs: ['shopper', 'store-clerk', 'barista', 'shopper', 'courier', 'police'] },
  { match: /boulevard/i, jobs: ['courier', 'jogger', 'tourist', 'street-sweeper', 'police', 'shopper'] },
  {
    match: /metropolis|city|downtown/i,
    jobs: ['bellhop', 'police', 'firefighter', 'store-clerk', 'barista', 'courier', 'street-sweeper', 'tourist'],
  },
  { match: /harbou?r|port|dock/i, jobs: ['dock-worker', 'angler', 'police', 'courier', 'tourist'] },
  { match: /aquarium/i, jobs: ['aquarist', 'tourist', 'barista', 'store-clerk'] },
  { match: /farm|valley|field/i, jobs: ['farmhand', 'gardener', 'courier'] },
  { match: /lake|shore|coast|beach/i, jobs: ['angler', 'jogger', 'tourist', 'gardener'] },
  { match: /wood|forest|pine|highland|wild/i, jobs: ['ranger', 'jogger', 'gardener'] },
];
const DEFAULT_JOBS: readonly AmbientJobId[] = ['courier', 'jogger', 'tourist', 'gardener'];

/** The words every resident label carries: it is Ambient, it is decorative. */
export function ambientLabel(job: AmbientJobId): string {
  const j = AMBIENT_JOBS[job];
  return `Ambient · ${j.label} · ${j.activity} · decorative`;
}

export function jobsForDistrict(label: string): readonly AmbientJobId[] {
  return DISTRICT_JOBS.find((d) => d.match.test(label))?.jobs ?? DEFAULT_JOBS;
}
