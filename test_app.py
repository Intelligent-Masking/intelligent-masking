"""자체 점검 (API 호출 없음): python test_app.py"""
import os
os.environ.setdefault("UPSTAGE_API_KEY", "x")
import app

assert app.parse_grade("Decision: 상\n근거범주: x") == "상"
assert app.parse_grade("Decision: **중**") == "중"
assert app.parse_grade("없음") is None
assert app.NEED["상"] == ["mid", "low"] and app.NEED["중"] == ["low"] and app.NEED["하"] == []

# 재판정 검증: 통과(목표와 같음) / 미달(더 높음) / 과마스킹(더 낮음) / 불가(읽기 실패)
import tempfile
from pathlib import Path
deleted = []
class _Files:
    def create(self, **kw): return type("F", (), {"id": "file_x"})()
    def delete(self, fid): deleted.append(fid)
class _Client: files = _Files()
app.scope = lambda a: "proj_x"; app.client = lambda p=None: _Client()
def fake(decision):
    app.run_agent = lambda a, f: {"output": [{"model": "Instruct - 등급", "content": [{"text": decision}]}]}
out = Path(tempfile.mkdtemp())
for decision, lv, want in (("Decision: 중", "mid", "통과"), ("Decision: 상", "mid", "미달"), ("Decision: 하", "mid", "과마스킹"),
                           ("Decision: 하", "low", "통과"), ("Decision: 중", "low", "미달"), ("이상한 응답", "low", "불가")):
    fake(decision); assert app.verify(lv, b"%PDF", out)["검증"] == want, (decision, lv)
assert len(deleted) == 6   # 재판정용 업로드는 매번 지운다

# PDF 주소는 Upstage https 도메인만 허용
assert app.trusted_url("https://studio.upstage.ai/files/job-results/a.pdf")
assert not app.trusted_url("http://studio.upstage.ai/a.pdf") and not app.trusted_url("https://evil.com/a.pdf")
assert not app.trusted_url("https://studio.upstage.ai.evil.com/a.pdf") and not app.trusted_url("https://evilupstage.ai/a.pdf")
# 업로드 크기 제한 / PDF 아님 거부
import io
from fastapi import UploadFile
app.AGENT_GRADE = "agt_x"; app.AGENT_SPLIT = ""; app.MAX_UPLOAD = 100
try: app.process(UploadFile(file=io.BytesIO(b"%PDF"), filename="a.pdf")); raise SystemExit("분할 에이전트 없이 통과")
except app.HTTPException as e: assert e.status_code == 500 and "AGENT_SPLIT" in e.detail   # 분할은 필수
app.AGENT_SPLIT = "agt_s"
for body, code in ((b"%PDF" + b"x" * 200, 413), (b"hello", 400)):
    try: app.process(UploadFile(file=io.BytesIO(body), filename="a.pdf")); raise SystemExit("거부되지 않음")
    except app.HTTPException as e: assert e.status_code == code, (e.status_code, code)

# 분할 응답 해석: 빈틈·겹침이 있거나 못 읽으면 None, 한 문서면 범위 1개
import json, pymupdf
R = lambda *docs: {"output": [{"step_type": "document-classify", "model": "step_2_classify", "content": [{"text": f"문서{i}", "additional_values": json.dumps({"page_ranges": d})} for i, d in enumerate(docs)]}]}   # 실제 응답 형식(additional_values.page_ranges, 1부터)
assert app.parse_split(R([[1, 2]], [[3, 3]], [[4, 4]]), 4) == [("문서0", [(0, 1)]), ("문서1", [(2, 2)]), ("문서2", [(3, 3)])]
assert app.parse_split(R([[3, 3]], [[1, 2]], [[4, 4]]), 4) == [("문서1", [(0, 1)]), ("문서0", [(2, 2)]), ("문서2", [(3, 3)])]   # 순서가 섞여도 첫 쪽 순
assert app.parse_split(R([[1, 4]]), 4) == [("문서0", [(0, 3)])]   # 한 문서
assert app.parse_split(R([[1, 1], [3, 3]], [[2, 2]], [[4, 4]]), 4) == [("문서0", [(0, 0), (2, 2)]), ("문서1", [(1, 1)]), ("문서2", [(3, 3)])]   # 떨어진 쪽
assert app.parse_split(R([[1, 2]], [[4, 4]]), 4) is None     # 3쪽이 빠짐
NR = lambda *names: {"output": [{"step_type": "document-classify", "content": [{"text": n, "additional_values": json.dumps({"document_type": {"_value": n}})} for n in names]}]}   # 나뉘지 않은 문서의 실제 응답: page_ranges 없음
assert app.parse_split(NR("한 문서"), 3) == [("한 문서", [(0, 2)])]
assert app.parse_split(NR("가", "나"), 3) is None               # 여러 건인데 쪽 범위가 없으면 믿지 않는다
assert app.parse_split(R([[1, 2]], [[2, 4]]), 4) is None     # 겹침
assert app.parse_split({"output": [{"content": [{"text": "엉뚱한 응답"}]}]}, 4) is None
def pdf(n):
    d = pymupdf.open()
    for _ in range(n): d.new_page()
    return d.tobytes()
