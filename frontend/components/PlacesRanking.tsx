'use client';

import { Fragment } from "react";
import { Badge } from "@/components/ui/badge";
import PlaceCard from "@/components/PlaceCard";
import type { Category, Place } from "@/types";
import { CATEGORY_COLORS, CATEGORY_LABELS } from "@/types";

interface Props {
  places: Place[];
  expandedIds: string[];
  onPlaceClick: (placeId: string) => void;
  activeLabel: string | null;
  onLabelClick: (label: string) => void;
}

const RATING_RANK: Record<string, number> = { double: 2, up: 1, down: -1 };
const RATING_EMOJI: Record<string, string> = { double: "👍👍", up: "👍", down: "👎" };
const ratingRank = (p: Place) => (p.my_rating ? RATING_RANK[p.my_rating] : 0);

export default function PlacesRanking({ places, expandedIds, onPlaceClick, activeLabel, onLabelClick }: Props) {
  const expanded = new Set(expandedIds);
  const ranked = [...places]
    .filter((p) => p.is_place)
    .sort((a, b) => b.source_urls.length - a.source_urls.length || ratingRank(b) - ratingRank(a));

  if (ranked.length === 0) {
    return <p className="text-sm text-zinc-400">No places yet.</p>;
  }

  return (
    <div>
      <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wide mb-3">
        Places by mentions
      </h2>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-zinc-400 text-xs">
            <th className="text-left pb-2 font-medium">#</th>
            <th className="text-left pb-2 font-medium">Place</th>
            <th className="text-left pb-2 font-medium hidden sm:table-cell">Category</th>
            <th className="text-right pb-2 font-medium">Mentions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100">
          {ranked.map((place, i) => {
            const isExpanded = expanded.has(place.id);
            return (
              <Fragment key={place.id}>
                <tr
                  className={`cursor-pointer transition-colors hover:bg-zinc-50 ${
                    isExpanded ? "bg-zinc-100" : ""
                  }`}
                  onClick={() => onPlaceClick(place.id)}
                >
                  <td className="py-2 pr-2 text-zinc-400 tabular-nums">{i + 1}</td>
                  <td className="py-2 font-medium text-zinc-900">
                    <span className="flex items-center gap-1.5">
                      <span
                        className={`text-zinc-400 text-[0.7rem] transition-transform ${isExpanded ? "rotate-90" : ""}`}
                        aria-hidden
                      >
                        ▸
                      </span>
                      <span className="truncate max-w-[180px]">{place.location_name}</span>
                      {place.my_rating && (
                        <span title="Your rating" className="shrink-0 text-xs">{RATING_EMOJI[place.my_rating]}</span>
                      )}
                      {place.want_to_go && (
                        <span title="On your want-to-go list" className="shrink-0 text-amber-500 text-xs">★</span>
                      )}
                      {place.needs_review && (
                        <span title="Location is a best guess — not yet verified" className="shrink-0 text-amber-600 text-xs">⚠</span>
                      )}
                    </span>
                    {place.city && (
                      <span className="block pl-4 text-xs text-zinc-400 font-normal">{place.city}</span>
                    )}
                  </td>
                  <td className="py-2 hidden sm:table-cell">
                    {place.category && (
                      <Badge className={`text-xs py-0 ${CATEGORY_COLORS[place.category as Category]}`}>
                        {CATEGORY_LABELS[place.category as Category]}
                      </Badge>
                    )}
                  </td>
                  <td className="py-2 text-right tabular-nums text-zinc-500">
                    {place.source_urls.length}
                  </td>
                </tr>
                {isExpanded && (
                  <tr>
                    <td colSpan={4} className="p-0">
                      <PlaceCard
                        place={place}
                        className="mb-3 mt-1"
                        activeLabel={activeLabel}
                        onLabelClick={onLabelClick}
                        onClose={() => onPlaceClick(place.id)}
                      />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
