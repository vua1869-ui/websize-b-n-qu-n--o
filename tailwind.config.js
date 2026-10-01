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
        sans: ['"Plus Jakarta Sans"', "-apple-system", "BlinkMacSystemFont", '"Segoe UI"', "Roboto", "sans-serif"],
        serif: ['"Playfair Display"', "Georgia", "serif"],
      },
    },
  },
  plugins: [],
};
