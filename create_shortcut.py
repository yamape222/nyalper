"""
create_shortcut.py
デスクトップににゃるぱー秘書のショートカットを作成するスクリプト

使い方：
  python create_shortcut.py
"""

import sys
from pathlib import Path


def create_shortcut():
    try:
        import winshell
        from win32com.client import Dispatch
    except ImportError:
        # winshellがない場合はpipでインストール
        print("必要なライブラリをインストール中...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                              "winshell", "pywin32", "--quiet"])
        import winshell
        from win32com.client import Dispatch

    # パス設定
    base_dir   = Path(__file__).resolve().parent
    app_path   = base_dir / "nyalper_app.py"
    python_exe = Path(sys.executable)
    desktop    = Path(winshell.desktop())
    shortcut_path = desktop / "🐱 にゃるぱー秘書.lnk"

    # ショートカット作成
    shell = Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(str(shortcut_path))
    shortcut.Targetpath     = str(python_exe)
    shortcut.Arguments      = f'"{app_path}"'
    shortcut.WorkingDirectory = str(base_dir)
    shortcut.Description    = "にゃるぱー秘書 - 株式スクリーニングツール"
    shortcut.WindowStyle    = 1  # 通常ウィンドウ
    # 猫アイコン設定
    icon_path = base_dir / "nyalper_icon.ico"
    if icon_path.exists():
        shortcut.IconLocation = str(icon_path)
    shortcut.save()

    print(f"✅ ショートカットを作成しました！")
    print(f"   場所: {shortcut_path}")
    print(f"   ダブルクリックで起動できます🐱")


def create_bat_fallback():
    """winshellが使えない場合のバッチファイル方式"""
    base_dir = Path(__file__).resolve().parent
    app_path = base_dir / "nyalper_app.py"
    python_exe = Path(sys.executable)

    # デスクトップにバッチファイルを作成
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        desktop = Path.home() / "OneDrive" / "デスクトップ"
    if not desktop.exists():
        desktop = Path.home() / "OneDrive" / "Desktop"

    bat_path = desktop / "にゃるぱー秘書.bat"
    bat_content = f'''@echo off
chcp 65001 > nul
cd /d "{base_dir}"
"{python_exe}" "{app_path}"
'''
    bat_path.write_text(bat_content, encoding="utf-8")
    print(f"✅ 起動ファイルを作成しました！")
    print(f"   場所: {bat_path}")
    print(f"   ダブルクリックで起動できます🐱")
    return bat_path


if __name__ == "__main__":
    print("🐱 にゃるぱー秘書 ショートカット作成")
    print("=" * 40)

    try:
        create_shortcut()
    except Exception as e:
        print(f"ショートカット作成失敗: {e}")
        print("バッチファイルで代替作成します...")
        try:
            create_bat_fallback()
        except Exception as e2:
            print(f"❌ 作成失敗: {e2}")
            print("\n手動でショートカットを作成してください：")
            base_dir = Path(__file__).resolve().parent
            print(f"  対象: {sys.executable} {base_dir / 'nyalper_app.py'}")
