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
class _Files:
    def create(self, **kw): return type("F", (), {"id": "file_x"})()
class _Client: files = _Files()
app.scope = lambda a: "proj_x"; app.client = lambda p=None: _Client()
def fake(decision):
    app.run_agent = lambda a, f: {"output": [{"model": "Instruct - 등급", "content": [{"text": decision}]}]}
out = Path(tempfile.mkdtemp())
for decision, lv, want in (("Decision: 중", "mid", "통과"), ("Decision: 상", "mid", "미달"), ("Decision: 하", "mid", "과마스킹"),
                           ("Decision: 하", "low", "통과"), ("Decision: 중", "low", "미달"), ("이상한 응답", "low", "불가")):
    fake(decision); assert app.verify(lv, b"%PDF", out)["검증"] == want, (decision, lv)

# PDF 주소는 Upstage https 도메인만 허용
assert app.trusted_url("https://studio.upstage.ai/files/job-results/a.pdf")
assert not app.trusted_url("http://studio.upstage.ai/a.pdf") and not app.trusted_url("https://evil.com/a.pdf")
assert not app.trusted_url("https://studio.upstage.ai.evil.com/a.pdf") and not app.trusted_url("https://evilupstage.ai/a.pdf")
# 업로드 크기 제한 / PDF 아님 거부
import io
from fastapi import UploadFile
app.AGENT_GRADE = "agt_x"; app.MAX_UPLOAD = 100
for body, code in ((b"%PDF" + b"x" * 200, 413), (b"hello", 400)):
    try: app.process(UploadFile(file=io.BytesIO(body), filename="a.pdf")); raise SystemExit("거부되지 않음")
    except app.HTTPException as e: assert e.status_code == code, (e.status_code, code)

# 마스킹 에이전트 한쪽이 실패해도 다른 쪽 결과는 살린다
app.RUNS = Path(tempfile.mkdtemp()); app.AGENT_GRADE, app.AGENT_MASK_MID, app.AGENT_MASK_LOW = "agt_g", "agt_m", "agt_l"
def fake_agent(a, f):
    if a == "agt_g": return {"output": [{"model": "Instruct - 등급", "content": [{"text": "Decision: 상"}]}]}
    if a == "agt_m": raise app.HTTPException(502, "중 마스킹 실패")
    return {"output": []}
app.run_agent = fake_agent; app.find_pdf = lambda r, ex: b"%PDF-masked"
app.verify = lambda lv, pdf, out: {"재판정": "하", "검증": "통과", "검증메모": "ok"}
(app.RUNS / "r1").mkdir()
r = app.pipeline("r1", b"%PDF", "a.pdf", lambda i: None)
assert [o["등급"] for o in r["마스킹본"]] == ["하"] and "중 마스킹 실패" in r["비고"][0], r
print("OK")
