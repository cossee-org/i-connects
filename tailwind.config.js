/** @type {import('tailwindcss').Config} */
//
// i•CONNECTS brand tokens — ported from STAPLE-dev/tailwind.config.js so the
// Django rewrite shares a single visual language with the prototype.
//
// Accent coral `#f03d30` is the resolved value for the ambiguous `#231f20` on
// the original brand sheet (see Ultra Plan §A1). `#231f20` is retained only as
// `brand.dark` for deep shadows / heavy text accents.
//
module.exports = {
  content: [
    "./templates/**/*.html",
    "./apps/**/templates/**/*.html",
    "./apps/**/*.py",
    "./static/js/**/*.js",
  ],
  // The task calendar's range bars (templates/tasks/partials/calendar_range_bar.html)
  // position each bar with `col-start-{{ bar.start_col }}` / `col-end-{{ bar.end_col|add:"1" }}`,
  // where the column number is computed server-side and interpolated at render time.
  // Tailwind's content scanner only detects literal class strings in source files, so
  // these dynamically-built class names must be safelisted to always generate.
  safelist: [
    { pattern: /^col-start-[1-8]$/ },
    { pattern: /^col-end-[1-9]$/ },
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          navy: "#323895",
          coral: "#f03d30",
          light: "#bdcbe8",
          lighter: "#eaeff8",
          dark: "#231f20",
          text: "#333333",
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [require("daisyui")],
  daisyui: {
    themes: [
      {
        iconnects: {
          primary: "#323895",
          "primary-content": "#ffffff",
          secondary: "#4a5568",
          "secondary-content": "#ffffff",
          accent: "#f03d30",
          "accent-content": "#ffffff",
          neutral: "#333333",
          "neutral-content": "#ffffff",
          "base-100": "#ffffff",
          "base-200": "#eaeff8",
          "base-300": "#bdcbe8",
          "base-content": "#333333",
          info: "#3b82f6",
          "info-content": "#ffffff",
          success: "#16a34a",
          "success-content": "#ffffff",
          warning: "#d97706",
          "warning-content": "#ffffff",
          error: "#dc2626",
          "error-content": "#ffffff",
        },
      },
    ],
    darkTheme: false,
    logs: false,
  },
};
