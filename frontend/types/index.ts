export type Category = "eat" | "see_visit" | "do" | "shop" | "service" | "guide";

export interface Author {
  username: string;
  platform_id: string | null;
  platform: string;
  profile_url: string | null;
}

export interface Place {
  id: string;
  created_by_job_id: string | null;
  source_urls: string[];
  platform: string | null;
  primary_author: string | null;
  primary_author_id: string | null;
  primary_author_profile_url: string | null;
  all_authors: Author[];
  earliest_date_posted: string | null;
  location_name: string | null;
  category: Category | null;
  subcategory: string | null;
  is_place: boolean;
  venue: string | null;
  country: string | null;
  city: string | null;
  summary: string | null;
  labels: string[] | null;
  insider_tips: string | null;
  lat: number | null;
  lng: number | null;
  raw_caption: string | null;
  tagged_accounts: string[] | null;
  transcript_missing: boolean;
  created_at: string;
  vote_score: number;
  current_vote: "up" | "down" | null;
}

export interface Job {
  id: string;
  status: "pending" | "processing" | "complete" | "complete_with_errors" | "paused" | "cancelled";
  total_urls: number;
  processed: number;
  current_url: string | null;
  pending_review: Array<{ url: string; reason: string }>;
  failed_urls: Array<{ url: string; error: string }>;
  warnings: Array<{ code: string; message: string }>;
  paused_reason: string | null;
  remaining_posts: Array<{ url: string; caption?: string }>;
  places: Place[];
  created_at: string;
}

export interface LeaderboardEntry {
  username: string;
  platform_id: string | null;
  profile_url: string | null;
  total_score: number;
  attributed_count: number;
  mentioned_count: number;
}

export const CATEGORY_LABELS: Record<Category, string> = {
  eat: "Eat",
  see_visit: "See & Visit",
  do: "Do",
  shop: "Shop",
  service: "Service",
  guide: "Guide",
};

export const CATEGORY_COLORS: Record<Category, string> = {
  eat: "bg-orange-100 text-orange-800",
  see_visit: "bg-blue-100 text-blue-800",
  do: "bg-green-100 text-green-800",
  shop: "bg-purple-100 text-purple-800",
  service: "bg-pink-100 text-pink-800",
  guide: "bg-yellow-100 text-yellow-800",
};
