export default function BookDetailPage({ params }: { params: { id: string } }) {
  return (
    <main>
      <h1>Book Details</h1>
      <p>Book {params.id} — coming soon</p>
    </main>
  );
}
