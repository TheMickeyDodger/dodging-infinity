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
  | CityPresentationLoaded
  | DiSeatPreferencesLoaded
  | AmbientSpritesLoaded
  | DiEvidenceContent
  | DiCodeReview
  | DiMissionRequestReply
  | DiDeliveryReply
  | DiHerdrReply;

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
  | SaveAmbientState
  | SaveCityPresentation
  | SaveDiSeatPreferences
  | DiEvidenceRead
  | DiCodeReviewRead
  | DiCodeReviewMissionRead
  | DiMissionRequest
  | DiDeliveryRequest
  | DiHerdrRequest;

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
  evidence?: DiEvidenceLibrary;
  codeReview?: DiCodeReviewOffer;
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
  codeReview?: DiCodeReviewOffer;
}

export interface DiCodeReviewOffer {
  state: DiCodeReviewOfferState;
  reason: string | null;
  taskId: string | null;
  listing: string | null;
}

export type DiCodeReviewOfferState = 'offered' | 'unavailable';

export interface DiMissionV2 {
  workflowPhase?: DiWorkflowPhase;
  deliveryLink?: DiMissionDeliveryLink;
  deliveryHandoff?: DiMissionDeliveryHandoff;
  projected: boolean;
  taskChains: DiTaskChain[];
  childrenRecord: DiChildrenRecord;
  missionState: DiMissionStateSummary;
  canonicalReview: DiCanonicalReview;
}

export interface DiWorkflowPhase {
  state: DiWorkflowPhaseState;
  phase: string | null;
  unrecognized: string | null;
  problem: string | null;
}

export type DiWorkflowPhaseState = 'observed' | 'not_applicable' | 'not_reported';

export interface DiMissionDeliveryLink {
  state: DiMissionDeliveryLinkState;
  deliveryId: string | null;
  basis: DiMissionDeliveryLinkBasis | null;
}

export type DiMissionDeliveryLinkState =
  | 'linked'
  | 'unlinked_model_not_recorded'
  | 'unlinked_store_not_registered'
  | 'unlinked_store_unavailable'
  | 'unlinked_reader_failed'
  | 'unlinked_no_record'
  | 'unlinked_ambiguous'
  | 'unlinked_sources_incomplete'
  | 'unlinked_id_not_addressable'
  | 'unlinked_repository_not_recorded'
  | 'unlinked_task_worktree_not_recorded';

export type DiMissionDeliveryLinkBasis =
  'record_mission_binding' | 'record_repository_binding' | 'record_task_worktree_binding';

export interface DiMissionDeliveryHandoff {
  state: DiHandoffState;
  linkBasis: string | null;
  repository: string | null;
  engineeringTaskId: string | null;
  gitDirRealpath: string | null;
  branch: DiHandoffBranch | null;
  evidence: DiHandoffEvidence | null;
  stagedCandidate: DiHandoffStagedCandidate | null;
  workflowId: DiHandoffWorkflowId | null;
  blockers: DiHandoffBlocker[];
}

export type DiHandoffState = 'observed' | 'not_applicable' | 'not_reported';

export interface DiHandoffBranch {
  state: DiHandoffBranchState;
  ref: string | null;
}

export type DiHandoffBranchState = 'named' | 'detached' | 'unreadable';

export interface DiHandoffEvidence {
  state: DiHandoffEvidenceState;
  reason: string | null;
}

export type DiHandoffEvidenceState = 'collected' | 'refused' | 'unreadable';

export type DiHandoffStagedCandidate = 'not_determined';

export type DiHandoffWorkflowId = 'human_supplied';

export type DiHandoffBlocker =
  | 'git_dir_not_read'
  | 'head_detached'
  | 'head_not_read'
  | 'evidence_not_collected'
  | 'task_id_not_recorded';

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

export interface DiEvidenceLibrary {
  cursor: string | null;
  execution: DiEvidenceExecution;
  currency: DiEvidenceCurrency;
  currencyReason: string | null;
  contentReads: DiEvidenceContentReads;
  artifacts: DiEvidenceArtifactSection;
  verification: DiEvidenceRecordSection;
  reviews: DiEvidenceReviewSection;
  receipts: DiEvidenceReceiptSection;
}

export interface DiEvidenceExecution {
  basis: DiEvidenceExecutionBasisListed;
  identityState: DiEvidenceExecutionIdentityState;
  identityProblem: string | null;
  lifecycleState: string | null;
  taskId: string | null;
}

