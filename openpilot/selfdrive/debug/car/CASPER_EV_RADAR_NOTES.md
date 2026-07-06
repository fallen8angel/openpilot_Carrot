# Casper EV (Continental MRR-35) 레이더트랙 지원 — 조사·테스트 노트

> 목적: `HYUNDAI_CASPER_EV`에서 레이더트랙(원시 radar point) 지원 추가.
> 이 문서는 전용 테스트 분기의 작업 로그. 테스트 반복하며 계속 갱신.

---

## 0. 대상 차량 / 하드웨어

| 항목 | 값 |
|------|-----|
| 차종 | Hyundai Casper EV 2024 (`HYUNDAI_CASPER_EV`, 플랫폼 AX EV) |
| VIN(로그) | KMHB3511FSW032357 |
| 전방 레이더 P/N | 99110-GX000 (Supplier AS100A8330) |
| 레이더 모델 | **Continental MRR-35** (※ 기존 지원차들은 대부분 **Mando**) |
| CAN | 클래식 CAN (CAN FD 아님) |
| 차량 플래그 | `CAMERA_SCC \| CHECKSUM_CRC8 \| EV` — **MANDO_RADAR 없음** |

---

## 1. openpilot 레이더트랙 구조 (배경)

### 1-1. `EnableRadarTracks` 파라미터 (carrot_settings.json)
| 값 | 의미 |
|----|------|
| -2 | VOACC 비전 only 시험 |
| -1 | SCC 항상 사용 |
| **0** | SCC 레이더 사용 (기본) — 선두차 1개 오브젝트만 |
| 1 | 레이더트랙 사용 |
| 2 | 레이더트랙 + 저속 SCC |
| 3 | 레이더트랙 + 저속 SCC + 끼어듦 + 스텔스 |

### 1-2. SCC vs 레이더트랙
- **SCC 모드**: `SCC11`/`SCC_CONTROL`에서 선두차 **1개**(`ACC_ObjDist`,`ACC_ObjRelSpd`)만. → `radar_interface.py:_update_scc`
- **레이더트랙 모드**: 레이더 ECU가 쏘는 개별 트랙(`RADAR_TRACK_*`) **수십 개** 파싱. → `radar_interface.py:_update`

### 1-3. "그룹" = 레이더 메시지 주소 배치 (interface.py에서 fingerprint로 자동판별)
| 그룹 | 감지 | 시작주소/개수 | 예시 |
|------|------|--------------|------|
| 1 | ACAN에 `0x210` | 0x210, 16 (msg당 2트랙) | 아이오닉5, EV6, K8, 스타리아 |
| 2 | (기본) | 0x3A5, 32 | 아이오닉5PE, 쏘렌토, 카니발, EV9, K5… |
| 3 | ACAN에 `0x400`+`0x41D` | 0x400, 30 (OBJECT_LENGTH 有) | 쏘렌토 MQ4, 카니발 KA4 (CANFD) |
| 4 (클래식 CAN) | `MANDO_RADAR` 플래그 | **0x500, 32** (bus 1) | 소나타/팰리세이드/싼타페 등 |

### 1-4. 클래식 CAN 레이더트랙 = `MANDO_RADAR` 플래그
- `values.py:108` 주석: *"If 0x500 is present on bus 1 it probably has a Mando radar outputting radar points."*
- `MANDO_RADAR`(2^12) 플래그 → `values.py:172`에서 `Bus.radar: hyundai_kia_mando_front_radar_generated` DBC 연결.
- DBC 포맷(0x500~0x51F, AZIMUTH/STATE/LONG_DIST/REL_SPEED…)은 **만도 전용**.
- `radar_interface.py:37`은 클래식 CAN이면 **무조건** `hyundai_kia_mando_front_radar_generated` 사용.

### 1-5. 레이더 포인트 켜기 = UDS (Mando 기준)
- `selfdrive/debug/car/hyundai_enable_radar_points.py`: 레이더(0x7d0)의 DID `0x0142`를 Read/Write.
  - default: `00 00 00 01 00 00` / tracks-enabled: `00 00 00 01 00 01`
- `SUPPORTED_FW_VERSIONS`에 소나타/팰리세이드/싼타페/G70/K5 만도 FW만 등록. **`99110-GX000`(MRR-35) 없음.**
- Carrot의 `interface.py:enable_radar_tracks()`도 동일 명령을 init 때 자동 전송(`EnableRadarTracks>0 and not CANFD`). CAMERA_SCC면 `sccBus=2`.

---

## 2. Casper EV가 지금 안 되는 이유 (2가지)

1. **플래그 부재** — `HYUNDAI_CASPER_EV`에 `MANDO_RADAR`도 `RADAR_GROUP`도 없음 → 레이더 DBC 자체가 연결 안 됨. 코드가 "이 차에 트랙 뿌리는 레이더 있다"를 모름.
2. **레이더 모델 상이** — MRR-35(Continental) ≠ Mando. 유일한 클래식 CAN 파서(`hyundai_kia_mando_front_radar_generated`)는 만도 비트배치라 MRR-35엔 안 맞음. 또한 FW가 enable 스크립트 목록에 없어 UDS 켜기도 미확인.

