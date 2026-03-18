/**
 * Tandem — Theme definitions
 * Five dark colour schemes selectable per user.
 * applyTheme() sets CSS custom properties on :root and swaps the favicon.
 */

export const THEMES = {
  "blueprint": {
    label: "Blueprint",
    accent: "#7c3aed",
    secondaryAccent: "#a78bfa",
    favicon: "/favicon-blueprint.svg",
    vars: {
      "--bg-primary":       "#0f0f1a",
      "--bg-secondary":     "#1a1a2e",
      "--bg-card":          "#16213e",
      "--bg-card-hover":    "#1a2744",
      "--bg-input":         "#0d1b2a",
      "--text-primary":     "#e8e8f0",
      "--text-secondary":   "#a0a0c0",
      "--text-muted":       "#6a6a8a",
      "--border":           "#2a2a4a",
      "--border-light":     "#3a3a5a",
      "--accent":           "#7c3aed",
      "--accent-hover":     "#6d28d9",
      "--accent-light":     "rgba(124, 58, 237, 0.15)",
      "--accent-glow":      "rgba(124, 58, 237, 0.4)",
      "--accent-secondary": "#a78bfa",
    },
  },

  "forest-night": {
    label: "Forest Night",
    accent: "#22c55e",
    secondaryAccent: "#14b8a6",
    favicon: "/favicon-forest-night.svg",
    vars: {
      "--bg-primary":       "#080f0a",
      "--bg-secondary":     "#0d1f13",
      "--bg-card":          "#0f2018",
      "--bg-card-hover":    "#142a1e",
      "--bg-input":         "#0a1a10",
      "--text-primary":     "#e8f0eb",
      "--text-secondary":   "#90b09a",
      "--text-muted":       "#5a7a62",
      "--border":           "#1a3a24",
      "--border-light":     "#2a4a34",
      "--accent":           "#22c55e",
      "--accent-hover":     "#16a34a",
      "--accent-light":     "rgba(34, 197, 94, 0.15)",
      "--accent-glow":      "rgba(34, 197, 94, 0.4)",
      "--accent-secondary": "#14b8a6",
    },
  },

  "ember": {
    label: "Ember",
    accent: "#f59e0b",
    secondaryAccent: "#ea580c",
    favicon: "/favicon-ember.svg",
    vars: {
      "--bg-primary":       "#0f0905",
      "--bg-secondary":     "#1c1008",
      "--bg-card":          "#1a1208",
      "--bg-card-hover":    "#221808",
      "--bg-input":         "#120a04",
      "--text-primary":     "#f0e8e0",
      "--text-secondary":   "#c0a080",
      "--text-muted":       "#8a6a40",
      "--border":           "#3a2010",
      "--border-light":     "#4a3020",
      "--accent":           "#f59e0b",
      "--accent-hover":     "#d97706",
      "--accent-light":     "rgba(245, 158, 11, 0.15)",
      "--accent-glow":      "rgba(245, 158, 11, 0.4)",
      "--accent-secondary": "#ea580c",
    },
  },

  "aurora": {
    label: "Aurora",
    accent: "#06b6d4",
    secondaryAccent: "#d946ef",
    favicon: "/favicon-aurora.svg",
    vars: {
      "--bg-primary":       "#07060f",
      "--bg-secondary":     "#10102a",
      "--bg-card":          "#0e1028",
      "--bg-card-hover":    "#141434",
      "--bg-input":         "#080820",
      "--text-primary":     "#e8e8f8",
      "--text-secondary":   "#9090c0",
      "--text-muted":       "#6060a0",
      "--border":           "#20204a",
      "--border-light":     "#30305a",
      "--accent":           "#06b6d4",
      "--accent-hover":     "#0891b2",
      "--accent-light":     "rgba(6, 182, 212, 0.15)",
      "--accent-glow":      "rgba(6, 182, 212, 0.4)",
      "--accent-secondary": "#d946ef",
    },
  },

  "slate": {
    label: "Slate",
    accent: "#38bdf8",
    secondaryAccent: "#a78bfa",
    favicon: "/favicon-slate.svg",
    vars: {
      "--bg-primary":       "#0c0e12",
      "--bg-secondary":     "#171c26",
      "--bg-card":          "#1a1f2e",
      "--bg-card-hover":    "#1e2438",
      "--bg-input":         "#0f1218",
      "--text-primary":     "#e8eaf0",
      "--text-secondary":   "#9098b0",
      "--text-muted":       "#606880",
      "--border":           "#282e3a",
      "--border-light":     "#343c4a",
      "--accent":           "#38bdf8",
      "--accent-hover":     "#0ea5e9",
      "--accent-light":     "rgba(56, 189, 248, 0.12)",
      "--accent-glow":      "rgba(56, 189, 248, 0.3)",
      "--accent-secondary": "#a78bfa",
    },
  },
}

export const DEFAULT_THEME = "blueprint"
export const THEME_SLUGS = Object.keys(THEMES)

export function applyTheme(slug) {
  const theme = THEMES[slug] || THEMES[DEFAULT_THEME]
  const root = document.documentElement
  for (const [prop, value] of Object.entries(theme.vars)) {
    root.style.setProperty(prop, value)
  }
  const favicon = document.getElementById("favicon")
  if (favicon) favicon.href = theme.favicon
}
