/** Chỉ cần khi chỉnh giao diện. Xem README mục "Chỉnh sửa giao diện". */
module.exports = {
  content: ["./app/templates/**/*.html", "./app/static/js/**/*.js"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#FFFDF5", 100: "#FFF8E7", 200: "#FFEFC2", 500: "#D4AF37",
          600: "#B8960C", 700: "#9A7D0A", 800: "#7C6508",
        },
        gold: {
          50: "#FFFDF5", 100: "#FFF8E7", 200: "#FFEFC2", 300: "#FFE494",
          400: "#F5D061", 500: "#D4AF37", 600: "#B8960C", 700: "#9A7D0A",
          800: "#7C6508", 900: "#5E4C06",
        },
        noir: {
          50: "#F5F5F6", 100: "#EEEEF0", 200: "#D4D4D8", 300: "#A1A1AA",
          400: "#71717A", 500: "#52525B", 600: "#3F3F46", 700: "#2A2A30",
          800: "#1A1A1F", 900: "#111113", 950: "#08080A",
        },
      },
      fontFamily: {
        sans: ['"Open Sans"', '"Segoe UI"', "-apple-system", "Roboto", "sans-serif"],
        serif: ['"Open Sans"', '"Segoe UI"', "-apple-system", "Roboto", "sans-serif"],
      },
      /* ── Thang cỡ chữ chuẩn – URBAN REVIVO STYLE (Min 14px) ─────────────
         body: 16px (1rem), line-height 1.6
         body-sm: 15px (0.9375rem)
         label: 14px (0.875rem) – nhỏ nhất được phép cho văn bản
         menu/nút: 15px (0.9375rem), font-weight 600
         h3: 18px (1.125rem)
         h2: 24px (1.5rem), sm: 28px (1.75rem)
         h1: 28px (1.75rem), lg: 32px (2rem)
         hero h1: 36px (2.25rem), sm: 48px (3rem)
         giá: 26–28px, font-weight 600
      ──────────────────────────────────────────────────────────────────── */
      fontSize: {
        "hero":    ["2.25rem",  { lineHeight: "1.2",  letterSpacing: "-0.02em" }],
        "display": ["2rem",     { lineHeight: "1.2",  letterSpacing: "-0.02em" }],
        "h1":      ["1.75rem",  { lineHeight: "1.25", letterSpacing: "-0.01em" }],
        "h2":      ["1.5rem",   { lineHeight: "1.3",  letterSpacing: "-0.01em" }],
        "h3":      ["1.125rem", { lineHeight: "1.4"  }],
        "menu":    ["0.9375rem",{ lineHeight: "1.5"  }],
        "body":    ["1rem",     { lineHeight: "1.6"  }],
        "body-sm": ["0.9375rem",{ lineHeight: "1.55" }],
        "label":   ["0.875rem", { lineHeight: "1.5"  }],
        "xs":      ["0.875rem", { lineHeight: "1.5"  }], /* map xs về 14px */
        "sm":      ["0.875rem", { lineHeight: "1.5"  }], /* map sm về 14px */
        "10px":    ["0.875rem", { lineHeight: "1.5"  }],
        "11px":    ["0.875rem", { lineHeight: "1.5"  }],
        "12px":    ["0.875rem", { lineHeight: "1.5"  }],
        "13px":    ["0.875rem", { lineHeight: "1.5"  }],
        "14px":    ["0.875rem", { lineHeight: "1.5"  }],
        "15px":    ["0.9375rem",{ lineHeight: "1.55" }],
      },
    },
  },
  safelist: [
    'bg-[#111111]',
    'bg-[#FAF8F5]',
    'bg-black',
    'bg-white',
    'text-[#111111]',
    'text-[#FAF8F5]',
    'text-white',
    'text-black',
    'border-[#111111]',
    'border-[#E5E2DC]',
    'btn',
    'btn-primary',
    'btn-outline',
    'btn-soft',
    'btn-dark',
    'chip',
    'chip-on',
    'max-w-[1600px]',
    '2xl:px-16',
    'lg:px-10',
    'lg:col-span-7',
    'lg:col-span-5',
    'xl:col-span-7',
    'xl:col-span-5',
    'lg:flex-col',
    'lg:overflow-y-auto',
    'lg:w-auto',
    'lg:h-20',
    'lg:w-16',
    'text-body',
    'text-body-sm',
    'text-label',
    'heading-card',
    'heading-section',
    'heading-page',
    'price',
  ],
  plugins: [],
};
