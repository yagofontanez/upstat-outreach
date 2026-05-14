export function requireAuth(req, res, next) {
  if (req.session?.authed) return next();
  if (req.path.startsWith("/api/"))
    return res.status(401).json({ error: "unauthorized" });
  return res.redirect("/login");
}

export function checkPassword(pwd) {
  const expected = process.env.UI_PASSWORD;
  if (!expected) return false;
  return pwd === expected;
}