one, four = pdf(1), pdf(4)

# 마스킹 에이전트 한쪽이 실패해도 다른 쪽 결과는 살린다 (분할 결과 = 한 문서)
app.RUNS = Path(tempfile.mkdtemp()); app.AGENT_GRADE, app.AGENT_MASK_MID, app.AGENT_MASK_LOW = "agt_g", "agt_m", "agt_l"
def fake_agent(a, f):
    if a == "agt_s": return R([[1, 1]])
    if a == "agt_g": return {"output": [{"model": "Instruct - 등급", "content": [{"text": "Decision: 상"}]}]}
    if a == "agt_m": raise app.HTTPException(502, "중 마스킹 실패")
    return {"output": []}
app.run_agent = fake_agent; app.find_pdf = lambda r, ex: b"%PDF-masked"
app.verify = lambda lv, pdf, out: {"재판정": "하", "검증": "통과", "검증메모": "ok"}
(app.RUNS / "r1").mkdir()
r = app.pipeline("r1", one, "a.pdf", lambda i: None)
assert "결합" not in r and r["원본url"].endswith("/r1/input.pdf") and r["문서종"] == "문서0"   # 문서종 = 분할 결과의 이름
assert [o["등급"] for o in r["마스킹본"]] == ["하"] and "중 마스킹 실패" in r["비고"][0], r
assert len(deleted) == 8   # 분할·등급 업로드 사본도 처리 후 지운다
def split_agent(resp, base=fake_agent):
    return lambda a, f: resp if a == "agt_s" else base(a, f)
# 4쪽 = 문서 3건: 문서마다 자기 폴더·원본·결과를 가진다
app.run_agent = split_agent(R([[1, 2]], [[3, 3]], [[4, 4]])); (app.RUNS / "r2").mkdir()
r = app.pipeline("r2", four, "combo.pdf", lambda i: None)
assert r["결합"] and r["등급"] == "상" and [d["쪽"] for d in r["문서들"]] == ["1-2", "3", "4"], r
ids = [d["원본url"].split("/")[2] for d in r["문서들"]]
assert len(set(ids)) == 3 and [len(pymupdf.open(app.RUNS / i / "input.pdf")) for i in ids] == [2, 1, 1]
assert [d["문서종"] for d in r["문서들"]] == ["문서0", "문서1", "문서2"]
assert (app.RUNS / "r2" / "raw_split.json").exists()
# 문서마다 등급이 다르면 가장 높은 등급, 한 건이 실패해도 나머지는 살린다
real_run_doc = app.run_doc
def fake_doc(sid, data, name, step, doc=""):
    if name.endswith("_3.pdf"): raise app.HTTPException(502, "등급 실패")
    return {"등급": "하" if name.endswith("_1.pdf") else "중", "마스킹본": [], "비고": []}
app.run_doc = fake_doc
r = app.pipeline("r2", four, "combo.pdf", lambda i: None)
assert r["등급"] == "중" and [d.get("등급") for d in r["문서들"]] == ["하", "중", None] and "등급 실패" in r["문서들"][2]["오류"], r
app.run_doc = lambda *a: (_ for _ in ()).throw(app.HTTPException(502, "전부 실패"))
try: app.pipeline("r2", four, "combo.pdf", lambda i: None); raise SystemExit("전부 실패했는데 통과")
except app.HTTPException as e: assert e.status_code == 502
app.run_doc = real_run_doc
# 분할 결과가 한 문서면 원본 그대로 예전 결과 형태
app.run_agent = split_agent(R([[1, 4]])); (app.RUNS / "r4").mkdir()
r = app.pipeline("r4", four, "one.pdf", lambda i: None)
assert "결합" not in r and r["원본url"].endswith("/r4/input.pdf"), r
# 분할을 못 하면 한 문서로 추측하지 않고 실패(호출 실패 / 응답을 못 읽음 / 쪽이 어긋남)
def boom(a, f):
    if a == "agt_s": raise app.HTTPException(502, "분할 실패")
    return fake_agent(a, f)
for agent in (boom, split_agent({"output": []}), split_agent(R([[1, 2]], [[4, 4]]))):
    app.run_agent = agent; (app.RUNS / "r5").mkdir(exist_ok=True)
    try: app.pipeline("r5", four, "x.pdf", lambda i: None); raise SystemExit("분할 실패인데 통과")
    except app.HTTPException as e: assert e.status_code == 502 and "분할" in e.detail, e.detail
print("OK")
