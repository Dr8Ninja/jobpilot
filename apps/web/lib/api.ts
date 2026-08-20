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
  location_kind: string;
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
  location_kind: string;
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

export type SkillGapRow = {
  skill: string;
  job_count: number;
  companies: string[];
  examples: { company: string; title: string; job_id: number }[];
};

/** The queue is India + remote by default; overseas roles live in their own tab. */
export const PREFERRED_LOCATIONS = "india,remote";

const BASE = process.env.JOBPILOT_API_URL ?? "http://127.0.0.1:8000";

/** These calls run on the Next server during rendering, so the token is read
 *  from the server environment and never reaches the browser. The dashboard's
 *  own action buttons go through `app/api/[...path]/route.ts` for the same
 *  reason. */
function authHeaders(): HeadersInit {
  const token = process.env.JOBPILOT_API_TOKEN;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function raw(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(`${BASE}${path}`, {
    cache: "no-store",
    ...init,
    headers: { ...authHeaders(), ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail ?? `Request failed: ${response.status}`);
  }
  return response;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return (await raw(path, init)).json() as Promise<T>;
}

/** The API caps a page; asking for the cap keeps today's queue whole. */
export const QUEUE_PAGE_SIZE = 500;

/** `total` is the unpaginated count, so a truncated page can say so rather than
 *  looking like a queue that quietly got shorter. */
export async function getQueue(
  status?: string,
  location = PREFERRED_LOCATIONS,
  limit = QUEUE_PAGE_SIZE,
): Promise<{ cards: QueueCard[]; total: number }> {
  const query = new URLSearchParams();
  if (status) query.set("status", status);
  if (location) query.set("location", location);
  query.set("limit", String(limit));
  const response = await raw(`/api/queue?${query.toString()}`);
  const cards = (await response.json()) as QueueCard[];
  const total = Number(response.headers.get("X-Total-Count") ?? cards.length);
  return { cards, total };
}
export const getCounts = (location = PREFERRED_LOCATIONS) =>
  request<StatusCount[]>(
    `/api/queue/counts${location ? `?location=${encodeURIComponent(location)}` : ""}`,
  );
export const getSkillGaps = (minJobs = 1) =>
  request<SkillGapRow[]>(`/api/skill-gaps?min_jobs=${minJobs}`);

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
