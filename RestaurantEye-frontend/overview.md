# RestaurantEye Frontend Overview

This project is still a React app, but instead of plain Create React App (CRA), it uses a more modern stack and a feature-based folder structure.

If you are coming from CRA, think of this as:

- Faster dev/build tool: Vite instead of react-scripts
- Better routing setup: React Router with nested layouts
- Utility-first styling: Tailwind CSS v4 + design tokens
- Prebuilt UI primitives: shadcn/ui + Radix UI
- Theme system (light/dark/system) already wired

## 1) Technology Stack (What makes it work)

### Core runtime

- React 19
- React DOM 19
- React Router DOM 7

### Build & development tooling

- Vite (dev server, bundling, production build)
- @vitejs/plugin-react (React support + Fast Refresh)
- ESLint with React Hooks + React Refresh rules

### Styling and UI system

- Tailwind CSS v4
- @tailwindcss/vite (Tailwind integrated through Vite plugin)
- shadcn/ui component patterns and registry (`components.json`)
- Radix UI primitives (dialog, tooltip, separator, slot, etc.)
- Utility helpers: `clsx`, `tailwind-merge`, `class-variance-authority`
- Icons: `lucide-react`

### Theming

- Custom `ThemeProvider` implementation using React Context + localStorage
- `ModeToggle` component to switch light/dark/system
- CSS variables in `src/index.css` define design tokens for both themes

### Other notable libraries

- `axios` for API requests (ready for backend integration)
- `@fontsource-variable/inter` available for typography

## 2) CRA vs this setup (quick mapping)

- `react-scripts start` -> `vite` (`npm run dev`)
- `react-scripts build` -> `vite build` (`npm run build`)
- `src/index.js` entry -> `src/main.jsx`
- Routing and app shell are explicitly structured in `src/App.jsx` and `src/layouts/AppLayout.jsx`
- Styling is primarily Tailwind classes + CSS variables, not traditional global CSS modules by default

## 3) How the app boots

Startup path:

1. `index.html` provides the root mount node.
2. `src/main.jsx` mounts React and wraps the app with providers:
   - `ThemeProvider`
   - `BrowserRouter`
   - `TooltipProvider`
3. `src/App.jsx` defines route tree.

This means global behavior (theme, routing, tooltips) is configured once at the root and available everywhere.

## 4) Folder structure explained

### Root-level files

- `package.json`: dependencies and scripts (`dev`, `build`, `lint`, `preview`)
- `vite.config.js`: Vite config, React plugin, Tailwind plugin, alias setup
- `jsconfig.json`: path alias support for `@/* -> src/*`
- `components.json`: shadcn/ui config (style, aliases, CSS path)
- `eslint.config.js`: lint rules and ignores
- `public/`: static assets served directly

### `src/` (application code)

- `main.jsx`
  - App entry point and top-level providers.

- `App.jsx`
  - Routing table.
  - Includes nested routes under `AppLayout` and separate routes like `/login` and `/live`.

- `layouts/`
  - Shared page shells. Example: `AppLayout.jsx` composes sidebar + content outlet.

- `features/`
  - Feature-first organization (domain modules).
  - Each folder (`auth`, `dashboard`, `reports`, etc.) contains feature screens/components.
  - This scales better than putting everything in one generic `components` folder.

- `components/ui/`
  - Reusable design-system style components (button, card, sidebar, sheet, tooltip, etc.).
  - Mostly shadcn/Radix-based building blocks.

- `components/web/`
  - App-level reusable components specific to this product (e.g., theme controls/provider wrappers).

- `context/`
  - React context definitions used by providers/hooks.

- `hooks/`
  - Custom hooks (theme, mobile detection, etc.).
  - Note: both `.js` and `.ts` versions currently exist for some utilities/hooks, which suggests gradual TypeScript readiness.

- `lib/`
  - Shared utility functions (e.g., `cn()` for className composition).

- `index.css` and `App.css`
  - Global style entry and theme token definitions.

## 5) Routing architecture

Defined in `src/App.jsx`:

- `/login` -> Auth page
- `/` -> `AppLayout` (layout wrapper)
  - Redirect index route to `/dashboard`
  - `/dashboard`, `/reports`, `/alerts`, `/profile` render inside layout (`<Outlet />`)
- `/live` -> standalone page outside `AppLayout`

So the app uses nested routing: shared chrome (sidebar/layout) is centralized once and reused.

## 6) Styling architecture

Main styling layers:

1. Tailwind utility classes in JSX.
2. Theme/design tokens in `src/index.css` via CSS variables.
3. UI primitives from shadcn/Radix components.

`src/index.css` defines color, spacing, shadow, typography tokens for light and dark modes. Components consume these via Tailwind utilities and semantic CSS variables (`bg-background`, `text-foreground`, etc.).

## 7) Aliases and imports

The project uses alias imports like:

- `@/components/ui/button`
- `@/features/auth/auth`

Why this helps:

- Avoids long relative paths like `../../../../components/...`
- Makes refactors easier
- Keeps imports cleaner as project grows

Alias is configured in both:

- `vite.config.js` (runtime/bundler resolution)
- `jsconfig.json` (editor/IntelliSense resolution)

## 8) Why this structure is useful

Compared to plain CRA starter style, this architecture gives:

- Faster local development and build performance (Vite)
- Better scalability through feature-based modules
- Consistent UI system through reusable primitives
- Cleaner global app composition (providers + layout + nested routing)
- Built-in theme support and modern CSS token system

## 9) Daily development commands

- Install dependencies: `npm install`
- Run dev server: `npm run dev`
- Build production bundle: `npm run build`
- Preview production build: `npm run preview`
- Run linter: `npm run lint`

---

If you want, I can also create a second file like `cra-migration-notes.md` that gives one-to-one examples ("In CRA you did X, here do Y") for routing, styling, and shared components.
