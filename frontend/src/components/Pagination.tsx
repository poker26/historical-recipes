"use client";

interface PaginationProps {
  page: number;       // zero-based current page
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
}

/**
 * Compact Prev / Next pager shown under long lists. Renders nothing when
 * everything fits on one page. Page index is zero-based.
 */
export default function Pagination({ page, pageSize, total, onPageChange }: PaginationProps) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  if (total <= pageSize) return null;

  const from = page * pageSize + 1;
  const to = Math.min(total, (page + 1) * pageSize);

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 12,
        marginTop: 16,
        flexWrap: "wrap",
      }}
    >
      <span style={{ color: "var(--text-muted)", fontSize: 13 }}>
        {from}–{to} из {total}
      </span>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <button
          className="btn btn-outline btn-sm"
          disabled={page <= 0}
          onClick={() => onPageChange(page - 1)}
        >
          ← Назад
        </button>
        <span style={{ color: "var(--text-muted)", fontSize: 13, minWidth: 90, textAlign: "center" }}>
          Стр. {page + 1} / {pageCount}
        </span>
        <button
          className="btn btn-outline btn-sm"
          disabled={page >= pageCount - 1}
          onClick={() => onPageChange(page + 1)}
        >
          Вперёд →
        </button>
      </div>
    </div>
  );
}
