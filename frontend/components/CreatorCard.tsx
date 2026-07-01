'use client';

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import type { Category, Place } from "@/types";
import { CATEGORY_COLORS, CATEGORY_LABELS } from "@/types";

interface Props {
  username: string;
  places: Place[];
  onClose: () => void;
}

function getHandle(username: string, profileUrl: string | null | undefined): string | null {
  if (profileUrl) {
    const slug = profileUrl.replace(/\/$/, "").split("/").pop();
    if (slug && /^[\w.]+$/.test(slug)) return slug;
  }
  if (/^[\w.]+$/.test(username)) return username;
  return null;
}

export default function CreatorCard({ username, places, onClose }: Props) {
  const authored = places.filter((p) => p.primary_author === username);
  const mentioned = places.filter(
    (p) => p.primary_author !== username && p.all_authors.some((a) => a.username === username)
  );

  const profileUrl =
    authored[0]?.primary_author_profile_url ??
    places
      .find((p) => p.all_authors.some((a) => a.username === username))
      ?.all_authors.find((a) => a.username === username)?.profile_url ??
    null;

  const handle = getHandle(username, profileUrl);
  const isDisplayName = !handle || handle !== username;
  const totalPosts = new Set(authored.flatMap((p) => p.source_urls)).size;

  return (
    <Card className="mt-4">
      <CardHeader className="pb-2 pt-4">
        <div className="flex items-start justify-between gap-2">
          <div>
            {/* Handle (or display name if no handle known) as the primary identifier */}
            <div className="font-semibold text-zinc-900">
              {profileUrl ? (
                <a
                  href={profileUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-600 hover:underline"
                >
                  {handle ? `@${handle}` : username} ↗
                </a>
              ) : handle ? (
                <span>@{handle}</span>
              ) : (
                <span>{username}</span>
              )}
            </div>
            {/* Display name below handle, like Instagram's profile UI */}
            {isDisplayName && handle && (
              <p className="text-xs text-zinc-500 mt-0.5">{username}</p>
            )}
            <div className="flex gap-3 text-xs text-zinc-500 mt-0.5">
              <span>{authored.length} place{authored.length !== 1 ? "s" : ""}</span>
              <span>{totalPosts} post{totalPosts !== 1 ? "s" : ""}</span>
              {mentioned.length > 0 && (
                <span>tagged in {mentioned.length} more</span>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-zinc-400 hover:text-zinc-700 transition-colors text-lg leading-none"
          >
            ×
          </button>
        </div>
      </CardHeader>

      <CardContent className="space-y-0 max-h-72 overflow-y-auto pb-4">
        {authored.length === 0 ? (
          <p className="text-sm text-zinc-400">No attributed places.</p>
        ) : (
          authored.map((place) => (
            <div
              key={place.id}
              className="flex items-start justify-between gap-3 py-2 border-b border-zinc-100 last:border-0"
            >
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-zinc-900 truncate">
                  {place.location_name}
                </p>
                <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
                  {place.category && (
                    <Badge
                      className={`text-xs py-0 ${CATEGORY_COLORS[place.category as Category]}`}
                    >
                      {CATEGORY_LABELS[place.category as Category]}
                    </Badge>
                  )}
                  {place.subcategory && (
                    <span className="text-xs text-zinc-400">
                      {place.subcategory.replace(/_/g, " ")}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex flex-col items-end gap-0.5 shrink-0">
                {place.source_urls.map((url, i) => (
                  <a
                    key={url}
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-blue-500 hover:underline whitespace-nowrap"
                  >
                    {place.source_urls.length > 1 ? `Post ${i + 1} ↗` : "Post ↗"}
                  </a>
                ))}
              </div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}
