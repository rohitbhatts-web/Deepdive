/** Decorative teal/blue wave behind the run header. Purely ornamental: hidden from assistive technology, not interactive, not animated, and it carries no data. */
export function Wave({ className }: { className?: string }) {
  return (
    <svg aria-hidden="true" focusable="false" viewBox="0 0 600 200" preserveAspectRatio="xMaxYMid slice" className={className}>
      <defs>
        <linearGradient id="wave-a" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#14b8a6" stopOpacity="0" />
          <stop offset="0.35" stopColor="#14b8a6" stopOpacity="0.85" />
          <stop offset="1" stopColor="#2563eb" stopOpacity="0.9" />
        </linearGradient>
        <linearGradient id="wave-b" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#38bdf8" stopOpacity="0" />
          <stop offset="0.4" stopColor="#38bdf8" stopOpacity="0.8" />
          <stop offset="1" stopColor="#6366f1" stopOpacity="0.8" />
        </linearGradient>
        <radialGradient id="wave-orb" cx="0.35" cy="0.3" r="0.8">
          <stop offset="0" stopColor="#ffffff" stopOpacity="0.9" />
          <stop offset="0.45" stopColor="#7dd3fc" stopOpacity="0.55" />
          <stop offset="1" stopColor="#2563eb" stopOpacity="0.5" />
        </radialGradient>
      </defs>
      <g fill="none" strokeLinecap="round">
        {Array.from({ length: 14 }, (_, i) => (
          <path key={i} d={`M0 ${125 + i * 4} C 120 ${30 + i * 8}, 220 ${195 - i * 6}, 320 ${100 + i * 3} S 520 ${20 + i * 9}, 600 ${60 + i * 4}`}
            stroke={i % 2 ? 'url(#wave-b)' : 'url(#wave-a)'} strokeWidth={i % 3 === 0 ? 2 : 1.2} />
        ))}
      </g>
      <circle cx="540" cy="58" r="30" fill="url(#wave-orb)" />
      <circle cx="470" cy="132" r="11" fill="url(#wave-orb)" />
      <circle cx="418" cy="40" r="6" fill="url(#wave-orb)" />
    </svg>
  )
}
