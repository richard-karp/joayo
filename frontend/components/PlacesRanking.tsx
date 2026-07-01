'use client';

import { Badge } from "@/components/ui/badge";
import type { Category, Place } from "@/types";
import { CATEGORY_COLORS, CATEGORY_LABELS } from "@/types";

interface Props {
  places: Place[];
  selectedPlaceId: string | null;
  onPlaceClick: (placeId: string) => void;
}

export default function PlacesRanking({ places, selectedPlaceId, onPlaceClick }: Props) {
  const ranked = [...places]
    .filter((p) => p.is_place)
    .sort((a, b) => b.source_urls.length - a.source_urls.length || b.vote_score - a.vote_score);

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
          {ranked.map((place, i) => (
            <tr
              key={place.id}
              className={`cursor-pointer transition-colors hover:bg-zinc-50 ${
                selectedPlaceId === place.id ? "bg-zinc-100" : ""
              }`}
              onClick={() => onPlaceClick(place.id)}
            >
              <td className="py-2 pr-2 text-zinc-400 tabular-nums">{i + 1}</td>
              <td className="py-2 font-medium text-zinc-900">
                <span className="block truncate max-w-[180px]">{place.location_name}</span>
                {place.city && (
                  <span className="text-xs text-zinc-400 font-normal">{place.city}</span>
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
          ))}
        </tbody>
      </table>
    </div>
  );
}
