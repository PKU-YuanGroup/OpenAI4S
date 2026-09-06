import { useEffect, useRef } from "preact/hooks";
import {
  destroyActionTimelineView,
  renderActionTimeline,
} from "../../features/timeline/island";
import "./Timeline.css";

/**
 * Container + lifecycle for the Action Timeline island.
 * The virtualized ledger, overview SVG, and sidebar panels are created
 * imperatively inside `#dock-timeline` (app.js:5097-5154).
 */
export function Timeline() {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    renderActionTimeline();
    return () => destroyActionTimelineView();
  }, []);
  return (
    <div
      id="dock-timeline"
      class="dock-pane"
      aria-live="polite"
      ref={host}
    />
  );
}
