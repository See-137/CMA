/** @type {import('tailwindcss').Config} */

// CMA "Scrooge" design language — a Victorian counting-house ledger:
// forest-green felt, antique gold leaf, parchment, oxblood ink.
// The built-in slate/teal/rose/amber/emerald scales are intentionally
// REMAPPED to this palette so existing utility classes across the app adopt
// the brand without touching every file.

const ink = {
  50: '#f6f2e8',
  100: '#e9e1cd',
  200: '#d6c9a8',
  300: '#b8a87f',
  400: '#8f8367',
  500: '#6b6450',
  600: '#4d4a3c',
  700: '#39392f',
  800: '#262a23',
  900: '#161c17',
  950: '#0c1410',
};

const forest = {
  50: '#eef6f0',
  100: '#d4e9da',
  200: '#a9d3b6',
  300: '#79b78f',
  400: '#4f996a',
  500: '#2f7350',
  600: '#245c40',
  700: '#1d4a34',
  800: '#17392a',
  900: '#112c20',
  950: '#0a1a13',
};

const gold = {
  50: '#fbf6e7',
  100: '#f5e9c4',
  200: '#ecd488',
  300: '#e0bd55',
  400: '#d0a23a',
  500: '#b8862c',
  600: '#9a6c22',
  700: '#7c531c',
  800: '#5f3f18',
  900: '#4a3214',
  950: '#2a1c0a',
};

const oxblood = {
  50: '#fbf0ee',
  100: '#f5d9d4',
  200: '#e8b1a8',
  300: '#d8847a',
  400: '#c25a50',
  500: '#a83a3a',
  600: '#8f2f2f',
  700: '#732828',
  800: '#572020',
  900: '#3f1818',
  950: '#240d0d',
};

export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        display: ['Fraunces', 'ui-serif', 'Georgia', 'serif'],
        sans: ['"Hanken Grotesk"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      colors: {
        // Warm the pure-white default to aged paper.
        white: '#fbf7ee',
        brand: forest,
        gold,
        ink,
        oxblood,
        // Remap built-ins so legacy utility classes inherit the brand.
        teal: forest,
        emerald: forest,
        slate: ink,
        rose: oxblood,
        amber: gold,
      },
      boxShadow: {
        subtle: '0 1px 2px 0 rgb(12 20 16 / 0.06)',
        card: '0 1px 2px 0 rgb(12 20 16 / 0.06), 0 8px 24px -12px rgb(12 20 16 / 0.18)',
        'card-hover':
          '0 2px 4px 0 rgb(12 20 16 / 0.08), 0 16px 40px -16px rgb(12 20 16 / 0.28)',
        elevated: '0 24px 60px -24px rgb(12 20 16 / 0.45)',
        emboss:
          'inset 0 1px 0 0 rgb(255 255 255 / 0.18), inset 0 -1px 0 0 rgb(12 20 16 / 0.12)',
        'glow-gold': '0 0 24px -6px rgb(208 162 58 / 0.45)',
        'glow-teal': '0 0 24px -6px rgb(47 115 80 / 0.45)',
      },
      backgroundImage: {
        'gold-leaf': 'linear-gradient(135deg, #e0bd55 0%, #d0a23a 45%, #9a6c22 100%)',
        'felt': 'linear-gradient(160deg, #17392a 0%, #0a1a13 100%)',
      },
      keyframes: {
        'fade-in': {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'fade-in-fast': {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        'scale-in': {
          '0%': { opacity: '0', transform: 'scale(0.96)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        'slide-in-left': {
          '0%': { opacity: '0', transform: 'translateX(-12px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        'pulse-subtle': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.55' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        'coin-flip': {
          '0%': { transform: 'rotateY(0deg)' },
          '100%': { transform: 'rotateY(360deg)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 0.5s cubic-bezier(0.16,1,0.3,1) forwards',
        'fade-in-fast': 'fade-in-fast 0.25s ease-out forwards',
        'scale-in': 'scale-in 0.3s cubic-bezier(0.16,1,0.3,1) forwards',
        'slide-in-left': 'slide-in-left 0.35s cubic-bezier(0.16,1,0.3,1) forwards',
        'pulse-subtle': 'pulse-subtle 2.4s ease-in-out infinite',
        shimmer: 'shimmer 2.5s linear infinite',
        'coin-flip': 'coin-flip 1.2s ease-in-out',
      },
    },
  },
  plugins: [],
};
