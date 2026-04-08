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
  // wizard statuses
  classified: "badge-blue",
  extracted: "badge-blue",
  analyzed: "badge-yellow",
  recipes_extracted: "badge-yellow",
  completed: "badge-green",
  skipped: "badge-gray",
  started: "badge-blue",
  split: "badge-blue",
  postprocessed: "badge-yellow",
};

export default function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_COLORS[status] || "badge-gray";
  return <span className={`badge ${cls}`}>{status}</span>;
}
