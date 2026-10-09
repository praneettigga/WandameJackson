/** A capture diagram, not a preview or a generated reconstruction. */
export function CaptureGuide() {
  return (
    <div className="capture-guide-hero">
      <div className="capture-hero-copy">
        <span className="capture-eyebrow">FROM YOUR CAMERA TO YOUR CANVAS</span>
        <h2>
          Bring your room
          <br />
          into 3D.
        </h2>
        <p>
          One slow walkthrough. Overlapping views.
          <br />A colored mesh you can orbit and export.
        </p>
        <div className="capture-format-tags">
          <span>10–60 sec video</span>
          <span>20–40 photos</span>
        </div>
      </div>
      <svg
        className="capture-room-diagram"
        viewBox="0 0 600 430"
        role="img"
        aria-label="Capture guide: walk around a room, keeping overlapping views of furniture and corners"
      >
        <defs>
          <linearGradient id="room-wall" x2="0" y2="1">
            <stop stopColor="#34444a" />
            <stop offset="1" stopColor="#253238" />
          </linearGradient>
          <radialGradient id="room-glow">
            <stop stopColor="#d8aa65" stopOpacity=".13" />
            <stop offset="1" stopColor="#d8aa65" stopOpacity="0" />
          </radialGradient>
        </defs>
        <ellipse cx="306" cy="307" rx="265" ry="110" fill="url(#room-glow)" />
        <path d="M95 279 300 163 511 280 305 400Z" fill="#2c393c" stroke="#697c7d" />
        <path d="M95 279V111L300 8V163Z" fill="url(#room-wall)" stroke="#637779" />
        <path d="M300 8 511 120V280L300 163Z" fill="#29383e" stroke="#637779" />
        {[0, 1, 2, 3].map((i) => (
          <g key={i} stroke="#526569" opacity=".3">
            <path d={`M${136 + i * 41} ${256 - i * 23}l211 117`} />
            <path d={`M${137 + i * 42} ${303 + i * 24}l205 -116`} />
          </g>
        ))}
        <path d="m357 72 91 48v68l-91-48Z" fill="#567579" stroke="#a7c3ba" />
        <path d="m402 97v65m-45-56 91 47" stroke="#a7c3ba" />
        <path d="m137 224 76-43 78 44-77 44Z" fill="#bd996a" />
        <path d="m137 224v32l77 44v-31Z" fill="#8a7055" />
        <path d="m214 269 77-44v33l-77 42Z" fill="#a28561" />
        <path
          d="m137 224v-31l76-42v30m-76 12 78 43v33m0-33 76-43v32"
          fill="#806c55"
          stroke="#d3b180"
        />
        <path d="m321 247 66-38 49 28-65 38Z" fill="#638981" />
        <path d="m321 247v15l50 29v-16m65-38v16l-65 38" fill="#405e59" stroke="#87a59a" />
        <path d="m335 269 1 18m83-26v16" stroke="#87a59a" strokeWidth="4" />
        <path
          d="M181 308Q140 277 172 251M245 348Q304 383 363 344T451 290Q473 266 445 251"
          fill="none"
          stroke="#edba72"
          strokeWidth="2"
          strokeDasharray="6 7"
        />
        <g transform="translate(223 330)">
          <path d="m0 0 30-28 13 35Z" fill="#edba72" opacity=".16" />
          <rect x="-13" y="-6" width="28" height="19" rx="5" fill="#edba72" />
          <circle cx="1" cy="3" r="5" fill="#293438" />
        </g>
        <circle cx="445" cy="251" r="5" fill="#edba72" />
        <circle cx="172" cy="251" r="4" fill="#edba72" />
        <text x="304" y="410" textAnchor="middle" fill="#a8b8b6" fontSize="12" letterSpacing="2">
          CAPTURE GUIDE · KEEP VIEWS OVERLAPPING
        </text>
      </svg>
      <div className="capture-guide-footer">
        <span>
          <b>01</b> Move slowly
        </span>
        <span>
          <b>02</b> Keep 70% overlap
        </span>
        <span>
          <b>03</b> Cover every corner
        </span>
      </div>
    </div>
  );
}
