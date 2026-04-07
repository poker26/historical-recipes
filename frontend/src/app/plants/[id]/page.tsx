export default function PlantDetailPage({ params }: { params: { id: string } }) {
  return (
    <main>
      <h1>Plant Details</h1>
      <p>Plant {params.id} — coming soon</p>
    </main>
  );
}
