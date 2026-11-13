export type Room = { id: string; name: string; temperature_c: number; temperature_tolerance_c?: number; relative_humidity: number; humidity_tolerance?: number; required_hours: number }
export type Station = { id: string; name: string; room_id: string }
export type Config = { rooms: Room[]; stations: Station[]; treatments: string[] }
export type PrepHeader = { project_number: string; labware_number: string; standard: string; regime: string }
/** One prep sheet row, column for column. */
export type PrepRow = { sheet: string; test: string; tested_count: string; conditions: string; conditioning: string; sample: string; sample_type: string; metadata?: Record<string, unknown> }
export type PrepProblem = { row: number; sheet: string; column: string; message: string }
export type Draft = { id: number; headers: Record<string, PrepHeader>; rows: PrepRow[]; revision: number; committed: boolean; stack_ids: number[]; source?: { filename: string; sha256?: string; format?: string }; warnings?: string[]; sheets?: { name: string; kind: string; candidate_count: number; selected?: boolean; warnings?: string[] }[]; problems?: PrepProblem[]; stack_count?: number; specimen_count?: number; reused_import?: boolean }
export type StackFields = { project_number: string; material_ref: string; test_code: string; preconditioning_code: string; specimen_count: number; metadata?: Record<string, unknown> }
export type Session = { id: number; room_id: string; entered_at: string; exited_at: string | null; source: string; corrected: boolean; recorded_at: string }
export type Specimen = { id: number; number: number; state: 'IN' | 'OUT'; room_id: string | null; entered_at: string | null; due_at: string | null; revision: number; sessions: Session[]; ENTRY?: string | null; EXIT?: string | null; entry_at?: string | null; exit_at?: string | null; raw_json_time?: Record<string, unknown>[]; status?: string; barcode_value: string; ready?: boolean }
export type AuditEvent = { id: number; kind: string; direction?: string; recorded_at: string; occurred_at?: string; actor: string; reason?: string; note?: string; stack_id: number; project_number?: string; material_ref?: string; specimen_ids: number[]; effective_specimen_ids?: number[]; effective_count?: number; effective_occurred_at?: string; effective_room_id?: string; affected_count: number; undone?: boolean; modified?: boolean; room_id?: string; payload?: Record<string, unknown>; can_undo?: boolean; version?: number }
export type Stack = StackFields & { id: number; removed_specimens?: { id: number; number: number; barcode_value: string; removed_at: string }[]; next_number?: number; version: number; created_at: string; is_demo: boolean; specimens: Specimen[]; events?: AuditEvent[]; scanned_specimen_id?: number | null; inside_count?: number; ready_count?: number; waiting_count?: number; finished_count?: number; awaiting_count?: number; oldest_entry_at?: string | null; next_ready_at?: string | null; server_time?: string }
export type Movement = { request_id: string; station_id: string; code: string; direction: 'IN' | 'OUT'; scope: 'stack' | 'specimen' | 'selection'; selected_specimen_ids?: number[]; expected_version?: number; acknowledge_early?: boolean }
export type MovementResult = { status: 'committed' | 'already_recorded' | 'selection_required' | 'acknowledgment_required' | 'rejected'; message: string; stack_id: number; affected_count: number; recorded_at?: string; event_id?: number; version?: number; specimens?: Specimen[] }
export type Printer = { name: string; is_default?: boolean; label_printer?: boolean; state?: string; alerts?: string[]; waiting_jobs?: number; warnings?: string[] }
export type PrinterInfo = { available: boolean; enabled: boolean; configured_queue?: string; preferred_queue?: string | null; printers: Printer[]; message: string }
export type PrintJob = { request_id: string; status: string; message?: string; waiting_cups_jobs?: number; job_id?: string; printer_queue?: string; affected_count?: number; label_count?: number; stack_ids?: number[]; selected_specimen_ids?: number[]; error?: string | null }
export type SpecimenRecord = Specimen & { stack_id: number; project_number: string; material_ref: string; test_code: string; preconditioning_code: string; metadata?: Record<string, unknown>; server_time?: string }
export type Stages = { total: number; labeled: number; entered: number; inside: number; ready: number; finished: number }
export type BatchRef = { id: number; label: string; created_at?: string }
export type PipelineStack = Stages & {
  id: number; project_number: string; material_ref: string; test_code: string; preconditioning_code: string
  registered_at: string; is_demo?: boolean; unlabeled_waiting: number; labeled_waiting: number
  labeled_at: string | null; print_attempt: string | null; batch?: BatchRef
}
export type AgeBucket = 'under_1d' | '1_to_3d' | 'over_3d'
export type ReadyItem = PipelineStack & { room_ready: number; room_inside: number; ready_since: string; waiting_seconds: number; age_bucket: AgeBucket }
export type ReadyRoom = { room_id: string; room_name: string; stacks: number; pieces: number; oldest_ready_since: string | null; buckets: Record<AgeBucket, number>; items: ReadyItem[] }
export type BatchCard = { id: number; label: string; created_at: string; open: boolean; stacks: number; stages: Stages }
export type DashboardData = {
  server_time: string
  rooms: { id: string; name: string; inside: number; ready: number }[]
  facets: { projects: string[]; tests: string[]; treatments: string[]; batches: BatchRef[] }
  gaps: { needs_labels: PipelineStack[]; not_scanned_in: PipelineStack[] }
  ready: ReadyRoom[]
  batches: BatchCard[]
}
export type BatchSummary = { id: number; label: string; created_at: string; open: boolean; stages: Stages; stacks: PipelineStack[]; server_time: string }
