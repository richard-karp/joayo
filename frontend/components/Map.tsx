'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import MapGL, { Marker, Popup, type MapRef } from "react-map-gl/mapbox";
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

// Camera-fit outlier trimming: a lone stray pin (e.g. a not-yet-reconciled bad row)
// shouldn't stretch the viewport and squash the real cluster into a corner.
const OUTLIER_FIT_RADIUS_M = 150_000; // pins farther than this from the median are excluded from the bounds
const MIN_PINS_TO_TRIM = 5;           // with only a few pins the median is unreliable — fit them all

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

function haversineM(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6_371_000;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dPhi = toRad(lat2 - lat1);
  const dLambda = toRad(lng2 - lng1);
  const a =
    Math.sin(dPhi / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLambda / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

interface Props {
  places: Place[];
  /** Ids of the places currently expanded in the list — their pins are enlarged and the map fits to them. */
  highlightedPlaceIds: string[];
}

export default function Map({ places, highlightedPlaceIds }: Props) {
  const mapRef = useRef<MapRef>(null);
  const [mapReady, setMapReady] = useState(false);
  const [popup, setPopup] = useState<Place | null>(null);
  const [localPlaces, setLocalPlaces] = useState<Place[]>(places);
  const [prevPlaces, setPrevPlaces] = useState(places);

  // Re-sync whenever the parent passes a new `places` array (any filter/refetch),
  // while preserving optimistic vote updates (which mutate localPlaces but not the
  // prop, so their reference is unchanged and not clobbered here).
  if (places !== prevPlaces) {
    setPrevPlaces(places);
    setLocalPlaces(places);
  }

  const mappable = useMemo(
    () => localPlaces.filter((p) => p.lat != null && p.lng != null),
    [localPlaces],
  );
  const highlightedSet = useMemo(() => new Set(highlightedPlaceIds), [highlightedPlaceIds]);

  // Fit to the highlighted pins when any are active, otherwise to every pin.
  // Memoized together so the sort/join and the haversine outlier pass only run
  // when the mappable set or the highlight set actually changes — not on every
  // render (popup open, vote, mapReady flip, …).
  const { focusKey, fitPins } = useMemo(() => {
    const activePins = mappable.filter((p) => highlightedSet.has(p.id));
    const focusPins = activePins.length > 0 ? activePins : mappable;
    const key = focusPins.map((p) => p.id).sort().join("|");

    // For the full (unexpanded) set, drop far-flung pins from the camera bounds
    // only — all pins still render. Expanded pins are fit exactly as chosen; a
    // handful of pins are always fit whole (the median isn't meaningful with too
    // few points).
    let pins = focusPins;
    if (activePins.length === 0 && focusPins.length >= MIN_PINS_TO_TRIM) {
      const mLat = median(focusPins.map((p) => p.lat!));
      const mLng = median(focusPins.map((p) => p.lng!));
      const kept = focusPins.filter(
        (p) => haversineM(p.lat!, p.lng!, mLat, mLng) <= OUTLIER_FIT_RADIUS_M,
      );
      if (kept.length > 0) pins = kept;
    }
    return { focusKey: key, fitPins: pins };
  }, [mappable, highlightedSet]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    // The mapbox instance mounts asynchronously and its `load`/`idle` events are
    // unreliable here (the prop callback can be missed, and background tabs pause
    // WebGL so `idle` may never fire), so poll for the ref with setTimeout (rAF is
    // frozen in background tabs). Once the map instance exists it can accept a
    // camera move; fitBounds only repositions the camera and doesn't need a
    // fully-loaded style.
    const check = () => {
      if (mapRef.current?.getMap?.()) setMapReady(true);
      else timer = setTimeout(check, 80);
    };
    check();
    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (!mapReady || fitPins.length === 0) return;
    const lngs = fitPins.map((p) => p.lng!);
    const lats = fitPins.map((p) => p.lat!);
    mapRef.current?.fitBounds(
      [
        [Math.min(...lngs), Math.min(...lats)],
        [Math.max(...lngs), Math.max(...lats)],
      ],
      { padding: 64, maxZoom: 14, duration: 600 },
    );
    // fitPins is derived deterministically from focusPins, captured via focusKey;
    // re-fitting only when the focus set changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapReady, focusKey]);

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
      ref={mapRef}
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
        const isHighlighted = highlightedSet.has(place.id);
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
