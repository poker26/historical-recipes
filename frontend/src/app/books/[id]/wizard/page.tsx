"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, WizardStatus, WizardSection, WizardRecipe } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";

const STEPS = [
  { num: 1, label: "Classify", key: "classify" },
  { num: 2, label: "Extract Text", key: "extract" },
  { num: 3, label: "Translate", key: "translate" },
  { num: 4, label: "Analyze Structure", key: "analyze" },
  { num: 5, label: "Extract Recipes", key: "recipes" },
  { num: 6, label: "Ingredients", key: "ingredients" },
  { num: 7, label: "Index", key: "index" },
];

const SECTION_TYPES = [
  "recipe_block", "bibliography", "toc", "introduction",
  "appendix", "chapter_header", "other",
];

const SECTION_COLORS: Record<string, string> = {
  recipe_block: "badge-green",
  bibliography: "badge-red",
  toc: "badge-gray",
  introduction: "badge-blue",
  appendix: "badge-yellow",
  chapter_header: "badge-gray",
  other: "badge-gray",
};

export default function WizardPage() {
  const params = useParams();
  const bookId = params.id as string;

  const [ws, setWs] = useState<WizardStatus | null>(null);
  const [activeStep, setActiveStep] = useState(1);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Step-specific state
  const [classifyResult, setClassifyResult] = useState<Record<string, unknown> | null>(null);
  const [extractResult, setExtractResult] = useState<Record<string, unknown> | null>(null);
  const [sections, setSections] = useState<WizardSection[]>([]);
  const [recipes, setRecipes] = useState<WizardRecipe[]>([]);
  const [ingredientResult, setIngredientResult] = useState<Record<string, unknown> | null>(null);
  const [indexResult, setIndexResult] = useState<Record<string, unknown> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const status = await api.wizardStatus(bookId);
      setWs(status);
      setActiveStep(status.wizard_step || 1);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [bookId]);

  useEffect(() => { refresh(); }, [refresh]);

  const runStep = async (fn: () => Promise<unknown>) => {
    setRunning(true);
    setError(null);
    try {
      const result = await fn();
      await refresh();
      return result;
    } catch (e) {
      setError(String(e));
      return null;
    } finally {
      setRunning(false);
    }
  };

  // Step handlers
  const handleClassify = () => runStep(async () => {
    const r = await api.wizardClassify(bookId);
    setClassifyResult(r as unknown as Record<string, unknown>);
  });

  const handleExtract = () => runStep(async () => {
    const r = await api.wizardExtract(bookId);
    setExtractResult(r as unknown as Record<string, unknown>);
  });

  const handleCleanup = () => runStep(async () => {
    await api.wizardCleanup(bookId);
  });

  const handleTranslate = () => runStep(async () => {
    await api.wizardTranslate(bookId);
  });

  const handleAnalyze = () => runStep(async () => {
    const r = await api.wizardAnalyze(bookId);
    setSections(r.sections);
  });

  const handleExtractRecipes = () => runStep(async () => {
    const r = await api.wizardExtractRecipes(bookId);
    setRecipes(r.recipes);
  });

  const handleMatchIngredients = () => runStep(async () => {
    const r = await api.wizardMatchIngredients(bookId);
    setIngredientResult(r as unknown as Record<string, unknown>);
  });

  const handleIndex = () => runStep(async () => {
    const r = await api.wizardIndex(bookId);
    setIndexResult(r as unknown as Record<string, unknown>);
  });

  const handleDeleteSection = async (sectionId: string) => {
    await api.wizardDeleteSection(bookId, sectionId);
    setSections(prev => prev.filter(s => s.id !== sectionId));
  };

  const handleChangeSectionType = async (sectionId: string, newType: string) => {
    await api.wizardUpdateSection(bookId, sectionId, { section_type: newType });
    setSections(prev => prev.map(s => s.id === sectionId ? { ...s, section_type: newType } : s));
  };

  const handleDeleteRecipe = async (recipeId: string) => {
    await api.wizardDeleteRecipe(bookId, recipeId);
    setRecipes(prev => prev.filter(r => r.id !== recipeId));
  };

  if (loading) return <div className="empty"><span className="spinner" /></div>;
  if (!ws) return <div className="empty">Book not found</div>;

  const currentStep = ws.wizard_step || 1;

  return (
    <>
      <div className="page-header">
        <div>
          <Link href={`/books/${bookId}`} style={{ color: "var(--text-muted)", fontSize: 13 }}>
            {ws.title} /
          </Link>
          <h1>Processing Wizard</h1>
        </div>
        <StatusBadge status={ws.status} />
      </div>

      {/* Step indicator */}
      <div style={{ display: "flex", gap: 4, marginBottom: 24 }}>
        {STEPS.map((step) => {
          const done = currentStep > step.num;
          const active = activeStep === step.num;
          return (
            <button
              key={step.num}
              onClick={() => setActiveStep(step.num)}
              style={{
                flex: 1,
                padding: "10px 8px",
                border: active ? "2px solid var(--blue)" : "1px solid var(--border)",
                borderRadius: 8,
                background: done ? "var(--green-bg, rgba(34,197,94,0.1))" : active ? "var(--blue-bg, rgba(59,130,246,0.1))" : "var(--bg-card)",
                cursor: "pointer",
                fontSize: 12,
                fontWeight: active ? 600 : 400,
                color: done ? "var(--green)" : active ? "var(--blue)" : "var(--text-muted)",
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: 16, marginBottom: 2 }}>{done ? "\u2713" : step.num}</div>
              {step.label}
            </button>
          );
        })}
      </div>

      {error && (
        <div className="card" style={{ background: "rgba(239,68,68,0.1)", border: "1px solid var(--red)", marginBottom: 16 }}>
          <pre style={{ fontSize: 12, color: "var(--red)", whiteSpace: "pre-wrap" }}>{error}</pre>
        </div>
      )}

      {/* Step content */}
      <div className="card" style={{ minHeight: 300 }}>
        {/* Step 1: Classify */}
        {activeStep === 1 && (
          <div>
            <h3 style={{ marginBottom: 16 }}>Step 1: Classify PDF</h3>
            <p style={{ color: "var(--text-muted)", marginBottom: 16 }}>
              Detect whether this is a text PDF or image PDF, and whether the language is modern or pre-reform Russian.
            </p>
            <button className="btn btn-primary" onClick={handleClassify} disabled={running}>
              {running ? "Analyzing..." : "Classify"}
            </button>
            {(classifyResult || ws.pdf_type) && (
              <div style={{ marginTop: 16, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div className="stat-card">
                  <div className="label">PDF Type</div>
                  <div className="value">{classifyResult?.pdf_type as string || ws.pdf_type}</div>
                </div>
                <div className="stat-card">
                  <div className="label">Language</div>
                  <div className="value">{classifyResult?.language as string || ws.language}</div>
                </div>
                <div className="stat-card">
                  <div className="label">Total Pages</div>
                  <div className="value">{classifyResult?.total_pages as number ?? ws.pages_count}</div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Step 2: Extract Text */}
        {activeStep === 2 && (
          <div>
            <h3 style={{ marginBottom: 16 }}>Step 2: Extract Text</h3>
            <p style={{ color: "var(--text-muted)", marginBottom: 16 }}>
              Extract text from all pages. Text PDFs use PyMuPDF, image PDFs use OCR + LLM fallback.
            </p>
            <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
              <button className="btn btn-primary" onClick={handleExtract} disabled={running}>
                {running ? "Extracting..." : "Extract All Pages"}
              </button>
              <button className="btn btn-outline" onClick={handleCleanup} disabled={running}>
                LLM Cleanup
              </button>
            </div>
            {(extractResult || ws.has_full_text) && (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
                <div className="stat-card">
                  <div className="label">Pages</div>
                  <div className="value">{extractResult?.total_pages as number ?? ws.pages_count}</div>
                </div>
                <div className="stat-card">
                  <div className="label">Text Length</div>
                  <div className="value">{((extractResult?.text_length as number ?? ws.text_length) / 1000).toFixed(0)}K chars</div>
                </div>
                <div className="stat-card">
                  <div className="label">Has Text</div>
                  <div className="value">{ws.has_full_text ? "Yes" : "No"}</div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Step 3: Translate */}
        {activeStep === 3 && (
          <div>
            <h3 style={{ marginBottom: 16 }}>Step 3: Translate</h3>
            <p style={{ color: "var(--text-muted)", marginBottom: 16 }}>
              For pre-reform Russian books: translate old orthography and archaic words to modern Russian.
              {ws.language === "modern_ru" && " This book is in modern Russian — you can skip this step."}
            </p>
            <button
              className="btn btn-primary"
              onClick={handleTranslate}
              disabled={running || ws.language === "modern_ru"}
            >
              {running ? "Translating..." : ws.language === "modern_ru" ? "Not Needed" : "Translate"}
            </button>
          </div>
        )}

        {/* Step 4: Analyze Structure */}
        {activeStep === 4 && (
          <div>
            <h3 style={{ marginBottom: 16 }}>Step 4: Analyze Structure</h3>
            <p style={{ color: "var(--text-muted)", marginBottom: 16 }}>
              LLM analyzes the book text and identifies sections: recipe blocks, bibliography, TOC, etc.
              You can correct section types or delete false positives.
            </p>
            <button className="btn btn-primary" onClick={handleAnalyze} disabled={running}>
              {running ? "Analyzing..." : "Analyze Structure"}
            </button>
            {sections.length > 0 && (
              <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 4 }}>
                  {sections.length} sections found
                  ({sections.filter(s => s.section_type === "recipe_block").length} recipe blocks)
                </div>
                {sections.map((s) => (
                  <div key={s.id} style={{ padding: 12, border: "1px solid var(--border)", borderRadius: 8, background: "var(--bg)" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                      <select
                        value={s.section_type}
                        onChange={(e) => handleChangeSectionType(s.id, e.target.value)}
                        style={{ fontSize: 12, padding: "2px 6px", borderRadius: 4, border: "1px solid var(--border)" }}
                      >
                        {SECTION_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                      </select>
                      <span className={`badge ${SECTION_COLORS[s.section_type] || "badge-gray"}`}>
                        L{s.start_line}–{s.end_line}
                      </span>
                      {s.estimated_recipe_count && (
                        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                          ~{s.estimated_recipe_count} recipes
                        </span>
                      )}
                      {s.confidence < 0.7 && (
                        <span className="badge badge-yellow">low confidence</span>
                      )}
                      <button
                        onClick={() => handleDeleteSection(s.id)}
                        style={{ marginLeft: "auto", color: "var(--red)", background: "none", border: "none", cursor: "pointer", fontSize: 14 }}
                        title="Delete section"
                      >
                        ×
                      </button>
                    </div>
                    <div style={{ fontWeight: 500, fontSize: 14, marginBottom: 4 }}>{s.title}</div>
                    {s.recipe_pattern && (
                      <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 4 }}>
                        Pattern: {s.recipe_pattern}
                      </div>
                    )}
                    <pre style={{ fontSize: 11, color: "var(--text-muted)", whiteSpace: "pre-wrap", maxHeight: 80, overflow: "auto" }}>
                      {s.content_preview}
                    </pre>
                  </div>
                ))}
              </div>
            )}
            {ws.sections_count > 0 && sections.length === 0 && (
              <div style={{ marginTop: 16, color: "var(--text-muted)" }}>
                {ws.sections_count} sections already analyzed. Click "Analyze Structure" to re-run.
              </div>
            )}
          </div>
        )}

        {/* Step 5: Extract Recipes */}
        {activeStep === 5 && (
          <div>
            <h3 style={{ marginBottom: 16 }}>Step 5: Extract Recipes</h3>
            <p style={{ color: "var(--text-muted)", marginBottom: 16 }}>
              LLM extracts structured recipes from identified recipe blocks.
              Review results and delete any false positives.
            </p>
            <button className="btn btn-primary" onClick={handleExtractRecipes} disabled={running}>
              {running ? "Extracting..." : "Extract Recipes"}
            </button>
            {recipes.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 8 }}>
                  {recipes.length} recipes extracted
                </div>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr><th>Name</th><th>Category</th><th>Ingredients</th><th>Preview</th><th></th></tr>
                    </thead>
                    <tbody>
                      {recipes.map((r) => (
                        <tr key={r.id}>
                          <td style={{ fontWeight: 500 }}>{r.name}</td>
                          <td><span className="badge badge-blue">{r.category}</span></td>
                          <td>{r.ingredients_count}</td>
                          <td style={{ maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--text-muted)", fontSize: 12 }}>
                            {r.text_preview}
                          </td>
                          <td>
                            <button
                              onClick={() => handleDeleteRecipe(r.id)}
                              style={{ color: "var(--red)", background: "none", border: "none", cursor: "pointer" }}
                            >
                              ×
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
            {ws.recipes_count > 0 && recipes.length === 0 && (
              <div style={{ marginTop: 16, color: "var(--text-muted)" }}>
                {ws.recipes_count} recipes already extracted. Click "Extract Recipes" to re-run.
              </div>
            )}
          </div>
        )}

        {/* Step 6: Ingredients */}
        {activeStep === 6 && (
          <div>
            <h3 style={{ marginBottom: 16 }}>Step 6: Match Ingredients</h3>
            <p style={{ color: "var(--text-muted)", marginBottom: 16 }}>
              Match extracted ingredients against the global dictionary.
              New ingredients are added automatically.
            </p>
            <button className="btn btn-primary" onClick={handleMatchIngredients} disabled={running}>
              {running ? "Matching..." : "Match Ingredients"}
            </button>
            {ingredientResult && (
              <div style={{ marginTop: 16, display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
                <div className="stat-card">
                  <div className="label">Matched</div>
                  <div className="value">{ingredientResult.matched as number}</div>
                </div>
                <div className="stat-card">
                  <div className="label">New Created</div>
                  <div className="value">{ingredientResult.new_created as number}</div>
                </div>
                <div className="stat-card">
                  <div className="label">Total</div>
                  <div className="value">{(ingredientResult.matched as number) + (ingredientResult.new_created as number)}</div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Step 7: Index */}
        {activeStep === 7 && (
          <div>
            <h3 style={{ marginBottom: 16 }}>Step 7: Vectorize & Index</h3>
            <p style={{ color: "var(--text-muted)", marginBottom: 16 }}>
              Generate BGE-M3 embeddings (dense + sparse) and index recipes to Qdrant collection recipes_v2.
            </p>
            <button className="btn btn-primary" onClick={handleIndex} disabled={running}>
              {running ? "Indexing..." : "Index to Qdrant"}
            </button>
            {indexResult && (
              <div style={{ marginTop: 16, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div className="stat-card">
                  <div className="label">Points Indexed</div>
                  <div className="value">{indexResult.points_indexed as number}</div>
                </div>
                <div className="stat-card">
                  <div className="label">Collection</div>
                  <div className="value">{indexResult.collection as string}</div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Navigation */}
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 16 }}>
        <button
          className="btn btn-outline"
          onClick={() => setActiveStep(Math.max(1, activeStep - 1))}
          disabled={activeStep === 1}
        >
          Back
        </button>
        <button className="btn btn-outline" onClick={refresh} disabled={running}>
          Refresh
        </button>
        <button
          className="btn btn-primary"
          onClick={() => setActiveStep(Math.min(7, activeStep + 1))}
          disabled={activeStep === 7}
        >
          Next Step
        </button>
      </div>

      {/* Processing Log */}
      <div className="card" style={{ marginTop: 24 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h3>Processing Log</h3>
          <button className="btn btn-outline" onClick={refresh} style={{ fontSize: 12, padding: "4px 10px" }}>
            Refresh
          </button>
        </div>
        {ws.logs.length === 0 ? (
          <div style={{ color: "var(--text-muted)" }}>No logs yet. Run a step to start processing.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Step</th><th>Status</th><th>Details</th><th>Time</th></tr></thead>
              <tbody>
                {ws.logs.map((log, i) => (
                  <tr key={i}>
                    <td>{log.step}</td>
                    <td><StatusBadge status={log.status} /></td>
                    <td style={{ fontSize: 12, color: "var(--text-muted)" }}>{log.details ? JSON.stringify(log.details) : ""}</td>
                    <td style={{ color: "var(--text-muted)", fontSize: 12 }}>{log.created_at ? new Date(log.created_at).toLocaleString("ru") : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
