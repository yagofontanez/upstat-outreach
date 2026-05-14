export function consoleProgress(event) {
  if (event.type === "log") {
    console.log(event.message);
  } else if (event.type === "item") {
    const idx = `[${event.index}/${event.total}]`;
    const name = (event.name || "").slice(0, 50);
    console.log(`  ${idx} ${name}… ${event.status || ""}`);
  } else if (event.type === "done") {
    if (event.message) console.log(event.message);
  } else if (event.type === "error") {
    console.error("erro:", event.message);
  }
}
