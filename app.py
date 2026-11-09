"""문서 등급 판정 + 한 단계 낮은 마스킹본 반환 웹서비스.
실행: README.md 참고.  흐름: 업로드 -> 등급 에이전트 -> 등급에 따라 필요한 Mask 에이전트만 병렬 호출 -> 마스킹본 반환(순차: 하 등급은 마스킹 비용 없음).
마스킹 PDF는 Mask 에이전트(Agents API) 출력에서 찾고, 만든 마스킹본은 등급 에이전트로 다시 판정해 목표 등급 도달을 검증한다.
"""
import base64, json, os, re, threading, time, urllib.request, uuid
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
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
AGENT_SPLIT = os.getenv("AGENT_SPLIT", "")   # 필수. 모든 문서는 먼저 분할 에이전트를 거쳐야 한 문서인지 여러 문서인지 알 수 있다.
# 에이전트별 config(워크플로우 버전). 비우면 에이전트의 default config = 최신 버전으로 실행된다.
CONFIG = {a: os.getenv(k, "") for a, k in ((AGENT_GRADE, "AGENT_GRADE_CONFIG"), (AGENT_MASK_MID, "AGENT_MASK_MID_CONFIG"), (AGENT_MASK_LOW, "AGENT_MASK_LOW_CONFIG"), (AGENT_SPLIT, "AGENT_SPLIT_CONFIG")) if a}
RUNS = HERE / "runs"; RUNS.mkdir(exist_ok=True)
MAX_UPLOAD = 20 * 1024 * 1024   # 업로드 최대 20MB

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

def trusted_url(s):
    """에이전트 출력의 PDF 주소는 Upstage 도메인(https)만 내려받는다."""
    u = urlparse(s)
    return u.scheme == "https" and (u.hostname == "upstage.ai" or (u.hostname or "").endswith(".upstage.ai"))

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
            if trusted_url(s) and ".pdf" in s.lower():
                with urllib.request.urlopen(s, timeout=60) as r: return r.read()
        except Exception:
            continue
    return None

STEPS = ["업로드", "등급 판정", "마스킹", "검증", "완료"]
JOBS = {}   # 실행 id -> {"step": 0~4, "done": bool, "result"|"error"}  (메모리, 서버 재시작하면 사라짐)

RANK = {"하": 0, "중": 1, "상": 2}

def drop_file(proj, fid):
    """처리가 끝나면 Upstage에 올린 사본을 지운다(금융 문서 잔류 방지). 실패해도 흐름은 막지 않는다."""
    try: client(proj).files.delete(fid)
    except Exception: pass

def verify(lv, pdf, out):
    """마스킹본을 등급 에이전트에 다시 넣어 목표 등급과 비교한다(통과 = 목표와 정확히 같음). 실패해도 전체 흐름은 막지 않는다."""
    target = LABEL[lv]
    fid = None
    try:
        proj = scope(AGENT_GRADE)
        fid = client(proj).files.create(file=(f"{lv}.pdf", pdf), purpose="user_data").id
        g = run_agent(AGENT_GRADE, {proj: fid})
        (out / f"raw_regrade_{lv}.json").write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
        got = parse_grade(step_text(g, "instruct"))
    except Exception:
        got = None
    finally:
        if fid: drop_file(proj, fid)
    if got is None: return {"재판정": None, "검증": "불가", "검증메모": "재판정 결과를 읽지 못했습니다. 마스킹본을 직접 확인해 주세요."}
    if got == target: return {"재판정": got, "검증": "통과", "검증메모": f"재판정 {got} 등급 · 목표 등급에 도달했습니다."}
    if RANK[got] > RANK[target]: return {"재판정": got, "검증": "미달", "검증메모": f"재판정 {got} 등급 · 목표({target})보다 높습니다. 마스킹이 덜 됐을 수 있습니다."}
    return {"재판정": got, "검증": "과마스킹", "검증메모": f"재판정 {got} 등급 · 목표({target})보다 낮습니다. 필요 이상으로 가려졌을 수 있습니다."}

