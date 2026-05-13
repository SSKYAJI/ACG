import { useLayoutEffect, useRef, useState } from "react";

/** Matches `.pipeline-grid` two-column breakpoint in index.css */
const PIPELINE_TWO_COL_MQ = "(min-width: 880px)";

function readScrollPaddingTopPx(): number {
  const raw = getComputedStyle(document.documentElement).scrollPaddingTop;
  const px = parseFloat(raw);
  return Number.isFinite(px) ? px : 80;
}

/**
 * Sticky pipeline headline: introductory flow is top-aligned with the right stack;
 * once native `position: sticky` engages, a sentinel above the sticky node leaves
 * the viewport band below `scroll-padding-top`, and we toggle centered flex layout.
 */
export function usePipelineAnchorPinned() {
  const sentinelRef = useRef<HTMLDivElement>(null);
  const [pinnedCentered, setPinnedCentered] = useState(false);

  useLayoutEffect(() => {
    const mq = window.matchMedia(PIPELINE_TWO_COL_MQ);
    let observer: IntersectionObserver | null = null;

    const teardown = () => {
      observer?.disconnect();
      observer = null;
    };

    const setup = () => {
      teardown();
      const node = sentinelRef.current;
      if (!mq.matches || !node) {
        setPinnedCentered(false);
        return;
      }
      const inset = readScrollPaddingTopPx();
      observer = new IntersectionObserver(
        ([entry]) => {
          setPinnedCentered(!entry.isIntersecting);
        },
        {
          root: null,
          rootMargin: `-${inset}px 0px 0px 0px`,
          threshold: 0,
        }
      );
      observer.observe(node);
    };

    setup();
    mq.addEventListener("change", setup);

    const ro = new ResizeObserver(() => setup());
    ro.observe(document.documentElement);

    return () => {
      mq.removeEventListener("change", setup);
      ro.disconnect();
      teardown();
    };
  }, []);

  return { sentinelRef, pinnedCentered };
}
