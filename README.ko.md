# Nflix — `plugin.video.nfbridge`

[English](README.md) | **한국어**

Kodi에서 Netflix를 네이티브 목록 화면으로 탐색하고, 재생은 Netflix가 신뢰하는
Microsoft Edge WebView2 창에서 하는 Kodi 애드온입니다. Windows에서는 최대 4K로 재생되며
Kodi에서 설정한 자막 폰트가 그대로 적용됩니다.

2026년 6월 DRM 조사에서 내린 결론을 바탕으로 만들었습니다. **오픈 애드온은 데스크톱에서
Netflix를 복호화할 수 없습니다**(VMP, Media Foundation TEE, 기기 키라는 경계 때문).
하지만 모든 데스크톱 OS에는 Netflix가 이미 신뢰하는 클라이언트가 있습니다. 그래서 이
애드온은 **재생을 그 클라이언트에 맡기고**, Kodi가 잘하는 일인 네이티브 탐색 UI를 맡습니다.

## 설치
1. [Releases](https://github.com/kkhunter2/plugin.video.nfbridge/releases)에서
   `plugin.video.nfbridge-<버전>.zip`을 받습니다.
2. Kodi → 애드온 → **zip 파일에서 설치**(Install from zip file) → 받은 zip을 고릅니다.
   **Kodi 20 이상**이 필요합니다.
3. 애드온을 열고 **넷플릭스 로그인**을 고릅니다. Netflix 창이 뜨면 거기서 한 번만 로그인하면 됩니다.

zip에는 자체 실행형 플레이어(`nfbridge_player.exe`)가 들어 있어서 Python을 따로 설치할
필요가 없습니다. 이 exe는 서명되지 않은 빌드라 Windows **스마트 앱 컨트롤**이 막을 수
있습니다. 그럴 때는 *설정 → 임베디드 플레이어 → WebView2 호스트용 Python*에
`pywebview`와 `fonttools`가 설치된 `python.exe` 경로를 지정하세요.

## 구조

```
상시 실행되는 WebView2 데몬 하나  (runtime/webview/webview_host.py -> nfbridge_player.exe)
   netflix.com을 화면 밖에서 띄워 두고, 한 번 로그인한 세션으로 두 가지를 맡음:
   - 탐색 : localhost RPC. 페이지 자신의 데이터 클라이언트(Apollo 캐시, falcor
            pathEvaluator)를 거쳐 데이터를 읽음
   - 재생 : /play가 같은 창을 /watch로 이동시키고 전체화면으로 앞에 띄움
            (PlayReady SL3000 -> 최대 4K, Kodi 자막 폰트 적용)

resources/lib/nfclient.py   데몬용 탐색 클라이언트 (표준 라이브러리만 사용)
resources/lib/routes.py     -> Kodi 디렉터리 목록
resources/lib/player.py     데몬 생명주기 + 재생 (임베디드) / 위임 모드 대체 경로
```

`playback_mode` 설정으로 임베디드와 위임 중 하나를 고릅니다. **Windows의 기본값은
임베디드**(WebView2)이고, 다른 OS에서는 위임(OS 브라우저나 앱)입니다.

> **임베디드 = Microsoft Edge WebView2 (Windows).** 시스템 Edge 엔진은 Microsoft가
> 서명했고 Netflix가 신뢰하기 때문에, Netflix가 **애드온 자체 창에서 최대 4K(PlayReady
> SL3000)로 재생**됩니다. 오버레이 ESN `NFCDIE`, 3840×2160으로 검증했습니다. 번들
> *Electron*은 **동작하지 않습니다**(Netflix가 M7121/D7353/e100 오류로 거부, VMP 서명
> 출처 문제). 그래서 임베디드는 Electron이 아니라 WebView2를 씁니다.
>
> **자막(임베디드):** Netflix 자막의 텍스트, 타이밍, 위치는 그대로 두고 **폰트만 Kodi에서
> 설정한 자막 폰트로 바꿉니다**(`media/Fonts`, `kodi_subs.py`). 색상과 외곽선 등도
> `!important` CSS로 함께 적용합니다. 폰트는 먼저 `fontfix.py`로 OTS 복구를 거칩니다.
> Chromium의 폰트 검사기가 Kodi/FreeType에서는 열리는 폰트를 거부하는 경우가 있기
> 때문입니다(예: 깨진 `post` 테이블). 복구할 수 없을 만큼 손상된 폰트면 사용자에게
> **알림**을 띄우고 Netflix 기본 폰트를 씁니다.
>
> **런타임:** 임베디드 플레이어는 자체 실행형 `nfbridge_player.exe`입니다
> (`runtime/webview/`, `build_exe.py`로 빌드. Python, pywebview, fonttools 포함, 약 15 MB).
> 그래서 외부 Python이 필요 없습니다. 두 가지 용도로 쓰입니다.
> `nfbridge_player.exe`는 플레이어를 실행하고, `nfbridge_player.exe fontfix <원본>
> <캐시> <결과파일>`은 폰트를 OTS 복구합니다. `player.py`는 설정된 `webview_python`
> (서명된 python.exe라 스마트 앱 컨트롤을 통과함)과 호스트 스크립트를 먼저 쓰고,
> 없으면 exe, 그것도 없으면 위임 모드를 씁니다.

## 탐색 — WebView 데이터 데몬
데몬은 Netflix 데이터를 *페이지 자신의 클라이언트를 거쳐* localhost로 읽습니다. 그래서
헤더나 API를 역공학할 필요가 없습니다(페이지 안에서조차 `pathEvaluator`를 직접 요청하면
412가 돌아옵니다). 0.2.0까지는 직접 HTTP로 탐색하는 계층이 있었지만 실제로 연결된 적이
없어 0.3.0에서 지웠습니다. 서로 어긋나는 두 경로보다 검증된 한 경로가 낫기 때문입니다.

**2026년 10월 Netflix 업데이트부터 탐색 페이지는 falcor LoCo가 아니라 GraphQL "Pinot"
페이지입니다.** `/browse`, `/browse/genre/<id>`, `/browse/my-list`는 페이지의 Apollo
캐시에 들어 있고(`Pinot*Page → sections → PinotCarouselSection → entities →
*EntityTreatment → Movie|Show:{videoId}`), 페이지가 스스로 나눠서 불러옵니다. 데몬의
`/pinot`은 페이지의 `browse-page+sentinel`을 화면 안으로 스크롤해 행을 더 불러오고,
행의 오른쪽 화살표를 눌러 타일을 더 불러온 뒤 캐시를 읽습니다. 작품별 데이터
(`videos[id]`의 줄거리, 이어보기 위치, 재생 시간, 시즌별 에피소드)는 여전히 falcor에서 가져옵니다.

`resources/lib/nfclient.py`가 데몬에 요청하는 기능:
- `home()` / `genre()` — 페이지의 행 목록(Pinot). 행의 "root"는 페이지 경로입니다.
- `row_videos()` — 행 하나의 타일(종류, 제목, 아트, 있으면 이어보기 위치와 재생 시간).
  줄거리, 이어보기 위치, 재생 시간은 falcor 일괄 요청 한 번으로 채웁니다.
- `my_list()` — `/browse/my-list`의 모든 행을 하나로 합친 목록
- `video_info()` — 작품 하나의 기본 정보. 종류가 없는 타일이 영화인지 시리즈인지 가릴 때 씁니다.
- `search()` — Netflix Pinot 검색 결과(Apollo)
- `episodes()` — 시리즈의 시즌과 에피소드(Apollo 시즌 목록 + falcor 에피소드)
- `profiles()` — 프로필 목록(falcor). `/switch`는 상단 계정 메뉴의
  `switch-profile-<guid>` 버튼을 누릅니다(`/ProfilesGate`는 이제 `/browse`로
  리다이렉트됩니다). **현재 프로필**은 Apollo의 `currentProfile`로 판단합니다
  (`userInfo.guid`는 계정 소유자의 값이라 전환해도 바뀌지 않습니다).

데몬 엔드포인트(`NF_RPC_PORT`): `/health`(`ready`, `login`, `ver`, `playing`),
`/context`(`profile`, `home` 포함), `/falcor`, `/art`, `/pinot`, `/search`,
`/episodes`, `/home`, `/switch`, `/play`, `/show`, `/quit`, `/eval`.

**생명주기.** 데몬은 Kodi와 분리된 프로세스라 Kodi를 다시 시작해도 살아 있습니다.
`nfclient.ensure()`는 데몬의 `/health` `ver`(런타임 파일 수정 시각 + 자막 폰트 이름)가
애드온이 지금 띄울 버전과 다르면 데몬을 다시 시작합니다. `service.py`는 Kodi 자체
플레이어가 재생 중일 때(숨은 탭이 오디오 장치를 잡지 않도록)와 Kodi가 종료될 때 데몬을
끕니다. 다음에 탐색하면 라우터가 다시 띄웁니다. 데몬의 WebView2 프로필(로그인 쿠키,
DRM 프로비저닝)과 복구된 폰트 캐시는 애드온의 **데이터** 폴더
(`addon_data/plugin.video.nfbridge/webview_profile`, `fontcache`)에 있어서, 애드온을
업데이트하거나 다시 설치해도 로그인이 풀리지 않습니다.

## 정당성
DRM을 우회하지 않고 키도 다루지 않습니다. 사용자마다 **자기 계정**으로 로그인하고,
재생은 Netflix가 이미 신뢰하는 클라이언트에서 이뤄집니다(Edge가 실제 SL3000 XMR
라이선스를 받아 4K로 재생).

## 진행 상황
- [x] 애드온 기본 구조(`addon.xml`, `default.py`, 라우터, 설정, en_GB / ko_KR)
- [x] **재생** — 임베디드 WebView2(4K + Kodi 자막 폰트 + OSD) / 위임 모드 대체 경로
- [x] **자체 실행형 런타임**(`nfbridge_player.exe`, 외부 Python 불필요)
- [x] **자막 폰트** — Kodi `media/Fonts` 폰트 + OTS 복구 + 사용자 알림
- [x] **탐색 데몬** — 홈 행, 포스터, 검색, 카테고리, 에피소드, 프로필
- [x] **한 번 로그인** — 데몬 창에서 로그인, 프로필 선택 화면에서 자동 선택
- [x] **프로필 전환** — 상단 계정 메뉴로 화면 밖에서 전환하고, 선택한 프로필(번호 +
      guid)을 기억합니다. 안 되면 창을 띄워 직접 고르게 합니다.
- [x] **D7361 방지** — GPU 드라이버나 PlayReady/MediaFoundation DLL이 바뀌면 WebView2의
      CDM 저장소를 비웁니다.
- [ ] macOS WKWebView 호스트. Linux는 계속 위임 모드

## 파일
| 파일 | 역할 |
|---|---|
| `default.py` | 진입점 |
| `service.py` | 오디오 장치 반환, 로컬 재생 시와 Kodi 종료 시 데몬 종료 |
| `resources/lib/router.py` | URL → 핸들러 연결, 데몬 복구 |
| `resources/lib/routes.py` | 핸들러 → Kodi 디렉터리 목록 / 재생 |
| `resources/lib/nfclient.py` | WebView 데이터 데몬용 탐색 클라이언트 |
| `resources/lib/player.py` | 데몬 생명주기 + 재생 |
| `resources/lib/kodi_subs.py` | Kodi 자막 폰트와 스타일 |
| `resources/lib/kodiutils.py` | Kodi API 도우미 |
| `resources/lib/cache.py` | 목록 디스크 캐시 |
| `runtime/webview/webview_host.py` | WebView2 데몬(탐색 RPC + 재생 + OSD) |
| `runtime/webview/fontfix.py` | OTS 폰트 복구(CLI) |
| `runtime/webview/build_exe.py` | 런타임 exe의 PyInstaller 빌드 |
| `tools/package.py` | exe가 오래됐으면 다시 빌드하고 애드온을 zip으로 묶음 |
| `resources/settings.xml` | 재생 방식, 프로필 번호, 경로 직접 지정 |

## 직접 빌드
```
pip install pywebview fonttools brotli pyinstaller   # 처음 한 번, 일반 Python에서
python tools/package.py                               # exe가 오래됐으면 다시 빌드하고 ../plugin.video.nfbridge-<버전>.zip 생성
```
만든 zip은 위의 [설치](#설치) 순서대로 Kodi에 설치합니다. Kodi 20 이상이 필요합니다.
