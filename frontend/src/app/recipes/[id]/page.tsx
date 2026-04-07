"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, Recipe } from "@/lib/api";

export default function RecipeDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const [recipe, setRecipe] = useState<Recipe | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getRecipe(id).then(setRecipe).catch(console.error).finally(() => setLoading(false));
  }, [id]);

  if (loading) return <div className="empty"><span className="spinner" /></div>;
  if (!recipe) return <div className="empty">Recipe not found</div>;

  return (
    <>
      <div className="page-header">
        <div>
          <Link href="/recipes" style={{ color: "var(--text-muted)", fontSize: 13 }}>Recipes /</Link>
          <h1>{recipe.name}</h1>
        </div>
        {recipe.category && <span className="badge badge-blue">{recipe.category}</span>}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        <div className="card">
          <h3 style={{ marginBottom: 12, color: "var(--text-muted)", fontSize: 13, textTransform: "uppercase" }}>Original Text</h3>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 14, lineHeight: 1.7 }}>
            {recipe.original_text || "—"}
          </pre>
        </div>
        <div className="card">
          <h3 style={{ marginBottom: 12, color: "var(--text-muted)", fontSize: 13, textTransform: "uppercase" }}>Normalized Text</h3>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 14, lineHeight: 1.7 }}>
            {recipe.normalized_text || "—"}
          </pre>
        </div>
      </div>
    </>
  );
}
