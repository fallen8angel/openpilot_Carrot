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

## 7. 로그 관찰 요약 (부팅 로그 참고)
- `SelectedCar = HYUNDAI_CASPER_EV`, `$$$CAMERA_SCC`
- `$$$OenpilotLongitudinalControl = True, CAMERA_SCC(8) or RadarTracks0` ← EnableRadarTracks=0, 롱컨은 CAMERA_SCC로 켜짐
- `DBC: hyundai_kia_generic`, `ECAN=0` (클래식 CAN)
- CanFD 프린트(`ACAN=`, `Radar Group X detected`) 전무 → CANFD 아님 확정
- 부팅 시 `iso-tp query bad response`/`isotp rx invalid` = FW 조회 실패(레이더트랙과 별개)
