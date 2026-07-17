'use client';

import Link from "next/link";
import MapLinks from "@/components/MapLinks";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import type { Category, Place } from "@/types";
import { CATEGORY_COLORS, CATEGORY_LABELS } from "@/types";

interface Props {
  place: Place;
  onClose: () => void;
  activeLabel?: string | null;
  onLabelClick?: (label: string) => void;
  className?: string;
}

export default function PlaceCard({ place, onClose, activeLabel, onLabelClick, className = "mt-4" }: Props) {
  const mentionCount = place.source_urls.length;

  return (
    <Card className={className}>
      <CardHeader className="pb-2 pt-4">
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 min-w-0">
            <p className="font-semibold text-zinc-900 leading-tight">{place.location_name}</p>
            <div className="flex items-center gap-1.5 mt-1 flex-wrap">
              {place.category && (
                <Badge className={`text-xs py-0 ${CATEGORY_COLORS[place.category as Category]}`}>
                  {CATEGORY_LABELS[place.category as Category]}
                </Badge>
              )}
              {place.subcategory && (
                <span className="text-xs text-zinc-400">{place.subcategory.replace(/_/g, " ")}</span>
              )}
            </div>
            <div className="flex gap-3 text-xs text-zinc-500 mt-0.5">
              {place.city && <span>{place.city}</span>}
              <span>{mentionCount} mention{mentionCount !== 1 ? "s" : ""}</span>
              <span>{place.all_authors.length} creator{place.all_authors.length !== 1 ? "s" : ""}</span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-zinc-400 hover:text-zinc-700 transition-colors text-lg leading-none shrink-0"
          >
            ×
          </button>
        </div>
      </CardHeader>

      <CardContent className="space-y-3 max-h-80 overflow-y-auto pb-4">
        {place.summary && (
          <p className="text-sm text-zinc-700 leading-relaxed">{place.summary}</p>
        )}
        {place.insider_tips && (
          <p className="text-xs text-zinc-500 italic">{place.insider_tips}</p>
        )}
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <MapLinks place={place} />
          <Link href={`/places/${place.id}`} className="text-xs text-blue-600 hover:underline shrink-0">
            View details ↗
          </Link>
        </div>
        {place.labels && place.labels.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {place.labels.map((label) => {
              const isActive = activeLabel === label;
              return (
                <button
                  key={label}
                  type="button"
                  onClick={() => onLabelClick?.(label)}
                  disabled={!onLabelClick}
                  title={onLabelClick ? `Filter by "${label}"` : undefined}
                  className={`px-2 py-0.5 rounded-full text-xs transition-colors ${
                    isActive
                      ? "bg-zinc-900 text-white"
                      : onLabelClick
                        ? "bg-zinc-100 text-zinc-600 hover:bg-zinc-200 cursor-pointer"
                        : "bg-zinc-100 text-zinc-600"
                  }`}
                >
                  {label}
                </button>
              );
            })}
          </div>
        )}

        {place.all_authors.length > 0 && (
          <div className="border-t border-zinc-100 pt-3">
            <p className="text-xs text-zinc-400 mb-1.5">Mentioned by</p>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              {place.all_authors.map((a, i) => {
                const handle = a.profile_url
                  ? a.profile_url.replace(/\/$/, "").split("/").pop()
                  : /^[\w.]+$/.test(a.username) ? a.username : null;
                const link = a.profile_url ?? (handle ? `https://www.instagram.com/${handle}/` : null);
                const key = a.platform_id || a.username || String(i);
                return link ? (
                  <a
                    key={key}
                    href={link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-blue-600 hover:underline"
                  >
                    @{handle ?? a.username}
                  </a>
                ) : (
                  <span key={key} className="text-xs text-zinc-500">
                    {a.username}
                  </span>
                );
              })}
            </div>
          </div>
        )}

        {place.source_urls.length > 0 && (
          <div className="border-t border-zinc-100 pt-3 flex flex-wrap gap-2">
            {place.source_urls.map((url, i) => (
              <a
                key={url}
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-blue-500 hover:underline"
              >
                {place.source_urls.length > 1 ? `Post ${i + 1} ↗` : "Post ↗"}
              </a>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
