export type QueueCard = {
  application_id: number;
  job_id: number;
  company: string;
  title: string;
  location: string | null;
  salary: string | null;
  match_score: number | null;
  status: string;
  source: string;
  description_quality: string;
  apply_url: string;
  has_pdf: boolean;
  warning_count: number;
  posted_at: string | null;
  created_at: string;
};

export type GateNote = {
  rule: string;
  severity: string;
  detail: string;
  evidence: string;
};

export type BulletDiff = {
  employment_index: number;
  company: string;
  original: string;
  rewritten: string;
  skills_referenced: string[];
  changed: boolean;
};

export type QueueDetail = {
  application_id: number;
  job_id: number;
  company: string;
  title: string;
  location: string | null;
  salary: string | null;
  status: string;
  source: string;
  description_quality: string;
  apply_url: string;
  description: string;
  match_score: number | null;
  rationale: string | null;
  must_have_coverage: string[];
  keyword_gaps: string[];
  seniority_fit: string | null;
  summary: string;
  diffs: BulletDiff[];
  skills_ordered: string[];
  whitelist_passed: boolean;
  warnings: GateNote[];
  rejections: GateNote[];
  attempts: number;
  has_pdf: boolean;
  posted_at: string | null;
};

export type StatusCount = { status: string; count: number };

const BASE = process.env.JOBPILOT_API_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { cache: "no-store", ...init });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail ?? `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const getQueue = (status?: string) =>
  request<QueueCard[]>(`/api/queue${status ? `?status=${encodeURIComponent(status)}` : ""}`);
export const getCounts = () => request<StatusCount[]>("/api/queue/counts");

/** Human-readable posting age. Undated postings say so rather than guessing. */
export function postedAge(iso: string | null): string {
  if (!iso) return "date unknown";
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "1 day ago";
  if (days < 30) return `${days} days ago`;
  const months = Math.floor(days / 30);
  return months === 1 ? "1 month ago" : `${months} months ago`;
}
export const getCard = (id: number) => request<QueueDetail>(`/api/queue/${id}`);
