/**
 * AUTO-GENERATED FROM core/asyncapi.yaml. DO NOT EDIT MANUALLY.
 *
 * Run `npm run asyncapi:generate` to regenerate.
 *
 * Source of truth: the yaml at core/asyncapi.yaml.
 * Editors and clients in any language can consume the spec directly.
 */

export type ServerMessage =
  | ProviderCapabilities
  | AgentCreated
  | AgentClosed
  | AgentSelected
  | ExistingAgents
  | AgentStatus
  | AgentToolStart
  | AgentToolDone
  | AgentToolsClear
  | AgentToolPermission
  | AgentToolPermissionClear
  | SubagentToolStart
  | SubagentToolDone
  | SubagentClear
  | SubagentToolPermission
  | AgentTeamInfo
  | AgentContextUsage
  | LayoutLoaded
  | FurnitureAssetsLoaded
  | CharacterSpritesLoaded
  | PetSpritesLoaded
  | FloorTilesLoaded
  | WallTilesLoaded
  | CarpetTilesLoaded
  | SettingsLoaded
  | HooksStatus
  | HooksConsentRequest
  | ExternalAssetDirectoriesUpdated
  | AreaMappingsLoaded
  | WorkspaceFolders
  | AgentDiagnostics
  | DiOfficeState
  | AmbientStateLoaded
  | AmbientSpritesLoaded;

export type ClientMessage =
  | WebviewReady
  | LaunchAgent
  | FocusAgent
  | CloseAgent
  | SaveAgentSeats
  | SaveLayout
  | SetSoundEnabled
  | SetLastSeenVersion
  | SetAlwaysShowLabels
  | SetGhostHeadlessAgents
  | SetHooksEnabled
  | HooksConsentResponse
  | SetHooksInfoShown
  | SetWatchAllSessions
  | ExportLayout
  | ImportLayout
  | OpenSessionsFolder
  | AddExternalAssetDirectory
  | RemoveExternalAssetDirectory
  | SaveAreaMappings
  | SetShowAreas
  | RequestDiagnostics
  | SaveAmbientState;

export interface ProviderCapabilities {
  type: 'providerCapabilities';
  readingTools: string[];
  subagentToolNames: string[];
}

export interface AgentCreated {
  type: 'agentCreated';
  id: number;
  folderName?: string;
  isExternal?: boolean;
  palette?: number;
  hueShift?: number;
}

export interface AgentClosed {
  type: 'agentClosed';
  id: number;
}

export interface AgentSelected {
  type: 'agentSelected';
  id: number;
}

export interface ExistingAgents {
  type: 'existingAgents';
  agents: number[];
  agentMeta: Record<string, AgentSeatMeta>;
  folderNames: Record<string, string>;
  externalAgents: Record<string, boolean>;
}

export interface AgentSeatMeta {
  palette?: number;
  hueShift?: number;
  seatId?: string;
}

export interface AgentStatus {
  type: 'agentStatus';
  id: number;
  status: AgentActivityStatus;
  awaitingInput?: boolean;
}

export type AgentActivityStatus = 'active' | 'waiting';

export interface AgentToolStart {
  type: 'agentToolStart';
  id: number;
  toolId: string;
  status: string;
  toolName?: string;
  permissionActive?: boolean;
  runInBackground?: boolean;
  isTeammateSpawn?: boolean;
}

export interface AgentToolDone {
  type: 'agentToolDone';
  id: number;
  toolId: string;
}

export interface AgentToolsClear {
  type: 'agentToolsClear';
  id: number;
}

export interface AgentToolPermission {
  type: 'agentToolPermission';
  id: number;
}

export interface AgentToolPermissionClear {
  type: 'agentToolPermissionClear';
  id: number;
}

export interface SubagentToolStart {
  type: 'subagentToolStart';
  id: number;
  parentToolId: string;
  toolId: string;
  status: string;
}

export interface SubagentToolDone {
  type: 'subagentToolDone';
  id: number;
  parentToolId: string;
  toolId: string;
}

export interface SubagentClear {
  type: 'subagentClear';
  id: number;
  parentToolId: string;
}

export interface SubagentToolPermission {
  type: 'subagentToolPermission';
  id: number;
  parentToolId: string;
}