def run_doc(rid, data, filename, stage, name=""):
    """문서 한 건: 등급 판정 -> 필요한 마스킹 -> 재판정 검증. name = 분할 에이전트가 붙인 문서 이름(화면의 문서종)."""
    uploaded = {}   # 프로젝트 -> 올린 원본 file id. 끝나면(실패해도) Upstage에서 지운다
    try: return _pipeline(rid, data, filename, stage, uploaded, name)
    finally:
        for pj, fid in uploaded.items(): drop_file(pj, fid)

def _pipeline(rid, data, filename, stage, uploaded, name):
    t0 = time.time(); out = RUNS / rid
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

    need = NEED[grade]; res, why = {}, {}   # why: 등급본을 못 만든 이유
    agents = {lv: a for lv, a in (("mid", AGENT_MASK_MID), ("low", AGENT_MASK_LOW)) if lv in need and a}
    for lv in need:
        if lv not in agents: why[lv] = f"AGENT_MASK_{lv.upper()} 가 .env 에 없습니다"
    def mask(a):   # 한쪽이 실패해도 다른 쪽 결과는 살린다
        try: return run_agent(a, uploaded), None
        except HTTPException as e: return None, str(e.detail)
    if agents:   # 필요한 마스킹만 병렬 호출
        stage(2); ensure(agents.values())
        with ThreadPoolExecutor(len(agents)) as ex:
            futs = {lv: ex.submit(mask, a) for lv, a in agents.items()}
        for lv, fu in futs.items():
            r, err = fu.result()
            if err: why[lv] = err
            else: res[lv] = r; (out / f"raw_{lv}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")

    outputs, notes, pdfs = [], [], {}
    for lv in need:
        pdf = find_pdf(res[lv], exclude | set(uploaded.values())) if lv in res else None
        if pdf is None:
            notes.append(f"{LABEL[lv]} 등급본을 만들지 못했습니다({why.get(lv, 'Mask 에이전트 출력에 PDF 없음')})"); continue
        (out / f"{lv}.pdf").write_bytes(pdf); pdfs[lv] = pdf
        outputs.append({"등급": LABEL[lv], "출처": "Studio Mask", "url": f"/runs/{rid}/{lv}.pdf"})
    if pdfs:   # 만든 마스킹본을 병렬로 다시 판정
        stage(3)
        with ThreadPoolExecutor(len(pdfs)) as ex:
            checks = {lv: ex.submit(verify, lv, b, out) for lv, b in pdfs.items()}
        for o, lv in zip(outputs, pdfs): o.update(checks[lv].result())
    field = lambda k: (re.search(k + r":\s*(.+)", decision) or [None, ""])[1].strip()
    stage(4)
    return {"등급": grade, "문서종": name,"근거범주": field("근거범주"), "근거항목": field("근거항목"),
            "마스킹본": outputs, "비고": notes, "원본url": f"/runs/{rid}/input.pdf", "소요초": round(time.time() - t0, 1)}

def parse_split(resp, n):
    """분할 응답(document-classify 단계의 content 항목 = 문서 1건)에서 쪽 범위를 읽는다.
    각 항목의 additional_values(JSON 문자열)에 page_ranges [[시작, 끝], ...] (1부터, 끝 포함)가 있다.
    문서마다 (문서 이름, [(시작, 끝), ...]) (쪽은 0부터)로 바꿔 첫 쪽 순서로 돌려준다. 문서 이름 = content 의 text(분류 결과).
    문서들이 n쪽을 빠짐·겹침 없이 덮을 때만 유효(1개 = 한 문서). 아니면 None."""
    docs = []
    for it in resp["output"]:
        if it.get("step_type") != "document-classify": continue
        for c in it.get("content", []):
            try:
                av = c["additional_values"]; av = json.loads(av) if isinstance(av, str) else av
                pr = av.get("page_ranges")   # 나뉘지 않은 한 문서는 쪽 범위 없이 분류만 온다
                docs.append((None if pr is None else sorted((int(a) - 1, int(b) - 1) for a, b in pr), (c.get("text") or "").strip()))
            except (AttributeError, KeyError, TypeError, ValueError): return None
    if len(docs) == 1 and docs[0][0] is None: docs = [([(0, n - 1)], docs[0][1])]
    if any(d is None for d, _ in docs): return None
    docs.sort()
    pages = sorted(p for d, _ in docs for a, b in d for p in range(a, b + 1))
    ok = docs and all(d and all(a <= b for a, b in d) for d, _ in docs) and pages == list(range(n))
    return [(name, d) for d, name in docs] if ok else None

