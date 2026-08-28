/* Apply the saved theme before the body is parsed to avoid a light flash. */
(function () {
  try {
    var theme = localStorage.getItem("os-theme");
    if (theme !== "dark" && theme !== "light" && theme !== "system") theme = "system";
    var dark = theme === "dark" || (theme === "system" && window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    document.documentElement.style.colorScheme = dark ? "dark" : "light";
  } catch (error) {}
})();
