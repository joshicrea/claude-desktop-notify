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

# 直前通知時刻の記録ファイル（クールダウン判定用）
LAST_NOTIFY_PATH = os.path.expandvars(r"%TEMP%\claude_desktop_notify_last.txt")

# クールダウン秒数：これ未満の間隔の Stop 通知は抑制（連続ターン中の通知連発を防ぐ）
NOTIFY_COOLDOWN_SEC = 60


def log_debug(message: str) -> None:
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(message + "\n")
    except Exception:
        pass


def build_toast_xml(title: str, message: str, launch_uri: str = "") -> str:
    safe_title = xml.sax.saxutils.escape(title, {'"': "&quot;", "'": "&apos;"})
    safe_message = xml.sax.saxutils.escape(message, {'"': "&quot;", "'": "&apos;"})
    if launch_uri:
        safe_uri = xml.sax.saxutils.escape(launch_uri, {'"': "&quot;", "'": "&apos;"})
        toast_attrs = f' launch="{safe_uri}" activationType="protocol"'
    else:
        toast_attrs = ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f"<toast{toast_attrs}>"
        "<visual>"
        '<binding template="ToastGeneric">'
        f"<text>{safe_title}</text>"
        f"<text>{safe_message}</text>"
        "</binding>"
        "</visual>"
        '<audio src="ms-winsoundevent:Notification.Default" />'
        "</toast>"
    )


def show_toast(title: str, message: str, launch_uri: str = "") -> None:
    xml_body = build_toast_xml(title, message, launch_uri)
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


def detect_editor_protocol() -> str:
    """実行中のエディタを検出してプロトコル名を返す。
    Cursor.exe / Code.exe の実体パスを確認して優先順位で判定。
    見つからなければ空文字（クリックアクション無効）を返す。
    """
    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates = [
        (os.path.join(local, "Programs", "cursor", "Cursor.exe"), "cursor"),
        (os.path.join(local, "Programs", "Microsoft VS Code", "Code.exe"), "vscode"),
        (os.path.join(program_files, "Microsoft VS Code", "Code.exe"), "vscode"),
        (os.path.join(local, "Programs", "Microsoft VS Code Insiders", "Code - Insiders.exe"), "vscode-insiders"),
    ]
    for path, proto in candidates:
        if os.path.exists(path):
            return proto
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

    # イベント種別に応じた固定文言（payload の title/message は信用しない。
    # ブログ記事本文等が混入する事故を防ぐため）
    if event_kind.lower() == "notification":
        title = "承認待ち"
        message = "Claude Code が承認を待っています"
    else:  # Stop
        title = "タスク完了"
        message = "Claude Code がタスクを完了しました"

    if tab_label:
        title = f"{title} — {tab_label}"

    # クールダウン判定：直前通知から短時間の場合はスキップ
    # （Notification イベント＝承認待ちは抑制せず必ず通知。Stop だけ抑制）
    import time
    now = time.time()
    if event_kind.lower() != "notification":
        try:
            if os.path.exists(LAST_NOTIFY_PATH):
                with open(LAST_NOTIFY_PATH, encoding="utf-8") as f:
                    last_ts = float(f.read().strip() or "0")
                if now - last_ts < NOTIFY_COOLDOWN_SEC:
                    log_debug(f"[main] cooldown skip (last={last_ts}, elapsed={now - last_ts:.1f}s)")
                    return 0
        except Exception:
            pass

    # クリック時に該当エディタウィンドウを前面化する URI を構築
    # 実体エディタを検出して対応プロトコル（cursor://, vscode://, vscode-insiders://）を選ぶ
    launch_uri = ""
    proto = detect_editor_protocol()
    if cwd and proto:
        import urllib.parse
        normalized = cwd.replace("\\", "/")
        encoded = urllib.parse.quote(normalized, safe="/:")
        launch_uri = f"{proto}://file/{encoded}"

    show_toast(title, message, launch_uri=launch_uri)

    # 通知発行時刻を記録（次回クールダウン判定用）
    try:
        with open(LAST_NOTIFY_PATH, "w", encoding="utf-8") as f:
            f.write(str(now))
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
