# 톤 JSON 형식 v1 (GE150-TONEPACK) — 정본

다른 AI 가 톤 최대 200개를 JSON 파일 하나로 만들면, 프로그램이 `.mo` 로 바꾸고 장비에 적용합니다.
AI 에게 줄 요청문은 기종별로 `prompts/GE150Pro_AI_프롬프트.txt`, `prompts/GE150_AI_프롬프트.txt` 에 있습니다(이 문서와 같은 규칙이며, 모델 목록은 카탈로그에서 자동으로 만듭니다).

## 1. 흐름
```
다른 AI → tonepack.json(1개, 프리셋 1~200개)
   → 프로그램 창에 끌어다 놓기(또는 [톤 JSON 불러오기])
   → 검사 → <json과 같은 폴더>\<json 이름>_mo_YYYYMMDD-HHMMSS\ 에 .mo 생성
   → 그 폴더가 자동으로 '톤 데이터 폴더'가 되어 표에 표시 → [장비에 적용]
```

## 2. 파일 구조
```json
{
  "format": "GE150-TONEPACK",
  "device": "GE150PRO",
  "version": 1,
  "title": "내 밴드 합주 200",
  "presets": [
    {
      "slot": "01A",
      "name": "Wonderwall CLN",
      "song": "Wonderwall",
      "artist": "Oasis",
      "role": "CLEAN",
      "modules": {
        "FX/COMP": {"on": true,  "model": "YELLOW COMP", "params": {"ATTACK": 50, "THRES": 45, "RATIO": 30, "LEVEL": 60}},
        "DS/OD":   {"on": false, "model": "TUBE DR",     "params": {"GAIN": 30, "TONE": 50, "VOLUME": 60}},
        "AMP":     {"on": true,  "model": "65 US DX",    "params": {"GAIN": 25, "BASS": 50, "MID": 50, "TREBLE": 55, "PRES": 50, "MST": 60}},
        "CAB":     {"on": true,  "model": "US DLX 112",  "params": {"TUBE": "6V6", "MIC": "SM57", "CENTER": 30, "DISTANCE": 40}},
        "NS GATE": {"on": true,  "model": "NOISE GATE",  "params": {"THRES": 40, "ATTACK": 50, "RELEASE": 50}},
        "EQ":      {"on": false, "model": "MOOER G",     "params": {"100Hz": 0, "250Hz": 0, "630Hz": 0, "1.6KHz": 0, "4KHz": 0}},
        "MOD":     {"on": true,  "model": "ANA-CHORUS",  "params": {"RATE": 30, "MIX": 40, "TONE": 50, "DEPTH": 45}},
        "DELAY":   {"on": false, "model": "DIGITAL",     "params": {"LEVEL": 20, "F.BACK": 25, "TIME": 380, "SUB-D": "OFF"}},
        "REVERB":  {"on": true,  "model": "ROOM",        "params": {"DECAY": 40, "LEVEL": 25, "P.DELAY": 20, "TONE": 50}}
      },
      "memo": "후렴 전 아르페지오",
      "sources": ["https://..."],
      "confidence": "C"
    }
  ]
}
```
(예시의 노브 이름·순서는 설명용이며, 실제 노브는 모델 목록을 따릅니다.)

