export default function RecipeDetailPage({ params }: { params: { id: string } }) {
  return (
    <main>
      <h1>Recipe Details</h1>
      <p>Recipe {params.id} — coming soon</p>
    </main>
  );
}