→ `EnableRadarTracks=1` 켜면: enable UDS가 MRR-35에 안 먹혀 `iso-tp query bad response` / `isotp - rx: invalid consecutive frame index` 에러. 설령 켜져도 만도 DBC로 파싱→유효트랙 0→SCC로 폴백.

---

## 3. rlog 분석 결과 (정상/대시캠 상태)

**로그:** `00001d5e--fad2682201--0--rlog.zst` (출근 주행, 63.5s, `HYUNDAI_CASPER_EV`)

### 3-1. 레이더 트랙 배열 없음 (결정적)
- 전 버스 통틀어 **연속 주소 런(≥8) 0건**. (트랙 배열이면 32/30개 연속이 떠야 함)
- `0x500~0x51F`: **6개만 흩어짐** (`0x500,0x507,0x50a,0x50b,0x50c,0x50e`) = 일반 PT 메시지, 트랙 배열 아님.
- bus 1 계열엔 UDS 진단응답(`0x7xx`,`0x18daXXf1`)만 → 레이더 오브젝트 0.
- **결론: MRR-35가 평상시 원시 트랙을 CAN에 안 뿌림.** 이 로그로는 트랙 해독 불가(데이터 부재).

### 3-2. 화면 "앞차 그래픽"의 정체 = SCC11 카메라 선두차 1개
`SCC11`(0x420, 카메라 버스 2) 수동 디코딩(리틀엔디안):
```
ACC_ObjStatus : bit22 len2      ACC_ObjDist : bit33 len11 (x0.1 m)
ACC_ObjRelSpd : bit44 len12 (x0.1, off -170)
```
결과: 63s 중 **78% 프레임에서 앞차 1개 추적, 거리 4.3~32.3m**, 상황따라 변동
(예: t=24s 앞 트임(status=0,204.6m) → t=40s 17.8m 재등장 → t=56s 8.4m).
→ 님이 화면에서 보는 "정면에 딱 하나"는 **레이더트랙이 아니라 카메라 SCC 선두차**. 정상 동작.

---

## 4. 분석 환경 (재현용)

- Windows, Python 3.12, `pip install zstandard pycapnp numpy`
- **주의**: `openpilot/tools/lib/logreader.py`는 Windows에서 `import fcntl`로 실패.
  → cereal 스키마를 pycapnp로 **직접 로드**해서 우회.
  ```python
  import zstandard, capnp
  capnp.remove_import_hook()
  log_capnp = capnp.load("openpilot/cereal/log.capnp",
                         imports=["openpilot/cereal", "opendbc_repo/opendbc/car"])
  dat = zstandard.ZstdDecompressor().stream_reader(open(path,"rb")).read()
  for e in log_capnp.Event.read_multiple_bytes(dat):
      if e.which() == "can":
          for c in e.can:  # c.src=bus, c.address, c.dat
              ...
  ```
- **재사용 스캐너**: `casper_radar_scan.py` (이 폴더)
  ```
  python casper_radar_scan.py <rlog.zst>                    # 단일: 트랙 시그니처 탐지
  python casper_radar_scan.py <normal.zst> <enabled.zst>    # diff: 켠 뒤 새 주소 블록
  ```

---

## 5. 테스트 계획 (다음 단계)

- [ ] **Step 1 — "켜지긴 하나" 확인 (정차 OK)**: 차 전원 ON(EV READY) + `EnableRadarTracks=1` + 부팅 30~60s.
  차 전원이 들어와야 레이더 ECU가 깨어나 트랙을 방송함(코마 디바이스만 부팅은 무의미). 앞에 차/벽 5~15m 두면 값 검증까지.
  → `casper_radar_scan.py <normal> <enabled>` 로 **새 주소 블록** 확인.
- [ ] **Step 1b — 안전 프로브 (권장 병행)**: openpilot 끈 상태로
  `python hyundai_enable_radar_points.py --read-only --scan-config-dids --bus 0` (안 되면 `--bus 2`).
  MRR-35의 config DID(`0x0142` 또는 스캔된 DID) 읽히는지/디폴트값 기록.
- [ ] **Step 2 — 비트 해독**: 트랙 블록이 나오면 → **움직이는 로그(앞차 거리 변화)** 확보 → 거리/횡위치/상대속도/유효플래그 비트 매핑.
- [ ] **Step 3 — 구현**: MRR-35 DBC 작성 + `radar_interface.py` MRR-35 분기 + `HYUNDAI_CASPER_EV`에 레이더 플래그 추가 (+ 필요시 enable 시퀀스).

⚠️ enable_radar_points 주석 경고: config 변경은 AEB/FCW에 영향 가능 (at your own risk). `--read-only` 먼저.

---

## 6. 수정 지점 (구현 시 건드릴 파일)

| 파일 | 관련 |
|------|------|
| `opendbc_repo/opendbc/car/hyundai/values.py` | `HYUNDAI_CASPER_EV` 플래그, `MANDO_RADAR`, `HyundaiPlatformConfig.init` |
| `opendbc_repo/opendbc/car/hyundai/radar_interface.py` | `get_radar_can_parser`, `_update`, 그룹 분기 |
| `opendbc_repo/opendbc/car/hyundai/interface.py` | `enable_radar_tracks`, 레이더 그룹 감지(L60~65), `radarUnavailable`(L170~178) |
| `opendbc_repo/opendbc/dbc/generator/hyundai/` | (신규) MRR-35 DBC generator |
| `openpilot/selfdrive/debug/car/hyundai_enable_radar_points.py` | `SUPPORTED_FW_VERSIONS`에 `99110-GX000` 추가(가능 시) |

