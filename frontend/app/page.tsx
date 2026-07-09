'use client';

import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import CategoryView from "@/components/CategoryView";
import CreatorCard from "@/components/CreatorCard";
import Filters from "@/components/Filters";
import Leaderboard from "@/components/Leaderboard";
import PlaceCard from "@/components/PlaceCard";
import PlacesRanking from "@/components/PlacesRanking";
import { getFilters, getPlaces, exportAllUrl } from "@/lib/api";
import type { Category, Place } from "@/types";

const Map = dynamic(() => import("@/components/Map"), { ssr: false });

type View = "creators" | "places" | "categories";

interface FilterData {
  countries: { name: string; place_count: number }[];
  cities: { name: string; country: string; place_count: number }[];
  subcategories: { name: string; category: string; place_count: number }[];
}

export default function DashboardPage() {
  const [filters, setFilters] = useState<FilterData | null>(null);
  const [selectedCountry, setSelectedCountry] = useState<string | null>(null);
  const [selectedCity, setSelectedCity] = useState<string | null>(null);
  const [selectedSubcategory, setSelectedSubcategory] = useState<string | null>(null);
  const [selectedLabel, setSelectedLabel] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [places, setPlaces] = useState<Place[]>([]);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<View>("places");

  // Creators view state
  const [activeCategory, setActiveCategory] = useState<Category | null>(null);
  const [authorFilter, setAuthorFilter] = useState<string | null>(null);
  const [selectedCreator, setSelectedCreator] = useState<string | null>(null);

  // Places / categories view state
  const [selectedPlaceId, setSelectedPlaceId] = useState<string | null>(null);
  const [highlightedPlaceId, setHighlightedPlaceId] = useState<string | null>(null);

  useEffect(() => {
    getFilters().then((data) => {
      setFilters(data);
      if (data.countries.length === 1) {
        setSelectedCountry(data.countries[0].name);
      }
    });
  }, []);

  useEffect(() => {
    if (!selectedCountry && filters && filters.countries.length > 1) return;
    // Debounce so typing in the search box doesn't fire a request per keystroke.
    const handle = setTimeout(() => {
      getPlaces({
        country: selectedCountry ?? undefined,
        city: selectedCity ?? undefined,
        subcategory: selectedSubcategory ?? undefined,
        label: selectedLabel ?? undefined,
        q: search.trim() || undefined,
      }).then((data) => {
        setPlaces(data);
        setLoading(false);
      });
    }, search ? 250 : 0);
    return () => clearTimeout(handle);
  }, [selectedCountry, selectedCity, selectedSubcategory, selectedLabel, search, filters]);

  const handleLabelClick = useCallback((label: string) => {
    setSelectedLabel((prev) => (prev === label ? null : label));
  }, []);

  const citiesForCountry = filters?.cities.filter((c) => c.country === selectedCountry) ?? [];

  const handleCreatorSelect = useCallback((username: string) => {
    setSelectedCreator((prev) => (prev === username ? null : username));
  }, []);

  const handleAuthorClick = useCallback((username: string) => {
    setAuthorFilter((prev) => (prev === username ? null : username));
  }, []);

  const handlePlaceClick = useCallback((placeId: string) => {
    setSelectedPlaceId((prev) => (prev === placeId ? null : placeId));
    setHighlightedPlaceId(placeId);
  }, []);

  const selectedPlace = places.find((p) => p.id === selectedPlaceId) ?? null;
  const needsPicker = filters && filters.countries.length > 1 && !selectedCountry;

  const VIEW_TABS: { id: View; label: string }[] = [
    { id: "places", label: "Places" },
    { id: "categories", label: "Categories" },
    { id: "creators", label: "Creators" },
  ];

  return (
    <div className="min-h-screen bg-zinc-50">
      <header className="bg-white border-b border-zinc-200 px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="font-bold text-zinc-900 text-lg tracking-tight">Place Extractor</h1>
          <p className="text-xs text-zinc-400">Locations extracted from social posts</p>
        </div>
        <div className="flex items-center gap-2">
          <a
            href={exportAllUrl(selectedCountry)}
            download
            className="text-sm text-zinc-500 hover:text-zinc-900 border border-zinc-200 rounded-lg px-3 py-1.5 transition-colors"
          >
            Download CSV
          </a>
          <Link
            href="/extract"
            className="text-sm text-zinc-500 hover:text-zinc-900 border border-zinc-200 rounded-lg px-3 py-1.5 transition-colors"
          >
            Extract new places
          </Link>
        </div>
      </header>

      <main className="max-w-screen-xl mx-auto px-6 py-8 space-y-6">
        {/* Country picker */}
        {needsPicker && (
          <div className="flex flex-col items-center py-12 gap-4">
            <p className="text-sm text-zinc-500">Choose a destination</p>
            <div className="flex flex-wrap gap-3 justify-center">
              {filters.countries.map((c) => (
                <button
                  key={c.name}
                  onClick={() => setSelectedCountry(c.name)}
                  className="px-5 py-2.5 rounded-xl bg-white border border-zinc-200 shadow-sm hover:border-zinc-400 text-sm font-medium text-zinc-700 transition-colors"
                >
                  {c.name}
                  <span className="ml-1.5 text-xs text-zinc-400">{c.place_count}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* City filter */}
        {selectedCountry && citiesForCountry.length > 0 && (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-zinc-400 mr-1">City</span>
            <button
              onClick={() => setSelectedCity(null)}
              className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
                selectedCity === null
                  ? "bg-zinc-900 text-white border-zinc-900"
                  : "bg-white text-zinc-600 border-zinc-200 hover:border-zinc-400"
              }`}
            >
              All
            </button>
            {citiesForCountry.map((c) => (
              <button
                key={c.name}
                onClick={() => setSelectedCity((prev) => (prev === c.name ? null : c.name))}
                className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
                  selectedCity === c.name
                    ? "bg-zinc-900 text-white border-zinc-900"
                    : "bg-white text-zinc-600 border-zinc-200 hover:border-zinc-400"
                }`}
              >
                {c.name}
                <span className="ml-1 opacity-60">{c.place_count}</span>
              </button>
            ))}
          </div>
        )}

        {/* Dashboard content */}
        {(selectedCountry || (filters && filters.countries.length === 0)) && !needsPicker && (
          <>
            {/* Country tabs */}
            {filters && filters.countries.length > 1 && (
              <div className="flex items-center gap-2 flex-wrap">
                {filters.countries.map((c) => (
                  <button
                    key={c.name}
                    onClick={() => { setSelectedCountry(c.name); setSelectedCity(null); }}
                    className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
                      selectedCountry === c.name
                        ? "bg-zinc-900 text-white border-zinc-900"
                        : "bg-white text-zinc-600 border-zinc-200 hover:border-zinc-400"
                    }`}
                  >
                    {c.name}
                    <span className="ml-1 opacity-60">{c.place_count}</span>
                  </button>
                ))}
              </div>
            )}

            {/* View tabs */}
            <div className="flex items-center gap-1 border-b border-zinc-200">
              {VIEW_TABS.map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => {
                    setView(tab.id);
                    setSelectedPlaceId(null);
                    setSelectedCreator(null);
                    setAuthorFilter(null);
                  }}
                  className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px ${
                    view === tab.id
                      ? "border-zinc-900 text-zinc-900"
                      : "border-transparent text-zinc-500 hover:text-zinc-700"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
              <span className="ml-auto text-sm text-zinc-400 pb-2">
                {loading ? "Loading…" : `${places.filter(p => p.is_place).length} place${places.filter(p => p.is_place).length !== 1 ? "s" : ""}`}
              </span>
            </div>

            {/* Search + subcategory filter (applies across all views) */}
            <div className="flex items-center gap-3 flex-wrap">
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search name, tags, summary…"
                className="flex-1 min-w-[12rem] max-w-sm px-3 py-1.5 rounded-lg border border-zinc-200 text-sm text-zinc-700 placeholder:text-zinc-400 focus:outline-none focus:border-zinc-400"
              />
              {filters && filters.subcategories.length > 0 && (
                <select
                  value={selectedSubcategory ?? ""}
                  onChange={(e) => setSelectedSubcategory(e.target.value || null)}
                  className="px-3 py-1.5 rounded-lg border border-zinc-200 text-sm text-zinc-700 bg-white focus:outline-none focus:border-zinc-400"
                >
                  <option value="">All types</option>
                  {filters.subcategories.map((s) => (
                    <option key={`${s.category}:${s.name}`} value={s.name}>
                      {s.name.replace(/_/g, " ")} ({s.place_count})
                    </option>
                  ))}
                </select>
              )}
              {selectedLabel && (
                <button
                  onClick={() => setSelectedLabel(null)}
                  className="inline-flex items-center gap-1 text-xs bg-zinc-900 text-white rounded-full px-3 py-1.5 hover:bg-zinc-700 transition-colors"
                  title="Clear label filter"
                >
                  🏷 {selectedLabel} <span className="opacity-70">✕</span>
                </button>
              )}
              {(search || selectedSubcategory || selectedLabel) && (
                <button
                  onClick={() => { setSearch(""); setSelectedSubcategory(null); setSelectedLabel(null); }}
                  className="text-xs text-zinc-500 hover:text-zinc-900 border border-zinc-200 rounded-lg px-3 py-1.5 transition-colors"
                >
                  Clear
                </button>
              )}
            </div>

            {/* Creators view */}
            {view === "creators" && (
              <>
                <div className="flex items-center gap-4 flex-wrap">
                  <Filters
                    activeCategory={activeCategory}
                    onCategoryChange={setActiveCategory}
                    authorFilter={authorFilter}
                    onAuthorFilterClear={() => setAuthorFilter(null)}
                  />
                </div>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
                  <div>
                    <Leaderboard
                      activeAuthor={authorFilter}
                      activeCategory={activeCategory}
                      onAuthorClick={handleAuthorClick}
                      onCreatorSelect={handleCreatorSelect}
                    />
                  </div>
                  <div className="sticky top-6 space-y-0">
                    <div className="h-[600px] rounded-xl overflow-hidden shadow-sm border border-zinc-200">
                      <Map places={places} highlightedPlaceId={highlightedPlaceId} />
                    </div>
                    {selectedCreator && (
                      <CreatorCard
                        username={selectedCreator}
                        places={places}
                        onClose={() => {
                          setSelectedCreator(null);
                          setAuthorFilter(null);
                        }}
                      />
                    )}
                  </div>
                </div>
              </>
            )}

            {/* Places view */}
            {view === "places" && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
                <div>
                  <PlacesRanking
                    places={places}
                    selectedPlaceId={selectedPlaceId}
                    onPlaceClick={handlePlaceClick}
                  />
                </div>
                <div className="sticky top-6">
                  <div className="h-[600px] rounded-xl overflow-hidden shadow-sm border border-zinc-200">
                    <Map places={places} highlightedPlaceId={highlightedPlaceId} />
                  </div>
                  {selectedPlace && (
                    <PlaceCard
                      place={selectedPlace}
                      activeLabel={selectedLabel}
                      onLabelClick={handleLabelClick}
                      onClose={() => {
                        setSelectedPlaceId(null);
                        setHighlightedPlaceId(null);
                      }}
                    />
                  )}
                </div>
              </div>
            )}

            {/* Categories view */}
            {view === "categories" && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
                <div>
                  <CategoryView
                    places={places}
                    selectedPlaceId={selectedPlaceId}
                    onPlaceClick={handlePlaceClick}
                  />
                </div>
                <div className="sticky top-6">
                  <div className="h-[600px] rounded-xl overflow-hidden shadow-sm border border-zinc-200">
                    <Map places={places} highlightedPlaceId={highlightedPlaceId} />
                  </div>
                  {selectedPlace && (
                    <PlaceCard
                      place={selectedPlace}
                      activeLabel={selectedLabel}
                      onLabelClick={handleLabelClick}
                      onClose={() => {
                        setSelectedPlaceId(null);
                        setHighlightedPlaceId(null);
                      }}
                    />
                  )}
                </div>
              </div>
            )}
          </>
        )}

        {/* Empty state */}
        {filters && filters.countries.length === 0 && !loading && (
          <div className="flex flex-col items-center py-20 gap-4 text-center">
            <p className="text-zinc-500 text-sm">No places extracted yet.</p>
            <Link
              href="/extract"
              className="text-sm font-medium text-blue-600 hover:underline"
            >
              Extract from Instagram posts →
            </Link>
          </div>
        )}
      </main>
    </div>
  );
}
