"""Report a stale scheduled run on the personal server; manual runs never reset the clock."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

URL = "https://api.github.com/repos/huukuujuu/openpilot/actions/workflows/ev6-upstream-sync.yml/runs?event=schedule&per_page=1"
STATE = Path.home() / ".local/state/ev6-sync"


def notice(runs, now):
  if not runs:
    return "EV6 동기화 경고 — GitHub에 예약 실행 기록이 없습니다."
  last = datetime.fromisoformat(runs[0]["created_at"].replace("Z", "+00:00"))
  if now - last >= timedelta(hours=3):
    return f"EV6 동기화 경고 — 3시간 이상 예약 실행이 없습니다. 마지막 예약 실행 {last.astimezone().isoformat(timespec='minutes')}."
  return ""


def check():
  STATE.mkdir(parents=True, exist_ok=True)
  alert = STATE / "notice"
  try:
    request = Request(URL, headers={"User-Agent": "ev6-sync-watch", "Cache-Control": "no-cache"})
    with urlopen(request, timeout=20) as response:
      message = notice(json.load(response)["workflow_runs"], datetime.now(timezone.utc))
  except (OSError, ValueError, KeyError) as exc:
    message = f"EV6 동기화 조회 실패 — GitHub 예약 실행 상태를 확인하지 못했습니다 ({type(exc).__name__})."
  if message:
    temporary = alert.with_suffix(".tmp")
    temporary.write_text(message + "\n", encoding="utf-8")
    temporary.replace(alert)
  else:
    alert.unlink(missing_ok=True)


if __name__ == "__main__":
  check()
