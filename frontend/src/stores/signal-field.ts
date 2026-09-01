import { signal, type Signal } from "@preact/signals";

const resetters: Array<() => void> = [];

/** Signal whose initial value is rebuilt from `init` on `resetStoreFields()`. */
export function field<T>(init: () => T): Signal<T> {
  const s = signal(init());
  resetters.push(() => {
    s.value = init();
  });
  return s;
}

export function resetStoreFields(): void {
  for (const reset of resetters) reset();
}
