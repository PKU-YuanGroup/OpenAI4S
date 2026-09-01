/* Apply the saved theme and document language before the body is parsed. */
(function () {
  try {
    var theme = localStorage.getItem("os-theme");
    if (theme !== "dark" && theme !== "light" && theme !== "system") theme = "system";
    var dark = theme === "dark" || (theme === "system" && window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    document.documentElement.style.colorScheme = dark ? "dark" : "light";
  } catch (error) {}
  try {
    var lang = localStorage.getItem("os-lang");
    if (lang !== "zh" && lang !== "en") {
      var locales = navigator.languages && navigator.languages.length
        ? navigator.languages
        : [navigator.language || ""];
      lang = Array.prototype.some.call(locales, function (item) {
        return /^zh/i.test(item);
      })
        ? "zh"
        : "en";
    }
    document.documentElement.lang = lang;
  } catch (error) {}
})();
