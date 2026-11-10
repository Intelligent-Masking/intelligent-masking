# Intelligent Masking

<p align="center">
  <img src="assets/readme-hero.png" width="100%" alt="단디를 참고한 금융 문서 개인정보 마스킹 콘셉트 일러스트">
</p>

<p align="center">
  <strong>금융 문서 속 개인정보를 확인하고, 공유에 필요한 만큼 가립니다.</strong><br>
  iM뱅크(대구은행) 해커톤 · 금융 PDF 개인정보 식별·검증·등급화·마스킹 프로젝트
</p>

## 프로젝트 소개

**Intelligent Masking**은 금융 PDF의 개인정보를 식별하고, 서식별 필수값을 검증한 뒤 **상·중·하 위험 등급과 목표 등급별 마스킹본**을 제공하는 프로젝트입니다. 원본과 결과 PDF를 비교·다운로드할 수 있는 웹 데모를 구현했습니다.

**지원 서식 20종** · **65개 공통 평가 항목 + 서식별 필수 항목** · **32개 PDF / 38개 문서**

[Upstage Studio 프로젝트](https://studio.upstage.ai/projects/proj_baHzy9KaZypvnTsB7qE8tj) · [최종 설계서](docs/submission/design.pdf)

## 목차

자세한 내용은 아래 문서를 눌러 확인할 수 있습니다.

<table width="100%">
  <thead>
    <tr>
      <th width="240" align="left">문서</th>
      <th width="592" align="left">내용</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><a href="docs/guide.md">서비스 화면·실행 안내</a></td>
      <td>실제 화면, 설치·API 설정, 실행·점검 방법과 현재 연동 범위</td>
    </tr>
    <tr>
      <td><a href="docs/workflows.md">워크플로우·등급 기준</a></td>
      <td>네 워크플로우, 실제 노드 캡처, 위험 등급과 예외 처리</td>
    </tr>
    <tr>
      <td><a href="docs/development.md">개선 과정·데이터셋</a></td>
      <td>기준 조사, 가중치·검증 규칙 개선, 기술 구성과 저장소 구조</td>
    </tr>
    <tr>
      <td><a href="docs/submission/README.md">최종 제출 자료</a></td>
      <td>설계서, 워크플로우 JSON, 데이터셋, 정답지, 유형표</td>
    </tr>
    <tr>
      <td><a href="#팀원과-역할">팀원과 역할</a></td>
      <td>팀원별 GitHub 프로필과 담당 역할</td>
    </tr>
  </tbody>
</table>

## 핵심 기능

<table width="100%">
  <thead>
    <tr>
      <th width="240" align="left">기능</th>
      <th width="592" align="left">핵심 내용</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><strong>문서 분류·정보 추출</strong></td>
      <td>지원 서식과 결합 문서를 처리하고 실제 기재 정보 추출</td>
    </tr>
    <tr>
      <td><strong>필수값 검증</strong></td>
      <td>서식별 누락·판독 확인 항목을 점검하고 보완 안내</td>
    </tr>
    <tr>
      <td><strong>위험 등급화</strong></td>
      <td>항목별 가중치·기재 건수·마스킹·정보 조합으로 상·중·하 판정</td>
    </tr>
    <tr>
      <td><strong>등급별 마스킹</strong></td>
      <td>상은 중·하 목표본, 중은 하 목표본 생성, 하는 원본 확인</td>
    </tr>
    <tr>
      <td><strong>결과 비교·다운로드</strong></td>
      <td>원본·마스킹본 미리보기와 PDF 다운로드</td>
    </tr>
  </tbody>
</table>

**PDF 업로드 → 문서 분석·검증 → 위험 등급 확인 → 마스킹 결과 비교·다운로드**

마스킹본의 중·하는 목표 등급입니다. 생성 결과를 재판정한 값은 아니며 공유 전 직접 확인합니다. [자세한 처리 범위](docs/workflows.md#위험-등급과-예외-처리)

## 팀원과 역할

<table width="100%">
  <thead>
    <tr>
      <th width="96" align="center">프로필</th>
      <th width="144" align="left">팀원</th>
      <th width="592" align="left">역할 및 담당 내용</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td align="center" valign="middle"><a href="https://github.com/Hlxecz"><img src="https://avatars.githubusercontent.com/u/162279890?v=4&amp;s=96" width="64" height="64" alt="장형준 GitHub 프로필"></a></td>
      <td valign="middle"><strong>장형준</strong><br><a href="https://github.com/Hlxecz">@Hlxecz</a></td>
      <td valign="middle"><strong>PM·팀장</strong><br>워크플로우·서비스 구현 · 문서 작성</td>
    </tr>
    <tr>
      <td align="center" valign="middle"><a href="https://github.com/greeniron78"><img src="https://avatars.githubusercontent.com/u/240195793?v=4&amp;s=96" width="64" height="64" alt="신보겸 GitHub 프로필"></a></td>
      <td valign="middle"><strong>신보겸</strong><br><a href="https://github.com/greeniron78">@greeniron78</a></td>
      <td valign="middle">워크플로우 구현<br>가중치 로직 구현</td>
    </tr>
    <tr>
      <td align="center" valign="middle"><a href="https://github.com/nohhyun03"><img src="https://avatars.githubusercontent.com/u/125971077?v=4&amp;s=96" width="64" height="64" alt="노현석 GitHub 프로필"></a></td>
      <td valign="middle"><strong>노현석</strong><br><a href="https://github.com/nohhyun03">@nohhyun03</a></td>
      <td valign="middle">워크플로우·서비스 구현<br>문서 작성</td>
    </tr>
    <tr>
      <td align="center" valign="middle"><a href="https://github.com/SeongJiness"><img src="https://avatars.githubusercontent.com/u/63137333?v=4&amp;s=96" width="64" height="64" alt="이성진 GitHub 프로필"></a></td>
      <td valign="middle"><strong>이성진</strong><br><a href="https://github.com/SeongJiness">@SeongJiness</a></td>
      <td valign="middle">테스트 · 워크플로우 구현<br>문서 작성</td>
    </tr>
  </tbody>
</table>
