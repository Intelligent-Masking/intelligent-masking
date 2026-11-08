"""자체 점검 (API 호출 없음): python test_app.py"""
import os
os.environ.setdefault("UPSTAGE_API_KEY", "x")
import app

assert app.parse_grade("Decision: 상\n근거범주: x") == "상"
assert app.parse_grade("Decision: **중**") == "중"
assert app.parse_grade("없음") is None
assert app.NEED["상"] == ["mid", "low"] and app.NEED["중"] == ["low"] and app.NEED["하"] == []
print("OK")