export type DiEvidenceExecutionBasisListed =
  'active_binding' | 'historical_completed_execution' | 'none';

export type DiEvidenceExecutionIdentityState =
  'not_reported' | 'established' | 'not_established' | 'not_applicable' | 'unexpected';

export type DiEvidenceCurrency = 'current' | 'stale';

export interface DiEvidenceContentReads {
  state: DiEvidenceContentReadsState;
  reason: string | null;
}

export type DiEvidenceContentReadsState = 'available' | 'unavailable';

export interface DiEvidenceArtifactSection {
  state: DiEvidenceSectionState;
  reason: string | null;
  total: number | null;
  items: DiEvidenceArtifact[];
}

export type DiEvidenceSectionState =
  | 'observed'
  | 'known_empty'
  | 'not_recorded'
  | 'projected_reason'
  | 'not_applicable'
  | 'unavailable'
  | 'refused';

export interface DiEvidenceArtifact {
  artifactId: string;
  key: string | null;
  role: string;
  locatorKind: string;
  contentDigest: string | null;
  availableReported: boolean | null;
  recordedAt: number | null;
  candidate: DiEvidenceCandidate;
  content: DiEvidenceContentRequest;
}

export type DiEvidenceCandidate = 'matches_observed' | 'differs_from_observed' | 'not_observed';

export interface DiEvidenceContentRequest {
  state: DiEvidenceContentRequestState;
  reason: string | null;
}

export type DiEvidenceContentRequestState = 'requestable' | 'not_requestable';

export interface DiEvidenceRecordSection {
  state: DiEvidenceSectionState;
  reason: string | null;
  total: number | null;
  items: DiEvidenceRecord[];
}

export interface DiEvidenceRecord {
  evidenceId: string;
  requirementKey: string | null;
  kind: string | null;
  standing: string | null;
  freshness: string | null;
  accepted: boolean | null;
  invalidated: boolean | null;
  verifyingKind: boolean | null;
  bearsOnClaim: boolean | null;
  drifted: boolean | null;
  unconfirmed: boolean | null;
  artifactIds: string[];
  artifactIdsTotal: number;
}

export interface DiEvidenceReviewSection {
  state: DiEvidenceSectionState;
  reason: string | null;
  total: number | null;
  items: DiEvidenceReview[];
}

export interface DiEvidenceReview {
  findings: DiEvidenceFindings;
  round: number | null;
  reviewerLogical: string | null;
  reviewerAgent: string | null;
  protocolToken: string | null;
  gate: string | null;
  verdict: string | null;
  currency: string | null;
  integrity: string | null;
  candidateApplicability: string | null;
}

export interface DiEvidenceFindings {
  state: DiEvidenceFindingsState;
  problem: string | null;
  count: number | null;
  items: DiEvidenceFinding[];
  candidateSha256: string | null;
  routedAt: number | null;
  provenance: string | null;
}

export type DiEvidenceFindingsState =
  'not_reported' | 'not_recorded' | 'empty' | 'present' | 'refused' | 'unexpected';

export interface DiEvidenceFinding {
  index: number;
  severity: DiEvidenceFindingSeverity;
  area: string | null;
  line: number | null;
  summary: string;
}

export type DiEvidenceFindingSeverity = 'blocker' | 'non_blocking';

export interface DiEvidenceReceiptSection {
  state: DiEvidenceSectionState;
  reason: string | null;
  total: number | null;
  items: DiEvidenceReceipt[];
}