---

## 8. 테스트 이력

### 2026-07-06 — RadarTracks=3 켠 뒤 첫 실측 (SEG0 시동직후 / SEG9 정차 10분)
로그: `20260706/00001d60--3983e5d700--0`(63s), `--9`(603s). 대조군 `00001d5e--…--0`.

**결과: 레이더는 살아있고 UDS에 응답하나, Carrot의 만도식 enable이 세션 단계에서 거부됨 → 트랙 안 나옴.**

1. **레이더 FW 확인** (t≈1.5s, `62 f100` 읽기 성공, ASCII):
   `AX_ RDR ---- 1.00 1.00 99110-GX000` → MRR-35 정상 응답. 레이더 UDS 통신 살아있음.
2. **enable_radar_tracks() 실행됨** (t≈13.8s, sendcan bus 2 → 0x7d0):
   `02 10 07` = **DiagnosticSessionControl, 세션타입 0x07(만도 전용 확장세션)** 요청.
3. **레이더가 거부** (0x7d8 응답): `03 7f 10 12`
   = **Negative Response, svc=0x10, NRC=0x12 (sub-functionNotSupported)**
   → **MRR-35는 세션타입 0x07을 지원 안 함.**
4. 세션이 거부돼서 뒤따르는 config write(`2e 01 42 …`)는 **아예 전송조차 안 됨** → 트랙 출력 안 켜짐.
5. `casper_radar_scan.py` diff: 켠 로그에 **새 트랙 주소 블록 0건** (UDS 응답 주소만 추가). SEG9(정차 10분)도 새 주소 0.
   - (참고: t=2~3s의 `7f 22 31`은 FW 핑거프린팅 중 미지원 DID 읽기 → 정상, 무관)

**해석 / 다음 방향** (데드엔드 아님):
- 레이더가 UDS로 말은 하는데 **세션 0x07만 거부**. = 만도 방식이 콘티넨탈엔 안 맞을 뿐, 다른 세션/DID가 있을 수 있음.
- 다음: openpilot 끈 상태로
  `python hyundai_enable_radar_points.py --read-only --scan-config-dids --bus 2`
  (이 스크립트는 **확장세션 0x03**을 시도하고 DID `0x0100~0x01ff`를 스캔 → MRR-35가 config 접근에 쓰는 세션/DID 발견 목적). bus 2 먼저(레이더가 카메라버스에서 응답 중), 안 되면 `--bus 0`.
- ⚠️ 현재 `hyundai_enable_radar_points.py`의 write 경로는 세션 `0x07` 하드코딩 → MRR-35엔 그대로 못 씀. read-only 스캔으로 먼저 올바른 세션/DID부터 찾아야 함.

### 2026-07-06 (2) — `hyundai_enable_radar_points.py --read-only --scan-config-dids --bus 2`
차에서 openpilot 정지 후 실행. 레이더 FW: `AX__ RDR -----  1.00 1.00 99110-GX000`.

**결정적: MRR-35엔 만도 enable DID `0x0142`가 없음.**
- `0x0142` 읽기: 기본세션·확장세션 **둘 다 실패** (`request out of range` = NRC 0x31 = DID 미존재).
- → 만도식(세션0x07 + DID 0x0142)은 프로토콜상 **완전 불가** 확정.

**읽힌 config DID 9개 (확장세션, 기본값 — 되돌림 기준이니 보존):**
| DID | 값(hex) | 바이트수 | 메모(추정) |
|-----|---------|---------|-----------|
| 0x0121 | `4b` ('K') | 1 | 마켓/변형 코드? (K=Korea/Kia?) |
| 0x0123 | `02 7e 00 1e` | 4 | config/version 워드? |
| 0x0125 | `01` | 1 | 플래그 |
| 0x0126 | `00` | 1 | 플래그 (0 = 비활성?) ← enable 후보? |
| 0x0127 | `fd` | 1 | 파라미터(253/-3) |
| 0x0128 | `03` | 1 | 파라미터 |
| 0x0129 | `00` | 1 | 플래그 (0 = 비활성?) ← enable 후보? |
| 0x0131 | `1e 1e` | 2 | 파라미터 쌍(30,30) |
| 0x0171 | `91` | 1 | 파라미터 |

**해석 (객관적):**
- 레이더는 확장세션(0x03)에서 이 DID들을 읽어줌 = config 공간은 있음.
- 그러나 **어느 DID가 "트랙/포인트 출력 ON"인지 매핑 정보가 없음.** 만도처럼 알려진 값이 아님.
- 리스크: (a) config write에 **SecurityAccess(0x27 seed/key) 잠금**이 걸려있을 가능성 큼 → 키 없으면 write NACK. (b) 안전장치(AEB/FCW) 영향 가능. (c) 애초에 MRR-35가 **원시 트랙 출력 모드를 아예 미지원**할 수도 있음.
- → **여기가 진짜 난관.** 만도식 배제는 확정됐고, 다음은 "외부 지식(콘티넨탈/MRR-35 트랙 enable 방법)" 또는 "위험한 시행착오 write"가 필요.

