export type ThemeMode = "light" | "dark" | "system";

/** Preference key shared with `theme-bootstrap.js`. Do not rename. */
export const THEME_STORAGE_KEY = "os-theme";

export type ApplyThemeOpts = { instant?: boolean };

type MolViewer = {
  setBackgroundColor: (color: string) => void;
  render?: () => void;
};

type HostWindow = Window & {
  S?: { _molViewer?: MolViewer };
  t?: (key: string, interpolated?: string) => string;
  hint?: (message: string) => void;
};

let theme: ThemeMode | undefined;
let watchingSystem = false;

function hostWindow(): HostWindow {
  return window as HostWindow;
}

function isThemeMode(value: string): value is ThemeMode {
  return value === "dark" || value === "light" || value === "system";
}

function storedTheme(): ThemeMode {
  try {
    const saved = localStorage.getItem(THEME_STORAGE_KEY);
    if (saved !== null && isThemeMode(saved)) return saved;
  } catch {
    /* private-mode / denied storage */
  }
  return "system";
}

export function getTheme(): ThemeMode {
  if (theme === undefined) theme = storedTheme();
  return theme;
}

export function themeIsDark(): boolean {
  const mode = getTheme();
  if (mode === "dark") return true;
  if (mode === "light") return false;
  try {
    if (typeof window.matchMedia !== "function") return false;
    return !!window.matchMedia("(prefers-color-scheme: dark)").matches;
  } catch {
    return false;
  }
}

function rethemeMolViewer(dark: boolean): void {
  try {
    const viewer = hostWindow().S?._molViewer;
    if (viewer && viewer.setBackgroundColor) {
      viewer.setBackgroundColor(dark ? "#1c1c19" : "white");
      if (viewer.render) viewer.render();
    }
  } catch {
    /* viewer gone or not yet mounted (F-18) */
  }
}

export function refreshThemeToggle(): void {
  const dark = themeIsDark();
  const name = dark ? "sun" : "moon";
  const translate = hostWindow().t;
  const title = typeof translate === "function" ? translate("theme.toggle") : "";
  for (const sel of ["#dash-theme", "#ws-theme"]) {
    const button = document.querySelector(sel);
    if (button === null) continue;
    const el = button as HTMLElement;
    el.dataset.icon = name;
    if (title !== "") {
      el.title = title;
      el.setAttribute("aria-label", title);
    }
  }
}

export function applyTheme(mode: string, opts?: ApplyThemeOpts): void {
  if (isThemeMode(mode)) theme = mode;
  else if (theme === undefined) theme = storedTheme();
  const dark = themeIsDark();
  const root = document.documentElement;
  if (opts && opts.instant) root.setAttribute("data-theme-instant", "");
  // data-theme on <html> is the only source of truth.
  root.setAttribute("data-theme", dark ? "dark" : "light");
  root.style.colorScheme = dark ? "dark" : "light";
  refreshThemeToggle();
  rethemeMolViewer(dark);
  if (opts && opts.instant) {
    requestAnimationFrame(() => {
      try {
        root.removeAttribute("data-theme-instant");
      } catch {
        /* document torn down */
      }
    });
  }
}

function toastTheme(mode: ThemeMode): void {
  const { t, hint } = hostWindow();
  if (typeof t !== "function" || typeof hint !== "function") return;
  hint(t("toast.theme", t("theme." + mode)));
}

export function setTheme(mode: string): void {
  theme = isThemeMode(mode) ? mode : "system";
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    /* private-mode / denied storage */
  }
  applyTheme(theme);
  toastTheme(theme);
}

export function cycleTheme(): void {
  // Quick toggle: light ↔ dark; from system, pick the opposite of the resolved value.
  if (getTheme() === "system") setTheme(themeIsDark() ? "light" : "dark");
  else setTheme(getTheme() === "dark" ? "light" : "dark");
}

function watchSystemTheme(): void {
  if (watchingSystem) return;
  watchingSystem = true;
  try {
    if (typeof window.matchMedia !== "function") {
      watchingSystem = false;
      return;
    }
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if (getTheme() === "system") applyTheme("system", { instant: true });
    };
    if (typeof mq.addEventListener === "function") mq.addEventListener("change", onChange);
    else if (typeof mq.addListener === "function") mq.addListener(onChange);
  } catch {
    watchingSystem = false;
  }
}

export function installTheme(): void {
  applyTheme(getTheme(), { instant: true });
  watchSystemTheme();
}
