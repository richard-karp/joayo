'use client';

import { useCallback, useState } from "react";
import MapGL, { Marker, Popup } from "react-map-gl/mapbox";
import "mapbox-gl/dist/mapbox-gl.css";
import type { Category, Place } from "@/types";
import { CATEGORY_COLORS, CATEGORY_LABELS } from "@/types";
import { voteOnPlace } from "@/lib/api";

const TOKEN = process.env.NEXT_PUBLIC_MAPBOX_TOKEN ?? "";

const PIN_COLORS: Record<Category, string> = {
  eat: "#f97316",
  see_visit: "#3b82f6",
  do: "#22c55e",
  shop: "#a855f7",
  service: "#ec4899",
  guide: "#eab308",
};

interface Props {
  places: Place[];
  highlightedPlaceId: string | null;
}

export default function Map({ places, highlightedPlaceId }: Props) {
  const [popup, setPopup] = useState<Place | null>(null);
  const [localPlaces, setLocalPlaces] = useState<Place[]>(places);

  if (places !== localPlaces && places.length !== localPlaces.length) {
    setLocalPlaces(places);
  }

  const mappable = localPlaces.filter((p) => p.lat != null && p.lng != null);

  const handleVote = useCallback(
    async (place: Place, vote: "up" | "down" | null) => {
      const next = place.current_vote === vote ? null : vote;
      try {
        const updated = await voteOnPlace(place.id, next);
        setLocalPlaces((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
        setPopup(updated);
      } catch {
        // silent — vote failure doesn't need a toast here
      }
    },
    []
  );

  if (!TOKEN) {
    return (
      <div className="flex items-center justify-center h-full rounded-xl border border-dashed border-zinc-300 bg-zinc-50 text-sm text-zinc-400">
        Set NEXT_PUBLIC_MAPBOX_TOKEN to enable the map.
      </div>
    );
  }

  const bounds = mappable.length > 0
    ? {
        longitude: mappable.reduce((s, p) => s + p.lng!, 0) / mappable.length,
        latitude: mappable.reduce((s, p) => s + p.lat!, 0) / mappable.length,
      }
    : { longitude: 126.978, latitude: 37.5665 }; // Seoul default

  return (
    <MapGL
      mapboxAccessToken={TOKEN}
      initialViewState={{
        ...bounds,
        zoom: mappable.length === 0 ? 10 : 11,
      }}
      style={{ width: "100%", height: "100%" }}
      mapStyle="mapbox://styles/mapbox/light-v11"
    >
      {mappable.map((place) => {
        const color = place.category ? PIN_COLORS[place.category as Category] : "#6b7280";
        const isHighlighted = place.id === highlightedPlaceId;
        return (
          <Marker
            key={place.id}
            longitude={place.lng!}
            latitude={place.lat!}
            anchor="bottom"
            onClick={(e) => {
              e.originalEvent.stopPropagation();
              setPopup(place);
            }}
          >
            <div
              title={place.location_name ?? ""}
              style={{ color }}
              className={`text-lg transition-transform cursor-pointer select-none ${isHighlighted ? "scale-150" : "scale-100 hover:scale-125"}`}
            >
              📍
            </div>
          </Marker>
        );
      })}

      {popup && (
        <Popup
          longitude={popup.lng!}
          latitude={popup.lat!}
          anchor="bottom"
          offset={32}
          onClose={() => setPopup(null)}
          maxWidth="280px"
        >
          <div className="p-1 space-y-2 text-sm">
            <div>
              <p className="font-semibold text-zinc-900 leading-tight">{popup.location_name}</p>
              {popup.category && (
                <span className={`inline-block mt-1 px-2 py-0.5 rounded-full text-xs ${CATEGORY_COLORS[popup.category as Category]}`}>
                  {CATEGORY_LABELS[popup.category as Category]}
                </span>
              )}
            </div>

            {popup.summary && (
              <p className="text-zinc-600 text-xs leading-relaxed">{popup.summary}</p>
            )}

            {popup.transcript_missing && (
              <p className="text-xs text-amber-600 italic">caption only</p>
            )}

            {popup.primary_author && (
              <p className="text-xs text-zinc-500">
                First posted by <span className="font-medium text-zinc-700">@{popup.primary_author}</span>
              </p>
            )}

            <div className="flex items-center gap-2 pt-1 border-t border-zinc-100">
              <div className="flex items-center gap-1">
                <button
                  onClick={() => handleVote(popup, "up")}
                  className={`px-1.5 rounded ${popup.current_vote === "up" ? "bg-green-100 text-green-700" : "text-zinc-400 hover:text-green-600"}`}
                >
                  👍
                </button>
                <span className={`font-medium tabular-nums text-xs ${popup.vote_score > 0 ? "text-green-600" : popup.vote_score < 0 ? "text-red-600" : "text-zinc-400"}`}>
                  {popup.vote_score > 0 ? `+${popup.vote_score}` : popup.vote_score}
                </span>
                <button
                  onClick={() => handleVote(popup, "down")}
                  className={`px-1.5 rounded ${popup.current_vote === "down" ? "bg-red-100 text-red-700" : "text-zinc-400 hover:text-red-600"}`}
                >
                  👎
                </button>
              </div>
              {popup.source_urls[0] && (
                <a
                  href={popup.source_urls[0]}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="ml-auto text-xs text-blue-500 hover:underline"
                >
                  Source ↗
                </a>
              )}
            </div>
          </div>
        </Popup>
      )}
    </MapGL>
  );
}
