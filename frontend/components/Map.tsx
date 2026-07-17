'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import MapGL, { Marker, Popup, type MapRef } from "react-map-gl/mapbox";
import "mapbox-gl/dist/mapbox-gl.css";
import Link from "next/link";
import Supercluster from "supercluster";
import type { Category, Place } from "@/types";
import { CATEGORY_COLORS, CATEGORY_LABELS } from "@/types";
import RatingControl from "@/components/RatingControl";
import MapLinks from "@/components/MapLinks";
import { haversineM, median } from "@/lib/geo";

const TOKEN = process.env.NEXT_PUBLIC_MAPBOX_TOKEN ?? "";

const PIN_COLORS: Record<Category, string> = {
  eat: "#f97316",
  see_visit: "#3b82f6",
  do: "#22c55e",
  shop: "#a855f7",
  service: "#ec4899",
  guide: "#eab308",
};

const ALL_CATEGORIES = Object.keys(PIN_COLORS) as Category[];

// Per-anchor popup offset so it clears the 📍 marker (which sits above the
// coordinate) whichever direction mapbox flips it toward at the map's edges.
const POPUP_OFFSET: Record<string, [number, number]> = {
  top: [0, 8], "top-left": [0, 8], "top-right": [0, 8],           // popup below the pin
  bottom: [0, -28], "bottom-left": [0, -28], "bottom-right": [0, -28], // popup above the pin
  left: [-14, -12], right: [14, -12],                            // popup beside the pin
};

// Camera-fit outlier trimming: a lone stray pin (e.g. a not-yet-reconciled bad row)
// shouldn't stretch the viewport and squash the real cluster into a corner.
const OUTLIER_FIT_RADIUS_M = 150_000; // pins farther than this from the median are excluded from the bounds
const MIN_PINS_TO_TRIM = 5;           // with only a few pins the median is unreliable — fit them all

