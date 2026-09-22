import { ImageResponse } from "next/og";

// Programmatically generated social-preview image (no fabricated
// screenshot or stock illustration): the site's own title and tagline
// over its own instrument tokens, using next/og.
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: "72px 80px",
          backgroundColor: "#0b0c0e",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div
            style={{
              display: "flex",
              width: 26,
              height: 26,
              border: "2px solid #4d7cff",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <div style={{ width: 8, height: 8, backgroundColor: "#4d7cff" }} />
          </div>
          <div style={{ display: "flex", fontSize: 30, color: "#edeef0", fontWeight: 700 }}>VeriGate</div>
          <div
            style={{
              display: "flex",
              marginLeft: 18,
              fontSize: 18,
              letterSpacing: 4,
              color: "#ff7a29",
              textTransform: "uppercase",
            }}
          >
            Exploratory controlled study
          </div>
        </div>

        <div
          style={{
            display: "flex",
            fontSize: 74,
            color: "#edeef0",
            fontWeight: 600,
            letterSpacing: "-0.03em",
            maxWidth: 1000,
            lineHeight: 1.02,
          }}
        >
          Do passing visible tests make AI reviewers overconfident?
        </div>

        <div style={{ display: "flex", alignItems: "flex-end", gap: 56, borderTop: "1px solid #2a2d34", paddingTop: 28 }}>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <div style={{ display: "flex", fontSize: 54, color: "#edeef0", fontWeight: 600 }}>12</div>
            <div style={{ display: "flex", fontSize: 17, color: "#7c838f", letterSpacing: 2 }}>CANDIDATES</div>
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <div style={{ display: "flex", fontSize: 54, color: "#edeef0", fontWeight: 600 }}>108</div>
            <div style={{ display: "flex", fontSize: 17, color: "#7c838f", letterSpacing: 2 }}>OBSERVATIONS</div>
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <div style={{ display: "flex", fontSize: 54, color: "#ff7a29", fontWeight: 600 }}>0/12</div>
            <div style={{ display: "flex", fontSize: 17, color: "#7c838f", letterSpacing: 2 }}>
              HIDDEN-SUITE PASSES
            </div>
          </div>
        </div>
      </div>
    ),
    { ...size },
  );
}
