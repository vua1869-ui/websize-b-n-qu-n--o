/** Chỉ cần khi chỉnh giao diện. Xem README mục "Chỉnh sửa giao diện". */
module.exports = {
  content: ["./app/templates/**/*.html", "./app/static/js/**/*.js"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#FFF5EE", 100: "#FFE6D5", 200: "#FFCBAA", 500: "#E8590C",
          600: "#D9480F", 700: "#B93C0B", 800: "#932F09",
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