**결정된 방향 (2026-07-06, 사용자): 증거 조금 더 수집 → 바로 위험 write 테스트.**

도구: `casper_radar_probe.py` (신규, 이 폴더)
- `--read --bus 2` : 식별 DID(0xF180~F19F) + config DID(0x0100~01FF) 덤프 (안전)
- `--write 0xNNNN HH --bus 2` : config DID 1개 write. **먼저 현재값 읽어 revert 명령 출력** 후 `WRITE` 입력 확인. (위험)

**write 실험 순서 (currently-00 단일바이트 플래그 우선 = enable 가능성 高, 값 손상 위험 低):**
1. `--write 0x0126 01` → 재시동 → 부팅(RadarTracks=3) → 로그 → `casper_radar_scan.py <정상> <신규>`로 트랙 블록 확인 → 없으면 `--write 0x0126 00` 복구
2. 안 되면 `0x0129`로 반복
3. NACK 0x33(securityAccessDenied) 뜨면 → **SecurityAccess 키 필요 = 우리 선에서 벽.** 마스터/커뮤니티 문의로 전환.

⚠️ 안전장치(AEB/FCW) 영향 위험. 정차·안전장소, revert 명령 보관, 후 제동 테스트 필수.

### 2026-07-06 (3) — `casper_radar_probe.py --read` (식별정보 추가 수집)
식별 DID(읽기전용, enable 단서는 없으나 기록):
| DID | 값 | 의미 |
|-----|-----|------|
| 0xF187 | `99110GX000` | 스페어파트 넘버 |
| 0xF18B | `20250313` | 제조일 2025-03-13 |
| 0xF18C | `25C13CA5472` | ECU 시리얼 |
| 0xF191 / F193 | `1.00` | HW 넘버 / 서플라이어 HW 버전 |
| 0xF197 | `FR_RDR` | 시스템명 = Front Radar |

**중요 관찰**: `0x0171`이 스캔①(0x91)→스캔②(0x90)로 **값 변동** = **동적 상태값(config 토글 아님)**. write 금지. 레이더가 실제 연산 중(살아있음)이라는 증거.
→ 순수 config 후보는 여전히 `0x0126`(00), `0x0129`(00). 다음: write 실험 진행.

### 2026-07-06 (4) — write 시도 → 세션 벽
`--write 0x0126 01 --bus 2` (확장세션 0x03 안에서 시도):
```
write REJECTED: NACK 0x7f = serviceNotSupportedInActiveSession
```
= **WriteDataByIdentifier(0x2E) 서비스가 확장세션(0x03)에선 미지원.** config write는 **다른 세션**에서만 가능.
- SecurityAccess 거부(0x33) 아님 → 순수 세션 문제.
- 만도는 write를 세션 **0x07**에서 했지만 MRR-35는 0x07 거부(NRC12). → MRR-35의 write 세션은 0x03도 0x07도 아닌 미지의 세션.

**남은 관문(냉정): (올바른 세션) + (아마 SecurityAccess seed/key) + (올바른 DID/값).** 세션은 스캔으로 찾을 수 있으나, 뒤에 보안잠금 있으면 제조사 키 없이는 **벽**.

**다음**: `casper_radar_probe.py --scan-sessions --bus 2` (신규) — 레이더가 받아주는 진단세션 열거(0x01~0x0f, 0x40~0x4f, 0x60~0x6f). 쓰기 가능한 비-기본 세션이 있으면 그 안에서 write 재시도. 0x01/0x03만 열리면 → 보안/미지세션 잠금으로 판단, 마스터/커뮤니티 문의로 전환.

### 2026-07-06 (5) — 세션 스캔 (READY 상태)
```
0x01 default    : ACCEPTED
0x02 programming: NACK 0x22 conditionsNotCorrect   ← 존재O, 조건 안 맞음
0x03 extended   : ACCEPTED
0x05 (제조사?)   : NACK 0x22 conditionsNotCorrect   ← 존재O, 조건 안 맞음
(그 외 0x04,0x06~0f,0x40~4f,0x60~6f: 미지원)
```
- 0x02/0x05가 **`subFunctionNotSupported`(없음)가 아니라 `conditionsNotCorrect`(0x22)** = **세션은 실재하나 진입 전제조건 미충족.** 보안거부(0x33)는 아직 아님.
- 0x22는 흔히 **시동/모터 상태 조건** → **READY(모터ON) 말고 ON-not-READY(시동버튼 2번, 브레이크 X)** 로 재시도할 가치 있음.

**신규 옵션**: `--try-session 0xNN` (세션 진입만 테스트), `--write ... --session 0xNN` (지정 세션에서 write).
**다음 실험(ON-not-READY 상태)**: `--try-session 0x02`, `--try-session 0x05` → ACCEPTED 뜨면 `--write 0x0126 01 --session 0x02`.
→ ON 상태에서도 0x22/0x33이면 → **보안잠금/전제조건 미지 = 소프트웨어 단독으론 벽 확정 → 마스터 문의로 전환.**