type ClusterProps = { cluster: false; placeId: string };
type PointFeature = GeoJSON.Feature<GeoJSON.Point, ClusterProps>;

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
  // Which categories render (all on by default); the on-map toggle chips flip these.
  const [hiddenCategories, setHiddenCategories] = useState<Set<Category>>(new Set());
  // Current viewport, used to recompute clusters as the user pans/zooms.
  const [view, setView] = useState<{ bbox: [number, number, number, number]; zoom: number } | null>(null);
  const [userLoc, setUserLoc] = useState<{ lat: number; lng: number } | null>(null);

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

  // Category-filtered pins feed the cluster index; fitBounds still uses the full set.
  const visiblePins = useMemo(
    () => mappable.filter((p) => !p.category || !hiddenCategories.has(p.category as Category)),
    [mappable, hiddenCategories],
  );
  const placeById = useMemo(() => {
    const m: Record<string, Place> = {};
    for (const p of visiblePins) m[p.id] = p;
    return m;
  }, [visiblePins]);
  const index = useMemo(() => {
    const sc = new Supercluster<ClusterProps>({ radius: 60, maxZoom: 16 });
    sc.load(visiblePins.map((p): PointFeature => ({
      type: "Feature",
      properties: { cluster: false, placeId: p.id },
      geometry: { type: "Point", coordinates: [p.lng!, p.lat!] },
    })));
    return sc;
  }, [visiblePins]);
  const clusters = useMemo(
    () => (view ? index.getClusters(view.bbox, Math.round(view.zoom)) : []),
    [index, view],
  );

  const syncView = useCallback(() => {
    const map = mapRef.current?.getMap();
    const b = map?.getBounds();
    if (!map || !b) return;
    setView({ bbox: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], zoom: map.getZoom() });
  }, []);

  const toggleCategory = useCallback((cat: Category) => {
    setHiddenCategories((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  }, []);

  const handleNearMe = useCallback(() => {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition((pos) => {
      const { latitude, longitude } = pos.coords;
      setUserLoc({ lat: latitude, lng: longitude });
      mapRef.current?.getMap()?.easeTo({ center: [longitude, latitude], zoom: 14, duration: 800 });
    });
  }, []);

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

  // Seed the viewport once the map exists so clusters have a bbox to render into
  // (subsequent pans/zooms update it via onMoveEnd).
  useEffect(() => {
    if (mapReady) syncView();
  }, [mapReady, syncView]);

  const handleMarkUpdate = useCallback((updated: Place) => {
    setLocalPlaces((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
    setPopup((cur) => (cur && cur.id === updated.id ? updated : cur));
  }, []);

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
    <div className="relative w-full h-full">
    <MapGL
      ref={mapRef}
      mapboxAccessToken={TOKEN}
      initialViewState={{
        ...bounds,
        zoom: mappable.length === 0 ? 10 : 11,
      }}
      style={{ width: "100%", height: "100%" }}
      mapStyle="mapbox://styles/mapbox/light-v11"
      onLoad={syncView}
      onMoveEnd={syncView}
    >
      {clusters.map((feature) => {
        const [lng, lat] = feature.geometry.coordinates;
        if (feature.properties.cluster) {
          const count = feature.properties.point_count;
          const size = 26 + Math.min(count, 300) / 10; // grows with count, capped
          return (
            <Marker
              key={`cluster-${feature.id}`}
              longitude={lng}
              latitude={lat}
              anchor="center"
              onClick={(e) => {
                e.originalEvent.stopPropagation();
                const zoom = Math.min(index.getClusterExpansionZoom(feature.id as number), 20);
                mapRef.current?.getMap()?.easeTo({ center: [lng, lat], zoom, duration: 500 });
              }}
            >
              <div
                style={{ width: size, height: size }}
                className="flex items-center justify-center rounded-full bg-zinc-900/80 text-white text-xs font-semibold shadow ring-2 ring-white cursor-pointer select-none"
              >
                {count}
              </div>
            </Marker>
          );
        }
        const place = placeById[feature.properties.placeId];
        if (!place) return null;
        const color = place.category ? PIN_COLORS[place.category as Category] : "#6b7280";
        const isHighlighted = highlightedSet.has(place.id);
        return (
          <Marker
            key={place.id}
            longitude={lng}
            latitude={lat}
            anchor="bottom"
            onClick={(e) => {
              e.originalEvent.stopPropagation();
              setPopup(place);
            }}
          >
            <div
              title={place.location_name ?? ""}
              style={{ color }}
              className={`text-lg transition-transform cursor-pointer select-none ${isHighlighted ? "scale-150" : "scale-100 hover:scale-125"} ${place.needs_review ? "opacity-60" : ""}`}
            >
              📍
            </div>
          </Marker>
        );
      })}

      {userLoc && (
        <Marker longitude={userLoc.lng} latitude={userLoc.lat} anchor="center">
          <div title="You are here" className="w-3.5 h-3.5 rounded-full bg-blue-500 ring-2 ring-white shadow" />
        </Marker>
      )}

      {popup && (
        <Popup
          longitude={popup.lng!}
          latitude={popup.lat!}
          // No fixed anchor: let mapbox pick it from available space so a pin near
          // the top edge opens the popup *below* the marker instead of clipping its
          // rating buttons above the map. The per-anchor offset keeps it clear of
          // the pin in whichever direction it opens.
          offset={POPUP_OFFSET}
          onClose={() => setPopup(null)}
          maxWidth="280px"
        >
          <div className="p-1 space-y-2 text-sm">
            <div>
              <p className="font-semibold text-zinc-900 leading-tight">{popup.location_name}</p>
              <div className="flex items-center gap-1.5 mt-1 flex-wrap">
                {popup.category && (
                  <span className={`inline-block px-2 py-0.5 rounded-full text-xs ${CATEGORY_COLORS[popup.category as Category]}`}>
                    {CATEGORY_LABELS[popup.category as Category]}
                  </span>
                )}
                {popup.needs_review && (
                  <span
                    title="Location is a best guess — not yet verified"
                    className="inline-block px-2 py-0.5 rounded-full text-xs bg-amber-100 text-amber-700"
                  >
                    ⚠ best guess
                  </span>
                )}
              </div>
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

            <div className="pt-1 border-t border-zinc-100 space-y-1.5">
              <div className="flex items-center gap-2">
                <RatingControl place={popup} onUpdate={handleMarkUpdate} size="sm" />
                <Link href={`/places/${popup.id}`} className="ml-auto text-xs text-blue-600 hover:underline">
                  Details ↗
                </Link>
              </div>
              <MapLinks place={popup} />
            </div>
          </div>
        </Popup>
      )}
    </MapGL>

      {/* On-map category toggles */}
      <div className="absolute top-2 left-2 flex flex-wrap gap-1 max-w-[70%]">
        {ALL_CATEGORIES.map((cat) => {
          const on = !hiddenCategories.has(cat);
          return (
            <button
              key={cat}
              onClick={() => toggleCategory(cat)}
              title={on ? `Hide ${CATEGORY_LABELS[cat]}` : `Show ${CATEGORY_LABELS[cat]}`}
              className={`text-[11px] px-2 py-0.5 rounded-full border shadow-sm transition-colors ${
                on ? "bg-white text-zinc-700 border-zinc-200" : "bg-zinc-100 text-zinc-400 border-transparent line-through"
              }`}
              style={on ? { boxShadow: `inset 3px 0 0 ${PIN_COLORS[cat]}` } : undefined}
            >
              {CATEGORY_LABELS[cat]}
            </button>
          );
        })}
      </div>

      {/* Near me */}
      <button
        onClick={handleNearMe}
        className="absolute top-2 right-2 text-xs px-2.5 py-1 rounded-full bg-white text-zinc-700 border border-zinc-200 shadow-sm hover:border-zinc-400 transition-colors"
      >
        📍 Near me
      </button>
    </div>
  );
}
