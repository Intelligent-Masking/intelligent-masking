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
print("OK")