---

### 2026-07-06 (6) — 주행 로그 (움직일 때 트랙 나오나 검증)
로그 `a.zst`(주행, vEgo 최대 26.2km/h, 절반 이동), `aa.zst`(잘린 로그, 무시).
- `a.zst` vs baseline diff: 새 주소는 전부 진단(0x7xx UDS)+0x40a 단발. **트랙 배열 0건.**
- **결론: 정차(로그2개) + 주행(26km/h)** 어느 상태에서도 MRR-35는 원시 트랙 배열을 CAN에 안 뿌림. "움직일 때만" 가설 기각.

---

## 9. 최종 상태 (2026-07-06 기준) — 소프트웨어 단독 경로 소진

**확정된 사실:**
1. MRR-35 살아있음 (UDS 응답, SCC 연산 중, 0x0171 동적값 변동).
2. 만도 방식 불가: 세션 0x07 거부(NRC12), DID 0x0142 없음.
3. config **읽기**는 확장세션(0x03)에서 가능(DID 9개), 그러나 **쓰기**는 0x03에서 미지원(NRC 0x7F).
4. 쓰기 가능 세션 후보(0x02 programming, 0x05)는 실재하나 `conditionsNotCorrect`(0x22)로 잠김. (보안/전제조건)
5. 정차·주행 어느 상태에서도 **원시 트랙 배열 미방송**.

**판정: 제조사 세션 키/지식 없이는 소프트웨어 단독으로 MRR-35 트랙 출력을 못 켬 = 현재 경로 벽.**
(미확인 잔여: ON-not-READY 상태에서 0x02/0x05 재시도 → `--try-session`. 이것도 0x22/0x33이면 벽 확정.)

**다음(외부 지식 필요):**
- 이 문서 들고 **Carrot 마스터에게 문의**: 콘티넨탈 MRR-35 트랙 enable 세션/SecurityAccess/DID 아는지.
- 또는 MRR-35가 애초에 CAN으로 원시 트랙을 안 뿌리는 설계(카메라 사설링크로만 전달)일 가능성 인정.

## 10. 리버스 가능성 — "A냐 B냐" 판별이 관건

만도 unlock이 개인 리버스로 가능했던 이유 = **보안잠금이 없었음**(세션0x07+DID0x0142 write가 그냥 먹힘). MRR-35의 write 세션(0x02/0x05)은 `conditionsNotCorrect`(0x22)로 막혀 아직 미확정:
- **(A) 전제조건만** (시동상태/루틴/tester-present) → 만도급, 개인 크랙 가능.
- **(B) SecurityAccess(seed/key)** → 브루트포스 불가, **펌웨어 덤프+디스어셈블로 seed→key 루틴 추출** 필요 = 상위 티어.

**판별 도구** (신규): `casper_radar_probe.py --request-seed 0x03 --bus 2`
- SEED 반환 → (B) 보안잠금 확정.
- 0x7F/0x31/0x12 → 그 세션엔 보안 없음(다른 세션 시도).
- 0x22 → 전제조건 문제.

**미완 실험 (다음 차 방문 시, ON-not-READY 상태):**
1. `--request-seed 0x03` (보안 유무 판별)
2. `--try-session 0x02` / `--try-session 0x05` (전제조건이 시동상태였나)
3. 0x02 열리면 `--request-seed 0x02`
→ 결과로 A/B 확정. B면 마스터/커뮤니티 or 펌웨어RE, A면 계속 크랙.

**참고**: 만도 리버스는 다수 유저·다수 차량·다수 로그의 집단작업이었음. 개인+단일차량은 불리하나 불가능은 아님. 단 **enable 단계에서 막혀 있어(트랙이 안 흐름) 포맷 해독으로 넘어가지도 못하는** 상태가 핵심 병목.

### 2026-07-06 (7) — seed 프로브 + 세션 재시도 (전부 READY 상태)
```
--request-seed 0x03 → NACK 0x12 subFunctionNotSupported  = 확장세션엔 SecurityAccess 서비스 없음(보안은 0x02 안에 있을 것)
--try-session 0x02  → NACK 0x22 conditionsNotCorrect     (아직 READY)
--try-session 0x05  → NACK 0x22 conditionsNotCorrect     (아직 READY)
```
**병목 재확인: 전선은 "세션 0x02/0x05 진입" 하나.** 아직 **ON-not-READY 미시도** + **0x03→0x02 체인 미시도**.

**남은 두 가설 (다음 실험):**
1. **체인 세션**: default→0x03→0x02 (많은 ECU가 extended 먼저 요구). → `try_session` 자동 테스트하게 개선(direct + via 0x03).
2. **시동상태**: ON-not-READY(시동버튼 2번, 브레이크 X). 지금까지 전부 READY라 미검증.

→ 다음: **ON-not-READY 상태**에서 `--try-session 0x02`, `--try-session 0x05` (이제 direct+via0x03 둘 다 자동 시도).
- ACCEPTED → 그 세션서 `--request-seed`로 보안 유무 확인 → 없으면 `--write` 크랙 계속.
- 여전히 0x22 → 전제조건이 시동/체인 아님(루틴/tester 등 미지) → 마스터 문의.
- 0x33 → 보안잠금 = 펌웨어RE 티어.

