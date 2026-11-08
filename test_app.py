"""자체 점검 (API 호출 없음): python test_app.py"""
import pymupdf
from pathlib import Path
import os
os.environ.setdefault("UPSTAGE_API_KEY", "x")
import app

assert app.parse_grade("Decision: 상\n근거범주: x") == "상"
assert app.parse_grade("Decision: **중**") == "중"
assert app.parse_grade("없음") is None

src = (app.HERE.parent / "작성본" / "상" / "01_FATCA_CRS_본인확인서_개인용.pdf").read_bytes()
text = lambda b: "".join(p.get_text() for p in pymupdf.open(stream=b, filetype="pdf"))
assert "537-62-4918" in text(src) and "김서연" in text(src)
vals = {"해외납세자번호": "537-62-4918", "성명": "김서연"}
mid = app.local_redact(src, vals, "mid"); low = app.local_redact(src, vals, "low")
assert "537-62-4918" not in text(mid) and "김서연" in text(mid)        # 중본: 상 정보만 가림
assert "537-62-4918" not in text(low) and "김서연" not in text(low)    # 하본: 일반 개인정보도 가림
assert app.NEED["상"] == ["mid", "low"] and app.NEED["하"] == []
print("OK")
