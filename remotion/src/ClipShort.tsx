import React from "react";
import {
  AbsoluteFill, Audio, Easing, Img, OffthreadVideo, Sequence, random, spring, staticFile, useCurrentFrame, useVideoConfig,
} from "remotion";
import { ClipProps, Fx, timeline } from "./schema";

// a system font: no network needed at render time (the dashboard preview uses the same face)
const fontFamily = '"Arial Black", "Arial", sans-serif';
const W = 1080;
const H = 1920;
// no box behind text, just a hard black shadow plus a soft glow so it reads on any background
const SHADOW = "0 6px 0 #000, 4px 4px 0 #000, -2px 2px 0 #000, 0 0 22px rgba(0,0,0,0.85), 0 0 6px rgba(0,0,0,0.95)";

type Band = "all" | "face" | "game";
const active = <T extends { at: number; dur: number }>(xs: T[], t: number) => xs.filter((x) => t >= x.at && t < x.at + x.dur);
const clamp01 = (v: number) => Math.max(0, Math.min(1, v));

/** Punch-in zoom for a band at output time t: eased in over 0.18 s, out over 0.25 s (or a hard cut). */
function zoomAt(fx: Fx, t: number, band: Band) {
  for (const z of active(fx.zooms, t)) {
    if (z.band !== band) continue;
    const k = z.ease ? Easing.inOut(Easing.cubic)(clamp01(Math.min((t - z.at) / 0.18, (z.at + z.dur - t) / 0.25))) : 1;
    return { s: 1 + (z.scale - 1) * k, x: z.x, y: z.y };
  }
  return { s: 1, x: 0.5, y: 0.5 };
}

/** Game audio level at output time t: the lowest duck of any meme or sound playing then. */
function duckAt(fx: Fx, t: number) {
  let v = 1;
  for (const o of active(fx.overlays, t)) v = Math.min(v, o.duck);
  for (const s of active(fx.sounds, t)) v = Math.min(v, s.duck);
  return v;
}

const GRADE: Record<Fx["grade"], { filter: string; tint: string | null }> = {
  none: { filter: "none", tint: null },
  warm: { filter: "saturate(1.18) contrast(1.06) brightness(1.02)", tint: "rgba(255,150,60,0.16)" },
  cold: { filter: "saturate(0.92) contrast(1.1) brightness(0.98)", tint: "rgba(60,140,255,0.18)" },
};

/** One band of the frame showing a crop of the source, scaled to cover the band. */
const Crop: React.FC<{
  p: ClipProps; rect: number[]; top: number; height: number; from: number; outStart: number; band: Band; muted: boolean;
}> = ({ p, rect, top, height, from, outStart, band, muted }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const [rx, ry, rw, rh] = rect;
  const cw = rw * p.srcWidth;
  const ch = rh * p.srcHeight;
  const s = Math.max(W / cw, height / ch);
  const vw = p.srcWidth * s;
  const vh = p.srcHeight * s;
  const left = -rx * p.srcWidth * s + (W - cw * s) / 2;
  const topIn = -ry * p.srcHeight * s + (height - ch * s) / 2;
  const z = zoomAt(p.fx, outStart + frame / fps, band);
  // game audio: silent over censored words, ducked under memes and sounds
  const volume = (f: number) => {
    const src = from + f / fps;
    if (p.fx.censor.some((c) => src >= c.start && src < c.end)) return 0;
    return duckAt(p.fx, outStart + f / fps);
  };
  return (
    <div style={{ position: "absolute", left: 0, top, width: W, height, overflow: "hidden", backgroundColor: "#000" }}>
      <div style={{ position: "absolute", inset: 0, transform: `scale(${z.s})`, transformOrigin: `${z.x * 100}% ${z.y * 100}%` }}>
        <OffthreadVideo
          src={staticFile(p.src)}
          trimBefore={Math.round(from * p.fps)}
          muted={muted}
          volume={muted ? 0 : volume}
          style={{ position: "absolute", left, top: topIn, width: vw, height: vh, maxWidth: "none" }}
        />
      </div>
    </div>
  );
};

/** All bands for one segment of the timeline. */
const Segment: React.FC<{ p: ClipProps; from: number; outStart: number }> = ({ p, from, outStart }) => {
  if (p.layout === "noFace") {
    return <Crop p={p} rect={p.gameRect} top={0} height={H} from={from} outStart={outStart} band="game" muted={false} />;
  }
  const fh = Math.round(H * p.split);
  const faceTop = p.layout === "faceTop";
  return (
    <>
      <Crop p={p} rect={p.faceRect} top={faceTop ? 0 : H - fh} height={fh} from={from} outStart={outStart} band="face" muted />
      <Crop p={p} rect={p.gameRect} top={faceTop ? fh : 0} height={H - fh} from={from} outStart={outStart} band="game" muted={false} />
    </>
  );
};

/** The stream itself: segments, then grade, whole-frame zoom and shake on top. */
const Picture: React.FC<{ p: ClipProps }> = ({ p }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const z = zoomAt(p.fx, t, "all");
  let dx = 0;
  let dy = 0;
  for (const s of active(p.fx.shakes, t)) {
    const fade = 1 - (t - s.at) / s.dur;
    dx += s.strength * fade * (random(`sx${frame}`) * 2 - 1);
    dy += s.strength * fade * (random(`sy${frame}`) * 2 - 1);
  }
  const g = GRADE[p.fx.grade];
  return (
    <AbsoluteFill style={{ transform: `translate(${dx}px, ${dy}px) scale(${z.s})`, transformOrigin: `${z.x * 100}% ${z.y * 100}%` }}>
      <AbsoluteFill style={{ filter: g.filter }}>
        {timeline(p).map(([a, b, o], i) => (
          <Sequence key={i} from={Math.round(o * fps)} durationInFrames={Math.max(1, Math.round((b - a) * fps))}>
            <Segment p={p} from={a} outStart={o} />
          </Sequence>
        ))}
      </AbsoluteFill>
      {g.tint ? <AbsoluteFill style={{ backgroundColor: g.tint, mixBlendMode: "soft-light" }} /> : null}
    </AbsoluteFill>
  );
};

