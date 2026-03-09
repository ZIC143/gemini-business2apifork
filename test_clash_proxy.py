#!/usr/bin/env python3
"""测试 Clash 代理功能"""
import os
import sys
import time
import yaml
import httpx

def test_proxy_control():
    """测试代理控制配置"""
    print("=" * 50)
    print("测试 1: 代理控制配置")
    print("=" * 50)

    from core import storage

    # 保存测试配置
    test_config = {
        "master_enabled": True,
        "auth_enabled": True,
        "chat_enabled": True,
        "port": 7890
    }

    result = storage.save_proxy_control_sync(test_config)
    print(f"✓ 保存配置: {result}")

    # 加载配置
    loaded = storage.load_proxy_control_sync()
    print(f"✓ 加载配置: {loaded}")

    if loaded and loaded.get("port") == 7890:
        print("✅ 代理控制配置测试通过")
        return True
    else:
        print("❌ 代理控制配置测试失败")
        return False

def test_clash_config():
    """测试 Clash 配置文件"""
    print("\n" + "=" * 50)
    print("测试 2: Clash 配置文件")
    print("=" * 50)

    config_path = "clash_config.yaml"

    if not os.path.exists(config_path):
        print("❌ 配置文件不存在")
        return False

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    print(f"✓ 端口: {cfg.get('mixed-port')}")
    print(f"✓ 代理数量: {len(cfg.get('proxies', []))}")
    print(f"✓ 代理组: {[g['name'] for g in cfg.get('proxy-groups', [])]}")

    if cfg.get("proxies"):
        print(f"✓ 第一个代理: {cfg['proxies'][0].get('name')}")

    print("✅ Clash 配置文件测试通过")
    return True

def test_proxy_connection(port=7890):
    """测试代理连接"""
    print("\n" + "=" * 50)
    print(f"测试 3: 代理连接 (端口 {port})")
    print("=" * 50)

    proxy_url = f"http://127.0.0.1:{port}"
    proxies = {"http://": proxy_url, "https://": proxy_url}

    try:
        with httpx.Client(proxies=proxies, timeout=10) as client:
            # 测试 Google 204
            resp = client.get("https://www.google.com/generate_204")
            if resp.status_code == 204:
                print(f"✅ 代理连接成功 (HTTP {resp.status_code})")
            else:
                print(f"⚠️ 代理响应异常 (HTTP {resp.status_code})")
                return False

            # 测试 IP
            ip_resp = client.get("https://api.ipify.org?format=json", timeout=5)
            if ip_resp.status_code == 200:
                proxy_ip = ip_resp.json().get("ip", "未知")
                print(f"✅ 当前代理 IP: {proxy_ip}")
            else:
                print("⚠️ 无法获取代理 IP")

            return True
    except httpx.ConnectError as e:
        print(f"❌ 无法连接到代理: {e}")
        print(f"⚠️ 请确认 Clash 已启动并监听端口 {port}")
        return False
    except Exception as e:
        print(f"❌ 代理测试异常: {e}")
        return False

def test_node_manager():
    """测试节点管理"""
    print("\n" + "=" * 50)
    print("测试 4: 节点管理")
    print("=" * 50)

    from core import node_manager

    nodes = node_manager.load_all_nodes()
    print(f"✓ 节点总数: {len(nodes)}")

    enabled = [n for n in nodes if n.get("enabled", True)]
    print(f"✓ 启用节点: {len(enabled)}")

    if enabled:
        for node in enabled[:3]:
            print(f"  - {node['name']}: {node.get('url', 'N/A')}")
            if node.get("proxy_config"):
                print(f"    配置: {node['proxy_config'].get('type', 'N/A')}")

    print("✅ 节点管理测试通过")
    return True

if __name__ == "__main__":
    print("🚀 开始测试 Clash 代理功能\n")

    results = []
    results.append(("代理控制配置", test_proxy_control()))
    results.append(("Clash 配置文件", test_clash_config()))
    results.append(("节点管理", test_node_manager()))
    results.append(("代理连接", test_proxy_connection(7890)))

    print("\n" + "=" * 50)
    print("测试结果汇总")
    print("=" * 50)

    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{name}: {status}")

    all_passed = all(r[1] for r in results)
    if all_passed:
        print("\n🎉 所有测试通过！")
        sys.exit(0)
    else:
        print("\n⚠️ 部分测试失败，请检查配置")
        sys.exit(1)
