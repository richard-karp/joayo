import type { Job, LeaderboardEntry, Place, Rating } from "@/types";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return res.json() as Promise<T>;
}

const EXTRACT_CODE_KEY = "joayo_extract_code";

// The extraction gate (X-Extract-Secret) — read from the passed value or the
// browser-stored access code. Empty when unset (local dev leaves the gate open).
function extractHeaders(secret?: string): Record<string, string> {
  const code =
    secret ??
    (typeof window !== "undefined" ? localStorage.getItem(EXTRACT_CODE_KEY) ?? "" : "");
  return code ? { "X-Extract-Secret": code } : {};
}

export async function submitExtract(formData: FormData, secret?: string): Promise<{ job_id: string }> {
  return request("/api/extract", { method: "POST", body: formData, headers: extractHeaders(secret) });
}

export async function getJob(jobId: string): Promise<Job> {
  return request(`/api/jobs/${jobId}`);
}

// Set (or clear, with null) the caller's rating. Rating a place marks it visited
// and clears any "want to go" flag.
export async function ratePlace(placeId: string, rating: Rating | null): Promise<Place> {
  return request(`/api/places/${placeId}/rating`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rating }),
  });
}

// Toggle the "want to go" wishlist flag.
export async function setWantToGo(placeId: string, wantToGo: boolean): Promise<Place> {
  return request(`/api/places/${placeId}/want-to-go`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ want_to_go: wantToGo }),
  });
}

export async function getLeaderboard(category?: string | null): Promise<LeaderboardEntry[]> {
  const qs = category ? `?category=${encodeURIComponent(category)}` : "";
  return request(`/api/leaderboard${qs}`);
}

export async function getPlaces(
  params?: {
    country?: string; city?: string; neighborhood?: string; subcategory?: string;
    label?: string; q?: string; rated?: boolean; want_to_go?: boolean; sort?: "new";
  }
): Promise<Place[]> {
  const qs = new URLSearchParams();
  if (params?.country) qs.set("country", params.country);
  if (params?.city) qs.set("city", params.city);
  if (params?.neighborhood) qs.set("neighborhood", params.neighborhood);
  if (params?.subcategory) qs.set("subcategory", params.subcategory);
  if (params?.label) qs.set("label", params.label);
  if (params?.q) qs.set("q", params.q);
  if (params?.rated) qs.set("rated", "true");
  if (params?.want_to_go) qs.set("want_to_go", "true");
  if (params?.sort) qs.set("sort", params.sort);
  const query = qs.toString() ? `?${qs}` : "";
  return request(`/api/places${query}`);
}

export async function getPlace(placeId: string): Promise<Place> {
  return request(`/api/places/${placeId}`);
}

export async function getFilters(): Promise<{
  countries: { name: string; place_count: number }[];
  cities: { name: string; country: string; place_count: number }[];
  neighborhoods: { name: string; city: string; place_count: number }[];
  subcategories: { name: string; category: string; place_count: number }[];
}> {
  return request("/api/filters");
}

export async function cancelJob(jobId: string): Promise<void> {
  await request(`/api/jobs/${jobId}/cancel`, { method: "POST" });
}

export async function resumeJob(jobId: string): Promise<void> {
  await request(`/api/jobs/${jobId}/resume`, { method: "POST", headers: extractHeaders() });
}

export function exportUrl(jobId: string): string {
  return `${BASE}/api/export/${jobId}`;
}

export function exportAllUrl(country?: string | null): string {
  const qs = country ? `?country=${encodeURIComponent(country)}` : "";
  return `${BASE}/api/export${qs}`;
}