export interface AgentTeamInfo {
  type: 'agentTeamInfo';
  id: number;
  teamName?: string;
  agentName?: string;
  isTeamLead?: boolean;
  leadAgentId?: number;
  teamUsesTmux?: boolean;
}

export interface AgentContextUsage {
  type: 'agentContextUsage';
  id: number;
  contextTokens: number;
  maxContextTokens: number;
}

export interface LayoutLoaded {
  type: 'layoutLoaded';
  layout: Record<string, any> | null;
  wasReset?: boolean;
}

export interface FurnitureAssetsLoaded {
  type: 'furnitureAssetsLoaded';
  catalog: FurnitureAssetMessage[];
  sprites: Record<string, string[][]>;
}

export interface FurnitureAssetMessage {
  id: string;
  name: string;
  label: string;
  category: string;
  file: string;
  width: number;
  height: number;
  footprintW: number;
  footprintH: number;
  isDesk: boolean;
  canPlaceOnWalls: boolean;
  groupId?: string;
  canPlaceOnSurfaces?: boolean;
  backgroundTiles?: number;
  orientation?: string;
  state?: string;
  mirrorSide?: boolean;
  rotationScheme?: string;
  animationGroup?: string;
  frame?: number;
}

export interface CharacterSpritesLoaded {
  type: 'characterSpritesLoaded';
  characters: CharacterSpriteSet[];
}

export interface CharacterSpriteSet {
  down: string[][][];
  up: string[][][];
  right: string[][][];
}

export interface PetSpritesLoaded {
  type: 'petSpritesLoaded';
  pets: PetSpriteFrameSet[];
  petNames: string[];
}

export interface PetSpriteFrameSet {
  walkDown: string[][][];
  idleDown: string[][][];
  walkUp: string[][][];
  idleUp: string[][][];
  walkRight: string[][][];
}

export interface FloorTilesLoaded {
  type: 'floorTilesLoaded';
  sprites: string[][][];
}

export interface WallTilesLoaded {
  type: 'wallTilesLoaded';
  sets: string[][][][];
}

export interface CarpetTilesLoaded {
  type: 'carpetTilesLoaded';
  sets: string[][][][];
}

export interface SettingsLoaded {
  type: 'settingsLoaded';
  soundEnabled: boolean;
  lastSeenVersion: string;
  extensionVersion: string;
  watchAllSessions: boolean;
  alwaysShowLabels: boolean;
  ghostHeadlessAgents: boolean;
  hooksEnabled: boolean;
  hooksInfoShown: boolean;
  externalAssetDirectories: string[];
  showAreas: boolean;
}

export interface HooksStatus {
  type: 'hooksStatus';
  providerId: string;
  installed: boolean;
}

export interface HooksConsentRequest {
  type: 'hooksConsentRequest';
  providerId: string;
  headline: string;
  disclosure: string;
}

export interface ExternalAssetDirectoriesUpdated {
  type: 'externalAssetDirectoriesUpdated';
  dirs: string[];
}

export interface AreaMappingsLoaded {
  type: 'areaMappingsLoaded';
  mappings: Record<string, string[]>;
}

export interface WorkspaceFolders {
  type: 'workspaceFolders';
  folders: WorkspaceFolder[];
}

export interface WorkspaceFolder {
  name: string;
  path: string;
}

export interface AgentDiagnostics {
  type: 'agentDiagnostics';
  agents: Record<string, any>[];
}

export interface DiOfficeState {
  type: 'diOfficeState';
  live: boolean;
  notLiveReason: string | null;
  connection: DiObservedValue;
  envelope: DiEnvelopeStatus;
  runtime: DiRuntimeStatus;
  times: DiTimes;
  coverage: DiCoverage;
  missions: DiMissionList;
  contractVersion: number;
  chainObservation: DiChainObservation;
  reception?: DiReception;
  runtimeAgents?: DiRuntimeAgents;
  fixtureMode?: boolean;
}

export interface DiObservedValue {
  state: DiObservationState;
  value: string | null;
  reason: string | null;
}

export type DiObservationState =
  | 'observed'
  | 'known_empty'
  | 'not_recorded'
  | 'projected_reason'
  | 'not_applicable'
  | 'unavailable';

