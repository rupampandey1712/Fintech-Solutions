import { useEffect, useRef, useState } from 'react';

const prefersReducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/** Animates from the previous value to `target` with an ease-out curve. */
export function useCountUp(target: number | null | undefined, durationMs = 900): number | null {
  const [value, setValue] = useState<number | null>(target ?? null);
  const fromRef = useRef(0);

  useEffect(() => {
    if (target === null || target === undefined || Number.isNaN(target)) {
      setValue(null);
      return;
    }
    if (prefersReducedMotion()) {
      setValue(target);
      fromRef.current = target;
      return;
    }
    const from = fromRef.current;
    const start = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const progress = Math.min(1, (now - start) / durationMs);
      const eased = 1 - Math.pow(1 - progress, 3);
      setValue(from + (target - from) * eased);
      if (progress < 1) frame = requestAnimationFrame(tick);
      else fromRef.current = target;
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target, durationMs]);

  return value;
}

export default useCountUp;
