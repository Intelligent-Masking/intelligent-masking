"""자체 점검 (API 호출 없음): python test_app.py"""
import os
os.environ.setdefault("UPSTAGE_API_KEY", "x")
import app

assert app.parse_grade("Decision: 상\n근거범주: x") == "상"
assert app.parse_grade("Decision: **중**") == "중"
assert app.parse_grade("없음") is None
assert app.NEED["상"] == ["mid", "low"] and app.NEED["중"] == ["low"] and app.NEED["하"] == []

import tempfile
from pathlib import Path
deleted = []
class _Files:
    def create(self, **kw): return type("F", (), {"id": "file_x"})()
    def delete(self, fid): deleted.append(fid)
class _Client: files = _Files()
app.scope = lambda a: "proj_x"; app.client = lambda p=None: _Client()

# PDF 주소는 Upstage https 도메인만 허용
assert app.trusted_url("https://studio.upstage.ai/files/job-results/a.pdf")
assert not app.trusted_url("http://studio.upstage.ai/a.pdf") and not app.trusted_url("https://evil.com/a.pdf")
assert not app.trusted_url("https://studio.upstage.ai.evil.com/a.pdf") and not app.trusted_url("https://evilupstage.ai/a.pdf")
# 업로드 크기 제한 / PDF 아님 거부
import io
from fastapi import UploadFile
app.AGENT_GRADE = "agt_x"; app.AGENT_SPLIT = ""; app.MAX_UPLOAD = 100
try: app.process(UploadFile(file=io.BytesIO(b"%PDF"), filename="a.pdf")); raise SystemExit("분할 에이전트 없이 통과")
except app.HTTPException as e: assert e.status_code == 503 and "서비스" in e.detail and "AGENT_" not in e.detail   # 분할은 필수
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
(app.RUNS / "r1").mkdir()
r = app.pipeline("r1", one, "a.pdf", lambda i: None)
assert "결합" not in r and r["원본url"].endswith("/r1/input.pdf") and r["문서종"] == "문서0"   # 문서종 = 분할 결과의 이름
assert [o["등급"] for o in r["마스킹본"]] == ["하"] and "중 마스킹 실패" in r["비고"][0], r
assert len(deleted) == 2   # 분할·등급 업로드 사본도 처리 후 지운다
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
# others: 등급 판정·마스킹 없이 분할 워크플로우의 Instruct 안내 문구만 보여준다
IR = lambda docs, notes: {"output": [
    {"step_type": "document-classify", "content": [{"text": n, "additional_values": json.dumps({"page_ranges": rs} if rs else {})} for n, rs in docs]},
    {"step_type": "instruct", "content": [{"text": json.dumps(tx), "additional_values": json.dumps({"page_ranges": rs} if rs else {})} for rs, tx in notes]}]}
NOTE = "Decision: 미지원" + chr(10)*2 + "문서유형: 회의록【†1】" + chr(10)*2 + "사유: 서식이 아니다【†2】"   # 실제 응답은 줄바꿈이 있는 텍스트
calls = []
app.run_doc = lambda sid, data, fname, step, doc="": (calls.append(doc), {"등급": "중", "문서종": doc, "마스킹본": [], "비고": [], "원본url": f"/runs/{sid}/input.pdf"})[1]
(app.RUNS / "r6").mkdir()
app.run_agent = split_agent(IR([("문서0", [[1, 2]]), ("others", [[3, 3]])], [([[3, 3]], NOTE)]))
r = app.pipeline("r6", pdf(3), "m.pdf", lambda i: None)
o = r["문서들"][1]
assert calls == ["문서0"] and r["등급"] == "중", (calls, r)                      # others 는 등급화에 보내지 않는다
assert o["미지원"] and o["문서종"] == "others" and o["문서유형"] == "회의록" and o["사유"] == "서식이 아니다" and "【" not in str(o), o
assert len(pymupdf.open(app.RUNS / o["원본url"].split("/")[2] / "input.pdf")) == 1 and r["문서들"][0]["쪽"] == "1-2" and o["쪽"] == "3"
# 한 문서가 others 면 단일 결과도 등급 없이 안내만
app.run_agent = split_agent(IR([("others", [[1, 1]])], [([[1, 1]], NOTE)]))
r = app.pipeline("r6", one, "o.pdf", lambda i: None)
assert calls == ["문서0"] and r["미지원"] and "등급" not in r and r["사유"] == "서식이 아니다" and r["원본url"].endswith("/r6/input.pdf"), r
# 나뉘지 않은 한 문서는 쪽 범위가 없다: 분류와 안내 모두 범위 없이 와도 짝지어진다
app.run_agent = split_agent(IR([("others", None)], [(None, NOTE)]))
r = app.pipeline("r6", one, "o.pdf", lambda i: None)
assert r["미지원"] and r["문서유형"] == "회의록", r
# 전부 others 인 결합 문서는 오류가 아니라 등급 없는 결과
app.run_agent = split_agent(IR([("others", [[1, 1]]), ("others", [[2, 2]])], [([[1, 1]], NOTE), ([[2, 2]], NOTE)]))
r = app.pipeline("r6", pdf(2), "oo.pdf", lambda i: None)
assert r["결합"] and r["등급"] is None and all(d["미지원"] for d in r["문서들"]) and calls == ["문서0"], r
app.run_doc = real_run_doc
print("OK")