def split_docs(rid, data, filename):
    """모든 문서를 분할 에이전트에 먼저 태워 문서 단위 쪽 범위(1개 이상)를 얻는다. 분할을 못 하면 한 문서로 추측하지 않고 실패시킨다."""
    fid = proj = None
    try:
        with pymupdf.open(stream=data, filetype="pdf") as d: n = len(d)
        proj = scope(AGENT_SPLIT); fid = client(proj).files.create(file=(filename, data), purpose="user_data").id
        r = run_agent(AGENT_SPLIT, {proj: fid})
        (RUNS / rid / "raw_split.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        segs = parse_split(r, n)
    except Exception as e:
        raise HTTPException(502, f"문서 분할에 실패했습니다: {str(getattr(e, 'detail', e))[:200]}")
    finally:
        if fid: drop_file(proj, fid)
    if segs is None: raise HTTPException(502, "문서 분할 결과를 읽지 못했습니다. runs/<id>/raw_split.json 의 응답 형식을 확인하세요.")
    return segs

def cut(data, ranges):
    with pymupdf.open(stream=data, filetype="pdf") as src, pymupdf.open() as dst:
        for a, b in ranges: dst.insert_pdf(src, from_page=a, to_page=b)
        return dst.tobytes()

def pipeline(rid, data, filename, stage):
    """분할 -> (문서마다 run_doc). 분할 결과가 한 문서면 원본 그대로 예전 결과 형태, 여러 문서면 {"결합": True, "문서들": [...]} (문서마다 자기 실행 폴더)."""
    t0 = time.time(); segs = split_docs(rid, data, filename)
    if len(segs) == 1: return run_doc(rid, data, filename, stage, segs[0][0])
    hi = [0]
    def step(i): hi[0] = max(hi[0], i); stage(hi[0])   # 문서들이 병렬이라 진행 단계는 가장 앞선 값을 쓴다
    def one(k, seg):
        name, ranges = seg; sid = uuid.uuid4().hex; (RUNS / sid).mkdir()
        try:
            pdf = cut(data, ranges); (RUNS / sid / "input.pdf").write_bytes(pdf)
            r = run_doc(sid, pdf, f"{Path(filename).stem}_{k}.pdf", step, name)
        except HTTPException as e: r = {"오류": str(e.detail)}
        except Exception as e: r = {"오류": f"{type(e).__name__}: {e}"}
        r["쪽"] = ",".join(f"{a + 1}-{b + 1}" if b > a else str(a + 1) for a, b in ranges)
        return r
    with ThreadPoolExecutor(min(len(segs), 3)) as ex: docs = list(ex.map(one, range(1, len(segs) + 1), segs))
    ok = [d["등급"] for d in docs if "등급" in d]
    if not ok: raise HTTPException(502, "분할한 문서를 모두 처리하지 못했습니다: " + "; ".join(d["오류"] for d in docs)[:300])
    stage(4)
    return {"결합": True, "등급": max(ok, key=RANK.get), "문서들": docs, "비고": [], "원본url": f"/runs/{rid}/input.pdf", "소요초": round(time.time() - t0, 1)}

def work(rid, data, filename):
    job = JOBS[rid]
    try: job["result"] = pipeline(rid, data, filename, lambda i: job.update(step=i))
    except HTTPException as e: job["error"] = str(e.detail)
    except Exception as e: job["error"] = f"{type(e).__name__}: {e}"
    job["done"] = True

@app.post("/api/process")
def process(file: UploadFile):
    if not AGENT_GRADE: raise HTTPException(500, "AGENT_GRADE 가 .env 에 없습니다")
    if not AGENT_SPLIT: raise HTTPException(500, "AGENT_SPLIT 가 .env 에 없습니다")
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD: raise HTTPException(413, f"파일이 너무 큽니다. {MAX_UPLOAD // 1024 // 1024}MB 이하의 PDF만 올릴 수 있습니다")
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
