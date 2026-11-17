import React from "react";
import { Composition } from "remotion";
import { ClipShort } from "./ClipShort";
import { clipSchema, defaultProps, timeline } from "./schema";

export const RemotionRoot: React.FC = () => (
  <Composition
    id="ClipShort"
    component={ClipShort}
    width={1080}
    height={1920}
    fps={60}
    durationInFrames={60}
    schema={clipSchema}
    defaultProps={defaultProps}
    calculateMetadata={({ props }) => {
      const fps = Math.min(60, Math.round(props.fps));
      const segs = timeline(props);
      const last = segs[segs.length - 1];
      const seconds = last[2] + (last[1] - last[0]);
      return { fps, durationInFrames: Math.max(1, Math.round(seconds * fps)) };
    }}
  />
);
