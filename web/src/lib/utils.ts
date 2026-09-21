import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import { useEffect, useRef, useState } from 'react'
import type { Category, EmailRow, ReviewReason } from './types'

export const cn = (...i: ClassValue[]) => twMerge(clsx(i))

export const CATEGORY_LABEL: Record<Category, string> = {
  BL_COMPARISON: 'DOCUMENT CHECK',
  SI_REQUEST: 'SI REQUEST',
  INVOICE_QUERY: 'INVOICE',
  GENERAL: 'GENERAL',
  SPAM: 'SPAM',
}

export const REASON_LABEL: Record<Exclude<ReviewReason, null>, string> = {
  missing_attachment: 'Missing attachment',
  wrong_doc_type: 'Wrong document type',
  unreadable: 'Unreadable file',
  missing_value: 'Missing field value',
}

export const REASON_DETAIL: Record<Exclude<ReviewReason, null>, string> = {
  missing_attachment: 'The email requests a comparison but one or both required documents are absent.',
  wrong_doc_type: 'An attached file is not a Shipping Instruction or Bill of Lading.',
  unreadable: 'The file is corrupted, illegible, or failed the legibility guardrail.',
  missing_value: 'One of the seven comparison fields could not be found with sufficient confidence.',
}

export const FIELD_LABEL: Record<string, string> = {
  shipper: 'Shipper',
  consignee: 'Consignee',
  notify_party: 'Notify Party',
  port_of_loading: 'Port of Loading',
  port_of_discharge: 'Port of Discharge',
  container_count: 'Container Count',
  gross_weight_kg: 'Gross Weight',
}

export function fmtValue(key: string, v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === '') return '—'
  if (key === 'gross_weight_kg' && typeof v === 'number') return `${v.toLocaleString('en-US')} kg`
  if (key === 'container_count') return `${v} ${Number(v) === 1 ? 'container' : 'containers'}`
  return String(v)
}

export function fmtField(key: string, v: string | number | null | undefined): string {
  return fmtValue(key, v)
}

export function explain(row: { key: string; label: string; si: unknown; bl: unknown }): string {
  const s = fmtValue(row.key, row.si as never)
  const b = fmtValue(row.key, row.bl as never)
  if (row.key === 'container_count') {
    const d = Number(row.bl) - Number(row.si)
    const more = d > 0
    return `The Bill of Lading declares ${Math.abs(d)} ${more ? 'additional' : 'fewer'} container${Math.abs(d) === 1 ? '' : 's'} compared with the Shipping Instruction (${s} vs ${b}).`
  }
  if (row.key === 'gross_weight_kg') {
    const d = Number(row.bl) - Number(row.si)
    return `Gross weight differs by ${Math.abs(d).toLocaleString('en-US')} kg — the Bill of Lading is ${d > 0 ? 'heavier' : 'lighter'} than instructed.`
  }
  return `The ${row.label.toLowerCase()} on the Bill of Lading (${b}) does not match the Shipping Instruction (${s}).`
}

export function senderName(s: string) {
  return s.split('@')[0].replace(/[._]/g, ' ')
}
export function senderDomain(s: string) {
  return s.split('@')[1] ?? ''
}

export function clock(d = new Date()) {
  return d.toLocaleTimeString('en-GB', { hour12: false })
}

export function greeting(d = new Date()) {
  const h = d.getHours()
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
}

export const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

export function useCountUp(target: number, duration = 1100, decimals = 0) {
  const [v, setV] = useState(0)
  const raf = useRef(0)
  useEffect(() => {
    const start = performance.now()
    const from = 0
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / duration)
      const e = 1 - Math.pow(1 - p, 4)
      setV(from + (target - from) * e)
      if (p < 1) raf.current = requestAnimationFrame(tick)
    }
    raf.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf.current)
  }, [target, duration])
  return decimals ? v.toFixed(decimals) : Math.round(v).toLocaleString('en-US')
}

export function isFlagged(r: EmailRow) {
  return r.category === 'BL_COMPARISON' && r.status !== 'OK'
}

export function pct(n: number, d: number, dp = 1) {
  return d ? ((n / d) * 100).toFixed(dp) : '0.0'
}
