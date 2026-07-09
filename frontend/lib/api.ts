import type { Job, LeaderboardEntry, Place } from "@/types";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export async function submitExtract(formData: FormData): Promise<{ job_id: string }> {
  return request("/api/extract", { method: "POST", body: formData });
}

export async function getJob(jobId: string): Promise<Job> {
  return request(`/api/jobs/${jobId}`);
}

export async function voteOnPlace(
  placeId: string,
  vote: "up" | "down" | null
): Promise<Place> {
  return request(`/api/places/${placeId}/vote`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ vote }),
  });
}

export async function getLeaderboard(category?: string | null): Promise<LeaderboardEntry[]> {
  const qs = category ? `?category=${encodeURIComponent(category)}` : "";
  return request(`/api/leaderboard${qs}`);
}

export async function getPlaces(
  params?: { country?: string; city?: string; subcategory?: string; label?: string; q?: string }
): Promise<Place[]> {
  const qs = new URLSearchParams();
  if (params?.country) qs.set("country", params.country);
  if (params?.city) qs.set("city", params.city);
  if (params?.subcategory) qs.set("subcategory", params.subcategory);
  if (params?.label) qs.set("label", params.label);
  if (params?.q) qs.set("q", params.q);
  const query = qs.toString() ? `?${qs}` : "";
  return request(`/api/places${query}`);
}

export async function getFilters(): Promise<{
  countries: { name: string; place_count: number }[];
  cities: { name: string; country: string; place_count: number }[];
  subcategories: { name: string; category: string; place_count: number }[];
}> {
  return request("/api/filters");
}

export async function cancelJob(jobId: string): Promise<void> {
  await request(`/api/jobs/${jobId}/cancel`, { method: "POST" });
}

export async function resumeJob(jobId: string): Promise<void> {
  await request(`/api/jobs/${jobId}/resume`, { method: "POST" });
}

export function exportUrl(jobId: string): string {
  return `${BASE}/api/export/${jobId}`;
}

export function exportAllUrl(country?: string | null): string {
  const qs = country ? `?country=${encodeURIComponent(country)}` : "";
  return `${BASE}/api/export${qs}`;
}
