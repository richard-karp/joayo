'use client';

import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import CategoryPills from "@/components/CategoryPills";
import CategoryView from "@/components/CategoryView";
import CreatorCard from "@/components/CreatorCard";
import Filters from "@/components/Filters";
import Leaderboard from "@/components/Leaderboard";
import PlacesRanking from "@/components/PlacesRanking";
import ThingsList from "@/components/ThingsList";
import { getFilters, getPlaces, exportAllUrl } from "@/lib/api";
import { isolateCategory, soleVisibleCategory, toggleCategory, visibleByCategory } from "@/lib/categories";
import type { Category, Place } from "@/types";

const Map = dynamic(() => import("@/components/Map"), { ssr: false });

type View = "creators" | "places" | "categories" | "things";

interface FilterData {
  countries: { name: string; place_count: number }[];
  cities: { name: string; country: string; place_count: number }[];
  neighborhoods: { name: string; city: string; place_count: number }[];
  subcategories: { name: string; category: string; place_count: number }[];
}

export default function DashboardPage() {
  const [filters, setFilters] = useState<FilterData | null>(null);
  const [selectedCountry, setSelectedCountry] = useState<string | null>(null);
  const [selectedCity, setSelectedCity] = useState<string | null>(null);
  const [selectedNeighborhood, setSelectedNeighborhood] = useState<string | null>(null);
  const [selectedSubcategory, setSelectedSubcategory] = useState<string | null>(null);
  const [selectedLabel, setSelectedLabel] = useState<string | null>(null);
  const [markFilter, setMarkFilter] = useState<null | "rated" | "want_to_go" | "needs_review">(null);
  const [sortNew, setSortNew] = useState(false);
  const [search, setSearch] = useState("");
  const [places, setPlaces] = useState<Place[]>([]);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<View>("places");

  // Category filter — owned here, not by the map or the list, so the pins and the
  // rows below them always show the same set. Holds the categories switched OFF;
  // the map's multi-toggle chips and the single-select pills both write to it.
  const [hiddenCategories, setHiddenCategories] = useState<Set<Category>>(new Set());

  // Creators view state
  const [authorFilter, setAuthorFilter] = useState<string | null>(null);
  const [selectedCreator, setSelectedCreator] = useState<string | null>(null);

  // Places / categories view state — ids of the rows expanded inline in the list.
  const [expandedPlaceIds, setExpandedPlaceIds] = useState<string[]>([]);

  // Collapse any inline-expanded rows whenever the underlying result set changes
  // (country/city/subcategory/label/search), so a lingering id can't silently
  // re-expand a row that later scrolls back into the same list. Done during render
  // (React's "adjust state on change" pattern) rather than in an effect, to avoid
  // a cascading post-render re-render.
  const filterKey = JSON.stringify([
    selectedCountry, selectedCity, selectedNeighborhood, selectedSubcategory, selectedLabel, markFilter, search,
    [...hiddenCategories].sort(),
  ]);
  const [prevFilterKey, setPrevFilterKey] = useState(filterKey);
  if (filterKey !== prevFilterKey) {
    setPrevFilterKey(filterKey);
    setExpandedPlaceIds([]);
  }

  useEffect(() => {
    getFilters().then((data) => {
      setFilters(data);
      if (data.countries.length === 1) {
        setSelectedCountry(data.countries[0].name);
      }
    });
  }, []);

  const placesReqRef = useRef(0);
  useEffect(() => {
    if (!selectedCountry && filters && filters.countries.length > 1) return;
    // Debounce so typing in the search box doesn't fire a request per keystroke.
    const handle = setTimeout(() => {
      const reqId = ++placesReqRef.current;
      getPlaces({
        country: selectedCountry ?? undefined,
        city: selectedCity ?? undefined,
        neighborhood: selectedNeighborhood ?? undefined,
        subcategory: selectedSubcategory ?? undefined,
        label: selectedLabel ?? undefined,
        q: search.trim() || undefined,
        rated: markFilter === "rated" || undefined,
        want_to_go: markFilter === "want_to_go" || undefined,
        needs_review: markFilter === "needs_review" || undefined,
        sort: sortNew ? "new" : undefined,
      }).then((data) => {
        // Ignore a stale response if a newer request has since been issued.
        if (reqId !== placesReqRef.current) return;
        setPlaces(data);
        setLoading(false);
      });
    }, search ? 250 : 0);
    return () => clearTimeout(handle);
  }, [selectedCountry, selectedCity, selectedNeighborhood, selectedSubcategory, selectedLabel, markFilter, sortNew, search, filters]);

  const handleLabelClick = useCallback((label: string) => {
    setSelectedLabel((prev) => (prev === label ? null : label));
  }, []);

  // Every list renders this; the map takes the unfiltered set and hides pins itself
  // so that switching a category off doesn't yank the camera to a new bounding box.
  const visiblePlaces = useMemo(
    () => visibleByCategory(places, hiddenCategories),
    [places, hiddenCategories],
  );
  const visibleVenues = useMemo(() => visiblePlaces.filter((p) => p.is_place), [visiblePlaces]);
  // Unfiltered. The Things pill row counts from allThings (a hidden category still needs
  // a count so it can be switched back on), and ThingsList uses allVenues purely as a
  // name→place lookup for links — a filtered lookup would silently drop the link on a
  // thing whose venue happens to sit in a hidden category.
  const allVenues = useMemo(() => places.filter((p) => p.is_place), [places]);
  const allThings = useMemo(() => places.filter((p) => !p.is_place), [places]);
  const things = useMemo(
    () => visibleByCategory(allThings, hiddenCategories),
    [allThings, hiddenCategories],
  );
  const activeCategory = soleVisibleCategory(hiddenCategories);

  const handleToggleCategory = useCallback((cat: Category) => {
    setHiddenCategories((prev) => toggleCategory(prev, cat));
  }, []);

  const handleCategorySelect = useCallback((cat: Category | null) => {
    setHiddenCategories((prev) => (cat === null ? new Set<Category>() : isolateCategory(prev, cat)));
  }, []);

  const citiesForCountry = filters?.cities.filter((c) => c.country === selectedCountry) ?? [];
  const neighborhoodsForCity = selectedCity
    ? (filters?.neighborhoods.filter((n) => n.city === selectedCity) ?? [])
    : [];

  const handleCreatorSelect = useCallback((username: string) => {
    setSelectedCreator((prev) => (prev === username ? null : username));
  }, []);

  const handleAuthorClick = useCallback((username: string) => {
    setAuthorFilter((prev) => (prev === username ? null : username));
  }, []);

  const handlePlaceClick = useCallback((placeId: string) => {
    setExpandedPlaceIds((prev) =>
      prev.includes(placeId) ? prev.filter((id) => id !== placeId) : [...prev, placeId],
    );
  }, []);

  const needsPicker = filters && filters.countries.length > 1 && !selectedCountry;

  const VIEW_TABS: { id: View; label: string }[] = [
    { id: "places", label: "Places" },
    { id: "categories", label: "Categories" },
    { id: "things", label: "Things" },
    { id: "creators", label: "Creators" },
  ];

  return (
    <div className="min-h-screen bg-zinc-50">
      <header className="sticky top-0 z-20 bg-white border-b border-zinc-200 px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="font-bold text-zinc-900 text-lg tracking-tight">joayo</h1>
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
              onClick={() => { setSelectedCity(null); setSelectedNeighborhood(null); }}
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
                onClick={() => {
                  setSelectedNeighborhood(null);
                  setSelectedCity((prev) => (prev === c.name ? null : c.name));
                }}
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

        {/* Neighborhood filter (nested under the selected city) */}
        {selectedCity && neighborhoodsForCity.length > 0 && (
          <div className="flex items-center gap-2 flex-wrap pl-4">
            <span className="text-xs text-zinc-400 mr-1">Area</span>
            {neighborhoodsForCity.map((n) => (
              <button
                key={n.name}
                onClick={() => setSelectedNeighborhood((prev) => (prev === n.name ? null : n.name))}
                className={`px-2.5 py-0.5 rounded-full text-xs border transition-colors ${
                  selectedNeighborhood === n.name
                    ? "bg-zinc-900 text-white border-zinc-900"
                    : "bg-white text-zinc-500 border-zinc-200 hover:border-zinc-400"
                }`}
              >
                {n.name}
                <span className="ml-1 opacity-60">{n.place_count}</span>
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
                    setExpandedPlaceIds([]);
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
                {loading
                  ? "Loading…"
                  : view === "things"
                  ? `${things.length} thing${things.length !== 1 ? "s" : ""}`
                  : `${visibleVenues.length} place${visibleVenues.length !== 1 ? "s" : ""}`}
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
              {([
                { id: "want_to_go", label: "★ Want to go" },
                { id: "rated", label: "Rated" },
                { id: "needs_review", label: "⚠ Needs review" },
              ] as const).map((chip) => (
                <button
                  key={chip.id}
                  onClick={() => setMarkFilter((prev) => (prev === chip.id ? null : chip.id))}
                  className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ${
                    markFilter === chip.id
                      ? "bg-zinc-900 text-white border-zinc-900"
                      : "bg-white text-zinc-600 border-zinc-200 hover:border-zinc-400"
                  }`}
                >
                  {chip.label}
                </button>
              ))}
              <button
                onClick={() => setSortNew((v) => !v)}
                title="Sort by most recent post"
                className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ${
                  sortNew
                    ? "bg-zinc-900 text-white border-zinc-900"
                    : "bg-white text-zinc-600 border-zinc-200 hover:border-zinc-400"
                }`}
              >
                Newest
              </button>
              {selectedLabel && (
                <button
                  onClick={() => setSelectedLabel(null)}
                  className="inline-flex items-center gap-1 text-xs bg-zinc-900 text-white rounded-full px-3 py-1.5 hover:bg-zinc-700 transition-colors"
                  title="Clear label filter"
                >
                  🏷 {selectedLabel} <span className="opacity-70">✕</span>
                </button>
              )}
              {/* hiddenCategories is included because the map chips can hide a category
                  in one view and the filter then follows you into another — Clear has to
                  be able to undo it from wherever you end up. */}
              {(search || selectedSubcategory || selectedLabel || markFilter || hiddenCategories.size > 0) && (
                <button
                  onClick={() => { setSearch(""); setSelectedSubcategory(null); setSelectedLabel(null); setMarkFilter(null); setHiddenCategories(new Set()); }}
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
                    onCategoryChange={handleCategorySelect}
                    authorFilter={authorFilter}
                    onAuthorFilterClear={() => setAuthorFilter(null)}
                  />
                </div>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
                  <div>
                    {/* activeCategory is null unless exactly ONE category is visible,
                        because /api/leaderboard takes a single `category`. So a
                        multi-category selection (only reachable via the map chips)
                        filters the pins and the lists but leaves the leaderboard
                        showing every creator. Making it follow means a repeatable
                        `category` param on the endpoint. */}
                    <Leaderboard
                      activeAuthor={authorFilter}
                      activeCategory={activeCategory}
                      onAuthorClick={handleAuthorClick}
                      onCreatorSelect={handleCreatorSelect}
                    />
                  </div>
                  <div className="order-first lg:order-none lg:sticky lg:top-6 space-y-0">
                    <div className="h-72 lg:h-[600px] rounded-xl overflow-hidden shadow-sm border border-zinc-200">
                      <Map
                        places={places}
                        highlightedPlaceIds={[]}
                        hiddenCategories={hiddenCategories}
                        onToggleCategory={handleToggleCategory}
                      />
                    </div>
                    {selectedCreator && (
                      <CreatorCard
                        username={selectedCreator}
                        places={visiblePlaces}
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
                    places={visiblePlaces}
                    expandedIds={expandedPlaceIds}
                    onPlaceClick={handlePlaceClick}
                    activeLabel={selectedLabel}
                    onLabelClick={handleLabelClick}
                  />
                </div>
                <div className="order-first lg:order-none lg:sticky lg:top-6">
                  <div className="h-72 lg:h-[600px] rounded-xl overflow-hidden shadow-sm border border-zinc-200">
                    <Map
                      places={places}
                      highlightedPlaceIds={expandedPlaceIds}
                      hiddenCategories={hiddenCategories}
                      onToggleCategory={handleToggleCategory}
                    />
                  </div>
                </div>
              </div>
            )}

            {/* Categories view */}
            {view === "categories" && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
                <div>
                  <CategoryView
                    places={places}
                    expandedIds={expandedPlaceIds}
                    onPlaceClick={handlePlaceClick}
                    activeLabel={selectedLabel}
                    onLabelClick={handleLabelClick}
                    hiddenCategories={hiddenCategories}
                    onCategorySelect={handleCategorySelect}
                  />
                </div>
                <div className="order-first lg:order-none lg:sticky lg:top-6">
                  <div className="h-72 lg:h-[600px] rounded-xl overflow-hidden shadow-sm border border-zinc-200">
                    <Map
                      places={places}
                      highlightedPlaceIds={expandedPlaceIds}
                      hiddenCategories={hiddenCategories}
                      onToggleCategory={handleToggleCategory}
                    />
                  </div>
                </div>
              </div>
            )}

            {/* Things view — the is_place=false items (dishes, products, tips) */}
            {view === "things" && (
              <div>
                <CategoryPills
                  items={allThings}
                  hiddenCategories={hiddenCategories}
                  onCategorySelect={handleCategorySelect}
                />
                <ThingsList
                  things={things}
                  venues={allVenues}
                  expandedIds={expandedPlaceIds}
                  onPlaceClick={handlePlaceClick}
                  activeLabel={selectedLabel}
                  onLabelClick={handleLabelClick}
                />
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
