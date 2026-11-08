"""문서 등급 판정 + 한 단계 낮은 마스킹본 반환 웹서비스.
실행: README.md 참고.  흐름: 업로드 -> 등급 에이전트 -> 등급에 따라 필요한 Mask 에이전트만 병렬 호출 -> 마스킹본 반환(순차: 하 등급은 마스킹 비용 없음).
마스킹 PDF는 Mask 에이전트(Agents API) 출력에서 찾고, 만든 마스킹본은 등급 에이전트로 다시 판정해 목표 등급 도달을 검증한다.
"""
import base64, json, os, re, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from openai import OpenAI, OpenAIError

HERE = Path(__file__).parent

# ---- 설정: .env (키는 코드에 쓰지 않는다) ----
for line in (HERE / ".env").read_text(encoding="utf-8").splitlines() if (HERE / ".env").exists() else []:
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())
AGENT_GRADE = os.getenv("AGENT_GRADE", "")
AGENT_MASK_MID = os.getenv("AGENT_MASK_MID", "")
AGENT_MASK_LOW = os.getenv("AGENT_MASK_LOW", "")
# 에이전트별 config(워크플로우 버전). 비우면 에이전트의 default config = 최신 버전으로 실행된다.
CONFIG = {a: os.getenv(k, "") for a, k in ((AGENT_GRADE, "AGENT_GRADE_CONFIG"), (AGENT_MASK_MID, "AGENT_MASK_MID_CONFIG"), (AGENT_MASK_LOW, "AGENT_MASK_LOW_CONFIG")) if a}
RUNS = HERE / "runs"; RUNS.mkdir(exist_ok=True)

NEED = {"상": ["mid", "low"], "중": ["low"], "하": []}   # 입력 등급 -> 필요한 낮은 등급본
LABEL = {"mid": "중", "low": "하"}

app = FastAPI()
app.mount("/assets", StaticFiles(directory=HERE / "assets"), name="assets")
_clients, _scope = {}, {}
def client(project=None):
    """에이전트가 프로젝트 소속이면 파일도 같은 프로젝트로 올려야 접근된다(x-project-id 헤더)."""
    if project not in _clients:
        if not os.getenv("UPSTAGE_API_KEY"): raise HTTPException(500, "UPSTAGE_API_KEY 가 .env 에 없습니다")
        _clients[project] = OpenAI(api_key=os.environ["UPSTAGE_API_KEY"], base_url="https://api.upstage.ai/v2",
                                  default_headers={"x-project-id": project} if project else None)
    return _clients[project]

def scope(agent_id):
    if agent_id not in _scope:
        try: _scope[agent_id] = client().get(f"/agents/{agent_id}", cast_to=object).get("project_id")
        except OpenAIError as e: raise HTTPException(502, f"에이전트 {agent_id[:12]}… 조회 실패: {e}")
    return _scope[agent_id]

def run_agent(agent_id, file_ids, timeout=600):
    proj = scope(agent_id); c = client(proj); file_id = file_ids[proj]
    body = [{"role": "user", "content": [{"type": "input_file", "file_id": file_id}]}]
    extra = {"config_id": CONFIG[agent_id]} if CONFIG.get(agent_id) else None   # shortcut: config_id 는 문서에 없는 파라미터(서버 응답으로 확인), 막히면 제거
    try: r = c.responses.create(model=agent_id, include=["all"], input=body, extra_body=extra)
    except OpenAIError as e: raise HTTPException(502, f"에이전트 {agent_id[:12]}… 호출 실패: {e}")
    t0 = time.time()
    while r.status in {"queued", "in_progress"}:
        if time.time() - t0 > timeout: raise HTTPException(504, f"에이전트 {agent_id} 시간 초과")
        time.sleep(2); r = c.responses.retrieve(r.id, include=["all"])
    d = r.model_dump()
    if d["status"] != "completed": raise HTTPException(502, f"에이전트 {agent_id} 실패: {d.get('error')}")
    return d

