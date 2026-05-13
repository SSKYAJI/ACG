import { useLayoutEffect, useRef, useState } from "react";

/** Matches `.pipeline-grid` two-column breakpoint in index.css */
const PIPELINE_TWO_COL_MQ = "(min-width: 880px)";

function readScrollPaddingTopPx(): number {
  const raw = getComputedStyle(document.documentElement).scrollPaddingTop;
  const px = parseFloat(raw);
  return Number.isFinite(px) ? px : 80;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function usePipelineAnchorPinned() {
  const sentinelRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const [translateY, setTranslateY] = useState(0);

  useLayoutEffect(() => {
    const mq = window.matchMedia(PIPELINE_TWO_COL_MQ);
    let frame = 0;

    const cancelFrame = () => {
      if (frame) {
        window.cancelAnimationFrame(frame);
        frame = 0;
      }
    };

    const update = () => {
      const sentinel = sentinelRef.current;
      const content = contentRef.current;

      if (!mq.matches || !sentinel || !content) {
        setTranslateY(0);
        return;
      }

      const inset = readScrollPaddingTopPx();
      const availableHeight = Math.max(0, window.innerHeight - inset);
      const contentHeight = content.getBoundingClientRect().height;
      const centeredOffset = Math.max(0, (availableHeight - contentHeight) / 2);
      const distancePastStickyTop = inset - sentinel.getBoundingClientRect().top;
      const ramp = Math.max(180, centeredOffset || 0);
      const progress = clamp(distancePastStickyTop / ramp, 0, 1);
      const next = progress * centeredOffset;

      setTranslateY((prev) => (Math.abs(prev - next) < 0.5 ? prev : next));
    };

    const requestUpdate = () => {
      cancelFrame();
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        update();
      });
    };

    requestUpdate();
    mq.addEventListener("change", requestUpdate);
    window.addEventListener("scroll", requestUpdate, { passive: true });
    window.addEventListener("resize", requestUpdate);

    const ro = new ResizeObserver(() => requestUpdate());
    ro.observe(document.documentElement);
    if (contentRef.current) {
      ro.observe(contentRef.current);
    }

    return () => {
      mq.removeEventListener("change", requestUpdate);
      window.removeEventListener("scroll", requestUpdate);
      window.removeEventListener("resize", requestUpdate);
      ro.disconnect();
      cancelFrame();
    };
  }, []);

  return { sentinelRef, contentRef, translateY };
}
