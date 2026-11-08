# Intelligent Masking

iM뱅크(대구은행) 해커톤 프로젝트 — 금융 문서의 개인정보 위험 등급을 분석하고 공유용 마스킹 문서를 만드는 서비스.

PDF를 올리면 등급(상·중·하)을 판정하고, 한 단계씩 낮춘 마스킹본을 돌려준다.
상 → 중본·하본 / 중 → 하본 / 하 → 등급만.

## 팀 작업 시작

```bash
git clone https://github.com/Intelligent-Masking/intelligent-masking.git
cd intelligent-masking
```

- 저장소는 비공개다. 조직 초대를 수락하고 이 저장소의 쓰기 권한을 받은 계정으로 로그인한다.
- 아래 실행 절차에 따라 각자 `.env`를 준비한다. API 키는 커밋하지 않는다.
- 작업 전 최신 `main`을 받고, `feat/design`처럼 작업별 브랜치를 만든다. 변경을 올린 뒤 PR을 통해 검토하고 `main`에 합친다.
- 원본·마스킹 PDF와 API 응답이 저장되는 `runs/`, 가상환경, `.env`는 Git에서 제외한다.

## 실행

1. `.env.example` 을 `.env` 로 복사하고 키와 Agent ID 3개를 채운다.
   - `AGENT_GRADE`: Parse → Classify → Extract(등급판정용) → Instruct(등급) 워크플로우
   - `AGENT_MASK_MID`: Parse(위치정보 켬) → Mask(마스킹_중본.json)
   - `AGENT_MASK_LOW`: Parse(위치정보 켬) → Mask(마스킹_하본.json)
2. macOS에서는 실행 환경을 만들고 필요한 패키지를 설치한다 (이 폴더에서):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

   Windows에서 서버 시작:

```bash
.venv/Scripts/python.exe -m uvicorn app:app --port 8000
```

3. 브라우저에서 http://localhost:8000 → PDF 업로드.

## 동작

1. PDF 업로드 → 등급 에이전트 실행(문서종 분류, 항목 추출, 등급 판정).
2. 등급에 필요한 마스킹만 병렬 호출한다: 상 → 중본·하본, 중 → 하본, 하 → 호출 없음(비용 절감).
3. 마스킹 PDF는 Mask 에이전트 응답의 `additional_values.pdf` 임시 URL에서 받는다. 못 찾으면 등급 에이전트가 뽑은 값으로 로컬에서 가린다.
4. 화면은 `/api/status/<id>` 를 1.5초마다 조회해 단계(업로드 → 등급 판정 → 마스킹 → 완료)를 보여 주고, 원본과 마스킹본을 나란히 미리보기로 보여 준다.
5. 실행마다 `runs/<id>/` 에 원본·응답 원문(`raw_*.json`)·결과 PDF가 저장된다.
6. 에이전트가 프로젝트 소속이면 파일도 같은 프로젝트로 올린다(소속은 `GET /agents/<id>` 로 조회).

## 알려진 한계

- 현재 폴더에는 로컬 마스킹에 필요한 `gen_catalog.py`와 기존 자체 점검용 PDF가 포함되어 있지 않다. `.env`의 `MASK_MODE=api`로 Studio API를 이용하며, 로컬 마스킹과 `test_app.py` 실행에는 원본 파일을 추가해야 한다.
- 로컬 마스킹은 텍스트 층이 있는 PDF에서만 된다(스캔본 불가). 값 문자열을 PDF 전체에서 찾으므로 인쇄 안내문의 같은 글자도 가려질 수 있고, 도장·체크박스는 가리지 못한다.
- 점검: `.venv/Scripts/python.exe -X utf8 test_app.py` (API 호출 없음).
- 작업 상태(`JOBS`)는 메모리에만 있어 서버를 재시작하면 사라진다. 인증·삭제 기능이 없으니 공개 배포하지 않는다.

## 화면과 로고

- 로고·심볼: `assets/brand-logo.svg`, `assets/brand-symbol.svg`
- GitHub 조직 대표 이미지: `assets/brand-avatar.png` (512×512 PNG)
- 웹 화면: `index.html` (추가 프레임워크 없이 실행)
- 결과 문서는 첫 페이지 이미지로 미리 보고, 크게 보기로 전체 PDF를 연다.
- 미리보기 점검: `.venv/bin/python -m unittest test_web -v` (외부 API 호출 없음).