export interface DiEnvelopeStatus {
  fresh: boolean;
  reason: string | null;
  served: string | null;
  verdictHealth: string | null;
  verdictProblem: string | null;
  envelopeProblem: string | null;
  envelopeDetail: string | null;
  retryAfterSeconds: number | null;
  registryHealth: string | null;
}

export interface DiRuntimeStatus {
  fresh: boolean;
  reason: string | null;
  configured: boolean | null;
  available: boolean | null;
  captureProblem: string | null;
  snapshotHealth: string | null;
  snapshotProblem: string | null;
}

export interface DiTimes {
  requestedAt: number | null;
  generatedAt: number | null;
  validUntil: number | null;
  snapshotObservedAt: number | null;
  evaluatedAt: number;
}

export interface DiCoverage {
  scope: string;
  statement: string;
  linkedChildRoles: string;
  linkedChildStatement: string;
}

export interface DiMissionList {
  state: DiObservationState;
  reason: string | null;
  missions: DiMission[];
}

export interface DiMission {
  missionRef: string;
  sourceId: string;
  nativeId: string;
  sourceKind: string;
  lifecycleModel: string;
  lifecycleState: string;
  standing: string;
  bindingState: string;
  bindingReason: string;
  producerDeclared: string;
  live: boolean;
  notLiveReason: string | null;
  objective: DiObservedValue;
  updatedAt: DiObservedValue;
  identitySource: DiObservedValue;
  paused: DiObservedValue;
  cancelRequested: DiObservedValue;
  ambiguity: DiObservedValue;
  members: DiMemberList;
  v2: DiMissionV2;
  herdrTask?: DiHerdrTask;
}

export interface DiMemberList {
  state: DiObservationState;
  reason: string | null;
  members: DiMember[];
}

export interface DiMember {
  characterId: number | null;
  coverage: string;
  memberRef: string;
  missionRef: string;
  logical: string;
  role: string;
  agent: string;
  provider: string;
  taskId: DiObservedValue;
  paneId: DiObservedValue;
  workspaceId: DiObservedValue;
  sessionRecorded: DiObservedValue;
  sessionLive: DiObservedValue;
  classification: string;
  reason: string;
  activity: string;
  live: boolean;
  notLiveReason: string | null;
  animateActive: boolean;
  model: DiObservedValue;
  cost: DiObservedValue;
  tokens: DiObservedValue;
  progress: DiObservedValue;
  chain: string;
  chainTaskId: string | null;
}

export interface DiMissionV2 {
  projected: boolean;
  taskChains: DiTaskChain[];
  childrenRecord: DiChildrenRecord;
  missionState: DiMissionStateSummary;
  canonicalReview: DiCanonicalReview;
}

export interface DiTaskChain {
  chain: string;
  chainTaskId: string | null;
  parentTaskId: string | null;
  bindingState: string;
  bindingReason: string;
  rootObservation: string;
  rootProblem: string | null;
  membershipEnded: boolean | null;
  membershipStatus: string | null;
  membershipRule: string;
  dependencyRecorded: boolean | null;
  dependencyResolution: string | null;
  dependencyRule: string | null;
  descendantsNotFollowed: number | null;
  declaredAgents: DiDeclaredAgent[];
}

export interface DiDeclaredAgent {
  logical: string;
  agent: string;
}

export interface DiChildrenRecord {
  state: string | null;
  problem: string | null;
  caveat: string | null;
  recordCount: number | null;
  otherParentCount: number | null;
}

export interface DiMissionStateSummary {
  state: string | null;
  problem: string | null;
  detail: string | null;
  cursor: string | null;
  baseline: string | null;
  candidateApplicability: string | null;
  kinds: DiMissionStateKind[];
  facts: DiReportFact[];
  completion: DiCompletion;
  provenance: DiReportProvenance;
}

export interface DiMissionStateKind {
  kind: string;
  supplied: string | null;
  reason: string | null;
}

export interface DiReportFact {
  name: string;
  state: DiObservationState;
  reason: string | null;
  standing: string | null;
  source: string | null;
  freshness: string | null;
  detail: string | null;
  observedAt: number | null;
  revision: number | null;
  position: number | null;
  entries: DiReportEntry[];
}

export interface DiReportEntry {
  label: string;
  value: string | null;
}

