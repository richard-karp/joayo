'use client';

import dynamic from "next/dynamic";
import Link from "next/link";
import { use, useEffect, useState } from "react";
import MapLinks from "@/components/MapLinks";
import RatingControl from "@/components/RatingControl";
import ReviewPanel from "@/components/ReviewPanel";
import { Badge } from "@/components/ui/badge";
import { getPlace } from "@/lib/api";
import type { Category, Place } from "@/types";
import { CATEGORY_COLORS, CATEGORY_LABELS } from "@/types";

const Map = dynamic(() => import("@/components/Map"), { ssr: false });

export default function PlaceDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [place, setPlace] = useState<Place | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    getPlace(id)
      .then((p) => { setPlace(p); setStatus("ready"); })
      .catch(() => setStatus("error"));
  }, [id]);

  return (
    <div className="min-h-screen bg-zinc-50">
      <header className="bg-white border-b border-zinc-200 px-6 py-4">
        <Link href="/" className="text-sm text-zinc-500 hover:text-zinc-900 transition-colors">
          ← Back to all places
        </Link>
      </header>

      <main className="max-w-screen-md mx-auto px-6 py-8">
        {status === "loading" && (
          <div className="space-y-3 animate-pulse">
            <div className="h-7 w-1/2 bg-zinc-200 rounded" />
            <div className="h-64 bg-zinc-100 rounded-xl" />
          </div>
        )}
        {status === "error" && <p className="text-sm text-zinc-500">Place not found.</p>}

        {status === "ready" && place && (
          <div className="space-y-5">
            <div>
              <h1 className="text-2xl font-bold text-zinc-900 leading-tight">{place.location_name}</h1>
              <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                {place.category && (
                  <Badge className={`text-xs ${CATEGORY_COLORS[place.category as Category]}`}>
                    {CATEGORY_LABELS[place.category as Category]}
                  </Badge>
                )}
                {place.subcategory && (
                  <span className="text-xs text-zinc-400">{place.subcategory.replace(/_/g, " ")}</span>
                )}
                {place.needs_review && (
                  <span title="Location is a best guess — not yet verified"
                        className="text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-700">
                    ⚠ best guess
                  </span>
                )}
              </div>
              <div className="flex gap-3 text-sm text-zinc-500 mt-1">
                {[place.neighborhood, place.city, place.country].filter(Boolean).join(" · ")}
              </div>
            </div>

            <div className="flex items-center justify-between gap-3 flex-wrap">
              <RatingControl place={place} onUpdate={setPlace} />
              <MapLinks place={place} />
            </div>

            <ReviewPanel place={place} onUpdate={setPlace} />

            {place.lat != null && place.lng != null && (
              <div className="h-72 rounded-xl overflow-hidden shadow-sm border border-zinc-200">
                <Map places={[place]} highlightedPlaceIds={[place.id]} />
              </div>
            )}

            {place.summary && (
              <p className="text-zinc-700 leading-relaxed">{place.summary}</p>
            )}
            {place.insider_tips && (
              <p className="text-sm text-zinc-500 italic">💡 {place.insider_tips}</p>
            )}

            {place.labels && place.labels.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {place.labels.map((l) => (
                  <span key={l} className="px-2 py-0.5 rounded-full text-xs bg-zinc-100 text-zinc-600">{l}</span>
                ))}
              </div>
            )}

            {place.all_authors.length > 0 && (
              <div className="border-t border-zinc-100 pt-4">
                <p className="text-xs text-zinc-400 mb-2">Mentioned by</p>
                <div className="flex flex-wrap gap-x-3 gap-y-1">
                  {place.all_authors.map((a, i) => {
                    const handle = a.profile_url
                      ? a.profile_url.replace(/\/$/, "").split("/").pop()
                      : /^[\w.]+$/.test(a.username) ? a.username : null;
                    const link = a.profile_url ?? (handle ? `https://www.instagram.com/${handle}/` : null);
                    const key = a.platform_id || a.username || String(i);
                    return link ? (
                      <a key={key} href={link} target="_blank" rel="noopener noreferrer"
                         className="text-sm text-blue-600 hover:underline">@{handle ?? a.username}</a>
                    ) : (
                      <span key={key} className="text-sm text-zinc-500">{a.username}</span>
                    );
                  })}
                </div>
              </div>
            )}

            {place.source_urls.length > 0 && (
              <div className="border-t border-zinc-100 pt-4 flex flex-wrap gap-3">
                {place.source_urls.map((url, i) => (
                  <a key={url} href={url} target="_blank" rel="noopener noreferrer"
                     className="text-sm text-blue-500 hover:underline">
                    {place.source_urls.length > 1 ? `Post ${i + 1} ↗` : "View source post ↗"}
                  </a>
                ))}
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