### 2026-07-06 (8) — 🎯 돌파: ON-not-READY에서 세션 진입 성공
```
--try-session 0x02 → direct: ACCEPTED
--try-session 0x05 → direct: ACCEPTED
```
- **conditionsNotCorrect(0x22)의 원인 = 시동상태 확정.** ON-not-READY(시동 2번, 브레이크 X)로 바꾸니 programming 세션(0x02)·0x05 모두 **직접 진입 성공**. 보안잠금 아님(진입 단계).
- → 시나리오 (A) 유력. 다음: **0x02 안에 SecurityAccess 있나** + **실제 write 되나**.

**다음 실험 (ON-not-READY 유지):**
1. `--request-seed 0x02` → 0x12/미지원=보안없음(write 가능성↑), seed=보안잠금.
2. `--write 0x0126 01 --session 0x02` → positive면 재시동→트랙 확인. 0x33이면 보안.
자세한 usability(영구성) 판단은 write 성공 후 재부팅 스캔으로.

### 2026-07-06 (9) — 보안 없음 확인 + 세션별 서비스 분리 파악
```
--request-seed 0x02 → NACK 0x12 subFunctionNotSupported = programming 세션에 SecurityAccess 없음 ✅
--write ... --session 0x02 → "cannot read current: NACK 0x7f svcNotSupportedInSession"
```
**핵심 구조 파악**: 서비스가 세션별로 갈림 —
- **ReadDataByIdentifier(0x22): 확장세션 0x03에서만** (programming 0x02에선 0x7F)
- **WriteDataByIdentifier(0x2E): programming 0x02 필요** (0x03에선 0x7F)

→ `do_write` 수정: **읽기는 0x03, 쓰기는 0x02, 읽기백은 0x03**. (0x0126 기본값=00 이미 확인됨, revert=00)
→ 보안 없음 확정이라 write 성공 가능성 높음. 다음: git pull 후 재실행.

### 2026-07-06 (10) — write 서비스(0x2E)가 0x02에서도 미지원
```
--write 0x0126 --session 0x02 → write REJECTED: NACK 0x7f svcNotSupportedInSession
```
**서비스별 세션 지원 현황:**
| 서비스 | 0x03 확장 | 0x02 프로그래밍 |
|--------|:---:|:---:|
| 읽기 0x22 | ✅ | ❌ 0x7F |
| 쓰기 0x2E | ❌ 0x7F | ❌ 0x7F |

→ **WriteDataByIdentifier(0x2E)가 두 세션 다 미지원.** 남은 세션 0x05 미시도.
가설: (a) 0x05가 write 세션이거나, (b) MRR-35 config가 0x2E가 아닌 **RoutineControl(0x31)/IOControl(0x2F)** 로 바뀌거나, (c) 이 config DID들이 read-only(=enable 메커니즘 자체가 다름/없음).
다음: `--write ... --session 0x05` 시도 → 안 되면 0x2E 경로 배제, 루틴/IO 탐색 or 마스터.

### 2026-07-06 (11) — 🎯 write 세션 = 0x05, 보안잠금 확정
```
--write 0x0126 --session 0x05 → NACK 0x33 securityAccessDenied
```
**write 서비스(0x2E) 세션별 최종:**
| 세션 | 0x2E write |
|------|-----------|
| 0x03 확장 | 0x7F 서비스없음 |
| 0x02 프로그래밍 | 0x7F 서비스없음 |
| **0x05 (공급자?)** | **0x33 = 서비스 있음, 보안키 필요** ✅ |

**결론: write 경로 = 세션 0x05 + SecurityAccess(seed/key).** = 시나리오 (B) 확정.
- 남은 관문: 0x05의 seed 받아서(→ `--request-seed 0x05`) key 계산. key 알고리즘은 제조사 것.
- 현대 ECU 일부는 seed/key 알고리즘이 커뮤니티에 알려져 있음(약한/표준 알고리즘) → 희망 여지. seed 길이 보고 판단.
다음: `--request-seed 0x05 --bus 2` (ON-not-READY) → seed 확인.

### 2026-07-06 (12) — 보안 레벨이 level 1 아님 + 온라인 리드
```
--request-seed 0x05 (level 1, 0x27 01) → NACK 0x12 subFunctionNotSupported
```
- write는 0x33(보안필요)인데 level 1 seed는 미지원 = **보안이 level 1이 아닌 다른 레벨(0x03/0x05/…/manufacturer)**.
- **신규**: `--scan-security 0x05` — requestSeed 홀수 레벨(0x01~0x7F) 전수 스캔해 seed 반환 레벨 탐지. (requestSeed는 키 안 보내니 락아웃 카운터 안 올림 = 안전)

**온라인 리드 (사용자 방침: 우리+구글로):**
- **UnlockECU** = Bosch/**Continental**/Delphi/Daimler 등 seed-key 언락 툴(무료, 독점DLL 불필요). MRR-35=콘티넨탈이라 **직접 후보**. seed 받은 뒤 매칭 시도.
- commaai/openpilot #1294(Hyundai radar bounty), #1346(radar interface) — 과거 만도 리버스 맥락.

