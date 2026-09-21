export type Category = 'BL_COMPARISON' | 'SI_REQUEST' | 'INVOICE_QUERY' | 'GENERAL' | 'SPAM'
export type Status = 'OK' | 'MISMATCH' | 'NEEDS_REVIEW'
export type ReviewReason = 'missing_attachment' | 'wrong_doc_type' | 'unreadable' | 'missing_value' | null

export interface Evidence { value: unknown; snippet: string; line_number: number; confidence: number }
export interface DocFields {
  doc_type: string
  is_scanned: boolean
  is_legible: boolean
  raw_text: string
  evidence_spans: Record<string, Evidence>
}
export interface CompareRow {
  key: string
  label: string
  si: string | number | null
  bl: string | number | null
  match: boolean
  missing: boolean
  si_evidence: Evidence | null
  bl_evidence: Evidence | null
}
export interface Meta { booking: string | null; vessel: string | null; oc_no: string | null; carrier: string }
export interface Resolution { action: string; at: string; block?: number; [k: string]: unknown }

export interface Amendment {
  status: 'sent' | 'confirmed'
  at: string
  recipient: string
  subject: string
  block: number | null
  auto: boolean
  body?: string
  reason?: string
  fields?: { key: string; label: string; si: string | number | null; bl: string | number | null }[]
}
export interface CaseInfo { decision: string; reason: string; can_send?: boolean; block_reason?: string | null }

export interface EmailRow {
  id: string
  shipment: string
  sender: string
  subject: string
  category: Category
  status: Status
  review_reason: ReviewReason
  defect_fields: string[]
  confidence: number
  attachments: string[]
  meta: Meta
  resolution?: Resolution | null
  amendment?: Amendment | null
  case?: CaseInfo | null
}
export interface EmailDetail extends EmailRow {
  body: string
  si: DocFields | null
  bl: DocFields | null
  comparison: CompareRow[]
}
export interface Summary {
  total: number
  categories: Record<string, number>
  comparisons: number
  ok: number
  mismatch: number
  needs_review: number
  defect_fields: Record<string, number>
  avg_verify_ms?: number
}
export interface AuditBlock {
  index: number
  timestamp: string
  actor: string
  email_id: string
  action: string
  details: Record<string, unknown>
  previous_hash: string
  block_hash: string
}
export interface CopilotReply {
  kind: 'help' | 'not_comparison' | 'review' | 'clear' | 'mismatch'
  shipment?: string
  email?: string
  message?: string
  category?: string
  reason?: string
  confidence?: number
  recommendation?: string
  issues?: CompareRow[]
  evidence?: string[]
}
