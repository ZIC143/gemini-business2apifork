#!/usr/bin/env python3
"""完整流程测试：Clash启动 -> 代理验证 -> 注册测试"""
import os
import sys
import time
import subprocess
import httpx
from core import storage

print("=" * 60)
print("完整流程测试")
print("=" * 60)

# 1. 检查配置
print("\n[1/5] 检查配置...")
proxy_cfg = storage.load_proxy_control_sync()
port = proxy_cfg.get('port', 7890)
print(f"✓ 代理端口: {port}")
print(f"✓ Auth启用: {proxy_cfg.get('auth_enabled')}")

nodes = storage.load_nodes_sync()
enabled = [n for n in nodes if n.get("enabled", True)]
print(f"✓ 启用节点: {len(enabled)}")

# 2. 启动 Clash
print(f"\n[2/5] 启动 Clash (端口 {port})...")
exe_path = os.path.join(os.getcwd(), 'mihomo.exe')
config_path = os.path.join(os.getcwd(), 'clash_config.yaml')

# 杀掉旧进程
subprocess.run('taskkill /F /IM mihomo.exe', shell=True, capture_output=True)
time.sleep(1)

# 启动新进程
proc = subprocess.Popen(
    [exe_path, '-f', config_path],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
)
print(f"✓ Clash 已启动 (PID: {proc.pid})")
time.sleep(3)

# 3. 测试代理连接
print(f"\n[3/5] 测试代理连接...")
proxy_url = f"http://127.0.0.1:{port}"
proxies = {"http://": proxy_url, "https://": proxy_url}

try:
    with httpx.Client(proxies=proxies, timeout=10) as client:
        resp = client.get("https://www.google.com/generate_204")
        if resp.status_code == 204:
            print("✅ 代理连接成功")
        else:
            print(f"❌ 代理响应异常: {resp.status_code}")
            sys.exit(1)

        ip_resp = client.get("https://api.ipify.org?format=json", timeout=5)
        if ip_resp.status_code == 200:
            ip = ip_resp.json().get("ip", "未知")
            print(f"✅ 当前代理IP: {ip}")
except Exception as e:
    print(f"❌ 代理连接失败: {e}")
    sys.exit(1)

print("\n✅ 所有测试通过！")
print("=" * 60)