다음: `--scan-security 0x05 --bus 2` (ON-not-READY) → 어느 레벨이 seed 주는지 + seed 값/길이 확보 → 그걸로 UnlockECU/알고리즘 매칭.

### 2026-07-06 (13) — 🎯 SEED 확보! 보안 레벨 0x11
```
--scan-security 0x05 → level 0x11: SEED = 0xf2a22d7f9beff517 (len 8)
                       level 0x15: NACK 0x24 requestSequenceError (0x11 seed 받은 뒤라 정상)
```
- **write 언락 = 세션 0x05 + SecurityAccess level 0x11**(requestSeed 0x27 11 / sendKey 0x27 12).
- **seed = 8바이트(64비트).** key = f(seed), f는 미지의 알고리즘.
- **남은 유일 관문 = seed→key 알고리즘.** (64비트라 브루트포스 불가 → 알고리즘 필요)

**다음 확인:**
1. **seed 고정 vs 랜덤**: `--scan-security 0x05` 여러 번 재실행해 0x11 seed 비교. 고정이면 큰 단순화.
2. **알고리즘 조사(온라인)**: 콘티넨탈/현대 level 0x11 8바이트 seed-key. UnlockECU 콘티넨탈 지원목록.
3. **펌웨어 RE 옵션**: ReadMemoryByAddress(0x23) 되는지 → 되면 덤프해서 key루틴 추출.

### 2026-07-06 (14) — 🔑 알고리즘 후보 특정: DaimlerStandardSecurityAlgo (UnlockECU)
- 우리 seed: `f2a22d7f9beff517` (8B), 레벨 0x11, 세션 0x05.
- **UnlockECU db.json에서 8B seed + level 0x11 = 전부 `DaimlerStandardSecurityAlgoRefG`** (CCGW420, **EARS167(콘티넨탈 레이더)**, EPS_MFA2…, ESPC_167_11).
- 알고리즘 특성: **seed 8B → key 4B**, 내부 변환 후 **private key와 XOR** (private key는 known seed+key 쌍으로 복원 가능). Ref변형(A~G…)마다 private key 상수 다름.
- 가설: 콘티넨탈이 OEM간 보안 재사용 → **MRR-35도 DaimlerStandardSecurityAlgo 계열(아마 RefG)**.

**공략 계획:**
1. UnlockECU 소스에서 DaimlerStandardSecurityAlgo 변환 + Ref별 private key 상수 추출 → 파이썬 포팅(probe에 `--send-key`).
2. seed로 key 계산 → level 0x12로 sendKey → 언락 시도.
3. ⚠️ **틀린 key는 실패카운터 올림**(보통 3회→락아웃/딜레이). Ref 후보를 **가장 유력(RefG)부터** 소수만, 안 되면 전원 재투입 후 다음.
4. 언락되면 → 0x05 세션서 `--write 0x0126 01` → 트랙 확인.

**seed 고정/랜덤 확인 병행 중** (고정이면 계산·검증 훨씬 쉬움).

### 2026-07-06 (15) — 🔓 알고리즘 포팅 완료 + 후보 6개로 좁힘
UnlockECU 소스 확보(`DaimlerStandardSecurityAlgo.cs`, `...RefG.cs`). 알고리즘:
```
seedA=seed[0:4], seedB=seed[4:8] (big-endian u32)
RefG:  i1 = 3040238857*seedA + 2094854071
       i2 = 4126034881*seedB + 3555108353
key = (i1 ^ i2 ^ cryptoKey) & 0xFFFFFFFF   # 4바이트, cryptoKey=ECU별 상수
base:  kA=1103515245, kC=12345 (glibc LCG), i1=kA*A+kC, i2=kA*B+kC
```
**후보 cryptoKey (콘티넨탈 레이더 패밀리 = 최우선):** `B3687D8B`(EARS167 레이더 확정값), 형제 `B3687D83/87/89/8D/8F`, 그다음 `DEDE947A, FFFFFFFF, ...`.
- db.json에 `B3687D8X` 패밀리가 통째로 있음 = 콘티넨탈 레이더 변형들 → **MRR-35도 이 중 하나 확률 높음.**

**probe 신규 기능** (파이썬 포팅됨):
- `--unlock` : 세션0x05·level0x11에서 후보 cryptoKey 순차 시도, 언락되는 키 탐지(write 안 함).
- `--enable DID HEX` : 언락 성공 시 **같은 세션에서 바로 write** (예: `--enable 0x0126 01`).
- `--keys a,b,c` 후보 지정, `--variant refG|base`. 락아웃 시 남은 키 출력.
- ⚠️ 틀린 key는 실패카운터↑ → 보통 3회 후 lockout(0x36/0x37). 락아웃 뜨면 전원재투입 후 `--keys <남은키>`로 이어서.

**다음(ON-not-READY, git pull 후):** `--enable 0x0126 01 --bus 2` → B3687D8B부터 시도. 언락+write POSITIVE 뜨면 재부팅→트랙 확인.

