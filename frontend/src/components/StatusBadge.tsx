const STATUS_COLORS: Record<string, string> = {
  uploaded: "badge-gray",
  preprocessing: "badge-blue",
  ocr_pending: "badge-blue",
  ocr_done: "badge-yellow",
  postprocessing: "badge-yellow",
  normalized: "badge-yellow",
  parsed: "badge-blue",
  indexed: "badge-green",
  verified: "badge-green",
  processing: "badge-blue",
  failed: "badge-red",
  // chunk statuses
  raw: "badge-gray",
  cleaned: "badge-yellow",
  reviewed: "badge-green",
};

export default function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_COLORS[status] || "badge-gray";
  return <span className={`badge ${cls}`}>{status}</span>;
}
