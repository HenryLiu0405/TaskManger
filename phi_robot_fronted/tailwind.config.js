/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', '"Liberation Mono"', '"Courier New"', 'monospace'],
      },
      colors: {
        'neo-white': '#ffffff',
        'neo-black': '#000000',
        'neo-primary': '#ED225D',
        'neo-secondary': '#2D7BB6',
      },
      boxShadow: {
        'neo': '8px 8px 0px 0px #000',
        'neo-sm': '4px 4px 0px 0px #000',
      },
    },
  },
  plugins: [],
}