export interface DiCompletion {
  state: DiObservationState;
  reason: string | null;
  reportedComplete: boolean | null;
  reportedSuccess: boolean | null;
  verifiedSuccess: boolean | null;
  closureVerified: boolean | null;
  contradicted: boolean | null;
  closure: string | null;
  holds: string[];
  holdsTotal: number | null;
  limitsIdentity: string | null;
  limitsSeparationOfDuties: string | null;
  limitsNonVerifyingEvidenceKinds: string[];
  limitsNonVerifyingEvidenceKindsTotal: number | null;
}

export interface DiReportProvenance {
  state: DiObservationState;
  reason: string | null;
  recordSource: string | null;
  revision: number | null;
  position: number | null;
  freshnessBoundSeconds: number | null;
  journalDigest: string | null;
  movedDuringCollection: string | null;
  collectionStatus: string | null;
  collectedAtPosition: number | null;
  headPosition: number | null;
  cursorPosition: number | null;
  drift: string[];
  driftTotal: number | null;
  sources: DiReportSource[];
}

export interface DiReportSource {
  kind: string;
  source: string | null;
  standing: string | null;
  observedAt: number | null;
  detail: string | null;
}

export interface DiCanonicalReview {
  state: string | null;
  problem: string | null;
  gate: string | null;
  verdict: string | null;
  currency: string | null;
  integrity: string | null;
  taskStatus: string | null;
  candidateApplicability: string | null;
  round: number | null;
  reviewerLogical: string | null;
  reviewerAgent: string | null;
  protocolToken: string | null;
}

export interface DiHerdrTask {
  rootAliases: string[];
  membership: string | null;
  schema: string | null;
  schemaVersion: number | null;
  taskRecordVersion: number | null;
  startedAt: number | null;
}

export interface DiChainObservation {
  projected: boolean;
  health: string | null;
  problem: string | null;
  observedAt: number | null;
  maxAgeSeconds: number | null;
  rootCount: number | null;
  unavailableCount: number | null;
  overCapacityCount: number | null;
}

export interface DiReception {
  contractVersion: number;
  categories: DiReceptionCategory[];
  entries: DiReceptionEntry[];
  entriesTotal: number;
  entriesCap: number;
  notices: DiReceptionNotice[];
  minPollIntervalSeconds: number | null;
  refreshStatement: string;
  dismissalStatement: string;
}

export interface DiReceptionCategory {
  category: DiReceptionCategoryId;
  label: string;
  statement: string;
  backing: string;
  outcome: DiReceptionOutcome;
  reason: string | null;
  counts: DiReceptionCounts;
  gaps: DiReceptionGap[];
  entriesTotal: number;
}

export type DiReceptionCategoryId =
  | 'blockers'
  | 'clarification'
  | 'authorization'
  | 'failed_verification'
  | 'ready_for_review'
  | 'delivery_decision';

export type DiReceptionOutcome =
  'present' | 'known_empty' | 'unsupported' | 'unavailable' | 'partial';

export interface DiReceptionCounts {
  present: number;
  knownEmpty: number;
  unsupported: number;
  unavailable: number;
}

export interface DiReceptionGap {
  outcome: DiReceptionOutcome;
  reason: string;
  missions: number;
}

export interface DiReceptionEntry {
  key: string;
  signature: string;
  category: DiReceptionCategoryId;
  missionRef: string;
  sourceId: string;
  nativeId: string;
  sourceKind: string;
  producerDeclared: string;
  objective: DiObservedValue;
  lifecycleModel: string;
  lifecycleState: string;
  standing: string;
  bindingState: string;
  bindingReason: string;
  live: boolean;
  notLiveReason: string | null;
  occurrences: number;
  identityConflict: boolean;
  causes: DiReceptionCause[];
  alsoUnknown: string[];
  current: boolean;
  route: string;
  routeIdentifiers: DiReportEntry[];
}

export interface DiReceptionCause {
  cause: string;
  label: string;
  details: DiReportEntry[];
  staleReasons: string[];
}

export interface DiReceptionNotice {
  reason: string;
  statement: string;
}

export interface DiRuntimeAgents {
  reported: boolean;
  state: string | null;
  problem: string | null;
  plainStatement: string;
  caveat: string | null;
  observedAt: number | null;
  firstPassAt: number | null;
  secondPassAt: number | null;
  counts: DiCount[];
  agents: DiRuntimeAgent[];
  agentsTotal: number;
  agentsCap: number;
  agentsOverBound: number;
  capture: DiRuntimeCapture;
  locators: DiLocatorSummary | null;
}

