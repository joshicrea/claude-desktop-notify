"""Claude Code transcript jsonl を監視し、ツール承認待ちを検知してトースト通知を発行する常駐スクリプト。

公式の Notification hook では VSCode 拡張版の「編集内容確認ダイアログ」を検知できないため、
非公式アプローチとして transcript jsonl の tool_use → tool_result の到達時間を監視する。

判定:
  - tool_use（Edit/Write/Bash 等）が記録された
  - そこから PENDING_THRESHOLD_SEC 秒経過しても同じ tool_use_id の tool_result が来ない
  → 承認ダイアログでユーザー応答待ち中と判定 → 通知発行

実行方法:
  Windows タスクスケジューラのログオン時起動か、手動で常駐起動。
  pythonw.exe で起動するとコンソール非表示。

ログ: %TEMP%\\claude_pending_monitor.log
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import tempfile
import xml.sax.saxutils
from pathlib import Path

# 監視対象ディレクトリ（Claude Code のセッション jsonl 置き場）
PROJECTS_DIR = Path.home() / ".claude" / "projects"

# 承認待ちと判定する閾値（秒）
# 自動承認される短い処理は数秒で tool_result が返るため、8秒を超えたら「承認ダイアログで止まっている」と判定
PENDING_THRESHOLD_SEC = 8.0

# 同じ tool_use を二重通知しないためのクールダウン
NOTIFY_COOLDOWN_SEC = 60.0

# 通知対象ツール（許可リスト方式：自動承認されるはずでもダイアログが出るのは編集系・実行系）
TARGET_TOOLS = {"Edit", "Write", "MultiEdit", "Bash", "NotebookEdit"}

# Windows PowerShell の AppUserModelID
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

LOG_PATH = os.path.expandvars(r"%TEMP%\claude_pending_monitor.log")


def log(msg: str) -> None:
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def make_tab_label(cwd: str) -> str:
    if not cwd:
        return ""
    parts = cwd.replace("\\", "/").rstrip("/").split("/")
    if len(parts) >= 2 and parts[-2]:
        return f"{parts[-2]}/{parts[-1]}"
    return parts[-1] if parts else ""


def build_toast_xml(title: str, message: str) -> str:
    st = xml.sax.saxutils.escape(title, {'"': "&quot;", "'": "&apos;"})
    sm = xml.sax.saxutils.escape(message, {'"': "&quot;", "'": "&apos;"})
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<toast><visual><binding template=\"ToastGeneric\">"
        f"<text>{st}</text><text>{sm}</text>"
        "</binding></visual>"
        '<audio src="ms-winsoundevent:Notification.Default" />'
        "</toast>"
    )


def show_toast(title: str, message: str) -> None:
    xml_body = build_toast_xml(title, message)
    xml_file = None
    ps_file = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False, encoding="utf-8") as xf:
            xf.write(xml_body)
            xml_file = xf.name
        ps_content = f"""$ErrorActionPreference='Stop'
