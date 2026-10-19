import os
import sys
import time
import socket
import subprocess
import argparse
import json
from pathlib import Path
import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "water-group"))

NAPCAT_DIR = ROOT_DIR / "tools" / "napcat-shell"
FWD_SCRIPT = ROOT_DIR / "water-group" / "fwd.py"
CONFIG_FILE = ROOT_DIR / "water-group" / "config.json"
CONFIG_EXAMPLE = ROOT_DIR / "water-group" / "config.example.json"
STATUS_FILE = ROOT_DIR / "water-group" / "status.json"

def is_port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.8) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False

def ensure_config():
    if not CONFIG_FILE.exists():
        if CONFIG_EXAMPLE.exists():
            import shutil
            shutil.copy2(CONFIG_EXAMPLE, CONFIG_FILE)
            print(f"[Init] 已从 config.example.json 创建初始配置文件: {CONFIG_FILE}")
        else:
            print(f"[Warn] 未找到配置文件模板: {CONFIG_EXAMPLE}")

LOGS_DIR = ROOT_DIR / "water-group" / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

def get_python_exe():
    venv_py = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable

def start_process_detached(cmd, cwd=None, log_name=None):
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    out_file = subprocess.DEVNULL
    err_file = subprocess.DEVNULL
    if log_name:
        log_path = LOGS_DIR / log_name
        out_file = open(log_path, "a", encoding="utf-8")
        err_file = out_file
    return subprocess.Popen(
        cmd,
        cwd=cwd or str(ROOT_DIR),
        stdout=out_file,
        stderr=err_file,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags
    )

def ensure_napcat():
    if is_port_open(43000) and is_port_open(43001):
        return True, "在线"
    
    bat_path = NAPCAT_DIR / "launcher-user.bat"
    if not bat_path.exists():
        return False, f"未找到启动脚本: {bat_path}"
    
    print("[启动] 正在后台启动 NapCat (OneBot v11)...")
    start_process_detached(["cmd.exe", "/c", "launcher-user.bat"], cwd=str(NAPCAT_DIR), log_name="napcat.log")
    
    for _ in range(12):
        time.sleep(1)
        if is_port_open(43000) and is_port_open(43001):
            return True, "启动成功并已连接"
    return False, "启动超时，请检查 NapCat 日志或是否需要扫码 (WebUI: http://127.0.0.1:46099)"

def ensure_tulpa():
    if is_port_open(48777) and is_port_open(47860):
        return True, "在线"
    
    print("[启动] 正在后台启动 Tulpa 主服务 (Web: 47860, MCP: 48777)...")
    start_process_detached([get_python_exe(), "app.py"], cwd=str(ROOT_DIR), log_name="tulpa.log")
    
    for _ in range(12):
        time.sleep(1)
        if is_port_open(48777) and is_port_open(47860):
            return True, "启动成功"
    return False, "启动超时，请查看 Tulpa 运行日志 (water-group/logs/tulpa.log)"

def ensure_forwarder():
    if is_port_open(18777):
        return True, "在线"
    
    if not FWD_SCRIPT.exists():
        return False, f"未找到转发脚本: {FWD_SCRIPT}"
    
    print("[启动] 正在后台启动端口转发 (18777 -> 48777)...")
    start_process_detached([get_python_exe(), str(FWD_SCRIPT)], cwd=str(ROOT_DIR), log_name="fwd.log")
    
    for _ in range(8):
        time.sleep(0.5)
        if is_port_open(18777):
            return True, "启动成功"
    return False, "端口 18777 监听失败 (查看 water-group/logs/fwd.log)"

def run_sync():
    try:
        from sync_groups import inspect_and_sync
        return inspect_and_sync()
    except Exception as e:
        print(f"[Warn] 同步群聊状态失败: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="Water-Group 一键环境就绪与后台启动器")
    parser.add_argument("--group", "-g", help="指定目标 QQ 群号")
    parser.add_argument("--status-only", action="store_true", help="仅检查服务状态，不自动拉起")
    args = parser.parse_args()

    print("========================================================================")
    print("           [WATER-GROUP] 一键后台服务自检与自动启动")
    print("========================================================================")

    ensure_config()

    if args.status_only:
        napcat_ok = is_port_open(43000)
        tulpa_ok = is_port_open(48777)
        fwd_ok = is_port_open(18777)
    else:
        napcat_ok, napcat_msg = ensure_napcat()
        tulpa_ok, tulpa_msg = ensure_tulpa()
        fwd_ok, fwd_msg = ensure_forwarder()

    print(f"1. NapCat OneBot  : {'[OK] 在线' if napcat_ok else '[FAIL] 未运行'} (HTTP: 43000, WS: 43001, WebUI: 46099)")
    print(f"2. Tulpa Backend  : {'[OK] 在线' if tulpa_ok else '[FAIL] 未运行'} (Web: 47860, MCP: 48777)")
    print(f"3. Port Forwarder : {'[OK] 在线' if fwd_ok else '[FAIL] 未运行'} (18777 -> 48777)")

    if not (napcat_ok and tulpa_ok):
        print("\n[ERROR] 核心后台服务未完全就绪，请检查上述报错。")
        sys.exit(1)

    print("\n4. 正在同步群列表与角色卡...")
    sync_res = run_sync()

    ready_groups = []
    if STATUS_FILE.exists():
        try:
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                sdata = json.load(f)
                ready_groups = sdata.get("ready_groups", [])
        except Exception:
            pass

    target_sess = None
    if args.group:
        for g in ready_groups:
            if str(g.get("group_id")) == str(args.group):
                target_sess = g
                break
    elif ready_groups:
        target_sess = ready_groups[0]

    print("------------------------------------------------------------------------")
    if target_sess:
        gid = target_sess.get("group_id")
        gname = target_sess.get("group_name")
        sid = target_sess.get("session_id")
        persona = target_sess.get("persona")
        mode = target_sess.get("trigger_mode", "mention_only")
        mention_only_bool = (mode == "mention_only")

        print(f"✓ 目标群聊      : {gid} ({gname})")
        print(f"✓ 生效 Session : {sid}")
        print(f"✓ 当前装载人设 : {persona}")
        print(f"✓ 触发唤醒模式 : {mode} ({'仅被@或点名唤醒，大群防烧 Token' if mention_only_bool else '自然插话'})")
        print("------------------------------------------------------------------------")
        print("【Agent 极速启动行动指南 - 严禁进行任何多余探索，直接调用 MCP】：")
        print(f"  MCP Server : \"tulpa\"")
        print(f"  MCP Tool   : \"wait_chat_messages\"")
        print(f"  Arguments  : {{")
        print(f"    \"session_id\": \"{sid}\",")
        print(f"    \"timeout_seconds\": 150,")
        print(f"    \"mention_only\": {str(mention_only_bool).lower()}")
        print(f"  }}")
        print("------------------------------------------------------------------------")
        
        output_payload = {
            "status": "ready",
            "target_group": gid,
            "session_id": sid,
            "persona": persona,
            "trigger_mode": mode,
            "next_call": {
                "server": "tulpa",
                "tool": "wait_chat_messages",
                "arguments": {
                    "session_id": sid,
                    "timeout_seconds": 150,
                    "mention_only": mention_only_bool
                }
            }
        }
        print(f"\n<!-- BOOTSTRAP_RESULT: {json.dumps(output_payload, ensure_ascii=False)} -->")
    else:
        print("[Warn] 暂无就绪会话。请检查 config.json 中的 groups 配置与 QQ 加入的群。")
        sys.exit(1)

    print("========================================================================")

if __name__ == "__main__":
    main()