const Flashes: React.FC<{ fx: Fx }> = ({ fx }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  return (
    <>
      {active(fx.flashes, t).map((f, i) => (
        <AbsoluteFill key={i} style={{ backgroundColor: f.color, opacity: 1 - (t - f.at) / f.dur }} />
      ))}
    </>
  );
};

/** A meme on top: pops in, plays with its own sound (the game is ducked under it). */
const Overlay: React.FC<{ o: Fx["overlays"][number] }> = ({ o }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  // full: a cut-away filling the frame; otherwise a framed pop-in at (x, y), w wide
  const style: React.CSSProperties = o.full
    ? { width: "100%", height: "100%", objectFit: o.fit }
    : { width: "100%", display: "block" };
  const media = o.kind === "video"
    ? <OffthreadVideo src={staticFile(o.src)} trimBefore={Math.round(o.from * fps)} volume={o.volume} style={style} />
    : <Img src={staticFile(o.src)} style={style} />;
  if (o.full) {
    return <AbsoluteFill style={{ backgroundColor: "#000" }}>{media}</AbsoluteFill>;
  }
  const pop = spring({ frame, fps, config: { damping: 13, stiffness: 240, mass: 0.6 } });
  const w = o.w * W;
  return (
    <div style={{
      position: "absolute", left: o.x * W - w / 2, top: o.y * H, width: w,
      transform: `translateY(-50%) scale(${0.6 + 0.4 * pop}) rotate(${o.rotate}deg)`, opacity: Math.min(1, pop * 1.5),
      filter: "drop-shadow(0 10px 24px rgba(0,0,0,0.6))",
    }}>
      {media}
    </div>
  );
};

const TextPop: React.FC<{ x: Fx["texts"][number] }> = ({ x }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const pop = spring({ frame, fps, config: { damping: 14, stiffness: 260, mass: 0.6 } });
  return (
    <div style={{
      position: "absolute", left: 50, right: 50, top: H * x.y, transform: `translateY(-50%) scale(${0.8 + 0.2 * pop})`,
      textAlign: "center", fontFamily, fontWeight: 900, fontSize: x.size, lineHeight: 1.05, color: x.color,
      textShadow: SHADOW, letterSpacing: "-0.01em",
    }}>
      {x.text}
    </div>
  );
};

/** One word at a time, no box, black shadow; emphasised words bigger in the accent colour. */
const Subtitle: React.FC<{ p: ClipProps }> = ({ p }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  let word: { text: string; out: number; emph: boolean } | null = null;
  for (const [a, b, o] of timeline(p)) {
    if (t < o || t >= o + (b - a)) continue;
    const src = a + (t - o);
    const w = p.words.find((x) => x.start <= src && src < x.end && x.start >= a);
    if (w) word = { text: w.text, out: o + (w.start - a), emph: !!w.emph };
  }
  if (!word) return null;
  const pop = spring({ frame: frame - Math.round(word.out * fps), fps, config: { damping: 14, stiffness: 260, mass: 0.6 } });
  const scale = (0.82 + 0.18 * pop) * (word.emph ? p.fx.emphScale : 1);
  const text = p.subUpper ? word.text.toUpperCase() : word.text;
  return (
    <div
      style={{
        position: "absolute", left: 40, right: 40, top: H * p.subY, transform: `translateY(-50%) scale(${scale})`,
        textAlign: "center", fontFamily, fontWeight: 900, fontSize: p.subSize, lineHeight: 1,
        color: word.emph ? p.fx.emphColor : p.subColor, letterSpacing: "-0.01em", textShadow: SHADOW,
      }}
    >
      {text}
    </div>
  );
};

const at = (s: number, fps: number) => Math.round(s * fps);
const len = (s: number, fps: number) => Math.max(1, Math.round(s * fps));

export const ClipShort: React.FC<ClipProps> = (p) => {
  const { fps } = useVideoConfig();
  const fx = p.fx;
  return (
    <AbsoluteFill style={{ backgroundColor: "#000", overflow: "hidden" }}>
      <Picture p={p} />
      <Flashes fx={fx} />
      {fx.overlays.map((o, i) => (
        <Sequence key={`o${i}`} from={at(o.at, fps)} durationInFrames={len(o.dur, fps)}>
          <Overlay o={o} />
        </Sequence>
      ))}
      {fx.sounds.map((s, i) => (
        <Sequence key={`s${i}`} from={at(s.at, fps)} durationInFrames={len(s.dur, fps)}>
          <Audio src={staticFile(s.src)} volume={s.volume} />
        </Sequence>
      ))}
      {fx.beep
        ? fx.censorOut.map((c, i) => (
          <Sequence key={`b${i}`} from={at(c.at, fps)} durationInFrames={len(c.dur, fps)}>
            <Audio src={staticFile(p.beepSrc)} volume={fx.beepVolume} />
          </Sequence>
        ))
        : null}
      {fx.texts.map((x, i) => (
        <Sequence key={`t${i}`} from={at(x.at, fps)} durationInFrames={len(x.dur, fps)}>
          <TextPop x={x} />
        </Sequence>
      ))}
      {p.subsEnabled ? <Subtitle p={p} /> : null}
    </AbsoluteFill>
  );
};
