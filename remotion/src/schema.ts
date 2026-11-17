import { z } from "zod";
import { zColor } from "@remotion/zod-types";

// [x, y, w, h], normalised 0..1 of the source frame
const rect = z.tuple([z.number().min(0).max(1), z.number().min(0).max(1), z.number().min(0.02).max(1), z.number().min(0.02).max(1)]);

export const wordSchema = z.object({ text: z.string(), start: z.number(), end: z.number(), emph: z.boolean().optional() });

// Effects, already checked and filled in by clipper/render.py (normalize_fx). `at`/`dur` are seconds of the
// finished Short; censor spans are seconds of the source clip.
const span = { at: z.number().min(0), dur: z.number().positive() };
export const fxSchema = z.object({
  grade: z.enum(["none", "warm", "cold"]),
  zooms: z.array(z.object({ ...span, scale: z.number(), x: z.number(), y: z.number(), band: z.enum(["all", "face", "game"]), ease: z.boolean() })),
  shakes: z.array(z.object({ ...span, strength: z.number() })),
  flashes: z.array(z.object({ ...span, color: z.string() })),
  overlays: z.array(z.object({
    ...span, src: z.string(), kind: z.enum(["video", "image"]), from: z.number(), x: z.number(), y: z.number(), w: z.number(),
    full: z.boolean(), fit: z.enum(["contain", "cover"]), volume: z.number(), duck: z.number(), rotate: z.number(),
  })),
  sounds: z.array(z.object({ ...span, src: z.string(), volume: z.number(), duck: z.number() })),
  texts: z.array(z.object({ ...span, text: z.string(), y: z.number(), color: z.string(), size: z.number() })),
  censor: z.array(z.object({ start: z.number(), end: z.number() })),
  censorOut: z.array(z.object(span)),
  beep: z.boolean(),
  beepVolume: z.number(),
  emphColor: z.string(),
  emphScale: z.number(),
});

export const clipSchema = z.object({
  src: z.string().describe("file in public/, e.g. clips/youtube-abc-1.mp4"),
  srcWidth: z.number().int().positive(),
  srcHeight: z.number().int().positive(),
  fps: z.number().positive().describe("source frame rate; the Short renders at the same rate"),
  clipLength: z.number().positive().describe("seconds"),
  trimStart: z.number().min(0).describe("main body starts here (s, clip-relative)"),
  trimEnd: z.number().min(0).describe("main body ends here (s, clip-relative)"),
  hookStart: z.number().min(0).describe("cold-open hot moment start (s)"),
  hookEnd: z.number().min(0).describe("cold-open hot moment end (s)"),
  hookEnabled: z.boolean(),
  layout: z.enum(["faceTop", "faceBottom", "noFace"]),
  split: z.number().min(0.2).max(0.6).describe("share of the frame for the face band"),
  faceRect: rect,
  gameRect: rect,
  subsEnabled: z.boolean(),
  subColor: zColor(),
  subSize: z.number().min(40).max(220),
  subY: z.number().min(0.05).max(0.95).describe("vertical centre of the word, 0 = top"),
  subUpper: z.boolean(),
  words: z.array(wordSchema),
  fx: fxSchema,
  beepSrc: z.string().describe("censor tone in public/"),
});

export type ClipProps = z.infer<typeof clipSchema>;
export type Fx = z.infer<typeof fxSchema>;

export const noFx: Fx = {
  grade: "none", zooms: [], shakes: [], flashes: [], overlays: [], sounds: [], texts: [], censor: [], censorOut: [],
  beep: true, beepVolume: 0.5, emphColor: "#FFD400", emphScale: 1.25,
};

export const defaultProps: ClipProps = {
  src: "clips/demo.mp4",
  srcWidth: 1920,
  srcHeight: 1080,
  fps: 60,
  clipLength: 30,
  trimStart: 0,
  trimEnd: 30,
  hookStart: 10,
  hookEnd: 13,
  hookEnabled: true,
  layout: "faceTop",
  split: 0.4,
  faceRect: [0.7, 0.05, 0.28, 0.35],
  gameRect: [0.2, 0, 0.6, 1],
  subsEnabled: true,
  subColor: "#FFFFFF",
  subSize: 110,
  subY: 0.4,
  subUpper: true,
  words: [],
  fx: noFx,
  beepSrc: "sfx/beep.wav",
};

/** Output timeline: [srcStart, srcEnd, outStart] segments, hook first, then the full clip. */
export function timeline(p: ClipProps): [number, number, number][] {
  const segs: [number, number, number][] = [];
  let o = 0;
  if (p.hookEnabled && p.hookEnd > p.hookStart) {
    segs.push([p.hookStart, p.hookEnd, 0]);
    o = p.hookEnd - p.hookStart;
  }
  segs.push([p.trimStart, Math.min(p.trimEnd, p.clipLength), o]);
  return segs;
}
