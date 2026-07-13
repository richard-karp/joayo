'use client';

import { Fragment, useState } from "react";
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

const CATEGORY_ORDER: Category[] = ["eat", "see_visit", "do", "shop", "service", "guide"];

export default function CategoryView({ places, expandedIds, onPlaceClick, activeLabel, onLabelClick }: Props) {
  const [activeCategory, setActiveCategory] = useState<Category | null>(null);
  const expanded = new Set(expandedIds);

  const placePlaces = places.filter((p) => p.is_place);

  const countsByCategory = CATEGORY_ORDER.reduce<Record<string, number>>((acc, cat) => {
    acc[cat] = placePlaces.filter((p) => p.category === cat).length;
    return acc;
  }, {});

  const filtered = placePlaces
    .filter((p) => !activeCategory || p.category === activeCategory)
    .sort((a, b) => b.source_urls.length - a.source_urls.length);

  return (
    <div>
      {/* Category pills */}
      <div className="flex items-center gap-2 flex-wrap mb-4">
        <button
          onClick={() => setActiveCategory(null)}
          className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
            activeCategory === null
              ? "bg-zinc-900 text-white border-zinc-900"
              : "bg-white text-zinc-600 border-zinc-200 hover:border-zinc-400"
          }`}
        >
          All
          <span className="ml-1 opacity-60">{placePlaces.length}</span>
        </button>
        {CATEGORY_ORDER.filter((cat) => countsByCategory[cat] > 0).map((cat) => (
          <button
            key={cat}
            onClick={() => setActiveCategory((prev) => (prev === cat ? null : cat))}
            className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
              activeCategory === cat
                ? "bg-zinc-900 text-white border-zinc-900"
                : "bg-white text-zinc-600 border-zinc-200 hover:border-zinc-400"
            }`}
          >
            {CATEGORY_LABELS[cat]}
            <span className="ml-1 opacity-60">{countsByCategory[cat]}</span>
          </button>
        ))}
      </div>

      {/* Place list */}
      <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wide mb-3">
        {activeCategory ? CATEGORY_LABELS[activeCategory] : "All categories"}
      </h2>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-zinc-400 text-xs">
            <th className="text-left pb-2 font-medium">Place</th>
            {!activeCategory && (
              <th className="text-left pb-2 font-medium hidden sm:table-cell">Category</th>
            )}
            <th className="text-right pb-2 font-medium">Mentions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100">
          {filtered.map((place) => {
            const isExpanded = expanded.has(place.id);
            return (
              <Fragment key={place.id}>
                <tr
                  className={`cursor-pointer transition-colors hover:bg-zinc-50 ${
                    isExpanded ? "bg-zinc-100" : ""
                  }`}
                  onClick={() => onPlaceClick(place.id)}
                >
                  <td className="py-2 font-medium text-zinc-900">
                    <span className="flex items-center gap-1.5">
                      <span
                        className={`text-zinc-400 text-[0.7rem] transition-transform ${isExpanded ? "rotate-90" : ""}`}
                        aria-hidden
                      >
                        ▸
                      </span>
                      <span className="truncate max-w-[200px]">{place.location_name}</span>
                    </span>
                    {place.city && (
                      <span className="block pl-4 text-xs text-zinc-400 font-normal">{place.city}</span>
                    )}
                  </td>
                  {!activeCategory && (
                    <td className="py-2 hidden sm:table-cell">
                      {place.category && (
                        <Badge className={`text-xs py-0 ${CATEGORY_COLORS[place.category as Category]}`}>
                          {CATEGORY_LABELS[place.category as Category]}
                        </Badge>
                      )}
                    </td>
                  )}
                  <td className="py-2 text-right tabular-nums text-zinc-500">
                    {place.source_urls.length}
                  </td>
                </tr>
                {isExpanded && (
                  <tr>
                    <td colSpan={activeCategory ? 2 : 3} className="p-0">
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