export interface DiCount {
  name: string;
  value: number;
}

export interface DiRuntimeAgent {
  agentRef: string;
  characterId: number | null;
  sourceId: string;
  rootRef: string;
  rootAliases: string[];
  logical: string;
  role: string | null;
  agent: string;
  provider: string | null;
  session: string | null;
  paneId: string | null;
  workspaceId: string | null;
  activity: string | null;
  identityState: string;
  identityReason: string;
  identityVerified: boolean;
  assignmentState: string;
  assignmentReason: string;
  taskRef: string | null;
  taskId: string | null;
  taskStatus: string | null;
  live: boolean;
  notLiveReason: string | null;
  plainText: string;
}

export interface DiRuntimeCapture {
  discovered: number | null;
  captured: number | null;
  unavailable: number | null;
  overCapacity: number | null;
  listingAnchor: boolean | null;
}

export interface DiLocatorSummary {
  registeredAliases: number;
  physicalRoots: number;
  sharedRootGroups: number;
  rootsWithCurrentTask: number;
  rootsWithoutCurrentTask: number;
  aliasesWithoutHerdState: number;
  excludedTotal: number;
  excludedByReason: DiCount[];
}

export interface AmbientStateLoaded {
  type: 'ambientStateLoaded';
  state: Record<string, any> | null;
}

export interface AmbientSpritesLoaded {
  type: 'ambientSpritesLoaded';
  sheets: AmbientSpriteSheet[];
}

export interface AmbientSpriteSheet {
  id: string;
  label: string;
  frameWidth: number;
  frameHeight: number;
  rows: string[];
  walk: number[];
  idle: number[];
  gesture: number[];
  frames: string[][][][];
}

export interface WebviewReady {
  type: 'webviewReady';
}

export interface LaunchAgent {
  type: 'launchAgent';
  folderPath?: string;
  bypassPermissions?: boolean;
}

export interface FocusAgent {
  type: 'focusAgent';
  id: number;
}

export interface CloseAgent {
  type: 'closeAgent';
  id: number;
}

export interface SaveAgentSeats {
  type: 'saveAgentSeats';
  seats: Record<string, SeatAssignment>;
}

export interface SeatAssignment {
  palette: number;
  hueShift: number;
  seatId: string | null;
}

export interface SaveLayout {
  type: 'saveLayout';
  layout: Record<string, any>;
}

export interface SetSoundEnabled {
  type: 'setSoundEnabled';
  enabled: boolean;
}

export interface SetLastSeenVersion {
  type: 'setLastSeenVersion';
  version: string;
}

export interface SetAlwaysShowLabels {
  type: 'setAlwaysShowLabels';
  enabled: boolean;
}

export interface SetGhostHeadlessAgents {
  type: 'setGhostHeadlessAgents';
  enabled: boolean;
}

export interface SetHooksEnabled {
  type: 'setHooksEnabled';
  providerId: string;
  enabled: boolean;
}

export interface HooksConsentResponse {
  type: 'hooksConsentResponse';
  providerId: string;
  choice: HooksConsentChoice;
}

export type HooksConsentChoice = 'install' | 'notNow' | 'never';

export interface SetHooksInfoShown {
  type: 'setHooksInfoShown';
}

export interface SetWatchAllSessions {
  type: 'setWatchAllSessions';
  enabled: boolean;
}

export interface ExportLayout {
  type: 'exportLayout';
}

export interface ImportLayout {
  type: 'importLayout';
}

export interface OpenSessionsFolder {
  type: 'openSessionsFolder';
}

export interface AddExternalAssetDirectory {
  type: 'addExternalAssetDirectory';
  path?: string;
}

export interface RemoveExternalAssetDirectory {
  type: 'removeExternalAssetDirectory';
  path: string;
}

export interface SaveAreaMappings {
  type: 'saveAreaMappings';
  mappings: Record<string, string[]>;
}

export interface SetShowAreas {
  type: 'setShowAreas';
  enabled: boolean;
}

export interface RequestDiagnostics {
  type: 'requestDiagnostics';
}

export interface SaveAmbientState {
  type: 'saveAmbientState';
  state: Record<string, any>;
}
