// The app always follows the system theme, so the class next-themes sets
// once the bundle loads is applied before first paint.
{
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
}
