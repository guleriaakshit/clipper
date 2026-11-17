import React from "react";
import { AbsoluteFill, OffthreadVideo, Sequence, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { ClipProps, timeline } from "./schema";

// local macOS system font: no network needed at render time (the dashboard preview uses the same face)
const fontFamily = '"Arial Black", "Arial", sans-serif';
const W = 1080;
const H = 1920;

/** One band of the frame showing a crop of the source, scaled to cover the band. */
const Crop: React.FC<{ p: ClipProps; rect: number[]; top: number; height: number; from: number; muted: boolean }> = ({ p, rect, top, height, from, muted }) => {
  const [rx, ry, rw, rh] = rect;
  const cw = rw * p.srcWidth;
  const ch = rh * p.srcHeight;
  const s = Math.max(W / cw, height / ch);
  const vw = p.srcWidth * s;
  const vh = p.srcHeight * s;
  const left = -rx * p.srcWidth * s + (W - cw * s) / 2;
  const topIn = -ry * p.srcHeight * s + (height - ch * s) / 2;
  return (
    <div style={{ position: "absolute", left: 0, top, width: W, height, overflow: "hidden", backgroundColor: "#000" }}>
      <OffthreadVideo
        src={staticFile(p.src)}
        trimBefore={Math.round(from * p.fps)}
        muted={muted}
        style={{ position: "absolute", left, top: topIn, width: vw, height: vh, maxWidth: "none" }}
      />
    </div>
  );
};

/** All bands for one segment of the timeline. */
const Segment: React.FC<{ p: ClipProps; from: number }> = ({ p, from }) => {
  if (p.layout === "noFace") {
    return <Crop p={p} rect={p.gameRect} top={0} height={H} from={from} muted={false} />;
  }
  const fh = Math.round(H * p.split);
  const faceTop = p.layout === "faceTop";
  return (
    <>
      <Crop p={p} rect={p.faceRect} top={faceTop ? 0 : H - fh} height={fh} from={from} muted />
      <Crop p={p} rect={p.gameRect} top={faceTop ? fh : 0} height={H - fh} from={from} muted={false} />
    </>
  );
};

/** One word at a time, no box, black shadow. */
const Subtitle: React.FC<{ p: ClipProps }> = ({ p }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  let word: { text: string; out: number } | null = null;
  for (const [a, b, o] of timeline(p)) {
    if (t < o || t >= o + (b - a)) continue;
    const src = a + (t - o);
    const w = p.words.find((x) => x.start <= src && src < x.end && x.start >= a);
    if (w) word = { text: w.text, out: o + (w.start - a) };
  }
  if (!word) return null;
  const pop = spring({ frame: frame - Math.round(word.out * fps), fps, config: { damping: 14, stiffness: 260, mass: 0.6 } });
  const scale = 0.82 + 0.18 * pop;
  const text = p.subUpper ? word.text.toUpperCase() : word.text;
  return (
    <div
      style={{
        position: "absolute", left: 40, right: 40, top: H * p.subY, transform: `translateY(-50%) scale(${scale})`,
        textAlign: "center", fontFamily, fontWeight: 900, fontSize: p.subSize, lineHeight: 1, color: p.subColor,
        letterSpacing: "-0.01em",
        textShadow: "0 6px 0 #000, 4px 4px 0 #000, -2px 2px 0 #000, 0 0 22px rgba(0,0,0,0.85), 0 0 6px rgba(0,0,0,0.95)",
      }}
    >
      {text}
    </div>
  );
};

export const ClipShort: React.FC<ClipProps> = (p) => {
  const { fps } = useVideoConfig();
  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      {timeline(p).map(([a, b, o], i) => (
        <Sequence key={i} from={Math.round(o * fps)} durationInFrames={Math.max(1, Math.round((b - a) * fps))}>
          <Segment p={p} from={a} />
        </Sequence>
      ))}
      {p.subsEnabled ? <Subtitle p={p} /> : null}
    </AbsoluteFill>
  );
};
