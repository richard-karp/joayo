'use client';

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import RatingControl from "@/components/RatingControl";
import type { Category, Place } from "@/types";
import { CATEGORY_COLORS, CATEGORY_LABELS } from "@/types";

interface Props {
  places: Place[];
  activeCategory: Category | null;
  authorFilter: string | null;
  onPlaceHover?: (placeId: string | null) => void;
}

function ItemCard({
  place,
  onUpdate,
  onHover,
  isSelected,
  associatedPlaces,
  associatedThings,
  onSelectItem,
}: {
  place: Place;
  onUpdate: (p: Place) => void;
  onHover?: (id: string | null) => void;
  isSelected?: boolean;
  associatedPlaces?: Place[];
  associatedThings?: Place[];
  onSelectItem?: (id: string) => void;
}) {
  function jumpTo(id: string) {
    onSelectItem?.(id);
    document.getElementById(`item-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  return (
    <Card
      id={`item-${place.id}`}
      className={`cursor-default transition-all ${
        isSelected
          ? "ring-2 ring-blue-400 shadow-md"
          : "hover:shadow-md"
      }`}
      onMouseEnter={() => onHover?.(place.id)}
      onMouseLeave={() => onHover?.(null)}
    >
      <CardContent className="pt-4 space-y-2">
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 min-w-0">
            <h3 className="font-semibold text-zinc-900 truncate">{place.location_name}</h3>
            <div className="flex items-center gap-2 mt-1 flex-wrap">
              {place.category && (
                <Badge className={`text-xs ${CATEGORY_COLORS[place.category as Category]}`}>
                  {CATEGORY_LABELS[place.category as Category]}
                </Badge>
              )}
              {place.subcategory && (
                <span className="text-xs text-zinc-400">{place.subcategory.replace(/_/g, " ")}</span>
              )}
              {!place.is_place && (
                <Badge variant="outline" className="text-xs text-zinc-500 border-zinc-300">
                  no fixed location
                </Badge>
              )}
              {place.transcript_missing && (
                <Badge variant="outline" className="text-xs text-amber-600 border-amber-300">
                  caption only
                </Badge>
              )}
            </div>
          </div>
          <RatingControl place={place} onUpdate={onUpdate} />
        </div>

        {place.summary && (
          <p className="text-sm text-zinc-600 leading-relaxed">{place.summary}</p>
        )}

        {place.insider_tips && (
          <p className="text-xs text-zinc-500 italic">💡 {place.insider_tips}</p>
        )}

        {place.labels && place.labels.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {place.labels.map((label) => (
              <span key={label} className="text-xs bg-zinc-100 text-zinc-600 px-2 py-0.5 rounded-full">
                {label}
              </span>
            ))}
          </div>
        )}

        {/* Thing → Places: where you can find this item */}
        {associatedPlaces && associatedPlaces.length > 0 && (
          <div className="pt-2 border-t border-zinc-100">
            <p className="text-xs text-zinc-500 mb-1.5">Find at:</p>
            <div className="flex flex-wrap gap-1.5">
              {associatedPlaces.map((p) => (
                <button
                  key={p.id}
                  onClick={() => jumpTo(p.id)}
                  className="text-xs bg-blue-50 text-blue-700 border border-blue-200 px-2 py-0.5 rounded-full hover:bg-blue-100 transition-colors"
                >
                  {p.location_name}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Place → Things: what you can find/do here */}
        {associatedThings && associatedThings.length > 0 && (
          <div className="pt-2 border-t border-zinc-100">
            <p className="text-xs text-zinc-500 mb-1.5">Also here:</p>
            <div className="flex flex-wrap gap-1.5">
              {associatedThings.map((t) => (
                <button
                  key={t.id}
                  onClick={() => jumpTo(t.id)}
                  className="text-xs bg-amber-50 text-amber-700 border border-amber-200 px-2 py-0.5 rounded-full hover:bg-amber-100 transition-colors"
                >
                  {t.location_name}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="pt-1 border-t border-zinc-100 text-xs text-zinc-500 space-y-0.5">
          {place.primary_author && (
            <p>
              First posted by{" "}
              <span className="font-medium text-zinc-700">@{place.primary_author}</span>
            </p>
          )}
          {place.all_authors.length > 1 && (
            <p>
              Also posted by{" "}
              {place.all_authors
                .filter((a) => a.username !== place.primary_author)
                .map((a) => `@${a.username}`)
                .join(", ")}
            </p>
          )}
          {place.source_urls.length > 0 && (
            <div className="flex flex-wrap gap-2 pt-0.5">
              {place.source_urls.map((url, i) => (
                <a
                  key={url}
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-500 hover:underline"
                >
                  Source {place.source_urls.length > 1 ? i + 1 : ""}
                </a>
              ))}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export default function PlaceList({ places, activeCategory, authorFilter, onPlaceHover }: Props) {
  const [localPlaces, setLocalPlaces] = useState<Place[]>(places);
  const [showThings, setShowThings] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  if (places !== localPlaces && places.length !== localPlaces.length) {
    setLocalPlaces(places);
  }

  function handleMarkUpdate(updated: Place) {
    setLocalPlaces((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
  }

  const filtered = localPlaces.filter((p) => {
    if (activeCategory && p.category !== activeCategory) return false;
    if (authorFilter && p.primary_author !== authorFilter) return false;
    return true;
  });

  const mappedPlaces = filtered.filter((p) => p.is_place);
  const things = filtered.filter((p) => !p.is_place);

  // Build lookup maps for bidirectional cross-referencing
  const placesByNameLower = new Map<string, Place>();
  mappedPlaces.forEach((p) => {
    if (p.location_name) placesByNameLower.set(p.location_name.toLowerCase(), p);
  });

  const thingsByVenueLower = new Map<string, Place[]>();
  things.forEach((t) => {
    if (t.venue) {
      const key = t.venue.toLowerCase();
      if (!thingsByVenueLower.has(key)) thingsByVenueLower.set(key, []);
      thingsByVenueLower.get(key)!.push(t);
    }
  });

  // Selecting an item from an "Also here" chip opens the Things section if needed
  function selectItem(id: string) {
    const isAThing = things.some((t) => t.id === id);
    if (isAThing) setShowThings(true);
    setSelectedId(id);
  }

  if (filtered.length === 0) {
    return (
      <p className="text-sm text-zinc-400 py-4">
        {localPlaces.length === 0 ? "No results extracted yet." : "No items match the current filter."}
      </p>
    );
  }

  return (
    <div className="space-y-6">
      {/* Places — shown on map */}
      {mappedPlaces.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wide">
            Places ({mappedPlaces.length})
          </h2>
          {mappedPlaces.map((place) => {
            const associatedThings = place.location_name
              ? thingsByVenueLower.get(place.location_name.toLowerCase())
              : undefined;
            return (
              <ItemCard
                key={place.id}
                place={place}
                onUpdate={handleMarkUpdate}
                onHover={onPlaceHover}
                isSelected={selectedId === place.id}
                associatedThings={associatedThings}
                onSelectItem={selectItem}
              />
            );
          })}
        </section>
      )}

      {/* Things — dishes, products, tips, etc. */}
      {things.length > 0 && (
        <section className="space-y-3">
          <button
            className="flex items-center gap-2 text-sm font-semibold text-zinc-500 uppercase tracking-wide hover:text-zinc-700"
            onClick={() => setShowThings((v) => !v)}
          >
            Things to know ({things.length})
            <span className="text-zinc-400">{showThings ? "▲" : "▼"}</span>
          </button>
          {showThings && things.map((thing) => {
            const venueKey = thing.venue?.toLowerCase();
            const associatedPlaces = venueKey
              ? ([placesByNameLower.get(venueKey)].filter(Boolean) as Place[])
              : undefined;
            return (
              <ItemCard
                key={thing.id}
                place={thing}
                onUpdate={handleMarkUpdate}
                isSelected={selectedId === thing.id}
                associatedPlaces={associatedPlaces?.length ? associatedPlaces : undefined}
                onSelectItem={selectItem}
              />
            );
          })}
        </section>
      )}
    </div>
  );
}
