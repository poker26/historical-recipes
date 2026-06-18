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

// Like apiFetch but also surfaces the X-Total-Count header so callers can
// paginate. Falls back to the page length when the header is absent.
export interface Paginated<T> {
  items: T[];
  total: number;
}

export async function apiFetchList<T>(path: string, options?: RequestInit): Promise<Paginated<T>> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`);
  }
  const items = (await res.json()) as T[];
  const header = res.headers.get("X-Total-Count");
  const total = header != null ? Number(header) : items.length;
  return { items, total };
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
  plant_id: string | null;
  plant_name: string | null;
}

export interface RecipeDetail extends Recipe {
  ingredients: RecipeIngredient[];
}

export interface Plant {
  id: string;
  name: string;
  name_latin: string | null;
  name_modern: string | null;
  names_historical: string[] | null;
  family: string | null;
  family_latin: string | null;
  parts_used: string[] | null;
  is_toxic: boolean;
  photo_url: string | null;
  photo_attribution: string | null;
  uses_count: number;
}

export interface PlantFilters {
  q?: string;
  compound?: string;
  action?: string;
  indication?: string;
  family?: string;
  is_toxic?: boolean;
}

export interface PlantFacets {
  compound_groups: { value: string; count: number }[];
  actions: { value: string; count: number }[];
}

export interface PlantMedicinalUse {
  id: string;
  part: string | null;
  action: string | null;
  action_system: string | null;
  indications: string | null;
  preparation: string | null;
  dosage: string | null;
  contraindications: string | null;
  original_text: string | null;
  confidence: number | null;
  source: string | null;
}

export interface PlantCompound {
  id: string;
  compound: string;
  compound_group: string | null;
  compound_id: string | null;
  part: string | null;
  notes: string | null;
  source: string | null;
}

export interface PlantHarvest {
  id: string;
  part: string | null;
  season: string | null;
  method: string | null;
  original_text: string | null;
  source: string | null;
}

export interface PlantHabitat {
  id: string;
  region: string | null;
  biotope: string | null;
  status: string | null;
  original_text: string | null;
  source: string | null;
}

export interface PlantToxicity {
  id: string;
  toxic_parts: string[] | null;
  symptoms: string | null;
  antidote: string | null;
  severity: string | null;
  original_text: string | null;
  source: string | null;
}

export interface PlantMention {
  id: string;
  book: string | null;
  original_name: string | null;
  page_number: number | null;
}

export interface PlantRecipeRef {
  id: string;
  name: string;
  category: string | null;
  book: string | null;
  year: number | null;
}

export interface PlantOilRef {
  id: string;
  name: string;
  name_latin: string | null;
  part: string | null;
  extraction: string | null;
  uses_count: number;
}

export interface PlantDetail extends Plant {
  description: string | null;
  photo_license: string | null;
  photo_source: string | null;
  inat_taxon_id: number | null;
  medicinal_uses: PlantMedicinalUse[];
  compounds: PlantCompound[];
  harvests: PlantHarvest[];
  habitats: PlantHabitat[];
  toxicities: PlantToxicity[];
  mentions: PlantMention[];
  recipes: PlantRecipeRef[];
  essential_oils: PlantOilRef[];
}

export interface Compound {
  id: string;
  name: string;
  name_latin: string | null;
  parent_id: string | null;
  compound_class: string | null;
  synonyms: string[];
  definition: string | null;
  linked_facts: number;
}

export interface CompoundPlantRef {
  id: string;
  name: string;
  name_latin: string | null;
  parts: string[];
  raw_names: string[];
}

export interface CompoundDetail {
  id: string;
  name: string;
  name_latin: string | null;
  parent: { id: string; name: string } | null;
  parent_id: string | null;
  compound_class: string | null;
  synonyms: string[];
  definition: string | null;
  original_text: string | null;
  children: { id: string; name: string }[];
  plants: CompoundPlantRef[];
  linked_facts: number;
}

export interface Oil {
  id: string;
  name: string;
  name_latin: string | null;
  plant_id: string | null;
  plant_name: string | null;
  plant_name_latin: string | null;
  part: string | null;
  extraction: string | null;
  aroma_profile: string | null;
  uses_count: number;
}

export interface OilUse {
  id: string;
  action: string | null;
  action_raw: string | null;
  indications: string | null;
  indication_concepts: string[];
  application: string | null;
  dosage: string | null;
  contraindications: string | null;
  original_text: string | null;
}

export interface OilDetail {
  id: string;
  name: string;
  name_latin: string | null;
  synonyms: string[];
  plant: { id: string; name: string; name_latin: string | null; photo_url: string | null } | null;
  source_plant_raw: string | null;
  compound_id: string | null;
  part: string | null;
  extraction: string | null;
  aroma_profile: string | null;
  description: string | null;
  original_text: string | null;
  uses: OilUse[];
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
  domain?: string;
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

// A running workflow plus the book it belongs to (dashboard /active feed).
export interface ActiveWorkflow extends WizardWorkflow {
  book_id: string;
  title: string | null;
  domain?: string;
}

// A long-running cleanup/dispatcher workflow on the ops dashboard.
export interface OpsWorkflow {
  id: string;
  type: string;
  status: string;                       // RUNNING | FAILED | COMPLETED | …
  start_time: string | null;
  close_time: string | null;
  progress?: Record<string, unknown> | null;   // latest heartbeat detail
  attempt?: number | null;
  error?: string | null;                // failure message when FAILED
}

// Canonical pipeline step order per domain (matches backend step_names_for_domain).
export const PIPELINE_STEP_NAMES = [
  "convert", "classify", "extract", "cleanup", "translate",
  "analyze", "extract_recipes", "match_ingredients", "index",
];
export const PIPELINE_STEP_NAMES_HERBALISM = [
  "convert", "classify", "extract", "cleanup", "translate",
  "analyze", "extract_plant_entries", "extract_recipes", "match_ingredients",
  "index", "enrich_inat",
];
export const PIPELINE_STEP_NAMES_REFERENCE = [
  "convert", "classify", "extract", "cleanup", "translate",
  "extract_vocabulary", "normalize_corpus", "index",
];
export const stepNamesForDomain = (domain?: string): string[] => {
  const d = (domain || "").toLowerCase();
  if (d === "herbalism" || d === "fungi") return PIPELINE_STEP_NAMES_HERBALISM;
  if (d === "reference") return PIPELINE_STEP_NAMES_REFERENCE;
  return PIPELINE_STEP_NAMES;
};

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
  listRecipes: (params?: { category?: string; book_id?: string; q?: string; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams();
    if (params?.category) qs.set("category", params.category);
    if (params?.book_id) qs.set("book_id", params.book_id);
    if (params?.q) qs.set("q", params.q);
    if (params?.limit != null) qs.set("limit", String(params.limit));
    if (params?.offset != null) qs.set("offset", String(params.offset));
    const s = qs.toString();
    return apiFetchList<Recipe>(`/api/recipes/${s ? `?${s}` : ""}`);
  },
  getRecipe: (id: string) => apiFetch<RecipeDetail>(`/api/recipes/${id}`),

  // Plants
  listPlants: (params?: PlantFilters & { limit?: number; offset?: number }) => {
    const qs = new URLSearchParams();
    if (params?.q) qs.set("q", params.q);
    if (params?.compound) qs.set("compound", params.compound);
    if (params?.action) qs.set("action", params.action);
    if (params?.indication) qs.set("indication", params.indication);
    if (params?.family) qs.set("family", params.family);
    if (params?.is_toxic != null) qs.set("is_toxic", String(params.is_toxic));
    if (params?.limit != null) qs.set("limit", String(params.limit));
    if (params?.offset != null) qs.set("offset", String(params.offset));
    const s = qs.toString();
    return apiFetchList<Plant>(`/api/plants/${s ? `?${s}` : ""}`);
  },
  getPlantFacets: () => apiFetch<PlantFacets>("/api/plants/facets"),
  getPlant: (id: string) => apiFetch<PlantDetail>(`/api/plants/${id}`),
  // Start the durable corpus-wide iNaturalist photo enrichment (Temporal sweep).
  runInatEnrichment: () =>
    apiFetch<{ status: string; workflow_id: string; run_id: string }>(
      "/api/plants/enrich-inat/run", { method: "POST" }),

  // Compounds (controlled phytochemistry vocabulary)
  listCompounds: () => apiFetch<Compound[]>("/api/compounds"),
  getCompound: (id: string) => apiFetch<CompoundDetail>(`/api/compounds/${id}`),
  normalizeCompounds: () =>
    apiFetch<{ status: string } & Record<string, unknown>>("/api/compounds/normalize", { method: "POST" }),

  // Essential oils (aromatherapy pillar)
  listOils: (params?: { q?: string; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams();
    if (params?.q) qs.set("q", params.q);
    if (params?.limit != null) qs.set("limit", String(params.limit));
    if (params?.offset != null) qs.set("offset", String(params.offset));
    const s = qs.toString();
    return apiFetch<{ items: Oil[]; total: number }>(`/api/oils${s ? `?${s}` : ""}`);
  },
  getOil: (id: string) => apiFetch<OilDetail>(`/api/oils/${id}`),
  oilsForCondition: (condition: string, limit = 50) =>
    apiFetch<{ condition: string; count: number; oils: (Oil & { matched_uses: number })[] }>(
      `/api/oils/for-condition?condition=${encodeURIComponent(condition)}&limit=${limit}`),
  normalizeOils: () =>
    apiFetch<{ status: string } & Record<string, unknown>>("/api/oils/normalize", { method: "POST" }),

  // Dictionaries
  listTerms: (category?: string) =>
    apiFetch<DictionaryTerm[]>(`/api/dictionaries/${category ? `?category=${category}` : ""}`),
  lookupTerm: (term: string) => apiFetch<DictionaryTerm[]>(`/api/dictionaries/lookup?term=${term}`),
  createTerm: (data: Omit<DictionaryTerm, "id">) =>
    apiFetch<DictionaryTerm>("/api/dictionaries/", { method: "POST", body: JSON.stringify(data) }),
  deleteTerm: (id: string) =>
    apiFetch<{ status: string }>(`/api/dictionaries/${id}`, { method: "DELETE" }),

  // Search
  search: (query: string, mode = "hybrid", collection?: string, limit = 10) =>
    apiFetch<SearchResponse>("/api/search/", {
      method: "POST",
      body: JSON.stringify({ query, mode, collection, limit }),
    }),

  // Indexing
  indexBook: (bookId: string) =>
    apiFetch<{ status: string; indexed: number }>(`/api/indexing/book/${bookId}`, { method: "POST" }),
  deindexBook: (bookId: string) =>
    apiFetch<{ status: string }>(`/api/indexing/book/${bookId}`, { method: "DELETE" }),

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
  activeWorkflows: () => apiFetch<ActiveWorkflow[]>("/api/wizard/active"),
  opsWorkflows: () => apiFetch<{ workflows: OpsWorkflow[] }>("/api/ops/workflows"),
  wizardWorkflowCancel: (bookId: string) =>
    apiFetch<{ status: string }>(`/api/wizard/${bookId}/workflow/cancel`, { method: "POST" }),

  // Data-quality «линтер гербария»
  qualitySummary: () => apiFetch<QualitySummaryRow[]>("/api/quality/summary"),
  qualityChecks: () => apiFetch<Record<string, QualityCheckMeta>>("/api/quality/checks"),
  qualityFindings: (params: { check_id?: string; severity?: string; status?: string; limit?: number; offset?: number }) => {
    const q = new URLSearchParams();
    if (params.check_id) q.set("check_id", params.check_id);
    if (params.severity) q.set("severity", params.severity);
    if (params.status) q.set("status", params.status);
    q.set("limit", String(params.limit ?? 50));
    q.set("offset", String(params.offset ?? 0));
    return apiFetch<QualityFinding[]>(`/api/quality/findings?${q.toString()}`);
  },
  qualitySweep: () => apiFetch<{ results: { check_id: string; found?: number; new?: number; updated?: number; staled?: number; error?: string }[] }>("/api/quality/sweep", { method: "POST", body: "null" }),
  qualityResolveTaxonomy: (limit = 1000) =>
    apiFetch<{ resolved: number; remaining: number; total_distinct: number; cached_total: number }>(`/api/quality/resolve-taxonomy?limit=${limit}`, { method: "POST" }),
  qualityTriage: (id: string, status: string, note?: string) =>
    apiFetch<{ id: string; status: string }>(`/api/quality/findings/${id}`, { method: "PATCH", body: JSON.stringify({ status, note }) }),
  qualityApply: (id: string) =>
    apiFetch<{ id: string; status: string; applied: string }>(`/api/quality/findings/${id}/apply`, { method: "POST" }),
  qualityDeleteEntity: (id: string) =>
    apiFetch<{ deleted: Record<string, string>; finding: string }>(`/api/quality/findings/${id}/delete-entity`, { method: "POST" }),

  // Nickname moderation (admin-only; /api/moderation is not in the public whitelist)
  moderationNicknames: (onlyCustom = true, limit = 200) =>
    apiFetch<{ devices: ModDevice[] }>(`/api/moderation/nicknames?only_custom=${onlyCustom}&limit=${limit}`),
  moderationBlock: (target: string, blocked: boolean) =>
    apiFetch<{ status: string; device_key: string; blocked: boolean }>(`/api/moderation/block`, {
      method: "POST",
      body: JSON.stringify({ target, blocked }),
    }),
};

export interface ModDevice {
  device_key: string;
  handle: string | null;
  nickname: string | null;
  blocked: boolean;
  last_seen: string | null;
}

export interface QualitySummaryRow {
  check_id: string;
  severity: string;
  status: string;
  count: number;
}

export interface QualityCheckMeta {
  severity: string;
  auto_fixable: boolean;
  description: string;
}

export interface QualityFinding {
  id: string;
  check_id: string;
  severity: string;
  entity_type: string;
  entity_id: string;
  title: string;
  evidence: Record<string, unknown> | null;
  suggested_fix: Record<string, unknown> | null;
  auto_fixable: boolean;
  status: string;
  first_seen: string;
  last_seen: string;
  note: string | null;
}