try {{
  [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
  [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null
  $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
  $xml.LoadXml([System.IO.File]::ReadAllText(@'
{xml_file}
'@, [System.Text.Encoding]::UTF8))
  $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier(@'
{APP_ID}
'@).Show($toast)
}} catch {{ exit 1 }}
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ps1", delete=False, encoding="utf-8") as pf:
            pf.write(ps_content)
            ps_file = pf.name
        CREATE_NO_WINDOW = 0x08000000
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", ps_file],
            capture_output=True, timeout=8, check=False, creationflags=CREATE_NO_WINDOW,
        )
    except Exception as e:
        log(f"show_toast exception: {e!r}")
    finally:
        for p in (xml_file, ps_file):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except Exception:
                    pass


def parse_jsonl_line(line: str) -> dict | None:
    try:
        return json.loads(line)
    except Exception:
        return None


def find_tool_use_and_results(jsonl_path: Path, after_offset: int) -> tuple[list[dict], list[str], int]:
    """jsonl を after_offset バイトから読み、tool_use リストと tool_result_id リストを返す。
    戻り値: (tool_use エントリ, tool_result の tool_use_id, 新しい offset)
    """
    tool_uses: list[dict] = []
    result_ids: list[str] = []
    try:
        size = jsonl_path.stat().st_size
        if size < after_offset:
            after_offset = 0  # ファイル切り詰めなどでオフセット異常
        with open(jsonl_path, "rb") as f:
            f.seek(after_offset)
            raw = f.read()
            new_offset = after_offset + len(raw)
        text = raw.decode("utf-8", errors="replace")
        for line in text.split("\n"):
            if not line.strip():
                continue
            rec = parse_jsonl_line(line)
            if not rec:
                continue
            # tool_use の抽出
            msg = rec.get("message") if isinstance(rec.get("message"), dict) else None
            if rec.get("type") == "assistant" and msg:
                content = msg.get("content")
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_use":
                            tool_uses.append({
                                "id": c.get("id"),
                                "name": c.get("name"),
                                "ts": time.time(),
                                "cwd": rec.get("cwd", ""),
                            })
            # tool_result の抽出
            if rec.get("type") == "user" and msg:
                content = msg.get("content")
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_result":
                            tid = c.get("tool_use_id")
                            if tid:
                                result_ids.append(tid)
        return tool_uses, result_ids, new_offset
    except FileNotFoundError:
        return [], [], after_offset
    except Exception as e:
        log(f"parse error {jsonl_path}: {e!r}")
        return [], [], after_offset


def main() -> int:
    log("=== pending_approval_monitor 起動 ===")

    # state: {jsonl_path: offset}
    file_offsets: dict[Path, int] = {}
    # 承認待ち候補: {tool_use_id: {"name", "ts", "cwd", "notified": bool}}
    pending: dict[str, dict] = {}
    # 通知済みクールダウン: {tool_use_id: notify_ts}
    notified_at: dict[str, float] = {}

    while True:
        try:
            # projects ディレクトリ配下の jsonl を全部取得
            jsonl_files = []
            if PROJECTS_DIR.exists():
                for sub in PROJECTS_DIR.iterdir():
                    if sub.is_dir():
                        jsonl_files.extend(sub.glob("*.jsonl"))

            # 新規ファイルは末尾から監視開始（起動前の履歴は無視）
            for p in jsonl_files:
                if p not in file_offsets:
                    try:
                        file_offsets[p] = p.stat().st_size
                    except Exception:
                        file_offsets[p] = 0

            # 各ファイルの差分を読む
            for p in jsonl_files:
                tu_list, res_ids, new_offset = find_tool_use_and_results(p, file_offsets[p])
                file_offsets[p] = new_offset
                for tu in tu_list:
                    if tu.get("name") in TARGET_TOOLS and tu.get("id"):
                        pending[tu["id"]] = {
                            "name": tu["name"],
                            "ts": tu["ts"],
                            "cwd": tu.get("cwd", ""),
                            "notified": False,
                        }
                for tid in res_ids:
                    if tid in pending:
                        del pending[tid]

            # 閾値経過した pending を通知
            now = time.time()
            for tid, info in list(pending.items()):
                if info["notified"]:
                    continue
                if now - info["ts"] >= PENDING_THRESHOLD_SEC:
                    last = notified_at.get(tid, 0)
                    if now - last < NOTIFY_COOLDOWN_SEC:
                        continue
                    tab = make_tab_label(info.get("cwd", ""))
                    title = f"承認待ち — {tab}" if tab else "承認待ち"
                    message = f"{info['name']} ツールの承認を待っています"
                    show_toast(title, message)
                    log(f"NOTIFY tool={info['name']} cwd={info.get('cwd','')} id={tid}")
                    info["notified"] = True
                    notified_at[tid] = now

            # クールダウンの古いエントリを掃除
            cutoff = now - NOTIFY_COOLDOWN_SEC * 2
            for tid in list(notified_at.keys()):
                if notified_at[tid] < cutoff:
                    del notified_at[tid]

            time.sleep(1.0)
        except KeyboardInterrupt:
            log("=== 停止 ===")
            return 0
        except Exception as e:
            log(f"main loop exception: {e!r}")
            time.sleep(3.0)


if __name__ == "__main__":
    sys.exit(main())
