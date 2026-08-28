import { signal } from "@preact/signals";

const ready = signal(true);

export function App() {
  return (
    <div id="workbench-shell">
      <p>OpenAI4S</p>
      {ready.value ? <span>ready</span> : null}
    </div>
  );
}
