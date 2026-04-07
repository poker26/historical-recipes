"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, Plant } from "@/lib/api";

export default function PlantDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const [plant, setPlant] = useState<Plant | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getPlant(id).then(setPlant).catch(console.error).finally(() => setLoading(false));
  }, [id]);

  if (loading) return <div className="empty"><span className="spinner" /></div>;
  if (!plant) return <div className="empty">Plant not found</div>;

  return (
    <>
      <div className="page-header">
        <div>
          <Link href="/plants" style={{ color: "var(--text-muted)", fontSize: 13 }}>Herbarium /</Link>
          <h1>{plant.name}</h1>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, fontSize: 14 }}>
          <div><span style={{ color: "var(--text-muted)" }}>Latin:</span> <em>{plant.name_latin || "—"}</em></div>
          <div><span style={{ color: "var(--text-muted)" }}>Family:</span> {plant.family || "—"}</div>
          <div>
            <span style={{ color: "var(--text-muted)" }}>Parts used:</span>{" "}
            {plant.parts_used?.length ? plant.parts_used.map((p) => (
              <span key={p} className="badge badge-green" style={{ marginLeft: 4 }}>{p}</span>
            )) : "—"}
          </div>
          <div>
            <span style={{ color: "var(--text-muted)" }}>Historical names:</span>{" "}
            {plant.names_historical?.join(", ") || "—"}
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        <div className="card">
          <h3 style={{ marginBottom: 12 }}>Properties</h3>
          <div className="empty">Properties will appear after processing herbalism books</div>
        </div>
        <div className="card">
          <h3 style={{ marginBottom: 12 }}>Used in Recipes</h3>
          <div className="empty">Recipe links will appear after indexing</div>
        </div>
      </div>
    </>
  );
}
