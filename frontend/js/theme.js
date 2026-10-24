// Loaded as a classic blocking script in <head> so the saved colour theme applies before the first paint
// (no flash of the wrong theme). "system" leaves data-theme unset and the CSS follows the OS setting.
(function () {
  try {
    var t = JSON.parse(localStorage.getItem("fmt.theme.v1") || '"system"');
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) { /* storage unavailable: follow the OS */ }
})();
