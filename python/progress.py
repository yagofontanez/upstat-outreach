"""Progresso no console — equivalente a lib/progress.js."""


def console_progress(event):
    etype = event.get("type")
    if etype == "log":
        print(event.get("message", ""))
    elif etype == "item":
        idx = f"[{event.get('index')}/{event.get('total')}]"
        name = (event.get("name") or "")[:50]
        print(f"  {idx} {name}… {event.get('status', '')}")
    elif etype == "done":
        if event.get("message"):
            print(event["message"])
    elif etype == "error":
        print(f"erro: {event.get('message', '')}")
