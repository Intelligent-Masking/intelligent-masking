"""Keep cloud analysis inside the response lifetime and deliver PDFs to the browser."""
import base64
import json
import queue
from pathlib import Path
import re
import shutil
import threading
import time

PDF_URL = re.compile(r"^/runs/([0-9a-f]{32})/(input|mid|low)\.pdf$")

def result_urls(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from result_urls(item)
    elif isinstance(value, list):
        for item in value:
            yield from result_urls(item)
    elif isinstance(value, str) and PDF_URL.fullmatch(value):
        yield value

def stream_results(service, rid, data, filename):
    events = queue.Queue()
    owned = {rid}
    # shortcut: cloud results stay in the current browser; durable history needs object storage.
    def run():
        try:
            result = service.pipeline(rid, data, filename, lambda step: events.put({"step": step}))
            for url in sorted(set(result_urls(result))):
                match = PDF_URL.fullmatch(url)
                run_id, stem = match.groups()
                name = stem + ".pdf"
                owned.add(run_id)
                pdf = service.get_run(run_id, name).path
                encoded = base64.b64encode(Path(pdf).read_bytes()).decode("ascii")
                try:
                    preview = base64.b64encode(service.preview_run(run_id, name).body).decode("ascii")
                except service.HTTPException:
                    preview = None
                events.put({"file": {"path": url, "pdf": encoded, "preview": preview}})
            events.put({"step": 3, "done": True, "result": result})
        except Exception as error:
            message = str(getattr(error, "detail", error))
            events.put({"error": message or "문서 분석을 완료하지 못했습니다."})
        finally:
            for run_id in owned:
                shutil.rmtree(service.RUNS / run_id, ignore_errors=True)
            events.put(None)

    # Remove abandoned temporary files from cloud invocations interrupted over an hour ago.
    for directory in service.RUNS.iterdir():
        if directory.is_dir() and re.fullmatch(r"[0-9a-f]{32}", directory.name) and time.time() - directory.stat().st_mtime > 3600:
            shutil.rmtree(directory, ignore_errors=True)
    threading.Thread(target=run, daemon=True).start()
    yield json.dumps({"step": 0}) + "\n"
    while True:
        try:
            event = events.get(timeout=10)
        except queue.Empty:
            yield "{\"heartbeat\":true}\n"
            continue
        if event is None:
            break
        yield json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
