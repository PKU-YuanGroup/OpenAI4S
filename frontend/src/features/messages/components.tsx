import { useEffect, useLayoutEffect, useRef } from "preact/hooks";
import { bindStreamingPre, type StreamingPreHandle } from "./delta";
import { bindMessageScroll, down, unbindMessageScroll } from "./scroll";

export type StreamingPreProps = {
  className?: string;
  handleRef?: { current: StreamingPreHandle | null };
};

/**
 * Tool-output `<pre>`. The text node is held on a ref; chunks call
 * `handle.append(delta)` → `textNode.appendData`. Do not set `textContent`
 * from the parent on every chunk.
 */
export function StreamingPre(props: StreamingPreProps) {
  const preRef = useRef<HTMLPreElement>(null);
  useLayoutEffect(() => {
    const pre = preRef.current;
    if (!pre) return;
    const textNode = document.createTextNode("");
    pre.appendChild(textNode);
    const handle = bindStreamingPre(textNode, "");
    if (props.handleRef) props.handleRef.current = handle;
    return () => {
      if (props.handleRef) props.handleRef.current = null;
    };
  }, [props.handleRef]);
  return <pre ref={preRef} className={props.className} />;
}

/**
 * Message column. `#messages` and `#jump-pill` keep the frozen DOM contract.
 * Live streaming is imperative (feed / flushRender) inside this host.
 */
export function MessageList() {
  const hostRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bindMessageScroll(hostRef.current);
    return () => unbindMessageScroll();
  }, []);
  return (
    <div className="messages-col">
      <div id="messages" ref={hostRef} />
      <button
        id="jump-pill"
        type="button"
        className="hidden"
        onClick={() => down(true)}
      />
    </div>
  );
}