### 2026-07-06 (16) — Daimler(4B키) 전부 0x13 → 키 길이가 다름
`--enable 0x0126 01` 결과: 후보 12개 **전부 `NACK 0x13 badLength/format`**, **락아웃 안 걸림**, seed 매번 다름(랜덤).
- **0x13 = 키 값 틀림(0x35)이 아니라 "키 길이/형식 틀림".** 우린 4바이트(Daimler) 보냈는데 레이더는 **다른 길이 키**를 기대. 0x13은 카운터 안 올림 → 락아웃 없었던 이유.
- ⇒ **알고리즘 계열이 Daimler(8→4B)가 아님.** 키 길이 미지 → 실측 필요.

**8바이트-키 알고리즘 후보(db.json):** **VDOSecurityAlgo(VDO=콘티넨탈 브랜드, 8→8, param K/InternalLevel)** ⭐, IC172Algo1(8→8, 하드코딩 키풀=상수 불필요), IC204(8→8, Salt), ArrayReverseAlgo(8→8). 소스 확보함.

**신규 `--probe-keylen`**: level 0x11에서 더미 제로키를 길이 1~16으로 보내 **맞는 길이 실측**(틀림=0x13, 맞음=0x35). 맞는 길이만 카운터↑라 안전.
**다음:** `--probe-keylen --bus 2` (ON-not-READY) → 키 길이 확정 → 그 길이 알고리즘(8이면 VDO/IC172부터) 포팅.

### 2026-07-06 (17) — 🎯 키 길이 = 8바이트 확정
`--probe-keylen`: **len 8만 `0x35`(invalidKey=올바른 길이), 나머지 전부 0x13.** 카운터 1만 증가(안전).
⇒ **seed 8B → key 8B.** 후보: VDOSecurityAlgo(콘티넨탈,K필요), **ArrayReverse(seed뒤집기, 상수無)**, **IC172(하드코딩 풀, 상수無)**, IC204(Salt).

**probe 개편**: unlock을 4바이트 Daimler → **8바이트 알고리즘 방식**으로 교체.
- `--algos arrayreverse,ic172` (기본), 각 알고리즘이 8B key 생성 → sendKey.
- `--enable 0x0126 01` : 언락(자기완결형 알고리즘부터) → 성공 시 같은 세션 write.
- 자기완결형(arrayreverse/ic172)은 **미지수 없음** → 맞으면 바로 뚫림. 틀리면 0x35(카운터↑) → 3회쯤서 락아웃, 전원재투입 후 `--algos <남은것>`.

### 2026-07-07 재개 — 8바이트 알고리즘 시도 준비 완료
probe 스크립트 8바이트 버전 반영+문법검증+포팅 자체테스트 완료(IC172 키 끝 "UECU" 확인).
**다음(ON-not-READY, git pull 후):** `--enable 0x0126 01 --bus 2` → arrayreverse→ic172 순 시도.
- 둘 다 0x35(invalidKey)면 자기완결형 아님 → VDO(콘티넨탈, 후보 K 상수들) 포팅으로.

### 2026-07-07 (2) — arrayreverse/ic172 실패 → UnlockECU 소진
```
arrayreverse → 0x35 invalidKey / ic172 → 0x35 invalidKey  (길이는 맞음, 알고리즘 다름)
```
- db.json 8→8 전수조사: **자기완결형(상수無)은 ArrayReverse·IC172 딱 2개** = 둘 다 실패.
- 나머지 8→8은 VDO(K 149후보, 전부 벤츠)·IC204(Salt 336, 벤츠) = **현대 상수 없음, 브루트포스 비현실적**(락아웃).
- 레이더 전용 알고리즘(LRR3/BoschConti/ORC166)은 4→4·2→2·8→4라 **8→8 불일치**.
- ⇒ **MRR-35 알고리즘은 UnlockECU(벤츠 DB)에 없음.** 온라인 검색(sunnypilot 등)도 만도 방식뿐.

**남은 것:**
1. (저비용) 상수 없는 **단순 변환 배치** 시도: complement/revcomplement/swaphalves/nibbleswap/rot/add/xor 등. probe에 `SIMPLE_ALGOS` 추가. 확률 낮지만 공짜. `--algos complement,revcomplement,swaphalves,nibbleswap,rotl1`
2. (고비용, 확정) **레이더 펌웨어 덤프 + 디스어셈블**로 seed→key 루틴 추출. UDS read(0x23)는 보안게이트라 순환 → **물리 플래시 덤프** 필요.

락아웃: 오늘 2회(0x35) 후에도 안 걸림 → 배치로 몇 개씩 시도 가능. 락아웃 뜨면 전원재투입+`--algos <남은것>`.

---

## 7. 로그 관찰 요약 (부팅 로그 참고)
- `SelectedCar = HYUNDAI_CASPER_EV`, `$$$CAMERA_SCC`
- `$$$OenpilotLongitudinalControl = True, CAMERA_SCC(8) or RadarTracks0` ← EnableRadarTracks=0, 롱컨은 CAMERA_SCC로 켜짐
- `DBC: hyundai_kia_generic`, `ECAN=0` (클래식 CAN)
- CanFD 프린트(`ACAN=`, `Radar Group X detected`) 전무 → CANFD 아님 확정
- 부팅 시 `iso-tp query bad response`/`isotp rx invalid` = FW 조회 실패(레이더트랙과 별개)