export interface DiEvidenceReceipt {
  artifactId: string;
  attested: boolean;
  deliveryId: string | null;
  step: string | null;
  receiptState: string | null;
  stepState: string | null;
  effectCompleted: boolean | null;
  contentDigest: string | null;
  recordedAt: number | null;
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
  sourceEntries?: DiReceptionSourceEntry[];
  sourceEntriesTotal?: number;
  sourceEntriesCap?: number;
  sourceCoverage?: DiReceptionSourceCoverage[];
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

export interface DiReceptionSourceEntry {
  key: string;
  signature: string;
  category: DiReceptionCategoryId;
  sourceId: string;
  proposalDigest: string;
  state: string;
  needsYou: boolean;
  presentedAt: number;
  expiresAt: number;
  mode: string;
  repositoryUrl: string | null;
  workflowId: string | null;
  matchingDeliveries: number | null;
  linkState: string;
  missionRef: string | null;
  authorizationCheck: string;
  current: boolean;
  staleReasons: string[];
  route: string;
}

export interface DiReceptionSourceCoverage {
  sourceId: string;
  label: string;
  outcome: DiReceptionOutcome;
  reason: string | null;
  statement: string;
  total: number | null;
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

export interface CityPresentationLoaded {
  type: 'cityPresentationLoaded';
  state: Record<string, any> | null;
}

export interface DiSeatPreferencesLoaded {
  type: 'diSeatPreferencesLoaded';
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

export interface DiEvidenceContent {
  type: 'diEvidenceContent';
  missionRef: string;
  artifactId: string;
  cursor: string;
  state: DiEvidenceAnswerState;
  decidedBy: DiEvidenceDecidedBy;
  problem: string | null;
  detail: string | null;
  artifact: DiEvidenceArtifactRecord | null;
  content: DiEvidenceText | null;
  observedAt: number | null;
}

export type DiEvidenceAnswerState = 'admitted' | 'refused';

export type DiEvidenceDecidedBy = 'office' | 'backend';

export interface DiEvidenceArtifactRecord {
  key: string | null;
  role: string;
  locatorKind: string;
  locator: string;
  contentDigest: string;
  recordedAt: number | null;
  admissionBasis: DiEvidenceAdmissionBasis[];
  executionBasis: DiEvidenceExecutionBasis;
}

export type DiEvidenceAdmissionBasis = 'accepted_evidence_artifact' | 'contract_required_artifact';

export type DiEvidenceExecutionBasis = 'active_binding' | 'historical_completed_execution';

export interface DiEvidenceText {
  text: string;
  byteCount: number;
  digest: string;
  media: DiEvidenceMedia;
}

export type DiEvidenceMedia = 'text/plain' | 'text/html';

export interface DiCodeReview {
  type: 'diCodeReview';
  missionRef: string;
  memberRef: string;
  taskId: string;
  listing: string;
  snapshot: string | null;
  fileId: string | null;
  view: DiCodeReviewView;
  state: DiCodeReviewState;
  decidedBy: DiEvidenceDecidedBy;
  problem: string | null;
  detail: string | null;
  target: DiCodeReviewTarget | null;
  comparison: DiCodeReviewComparison | null;
  overview: DiCodeReviewOverview | null;
  file: DiCodeReviewFile | null;
  observedAt: number | null;
  requestId?: string;
}

export type DiCodeReviewView = 'overview' | 'file';

export type DiCodeReviewState = 'observed' | 'refused';

export interface DiCodeReviewTarget {
  sourceId: string;
  worktree: string;
  head: string;
  branch: string | null;
  detached: boolean;
  attribution: 'shared_worktree';
}

export interface DiCodeReviewComparison {
  mode: 'head_to_worktree';
  baseKind: 'head_commit';
  baseOid: string;
  candidateKind: 'working_tree';
  covers: string[];
  doesNotCover: string[];
  filtersNotRun: string[];
}

export interface DiCodeReviewOverview {
  files: DiCodeReviewEntry[];
  totals: DiCodeReviewTotals;
}

export interface DiCodeReviewEntry {
  fileId: string;
  path: string;
  oldPath: string | null;
  change: string;
  staging: string;
  state: string;
  reason: string | null;
  additions: number | null;
  deletions: number | null;
}

export interface DiCodeReviewTotals {
  files: number;
  additions: number;
  deletions: number;
  withheld: number;
  unshown: number;
}

export interface DiCodeReviewFile {
  fileId: string;
  path: string;
  oldPath: string | null;
  change: string;
  staging: string;
  state: string;
  reason: string | null;
  additions: number | null;
  deletions: number | null;
  modeChange: DiCodeReviewModeChange | null;
  similarity: number | null;
  hunks: DiCodeReviewHunk[];
  truncated: boolean;
  linesTotal: number;
  linesRetained: number;
  bytesTotal: number;
  digestSha256: string | null;
}

export interface DiCodeReviewModeChange {
  old: string;
  new: string;
}

export interface DiCodeReviewHunk {
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  section: string | null;
  lines: DiCodeReviewLine[];
}

export interface DiCodeReviewLine {
  kind: string;
  oldNo: number | null;
  newNo: number | null;
  text: string;
  clipped: number;
}

export interface DiMissionRequestReply {
  type: 'diMissionRequestReply';
  seq: number;
  op: DiMissionRequestOp;
  state: DiMissionRequestState;
  problem: string | null;
  reason: string | null;
  answer: Record<string, any> | null;
}

export type DiMissionRequestOp =
  | 'refresh'
  | 'conversation'
  | 'request'
  | 'offer'
  | 'result'
  | 'turn'
  | 'submit'
  | 'decide'
  | 'checkApproval'
  | 'decline'
  | 'reject'
  | 'requestChanges'
  | 'withdraw'
  | 'run';

export type DiMissionRequestState = 'ok' | 'refused' | 'unavailable' | 'unknown';

export interface DiDeliveryReply {
  type: 'diDeliveryReply';
  seq: number;
  op: DiDeliveryOp;
  state: DiDeliveryState;
  problem: string | null;
  reason: string | null;
  answer: Record<string, any> | null;
}

export type DiDeliveryOp = 'status' | 'advanceOnce';

export type DiDeliveryState = 'ok' | 'refused' | 'unavailable' | 'unknown';

export interface DiHerdrReply {
  type: 'diHerdrReply';
  seq: number;
  op: DiHerdrOp;
  state: DiHerdrState;
  problem: string | null;
  reason: string | null;
  answer: DiHerdrAnswer | null;
}

export type DiHerdrOp = 'status' | 'launch';

export type DiHerdrState = 'ok' | 'refused' | 'unavailable';

export interface DiHerdrAnswer {
  agent: string;
  identityBasis: DiHerdrIdentityBasis;
  windowMinCols: number;
  windowMinRows: number;
  workspaceId: string;
  workspaceFocus: DiHerdrWorkspaceFocus;
  matchedPaneId: string | null;
  paneFocus: 'not_performed';
  terminal: string;
  terminalLabel: string;
  terminalState: DiHerdrTerminalState | null;
}

export type DiHerdrIdentityBasis = 'runtime_identity_verified' | 'team_roster_session_confirmed';

export type DiHerdrWorkspaceFocus = 'focused' | 'not_performed';

export type DiHerdrTerminalState = 'opened' | 'unconfirmed';

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

export interface SaveCityPresentation {
  type: 'saveCityPresentation';
  state: Record<string, any>;
}

export interface SaveDiSeatPreferences {
  type: 'saveDiSeatPreferences';
  state: Record<string, any>;
}

export interface DiEvidenceRead {
  type: 'diEvidenceRead';
  missionRef: string;
  artifactId: string;
  cursor: string;
}

export interface DiCodeReviewRead {
  type: 'diCodeReviewRead';
  missionRef: string;
  memberRef: string;
  taskId: string;
  listing: string;
  snapshot?: string;
  fileId?: string;
  requestId?: string;
}

export interface DiCodeReviewMissionRead {
  type: 'diCodeReviewMissionRead';
  missionRef: string;
  taskId: string;
  listing: string;
  snapshot?: string;
  fileId?: string;
  requestId?: string;
}

export interface DiMissionRequest {
  type: 'diMissionRequest';
  seq: number;
  op: DiMissionRequestOp;
  conversationId?: string | null;
  text?: string;
  requestRef?: string;
  turnId?: string;
  missionId?: string;
  revision?: number;
  digest?: string;
  reply?: string;
  offerDigest?: string;
  expiresAt?: number;
  scope?: string[];
  deliveryTargets?: string[];
  repositoryUrl?: string | null;
  command?: string;
  supersedes?: string | null;
  reason?: string;
}

export interface DiDeliveryRequest {
  type: 'diDeliveryRequest';
  seq: number;
  op: DiDeliveryOp;
  deliveryId: string;
  confirmation?: DiDeliveryConfirmation;
}

export interface DiDeliveryConfirmation {
  delivery_id: string;
  revision: number;
  candidate_identity_digest_sha256: string;
  step: DiDeliveryStep;
}

export type DiDeliveryStep = 'BASE_REFRESH' | 'COMMIT' | 'PUSH' | 'PR_CREATE';

export interface DiHerdrRequest {
  type: 'diHerdrRequest';
  seq: number;
  op: DiHerdrOp;
  missionRef: string;
}