def step_text(resp, key):
    """step 이름에 key 가 들어간 첫 단계의 본문 텍스트(대소문자 무시). 단계 이름은 노드 이름이라 'Instruct - 이름' 꼴이다."""
    for item in resp["output"]:
        if key.lower() in (item.get("model") or "").lower():
            t = "\n".join(c.get("text", "") for c in item.get("content", []))
            if t.startswith('"'):   # 문자열이 JSON 으로 한 번 더 감싸여 오는 경우
                try: t = json.loads(t)
                except ValueError: pass
            return t
    return ""

def parse_grade(text):
    m = re.search(r"Decision:\s*\W*([상중하])", text)
    return m.group(1) if m else None

def find_pdf(resp, exclude_ids):
    """Mask 에이전트 출력에서 PDF 바이트를 찾는다: base64 / file id / URL. 못 찾으면 None."""
    found = []
    def walk(x):
        if isinstance(x, dict): [walk(v) for v in x.values()]
        elif isinstance(x, list): [walk(v) for v in x]
        elif isinstance(x, str):
            s = x.strip()
            if s.startswith("{") or s.startswith("["):
                try: walk(json.loads(s)); return
                except ValueError: pass
            found.append(s)
    walk(resp["output"])
    for s in found:
        try:
            if s.startswith("JVBER"): return base64.b64decode(s)
            if re.fullmatch(r"file[-_]\w+", s) and s not in exclude_ids:
                data = client().files.content(s).read()
                if data[:4] == b"%PDF": return data
            if s.startswith("http") and ".pdf" in s.lower():
                import urllib.request
                with urllib.request.urlopen(s, timeout=60) as r: return r.read()
        except Exception:
            continue
    return None

STEPS = ["업로드", "등급 판정", "마스킹", "검증", "완료"]
JOBS = {}   # 실행 id -> {"step": 0~4, "done": bool, "result"|"error"}  (메모리, 서버 재시작하면 사라짐)

RANK = {"하": 0, "중": 1, "상": 2}

def verify(lv, pdf, out):
    """마스킹본을 등급 에이전트에 다시 넣어 목표 등급과 비교한다(통과 = 목표와 정확히 같음). 실패해도 전체 흐름은 막지 않는다."""
    target = LABEL[lv]
    try:
        proj = scope(AGENT_GRADE)
        fid = client(proj).files.create(file=(f"{lv}.pdf", pdf), purpose="user_data").id
        g = run_agent(AGENT_GRADE, {proj: fid})
        (out / f"raw_regrade_{lv}.json").write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
        got = parse_grade(step_text(g, "instruct"))
    except Exception:
        got = None
    if got is None: return {"재판정": None, "검증": "불가", "검증메모": "재판정 결과를 읽지 못했습니다. 마스킹본을 직접 확인해 주세요."}
    if got == target: return {"재판정": got, "검증": "통과", "검증메모": f"재판정 {got} 등급 · 목표 등급에 도달했습니다."}
    if RANK[got] > RANK[target]: return {"재판정": got, "검증": "미달", "검증메모": f"재판정 {got} 등급 · 목표({target})보다 높습니다. 마스킹이 덜 됐을 수 있습니다."}
    return {"재판정": got, "검증": "과마스킹", "검증메모": f"재판정 {got} 등급 · 목표({target})보다 낮습니다. 필요 이상으로 가려졌을 수 있습니다."}

