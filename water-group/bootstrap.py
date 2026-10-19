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

def check_tulpa_mcp_health(port: int = 48777, token: str = None, timeout: float = 1.5) -> tuple[bool, str]:
    """Probes the Tulpa MCP HTTP endpoint with a JSON-RPC POST request.
    
    Verifies that the ASGI application on port 48777 is not only listening on TCP
    but is actively processing JSON-RPC MCP requests (returns 200 on authorized ping
    or 401 on protocol endpoint verification).
    """
    url = f"http://127.0.0.1:{port}/mcp"
    headers = {
        "Host": f"127.0.0.1:{port}",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {
        "jsonrpc": "2.0",
        "id": "healthcheck",
        "method": "ping",
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=timeout)
        # 200 means full JSON-RPC response; 401 means MCP endpoint is active and guarded
        if resp.status_code in (200, 401):
            return True, f"HTTP POST 握手成功 (状态码: {resp.status_code})"
        return False, f"HTTP 返回异常状态码: {resp.status_code}"
    except Exception as e:
        return False, f"HTTP POST 握手失败: {e}"

def wait_tulpa_mcp_ready(port: int = 48777, token: str = None, timeout_seconds: float = 15.0) -> tuple[bool, str]:
    """Wait barrier ensuring Tulpa MCP HTTP POST JSON-RPC endpoint is fully ready.
    
    Prevents race conditions where an Agent invokes MCP right after TCP socket opens
    but before ASGI middleware finishes initialization, causing one-way client deadlock.
    """
    start_time = time.time()
    last_err = ""
    while time.time() - start_time < timeout_seconds:
        ok, msg = check_tulpa_mcp_health(port, token=token)
        if ok:
            return True, msg
        last_err = msg
        time.sleep(0.5)
    return False, f"Tulpa MCP 就绪等待超时 ({timeout_seconds}s): {last_err}"

def ensure_tulpa():
    if is_port_open(48777) and is_port_open(47860):
        ready, msg = check_tulpa_mcp_health(48777)
        if ready:
            return True, f"在线且 MCP 握手就绪 ({msg})"
    
    print("[启动] 正在后台启动 Tulpa 主服务 (Web: 47860, MCP: 48777)...")
    start_process_detached([get_python_exe(), "app.py"], cwd=str(ROOT_DIR), log_name="tulpa.log")
    
    ports_ok = False
    for _ in range(12):
        time.sleep(1)
        if is_port_open(48777) and is_port_open(47860):
            ports_ok = True
            break

    if not ports_ok:
        return False, "端口监听超时，请查看 Tulpa 运行日志 (water-group/logs/tulpa.log)"

    ready, msg = wait_tulpa_mcp_ready(48777, timeout_seconds=12.0)
    if ready:
        return True, f"启动成功且 MCP 握手就绪 ({msg})"
    return False, f"Tulpa 启动就绪屏障未通过: {msg}"

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
        tulpa_port_ok = is_port_open(48777) and is_port_open(47860)
        tulpa_ready, tulpa_msg = check_tulpa_mcp_health(48777) if tulpa_port_ok else (False, "端口未开放")
        tulpa_ok = tulpa_port_ok and tulpa_ready
        fwd_ok = is_port_open(18777)
    else:
        napcat_ok, napcat_msg = ensure_napcat()
        tulpa_ok, tulpa_msg = ensure_tulpa()
        fwd_ok, fwd_msg = ensure_forwarder()

    print(f"1. NapCat OneBot  : {'[OK] 在线' if napcat_ok else '[FAIL] 未运行'} (HTTP: 43000, WS: 43001, WebUI: 46099)")
    print(f"2. Tulpa Backend  : {'[OK] ' + tulpa_msg if tulpa_ok else '[FAIL] 未就绪 (' + tulpa_msg + ')'} (Web: 47860, MCP: 48777)")
    print(f"3. Port Forwarder : {'[OK] 在线' if fwd_ok else '[FAIL] 未运行'} (18777 -> 48777)")

    if not (napcat_ok and tulpa_ok):
        print("\n[ERROR] 核心后台服务未完全就绪，请检查上述报错。")
        sys.exit(1)

    print("\n4. 正在同步群列表与角色卡...")
    sync_res = run_sync()

    print("\n5. 正在装载群友动态小本本记忆库...")
    try:
        from memory_engine import get_memory_engine
        mem_engine = get_memory_engine()
        mem_engine.seed_from_persona()
        mem_count = mem_engine.get_member_count()
        print(f"✓ 群友动态记忆库就绪 : 已建立 {mem_count} 位核心群友档案 (时月/孩董等)，支持小本本自动记录与检索")
    except Exception as e:
        print(f"[Warn] 加载群友记忆库异常: {e}")

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
        persona_name = target_sess.get("persona")
        persona_preset = target_sess.get("persona_preset") or "yu_bao"
        mode = target_sess.get("trigger_mode", "mention_only")
        reply_addressing = target_sess.get("reply_addressing", "quote")
        mention_only_bool = (mode == "mention_only")

        # 查找对应人设卡文件
        persona_file = None
        for cand in [
            ROOT_DIR / "water-group" / "personas" / f"{persona_preset}.md",
            ROOT_DIR / "water-group" / "personas" / f"{persona_name}.md",
            ROOT_DIR / "water-group" / "personas" / "yu_bao.md",
        ]:
            if cand.exists():
                persona_file = cand
                break

        persona_content = ""
        if persona_file and persona_file.exists():
            try:
                persona_content = persona_file.read_text(encoding="utf-8")
            except Exception:
                pass

        print(f"✓ 目标群聊      : {gid} ({gname})")
        print(f"✓ 生效 Session : {sid}")
        print(f"✓ 当前装载人设 : {persona_name} ({persona_preset})")
        if persona_file:
            print(f"✓ 人设卡路径   : {persona_file.relative_to(ROOT_DIR)}")
        print(f"✓ 触发唤醒模式 : {mode} ({'仅被@或点名唤醒，大群防烧 Token' if mention_only_bool else '自然插话'})")
        print(f"✓ 回复寻址模式 : {reply_addressing} ({'首个气泡带 quote: true 开启 QQ 原生引用回复，精准定位原话' if reply_addressing == 'quote' else '普通回复'})")
        member_mappings = target_sess.get("member_mappings") or {}
        if member_mappings:
            annotated_summary = ", ".join(f"{v.get('name')}({k})" for k, v in member_mappings.items())
            print(f"✓ 核心群友标注 : {annotated_summary} (号码与名片已绑定，发言自动精准识别)")
        print("------------------------------------------------------------------------")
        if persona_content:
            print("【已加载人设卡核心内容 - 主脑必须严格以此设定进行思考与发言】：")
            print(persona_content.strip())
            print("------------------------------------------------------------------------")
        print("【Agent 极速启动行动指南 - 严禁进行任何多余探索，直接调用 MCP】：")
        print("  1. 必须牢记上述人设卡中的所有群友档案（如孩董喜欢🐻男/大胸猛男、时月的大网黄反差梗）及说话风格，严禁凭空瞎猜胡编事实！")
        print("  2. 发起长轮询 wait_chat_messages 前向用户输出一句就绪提示")
        print("  3. 调用 MCP 工具 wait_chat_messages 挂起监听：")
        print(f"     MCP Server : \"tulpa\"")
        print(f"     MCP Tool   : \"wait_chat_messages\"")
        print(f"     Arguments  : {{")
        print(f"       \"session_id\": \"{sid}\",")
        print(f"       \"timeout_seconds\": 150,")
        print(f"       \"mention_only\": {str(mention_only_bool).lower()}")
        print(f"     }}")
        print("  4. 动态小本本记忆已挂载：发言群友的精简画像会自动出现在 chat_guidance.member_dossiers 中；群友说“记在小本本上”会自动入库；随时可调用 query_member_dossier 查询群友档案与专属八卦；回复时多结合对方外号与历史黑料调侃！")
        print("  5. QQ 原生引用回复：回复具体群友时，send_chat_reply 的首个文字气泡必须带上 \"quote\": true，自动开启 QQ 原生引用回复卡片精准定位原话！")
        print("  6. 群成员精准标注：核心群友号码与名片已强绑定，上下文画像已透传 QQ/名片（如【群友：criss (QQ: 2057796208 / 名片: 时之锁-肠宝盒)】），务必结合其专属外号与核心狠料精准怼人！群里发“@🐟包 标注 <QQ/@> 是 <人名>”可秒级入库新群友！")
        print("------------------------------------------------------------------------")
        
        output_payload = {
            "status": "ready",
            "target_group": gid,
            "session_id": sid,
            "persona": persona_name,
            "persona_preset": persona_preset,
            "persona_file": str(persona_file.relative_to(ROOT_DIR)) if persona_file else None,
            "trigger_mode": mode,
            "reply_addressing": reply_addressing,
            "member_mappings": member_mappings,
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