## 3. 규칙
| 항목 | 규칙 | 위반 시 |
|---|---|---|
| `format`/`version` | `"GE150-TONEPACK"`(예전 이름 `"GE150PRO-TONEPACK"` 도 같은 뜻으로 받음) / `1` | 파일 전체 거부 |
| `device` | 선택. `"GE150PRO"` 또는 `"GE150"`. 이 JSON 이 어느 기종용인지 적습니다. 변환 대상 기종을 정하는 데 쓰입니다(아래 5절) | 모르는 값: 파일 전체 거부. 변환 대상과 다름: 경고 1건 |
| `presets` | 1~200개. 일부만 있어도 됨(있는 슬롯만 바뀜) | — |
| `slot` | `01A`~`50D`. 파일 안에서 중복 불가 | 그 프리셋 오류 |
| `name` | **공백 포함 GE150 Pro 16자 이하 / GE150 14자 이하, 영문·숫자·공백·키보드 기호(ASCII)만, 한글 불가.** 장비 이름 = `name` 그대로(슬롯 번호는 장비가 따로 표시하므로 붙이지 않음) | 오류 |
| `modules` | 키 9개: `FX/COMP`, `DS/OD`, `AMP`, `CAB`, `NS GATE`, `EQ`, `MOD`, `DELAY`, `REVERB` | 빠진 모듈: 경고 + OFF·첫 모델·기본값으로 채움. 모르는 키: 오류 |
| `on` | `true`/`false` | 오류 |
| `model` | **변환 대상 기종의** 모델 목록 이름(앞 번호 없이, 대소문자·공백·하이픈 차이 무시). 모델 수는 GE150 Pro 가 더 많습니다(예: REVERB `SHIMMER`, DS/OD `TIGHT METAL` 은 GE150 에 없음) | 오류(비슷한 이름 제안) |
| `params` | 그 모델의 노브 이름. **표시값**으로 적습니다(아래 4절). 노브 이름 별칭: `F.BACK` = `FEEDBACK`, `P.DELAY` = `PRE DELAY` (기종마다 부르는 이름이 달라 어느 쪽으로 적어도 받음) | 모르는 노브·범위 밖: 오류 / 빠진 노브: 경고 + 기본값 |
| 금지 모델 | AMP·CAB 의 `EMPTY`(사용자 샘플 칸), MOD 의 `PITCH SHIFT`·`DETUNE`(PITCH 값 규칙 미확정) | 오류 |
| `exp` | 생략 가능. 생략 시 `{"FUN_SWITCH":0,"MODULE_CTRL":0,"PARA_CTRL":6,"VOL_MAX":100,"VOL_MIN":0,"VOL_SWITCH":1}`(볼륨 페달) | — |
| 그 밖의 키 | `song`·`artist`·`role`·`memo`·`sources`·`confidence` 등은 변환에 쓰지 않음(엑셀로 보기의 요약 시트에는 song·artist·role 표시) | 무시 |

## 4. 표시값 → `.mo` 값 변환
| 노브 | 적는 값(표시값) | `.mo` 값 |
|---|---|---|
| 일반(0~100) | 정수 0~100 | 그대로 |
| EQ 대역(`100Hz` 등)·`GAIN1~3` | 정수 −16~16 (dB) | 값 + 16 |
| EQ `FREQ1~3` | 정수 0~18000 (Hz) | 그대로(장비 확인 전까지 경고) |
| DELAY `TIME`·`TIME A/B` | 정수 40~2500 (ms) | 그대로 |
| REVERB `P.DELAY`(GE150 `PRE DELAY`) | 정수 0~500 | 그대로 |
| 목록형 `TUBE`·`MIC`·`SUB-D`·`SUB A/B` | 글자(예 `"EL34"`, `"SM57"`, `"1/8D"`) | 목록 순번 |

- 기본값(빠진 노브): 0~100 → 50, EQ → 0dB, TIME → 400ms, P.DELAY/PRE DELAY → 20, FREQ → 범위 중간값(9000), 목록형 → 첫 항목
- 숫자 노브에 문자열을 넣거나 목록형에 숫자를 넣어도 오류로 처리합니다(관대하게 해석하지 않음).

## 5. 프로그램 동작
- 창에 `.json` 을 끌어다 놓거나 [톤 JSON 불러오기] 버튼으로 엽니다. 폴더를 끌어다 놓으면 톤 데이터 폴더로 엽니다.
- **변환 대상 기종**은 이 순서로 정합니다: 연결된 장비의 기종 → JSON 첫 파일의 `device` → 두 기종 중 고르는 작은 창(취소하면 변환하지 않음). 명령줄(`cli.py convert`)은 `--device` > JSON 의 `device` > GE150PRO 순서입니다.
- 모델·이름 길이·노브 이름은 모두 **대상 기종** 기준으로 검사합니다. GE150 Pro 용 톤을 GE150 에 넣으면 GE150 에 없는 모델과 15~16자 이름이 오류로 표시됩니다.
- **여러 `.json` 을 한꺼번에 끌어다 놓거나 고르면 하나로 합칩니다**(AI 출력 길이 제한 때문에 01~25·26~50 처럼 나눠 받는 경우). 파일 사이에 슬롯이 겹치면 오류. 출력 폴더 이름은 첫 파일 이름 기준.
- 오류가 1개라도 있으면 `.mo` 를 만들지 않고 오류 목록 창(슬롯·모듈·노브·사유)을 보여 줍니다. 경고만 있으면 만들고 경고 목록을 보여 줍니다.
- `.mo` 파일 이름은 `01A Wonderwall CLN.mo`. 같은 이름의 폴더가 이미 있으면 시각을 붙여 새로 만듭니다(덮어쓰지 않음). 변환 폴더에는 `tonepack-meta.json`(곡·아티스트·역할, 대상 기종)도 함께 저장됩니다.
- 명령줄: `python cli.py convert <tonepack.json...> [--out <출력 폴더>] [--device GE150|GE150PRO]`
