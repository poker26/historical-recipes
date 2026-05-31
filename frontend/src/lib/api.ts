const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

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
  source_format: string;
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
  book_title: string | null;
  book_author: string | null;
  book_year: number | null;
  name: string;
  category: string | null;
  original_text: string | null;
  normalized_text: string | null;
  year: number | null;
  indexed_at: string | null;
}

export interface RecipeIngredient {
  id: string;
  name: string;
  original_name: string | null;
  amount: string | null;
  unit: string | null;
}

export interface RecipeDetail extends Recipe {
  ingredients: RecipeIngredient[];
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

// === Wizard Types ===

export interface WizardProgress {
  running: boolean;
  step: string | null;
  status: string;  // running | completed | error | idle
  elapsed: number;
  messages: string[];
  error: string | null;
  result: Record<string, unknown> | null;
}

export interface WizardStatus {
  book_id: string;
  title: string;
  wizard_step: number;
  status: string;
  pdf_type: string;
  language: string;
  has_full_text: boolean;
  text_length: number;
  pages_count: number;
  sections_count: number;
  recipes_count: number;
  progress: WizardProgress | null;
  logs: { step: string; status: string; details: Record<string, unknown> | null; created_at: string | null }[];
}

export interface WizardSection {
  id: string;
  section_type: string;
  title: string;
  start_line: number;
  end_line: number;
  content_preview: string;
  recipe_pattern: string | null;
  estimated_recipe_count: number | null;
  confidence: number;
}

export interface WizardRecipe {
  id: string;
  name: string;
  category: string;
  ingredients_count: number;
  text_preview: string;
}

// Durable Temporal pipeline workflow state.
export interface WizardWorkflow {
  exists: boolean;
  workflow_id?: string;
  status: string;            // none | RUNNING | COMPLETED | FAILED | CANCELED | TERMINATED | UNKNOWN
  start_time?: string | null;
  close_time?: string | null;
  current_step?: string | null;     // canonical step name of the running activity
  current_detail?: string | null;   // latest heartbeat message ("Chunk 14/81")
  current_attempt?: number | null;   // retry attempt of the running activity
  completed_steps?: string[];        // canonical step names already done
  result?: Record<string, unknown> | null;
}

// Canonical pipeline step order (matches backend STEP_NAMES).
export const PIPELINE_STEP_NAMES = [
  "classify", "extract", "cleanup", "translate",
  "analyze", "extract_recipes", "match_ingredients", "index",
];

// === API functions ===

export const api = {
  // Books
  listBooks: (params?: { status?: string; domain?: string }) => {
    const q = new URLSearchParams();
    if (params?.status) q.set("status", params.status);
    if (params?.domain) q.set("domain", params.domain);
    const qs = q.toString();
    return apiFetch<Book[]>(`/api/books/${qs ? `?${qs}` : ""}`);
  },
  getBook: (id: string) => apiFetch<BookDetail>(`/api/books/${id}/`),
  uploadBook: (formData: FormData) => apiUpload<Book>("/api/books/upload", formData),
  deleteBook: (id: string) => apiFetch<{ status: string }>(`/api/books/${id}`, { method: "DELETE" }),
  processBook: (id: string) => apiFetch<{ status: string }>(`/api/books/${id}/process`, { method: "POST" }),
  getPages: (bookId: string) => apiFetch<BookPage[]>(`/api/books/${bookId}/pages/`),
  getChunks: (bookId: string) => apiFetch<BookChunk[]>(`/api/books/${bookId}/chunks/`),
  updateChunk: (bookId: string, chunkId: string, data: Partial<BookChunk>) =>
    apiFetch<BookChunk>(`/api/books/${bookId}/chunks/${chunkId}/`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  getLogs: (bookId: string) => apiFetch<ProcessingLog[]>(`/api/books/${bookId}/logs/`),

  // Recipes
  listRecipes: (params?: { category?: string; book_id?: string; q?: string }) => {
    const qs = new URLSearchParams();
    if (params?.category) qs.set("category", params.category);
    if (params?.book_id) qs.set("book_id", params.book_id);
    if (params?.q) qs.set("q", params.q);
    const s = qs.toString();
    return apiFetch<Recipe[]>(`/api/recipes/${s ? `?${s}` : ""}`);
  },
  getRecipe: (id: string) => apiFetch<RecipeDetail>(`/api/recipes/${id}/`),

  // Plants
  listPlants: (q?: string) => apiFetch<Plant[]>(`/api/plants/${q ? `?q=${q}` : ""}`),
  getPlant: (id: string) => apiFetch<Plant>(`/api/plants/${id}/`),

  // Dictionaries
  listTerms: (category?: string) =>
    apiFetch<DictionaryTerm[]>(`/api/dictionaries/${category ? `?category=${category}` : ""}`),
  lookupTerm: (term: string) => apiFetch<DictionaryTerm[]>(`/api/dictionaries/lookup/?term=${term}`),
  createTerm: (data: Omit<DictionaryTerm, "id">) =>
    apiFetch<DictionaryTerm>("/api/dictionaries/", { method: "POST", body: JSON.stringify(data) }),
  deleteTerm: (id: string) =>
    apiFetch<{ status: string }>(`/api/dictionaries/${id}/`, { method: "DELETE" }),

  // Search
  search: (query: string, mode = "hybrid", collection?: string, limit = 10) =>
    apiFetch<SearchResponse>("/api/search/", {
      method: "POST",
      body: JSON.stringify({ query, mode, collection, limit }),
    }),

  // Indexing
  indexBook: (bookId: string) =>
    apiFetch<{ status: string; indexed: number }>(`/api/indexing/book/${bookId}/`, { method: "POST" }),
  deindexBook: (bookId: string) =>
    apiFetch<{ status: string }>(`/api/indexing/book/${bookId}/`, { method: "DELETE" }),

  // Wizard
  wizardStatus: (bookId: string) =>
    apiFetch<WizardStatus>(`/api/wizard/${bookId}/status`),
  wizardProgress: (bookId: string) =>
    apiFetch<WizardProgress>(`/api/wizard/${bookId}/progress`),
  wizardCancel: (bookId: string) =>
    apiFetch<{ status: string; step?: string }>(`/api/wizard/${bookId}/cancel`, { method: "POST" }),
  wizardClassify: (bookId: string) =>
    apiFetch<{ status: string; pdf_type: string; language: string; total_pages: number }>(`/api/wizard/${bookId}/classify`, { method: "POST" }),
  wizardExtract: (bookId: string) =>
    apiFetch<{ status: string; total_pages: number; text_length: number }>(`/api/wizard/${bookId}/extract`, { method: "POST" }),
  wizardCleanup: (bookId: string) =>
    apiFetch<{ status: string; text_length: number; used_llm: boolean }>(`/api/wizard/${bookId}/cleanup`, { method: "POST" }),
  wizardTranslate: (bookId: string) =>
    apiFetch<{ status: string; reason?: string }>(`/api/wizard/${bookId}/translate`, { method: "POST" }),
  wizardAnalyze: (bookId: string) =>
    apiFetch<{ status: string; sections: WizardSection[] }>(`/api/wizard/${bookId}/analyze`, { method: "POST" }),
  wizardUpdateSection: (bookId: string, sectionId: string, data: { section_type: string; title?: string }) =>
    apiFetch<{ status: string }>(`/api/wizard/${bookId}/sections/${sectionId}`, { method: "PUT", body: JSON.stringify(data) }),
  wizardDeleteSection: (bookId: string, sectionId: string) =>
    apiFetch<{ status: string }>(`/api/wizard/${bookId}/sections/${sectionId}`, { method: "DELETE" }),
  wizardExtractRecipes: (bookId: string) =>
    apiFetch<{ status: string; recipes_count: number; recipes: WizardRecipe[] }>(`/api/wizard/${bookId}/extract-recipes`, { method: "POST" }),
  wizardDeleteRecipe: (bookId: string, recipeId: string) =>
    apiFetch<{ status: string }>(`/api/wizard/${bookId}/recipes/${recipeId}`, { method: "DELETE" }),
  wizardMatchIngredients: (bookId: string) =>
    apiFetch<{ status: string; matched: number; new_created: number }>(`/api/wizard/${bookId}/match-ingredients`, { method: "POST" }),
  wizardIndex: (bookId: string) =>
    apiFetch<{ status: string; points_indexed: number; collection: string }>(`/api/wizard/${bookId}/index`, { method: "POST" }),

  // Durable Temporal pipeline (run the whole pipeline as one workflow)
  wizardRun: (bookId: string, startStep?: string) =>
    apiFetch<{ status: string; workflow_id: string; run_id: string; start_step: string }>(
      `/api/wizard/${bookId}/run${startStep ? `?start_step=${startStep}` : ""}`,
      { method: "POST" },
    ),
  wizardWorkflow: (bookId: string) =>
    apiFetch<WizardWorkflow>(`/api/wizard/${bookId}/workflow`),
  wizardWorkflowCancel: (bookId: string) =>
    apiFetch<{ status: string }>(`/api/wizard/${bookId}/workflow/cancel`, { method: "POST" }),
};
