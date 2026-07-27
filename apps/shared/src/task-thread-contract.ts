export const TASK_THREAD_SCHEMA_VERSION = 1 as const

export const TASK_THREAD_STATUSES = [
  'queued',
  'starting',
  'running',
  'waiting_user',
  'waiting_approval',
  'completed',
  'failed',
  'interrupted',
  'archived'
] as const

export type TaskThreadStatus = (typeof TASK_THREAD_STATUSES)[number]

export type TaskThreadWorkspaceMode = 'none' | 'existing_project' | 'isolated_worktree'

export interface TaskThreadV1 {
  branch: string | null
  blocker: string | null
  created_at: string
  goal: string
  id: string
  last_summary: string | null
  pending_approval_id: string | null
  project_id: string | null
  runtime_session_id: string | null
  status: TaskThreadStatus
  stored_session_id: string | null
  title: string
  updated_at: string
  worker_profile_id: string
  workspace_kind: 'dir' | 'scratch' | 'worktree'
  workspace_path: string | null
}

export type TaskTurnKind = 'initial' | 'followup' | 'steer'
export type TaskTurnStatus = 'queued' | 'running' | 'completed' | 'failed' | 'interrupted'

export interface TaskTurnV1 {
  completed_at: string | null
  created_at: string
  id: string
  instruction: string
  kind: TaskTurnKind
  started_at: string | null
  status: TaskTurnStatus
  thread_id: string
}

export type TaskThreadEventName =
  | 'approval.requested'
  | 'approval.resolved'
  | 'thread.completed'
  | 'thread.created'
  | 'thread.failed'
  | 'thread.focused'
  | 'thread.interrupted'
  | 'thread.status_changed'
  | 'turn.completed'
  | 'turn.failed'
  | 'turn.queued'
  | 'turn.started'
  | 'turn.steered'
  | 'worker.message_completed'
  | 'worker.message_delta'

export interface TaskThreadEventV1<P = Record<string, unknown>> {
  causation_id: string | null
  correlation_id: string
  event_id: string
  name: TaskThreadEventName
  payload: P
  schema_version: typeof TASK_THREAD_SCHEMA_VERSION
  sequence: number
  thread_id: string | null
  timestamp: string
  turn_id: string | null
  voice_workspace_id: string | null
  worker_profile_id: string | null
}

export interface TaskApprovalV1 {
  allow_permanent: boolean
  approval_id: string
  choice: 'always' | 'deny' | 'once' | 'session' | null
  command: string
  created_at: string
  description: string
  resolved_at: string | null
  status: 'pending' | 'resolved'
  thread_id: string
}

export interface ThreadsCreateParams {
  goal: string
  idempotency_key: string
  project_id?: string
  title: string
  voice_workspace_id?: string
  worker_profile_id: string
  workspace_mode?: TaskThreadWorkspaceMode
}

export interface ThreadsCreateResult {
  thread: TaskThreadV1
}

export interface ThreadsListParams {
  include_archived?: boolean
}

export interface ThreadsListResult {
  cursor: number
  threads: TaskThreadV1[]
}

export interface ThreadsGetParams {
  thread_id: string
}

export interface ThreadsGetResult {
  thread: TaskThreadV1
}

export interface ThreadsInstructionParams {
  idempotency_key: string
  instruction: string
  thread_id: string
}

export interface ThreadsInstructionResult {
  accepted: 'queued' | 'started' | 'steered'
  thread: TaskThreadV1
  turn_id: string
}

export interface ThreadsInterruptParams {
  idempotency_key: string
  thread_id: string
}

export interface ThreadsInterruptResult {
  thread: TaskThreadV1
}

export interface ThreadsFocusParams {
  thread_id: string
  voice_workspace_id: string
}

export interface ThreadsFocusResult {
  focused_thread_id: string
}

export interface ThreadsEventsParams {
  after_sequence?: number
  limit?: number
  thread_id?: string
}

export interface ThreadsEventsResult {
  cursor: number
  events: TaskThreadEventV1[]
}

export interface ApprovalsListParams {
  thread_id?: string
}

export interface ApprovalsListResult {
  approvals: TaskApprovalV1[]
}

export interface ApprovalsRespondParams {
  approval_id: string
  choice: 'always' | 'deny' | 'once' | 'session'
}

export interface ApprovalsRespondResult {
  approval: TaskApprovalV1
}