# 새 등급 워크플로우(실제 응답 형식): validate green -> 'Instruct - 등급판정프롬프트'(표), red -> 'Instruct - 검토필요'(마크다운, 등급 없음)
TABLE = "| 개인정보 위험 등급 | 위험 점수 | 판정 근거 |\n|---|---|---|\n| 상 | 21.92 | 계좌번호 3건【†1】이 확인되어 상 등급입니다.【†2】 |"
CITED = "| 개인정보 위험 등급 | 위험 점수 | 판정 근거 |\n|---|---|---|\n| 상 | 13.91【†1】 | 성명【†2】이 확인되어 높은 가중【†3】 |"   # 점수 칸에도 인용 표시가 붙어 온다
assert app.parse_grade(CITED) == "상" and app.grade_basis(CITED) == "성명이 확인되어 높은 가중"
assert app.parse_grade(TABLE) == "상" and app.parse_grade(TABLE.replace("| 상 |", "| **중** |")) == "중"
assert app.grade_basis(TABLE) == "계좌번호 3건이 확인되어 상 등급입니다." and app.grade_basis("Decision: 상") == ""
REVIEW = "## 1. 판정\n**필수값 누락**【†1】\n\n## 2. 누락 항목\n- 페이지1 사업자등록번호 누락【†2】"
assert app.parse_grade(REVIEW) is None
rd = Path(tempfile.mkdtemp()); (rd / "r7").mkdir(); app.RUNS = rd
app.run_agent = lambda a, f: {"output": [{"model": "Instruct - 검토필요", "content": [{"text": REVIEW}]}]}
r = app.run_doc("r7", one, "a.pdf", lambda i: None, "환율FAX")
assert r["검토필요"] and "등급" not in r and "【" not in r["사유"] and "사업자등록번호 누락" in r["사유"] and "**" not in r["사유"] and "##" not in r["사유"], r
app.run_agent = lambda a, f: {"output": [{"model": "Instruct - 등급판정프롬프트", "content": [{"text": TABLE}]}]}
app.AGENT_MASK_MID = app.AGENT_MASK_LOW = ""
r = app.run_doc("r7", one, "a.pdf", lambda i: None, "잔액증명")
assert r["등급"] == "상" and r["근거항목"].startswith("계좌번호 3건") and "점수" not in r["근거항목"], r

# 등급 칸·점수 칸·근거 칸 어디에나 인용 표시가 붙어 온다 (실제 응답)
ALLCITED = "| 개인정보 위험 등급 | 위험 점수 | 판정 근거 |\n|---|---|---|\n| 상【†1】 | 13.73【†2】 | 고유식별정보 1건과 일반 개인식별정보가 확인되었습니다.【†3】【†4】 주소와 국적은 다건 보정이 적용되었습니다. |"
assert app.parse_grade(ALLCITED) == "상" and app.grade_basis(ALLCITED) == "고유식별정보 1건과 일반 개인식별정보가 확인되었습니다. 주소와 국적은 다건 보정이 적용되었습니다."
assert app.parse_grade(ALLCITED.replace("상【†1】", "중【†1】")) == "중" and app.parse_grade(ALLCITED.replace("상【†1】", "**하**【†1】")) == "하"
