import os
import subprocess
from typing import Any

from openpilot.system.hardware import HARDWARE
from openpilot.selfdrive.carrot.web_upload import web_upload_settings

from ...services.dashcam_upload_report import (
  upload_message_lines as upload_message_lines,
  upload_share_text as upload_share_text,
)
from ...services.params import HAS_PARAMS, Params
from ...services.web_settings import read_web_settings


def param_text(params: Any, key: str, default: str = "unknown") -> str:
  try:
    if not params:
      return default
    value = params.get(key)
    if isinstance(value, bytes):
      value = value.decode("utf-8", errors="replace")
    value = str(value or "").strip()
    return value or default
  except Exception:
    return default


def repo_dir() -> str:
  return os.environ.get("CARROT_REPO_DIR", "/data/openpilot")


def git_text(args: list[str], default: str = "") -> str:
  try:
    result = subprocess.run(
      ["git", *args],
      cwd=repo_dir(),
      capture_output=True,
      text=True,
      timeout=4,
    )
    if result.returncode == 0:
      value = (result.stdout or "").strip()
      return value or default
  except Exception:
    pass
  return default


def device_serial(params: Any) -> str:
  for key in ("HardwareSerial", "DeviceSerial", "Serial", "CarrotSerial"):
    value = param_text(params, key, "")
    if value:
      return value
  for env_key in ("CARROT_DEVICE_SERIAL", "DEVICE_SERIAL", "SERIAL"):
    value = os.environ.get(env_key, "").strip()
    if value:
      return value
  try:
    getter = getattr(HARDWARE, "get_serial", None)
    if callable(getter):
      value = str(getter() or "").strip()
      if value:
        return value
  except Exception:
    pass
  return "unknown"


def upload_metadata(params: Any) -> dict[str, str]:
  return {
    "carName": param_text(params, "CarName", "none"),
    "dongleId": param_text(params, "DongleId", "unknown"),
    "serial": device_serial(params),
    "branch": git_text(["branch", "--show-current"], "unknown"),
    "commit": git_text(["rev-parse", "--short", "HEAD"], "unknown"),
    "commitDate": git_text(["show", "-s", "--date=format:%Y-%m-%d %H:%M:%S", "--format=%cd", "HEAD"], "unknown"),
  }


def current_upload_metadata() -> dict[str, str]:
  return upload_metadata(Params() if HAS_PARAMS else None)


def upload_target_settings() -> tuple[str, str, bool]:
  """Return the API URL, optional static token, and whether to include viewing links."""
  try:
    settings = read_web_settings()
  except Exception:
    settings = {}
  dashcam_url = settings.get("dashcam_upload_url")
  if dashcam_url:
    return dashcam_url, "", False
  base_url, token = web_upload_settings(settings)
  return base_url, token, True
