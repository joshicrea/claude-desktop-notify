"""Claude Code Stop / Notification イベントで Windows トースト通知を出す hook。

実行環境: Windows 10 / 11 ネイティブ（PowerShell 経由で .NET ToastNotificationManager を呼ぶ）

使い方: settings.json の hooks コマンドで引数を渡す
  Stop:         python3 ~/.claude/hooks/desktop_notify.py Stop
  Notification: python3 ~/.claude/hooks/desktop_notify.py Notification

入力: stdin に Claude Code から JSON が渡される
  - 通常: {"session_id": ..., "transcript_path": ..., "cwd": ..., ...}
    （Windowsパスのバックスラッシュがエスケープ崩れで届くため
     json.loads は失敗することが多い。cwd は regex でも抽出する）
  - Notification 拡張: {"title": str, "message": str, ...}

ログ: %TEMP%\claude_desktop_notify.log
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import xml.sax.saxutils

# Windows PowerShell の AppUserModelID（Windows標準で登録済み）
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

LOG_PATH = os.path.expandvars(r"%TEMP%\claude_desktop_notify.log")


def log_debug(message: str) -> None:
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(message + "\n")
    except Exception:
        pass


def build_toast_xml(title: str, message: str) -> str:
    safe_title = xml.sax.saxutils.escape(title, {'"': "&quot;", "'": "&apos;"})
    safe_message = xml.sax.saxutils.escape(message, {'"': "&quot;", "'": "&apos;"})
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<toast>"
        "<visual>"
        '<binding template="ToastGeneric">'
        f"<text>{safe_title}</text>"
        f"<text>{safe_message}</text>"
        "</binding>"
        "</visual>"
        '<audio src="ms-winsoundevent:Notification.Default" />'
        "</toast>"
    )


def show_toast(title: str, message: str) -> None:
    xml_body = build_toast_xml(title, message)
    xml_file = None
    ps_file = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".xml", delete=False, encoding="utf-8"
        ) as xf:
            xf.write(xml_body)
            xml_file = xf.name

        ps_content = f"""$ErrorActionPreference = 'Stop'
try {{
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null
    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xmlText = [System.IO.File]::ReadAllText(@'
{xml_file}
'@, [System.Text.Encoding]::UTF8)
    $xml.LoadXml($xmlText)
    $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier(@'
{APP_ID}
'@)
    $notifier.Show($toast)
    Write-Host "OK"
}} catch {{
    Write-Host "ERROR: $_"
    exit 1
}}
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ps1", delete=False, encoding="utf-8"
        ) as pf:
            pf.write(ps_content)
            ps_file = pf.name

        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-File", ps_file,
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
        log_debug(
            f"[show_toast] rc={result.returncode} "
            f"out={result.stdout.decode('utf-8', errors='replace').strip()}"
        )
    except Exception as e:
        log_debug(f"[show_toast] exception={e!r}")
    finally:
        for p in (xml_file, ps_file):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except Exception:
                    pass


def extract_cwd_from_raw(raw: str) -> str:
    """Claude Code payload はWindowsパスの \\ が未エスケープで来るため
    json.loads は失敗することが多い。raw文字列から正規表現で cwd を取り出す。
    """
    m = re.search(r'"cwd"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    if not m:
        return ""
    return m.group(1).replace("\\\\", "\\")


def extract_transcript_path_from_raw(raw: str) -> str:
    m = re.search(r'"transcript_path"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    if not m:
        return ""
    return m.group(1).replace("\\\\", "\\")


def get_recent_user_text(transcript_path: str, max_len: int = 60) -> str:
    """transcript jsonl の末尾から最後のユーザーメッセージの冒頭を返す。
    Claude Code の jsonl 構造: 各行が JSON。type='user' の message.content にユーザー発言。
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        with open(transcript_path, encoding="utf-8") as f:
            lines = f.readlines()
        for line in reversed(lines[-50:]):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("type") != "user":
                continue
            msg = rec.get("message", {})
            content = msg.get("content", "") if isinstance(msg, dict) else ""
            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        text = c.get("text", "")
                        break
            text = text.strip()
            # システムメッセージ（<system-reminder> 等）はスキップ
            if not text or text.startswith("<"):
                continue
            # 1行に整形して切り詰める
            text = " ".join(text.split())
            if len(text) > max_len:
                text = text[:max_len] + "…"
            return text
        return ""
    except Exception:
        return ""


def make_tab_label(cwd: str) -> str:
    """cwd から短いタブラベルを生成。末尾2階層を返す。
    例:
      'C:\\Users\\hayas\\OneDrive\\デスクトップ\\git' -> 'デスクトップ/git'
      'c:\\...\\git\\販売ツール\\AI動画クリエイター' -> '販売ツール/AI動画クリエイター'
    """
    if not cwd:
        return ""
    parts = cwd.replace("\\", "/").rstrip("/").split("/")
    # 末尾2階層
    if len(parts) >= 2 and parts[-2]:
        return f"{parts[-2]}/{parts[-1]}"
    return parts[-1] if parts else ""


def main() -> int:
    import datetime as _dt
    # 引数で Stop / Notification を識別（settings.jsonで引数を渡す）
    event_kind = sys.argv[1] if len(sys.argv) > 1 else "Stop"

    # ログ恒常化：いつどのイベントで hook が呼ばれたかを必ず残す
    log_debug(f"[main] CALLED event={event_kind} at {_dt.datetime.now().isoformat()}")

    # Windows の sys.stdin はデフォルト cp932 のため、日本語パスを含む
    # Claude Code payload を読むと文字化け（サロゲート混入）が起きる。
    # 必ず stdin.buffer から UTF-8 で読む。
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    except Exception:
        try:
            raw = sys.stdin.read()
        except Exception:
            log_debug("[main] stdin read failed")
            return 0
    log_debug(f"[main] stdin len={len(raw)}")

    if not raw.strip():
        show_toast("Claude Code", "テスト通知")
        return 0

    # JSON parse を試みる。失敗しても tab_label は raw から regex で取れる。
    payload = None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        log_debug("[main] JSON parse failed (Windowsパスの未エスケープ)")

    # 無限ループ防止（payload がある場合のみチェック）
    if payload and payload.get("stop_hook_active") is True:
        return 0

    # タブ識別ラベル（cwd の末尾2階層）
    cwd = (payload.get("cwd") if payload else None) or extract_cwd_from_raw(raw)
    tab_label = make_tab_label(cwd)

    # 直前のユーザー指示を取得（「何のタスクか」を示す手がかり）
    transcript_path = (payload.get("transcript_path") if payload else None) or extract_transcript_path_from_raw(raw)
    user_hint = get_recent_user_text(transcript_path)

    # Notification イベントで custom title/message が来る場合
    if payload:
        custom_title = payload.get("title")
        custom_message = payload.get("message")
        if custom_title or custom_message:
            title = custom_title or "Claude Code"
            if tab_label:
                title = f"{title} — {tab_label}"
            show_toast(title, custom_message or "Claude Code からの通知")
            return 0

    # イベント種別に応じた既定の文言
    if event_kind.lower() == "notification":
        title = "承認待ち"
        message = "Claude Code が承認を待っています"
    else:  # Stop
        title = "タスク完了"
        message = "Claude Code がタスクを完了しました"

    if tab_label:
        title = f"{title} — {tab_label}"

    show_toast(title, message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
