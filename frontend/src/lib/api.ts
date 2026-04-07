const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

export async function apiUpload<T>(path: string, formData: FormData): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    throw new Error(`Upload error: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

// === Types ===

export interface Book {
  id: string;
  title: string;
  author: string | null;
  year: number | null;
  language: string;
  pdf_type: string;
  domain: string;
  file_path: string | null;
  status: string;
  tags: string[] | null;
  created_at: string;
  updated_at: string;
}

export interface BookDetail extends Book {
  pages_count: number;
  chunks_count: number;
  recipes_count: number;
  logs: ProcessingLog[];
}

export interface BookPage {
  id: string;
  book_id: string;
  page_number: number;
  image_path: string | null;
  raw_text: string | null;
  ocr_confidence: number | null;
  needs_review: boolean;
  status: string;
}

export interface BookChunk {
  id: string;
  book_id: string;
  chunk_index: number;
  raw_text: string | null;
  cleaned_text: string | null;
  normalized_text: string | null;
  chunk_type: string | null;
  page_start: number | null;
  page_end: number | null;
  layout_zone: string | null;
  status: string;
}

export interface ProcessingLog {
  id: string;
  book_id: string;
  step: string;
  status: string;
  details: Record<string, unknown> | null;
  created_at: string;
}

export interface Recipe {
  id: string;
  book_id: string;
  name: string;
  category: string | null;
  original_text: string | null;
  normalized_text: string | null;
  year: number | null;
  indexed_at: string | null;
}

export interface Plant {
  id: string;
  name: string;
  name_latin: string | null;
  names_historical: string[] | null;
  family: string | null;
  parts_used: string[] | null;
}

export interface DictionaryTerm {
  id: string;
  category: string;
  term_old: string;
  term_modern: string;
  context: string | null;
  source_book_id: string | null;
}

export interface SearchResult {
  id: string;
  score: number;
  collection: string;
  payload: Record<string, unknown>;
}

export interface SearchResponse {
  results: SearchResult[];
  query: string;
  mode: string;
  collections_searched: string[];
}

// === API functions ===

export const api = {
  // Books
  listBooks: (params?: { status?: string; domain?: string }) => {
    const q = new URLSearchParams();
    if (params?.status) q.set("status", params.status);
    if (params?.domain) q.set("domain", params.domain);
    const qs = q.toString();
    return apiFetch<Book[]>(`/api/books${qs ? `?${qs}` : ""}`);
  },
  getBook: (id: string) => apiFetch<BookDetail>(`/api/books/${id}`),
  uploadBook: (formData: FormData) => apiUpload<Book>("/api/books/upload", formData),
  deleteBook: (id: string) => apiFetch<{ status: string }>(`/api/books/${id}`, { method: "DELETE" }),
  processBook: (id: string) => apiFetch<{ status: string }>(`/api/books/${id}/process`, { method: "POST" }),
  getPages: (bookId: string) => apiFetch<BookPage[]>(`/api/books/${bookId}/pages`),
  getChunks: (bookId: string) => apiFetch<BookChunk[]>(`/api/books/${bookId}/chunks`),
  updateChunk: (bookId: string, chunkId: string, data: Partial<BookChunk>) =>
    apiFetch<BookChunk>(`/api/books/${bookId}/chunks/${chunkId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  getLogs: (bookId: string) => apiFetch<ProcessingLog[]>(`/api/books/${bookId}/logs`),

  // Recipes
  listRecipes: (params?: { category?: string; book_id?: string; q?: string }) => {
    const qs = new URLSearchParams();
    if (params?.category) qs.set("category", params.category);
    if (params?.book_id) qs.set("book_id", params.book_id);
    if (params?.q) qs.set("q", params.q);
    const s = qs.toString();
    return apiFetch<Recipe[]>(`/api/recipes${s ? `?${s}` : ""}`);
  },
  getRecipe: (id: string) => apiFetch<Recipe>(`/api/recipes/${id}`),

  // Plants
  listPlants: (q?: string) => apiFetch<Plant[]>(`/api/plants${q ? `?q=${q}` : ""}`),
  getPlant: (id: string) => apiFetch<Plant>(`/api/plants/${id}`),

  // Dictionaries
  listTerms: (category?: string) =>
    apiFetch<DictionaryTerm[]>(`/api/dictionaries${category ? `?category=${category}` : ""}`),
  lookupTerm: (term: string) => apiFetch<DictionaryTerm[]>(`/api/dictionaries/lookup?term=${term}`),
  createTerm: (data: Omit<DictionaryTerm, "id">) =>
    apiFetch<DictionaryTerm>("/api/dictionaries", { method: "POST", body: JSON.stringify(data) }),
  deleteTerm: (id: string) =>
    apiFetch<{ status: string }>(`/api/dictionaries/${id}`, { method: "DELETE" }),

  // Search
  search: (query: string, mode = "hybrid", collection?: string, limit = 10) =>
    apiFetch<SearchResponse>("/api/search", {
      method: "POST",
      body: JSON.stringify({ query, mode, collection, limit }),
    }),

  // Indexing
  indexBook: (bookId: string) =>
    apiFetch<{ status: string; indexed: number }>(`/api/indexing/book/${bookId}`, { method: "POST" }),
  deindexBook: (bookId: string) =>
    apiFetch<{ status: string }>(`/api/indexing/book/${bookId}`, { method: "DELETE" }),
};