def pipeline(rid, data, filename, stage):
    t0 = time.time(); out = RUNS / rid
    uploaded = {}
    def ensure(agent_ids):   # 에이전트 소속(프로젝트)마다 파일을 한 번씩 올린다
        for pj in {scope(a) for a in agent_ids}:
            if pj not in uploaded:
                uploaded[pj] = client(pj).files.create(file=(filename, data), purpose="user_data").id
    stage(0); ensure([AGENT_GRADE])
    stage(1); g = run_agent(AGENT_GRADE, uploaded)
    (out / "raw_grade.json").write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
    decision = step_text(g, "instruct"); grade = parse_grade(decision)
    if grade is None: raise HTTPException(502, f"등급을 읽지 못했습니다: {decision[:200]}")
    exclude = set(re.findall(r"file[-_]\w+", json.dumps(g.get("output", []), ensure_ascii=False)))

    need = NEED[grade]; res = {}
    agents = {lv: a for lv, a in (("mid", AGENT_MASK_MID), ("low", AGENT_MASK_LOW)) if lv in need and a}
    if agents:   # 필요한 마스킹만 병렬 호출
        stage(2); ensure(agents.values())
        with ThreadPoolExecutor(len(agents)) as ex:
            futs = {lv: ex.submit(run_agent, a, uploaded) for lv, a in agents.items()}
            res = {lv: fu.result() for lv, fu in futs.items()}
        for lv, r in res.items(): (out / f"raw_{lv}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")

    outputs, notes, pdfs = [], [], {}
    for lv in need:
        pdf = find_pdf(res[lv], exclude | set(uploaded.values())) if lv in res else None
        if pdf is None:
            notes.append(f"{LABEL[lv]} 등급본을 만들지 못했습니다(Mask 에이전트 출력에 PDF 없음)"); continue
        (out / f"{lv}.pdf").write_bytes(pdf); pdfs[lv] = pdf
        outputs.append({"등급": LABEL[lv], "출처": "Studio Mask", "url": f"/runs/{rid}/{lv}.pdf"})
    if pdfs:   # 만든 마스킹본을 병렬로 다시 판정
        stage(3)
        with ThreadPoolExecutor(len(pdfs)) as ex:
            checks = {lv: ex.submit(verify, lv, b, out) for lv, b in pdfs.items()}
        for o, lv in zip(outputs, pdfs): o.update(checks[lv].result())
    field = lambda k: (re.search(k + r":\s*(.+)", decision) or [None, ""])[1].strip()
    stage(4)
    return {"등급": grade, "문서종": step_text(g, "classify").strip(), "근거범주": field("근거범주"), "근거항목": field("근거항목"),
            "마스킹본": outputs, "비고": notes, "원본url": f"/runs/{rid}/input.pdf", "소요초": round(time.time() - t0, 1)}

def work(rid, data, filename):
    job = JOBS[rid]
    try: job["result"] = pipeline(rid, data, filename, lambda i: job.update(step=i))
    except HTTPException as e: job["error"] = str(e.detail)
    except Exception as e: job["error"] = f"{type(e).__name__}: {e}"
    job["done"] = True

@app.post("/api/process")
def process(file: UploadFile):
    if not AGENT_GRADE: raise HTTPException(500, "AGENT_GRADE 가 .env 에 없습니다")
    data = file.file.read()
    if data[:4] != b"%PDF": raise HTTPException(400, "PDF 파일만 지원합니다")
    rid = uuid.uuid4().hex; (RUNS / rid).mkdir(); (RUNS / rid / "input.pdf").write_bytes(data)
    JOBS[rid] = {"step": 0, "done": False}
    threading.Thread(target=work, args=(rid, data, file.filename or "input.pdf"), daemon=True).start()
    return {"run": rid, "steps": STEPS}

@app.get("/api/status/{rid}")
def status(rid: str):
    if rid not in JOBS: raise HTTPException(404, "알 수 없는 실행 id (서버가 재시작됐을 수 있습니다)")
    return JOBS[rid]

@app.get("/runs/{rid}/{name}")
def get_run(rid: str, name: str, download: int = 0):
    if not re.fullmatch(r"[0-9a-f]{32}", rid) or name not in {"mid.pdf", "low.pdf", "input.pdf"}: raise HTTPException(404)
    p = RUNS / rid / name
    if not p.exists(): raise HTTPException(404)
    disp = "attachment" if download else "inline"   # inline: 화면 미리보기용
    return FileResponse(p, media_type="application/pdf", headers={"Content-Disposition": f'{disp}; filename="{name}"'})

@app.get("/", response_class=HTMLResponse)
def index(): return (HERE / "index.html").read_text(encoding="utf-8")

@app.get("/runs/{rid}/{name}/preview")
def preview_run(rid: str, name: str):
    source = get_run(rid, name)
    try:
        with pymupdf.open(source.path) as doc:
            page = doc[0]
            scale = min(1.4, 1000 / page.rect.width, 1400 / page.rect.height)
            png = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
    except (RuntimeError, ValueError, IndexError):
        raise HTTPException(422, "문서 미리보기를 만들 수 없습니다. PDF를 직접 열어 확인해 주세요.")
    return Response(content=png, media_type="image/png")
