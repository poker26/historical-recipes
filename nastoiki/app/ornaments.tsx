// Hand-drawn SVG ornaments — the «алхимическая аптека» visual language.
// All server components, pure inline SVG (no deps, crisp at any size, themeable
// via currentColor). Used for atmosphere and section rhythm.

/** A copper distillation still (alambic) — line drawing. The signature motif. */
export function Alambic({ className, style }: { className?: string; style?: React.CSSProperties }) {
  return (
    <svg viewBox="0 0 200 220" className={className} style={style} fill="none"
      stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true">
      {/* pot / cucurbit */}
      <path d="M55 150 Q40 120 62 104 Q100 86 138 104 Q160 120 145 150 Q140 178 100 180 Q60 178 55 150 Z" />
      {/* fire base */}
      <path d="M70 180 h60 M78 190 q22 14 44 0 M92 200 q8 6 16 0" opacity="0.7" />
      {/* onion head / capital */}
      <path d="M78 104 Q74 74 100 62 Q126 74 122 104" />
      <path d="M100 62 V44 M92 48 h16" />
      {/* swan-neck condenser to a spout */}
      <path d="M122 92 Q168 92 172 128 Q174 156 150 168" />
      <path d="M150 168 q-4 10 6 12 l14 -2" />
      {/* drips */}
      <circle cx="176" cy="184" r="1.6" fill="currentColor" stroke="none" />
      <circle cx="176" cy="194" r="1.2" fill="currentColor" stroke="none" opacity="0.6" />
    </svg>
  );
}

/** A botanical sprig — umbellifer (дягиль/angelica) feel. Decorative. */
export function Sprig({ className, style, flip = false }: { className?: string; style?: React.CSSProperties; flip?: boolean }) {
  return (
    <svg viewBox="0 0 120 200" className={className} style={{ transform: flip ? "scaleX(-1)" : undefined, ...style }}
      fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M60 198 C60 150 58 110 60 70" />
      {/* leaves */}
      <path d="M60 168 C40 162 30 150 26 134 C46 138 56 150 60 166" />
      <path d="M60 150 C80 144 90 132 94 116 C74 120 64 132 60 148" />
      <path d="M60 132 C42 126 33 115 30 100 C48 104 57 115 60 130" />
      {/* umbel */}
      <path d="M60 70 l-14 -18 M60 70 l-7 -22 M60 70 l0 -24 M60 70 l7 -22 M60 70 l14 -18" />
      <circle cx="46" cy="52" r="2.4" fill="currentColor" stroke="none" />
      <circle cx="53" cy="48" r="2.4" fill="currentColor" stroke="none" />
      <circle cx="60" cy="46" r="2.4" fill="currentColor" stroke="none" />
      <circle cx="67" cy="48" r="2.4" fill="currentColor" stroke="none" />
      <circle cx="74" cy="52" r="2.4" fill="currentColor" stroke="none" />
    </svg>
  );
}

/** A slender wormwood (полынь) branch — feathery. */
export function Wormwood({ className, style, flip = false }: { className?: string; style?: React.CSSProperties; flip?: boolean }) {
  return (
    <svg viewBox="0 0 100 210" className={className} style={{ transform: flip ? "scaleX(-1)" : undefined, ...style }}
      fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
      <path d="M50 208 C52 150 48 96 50 40" />
      {[188, 168, 148, 128, 108, 88, 68].map((y, i) => {
        const len = 10 + i * 3;
        return (
          <g key={y} opacity={0.9}>
            <path d={`M50 ${y} q-${len} -6 -${len + 4} -16`} />
            <path d={`M50 ${y} q${len} -6 ${len + 4} -16`} />
          </g>
        );
      })}
      <path d="M50 40 q-6 -14 0 -30 q6 16 0 30" />
    </svg>
  );
}

/** An ornamental section divider — a centered flourish with a leaf lozenge. */
export function Divider({ className, style }: { className?: string; style?: React.CSSProperties }) {
  return (
    <svg viewBox="0 0 240 24" className={className} style={style} fill="none"
      stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden="true">
      <path d="M10 12 H96" opacity="0.55" />
      <path d="M144 12 H230" opacity="0.55" />
      <path d="M96 12 q10 -9 24 0 q14 9 24 0" />
      <path d="M120 4 l6 8 l-6 8 l-6 -8 z" fill="currentColor" stroke="none" />
      <circle cx="100" cy="12" r="1.6" fill="currentColor" stroke="none" />
      <circle cx="140" cy="12" r="1.6" fill="currentColor" stroke="none" />
    </svg>
  );
}

/** Corner flourish for framing cards. */
export function Corner({ className, style }: { className?: string; style?: React.CSSProperties }) {
  return (
    <svg viewBox="0 0 60 60" className={className} style={style} fill="none"
      stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
      <path d="M6 30 C6 16 16 6 30 6" />
      <path d="M14 30 C14 22 22 14 30 14" opacity="0.6" />
      <circle cx="30" cy="6" r="2" fill="currentColor" stroke="none" />
      <circle cx="6" cy="30" r="2" fill="currentColor" stroke="none" />
    </svg>
  );
}
