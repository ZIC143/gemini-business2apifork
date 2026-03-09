"""Clash Meta (mihomo.exe) 代理管理器"""
import os
import socket
import subprocess
import sys
import time
import urllib.parse
from typing import Dict, List, Optional

import requests
import yaml


class ClashManager:
    """管理 mihomo.exe 子进程和 Clash API"""

    def __init__(
        self,
        mihomo_path: str = "mihomo.exe",
        config_path: str = "clash_config.yaml",
        mixed_port: int = 17700,
        api_port: int = 19090,
        log_callback=None,
    ):
        self.mihomo_path = mihomo_path
        self.config_path = config_path
        self.mixed_port = self._find_available_port(mixed_port)
        self.api_port = self._find_available_port(api_port)
        self.api_url = f"http://127.0.0.1:{self.api_port}"
        self.process: Optional[subprocess.Popen] = None
        self.log_callback = log_callback
        self.runtime_config_path = config_path.replace(".yaml", "_runtime.yaml")

        if self.mixed_port != mixed_port:
            self._log("warning", f"端口 {mixed_port} 被占用，切换到 {self.mixed_port}")
        if self.api_port != api_port:
            self._log("warning", f"API端口 {api_port} 被占用，切换到 {self.api_port}")

    def start(self) -> bool:
        """启动 mihomo.exe 子进程"""
        if self.process:
            return True

        if not os.path.exists(self.mihomo_path):
            return False

        if not os.path.exists(self.config_path):
            return False

        # 准备运行时配置
        if not self._prepare_runtime_config():
            return False

        # 启动子进程
        config_dir = os.path.dirname(self.runtime_config_path) or "."
        cmd = [self.mihomo_path, "-d", config_dir, "-f", self.runtime_config_path]
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            self._log("info", f"Clash 已启动 (mixed-port={self.mixed_port})")
        except Exception:
            return False

        # 等待 API 就绪
        for _ in range(10):
            try:
                requests.get(self.api_url, timeout=1)
                return True
            except Exception:
                time.sleep(1)

        self.stop()
        return False

    def stop(self) -> None:
        """停止 mihomo.exe 子进程"""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None

    def is_running(self) -> bool:
        """检查 Clash 是否正在运行"""
        return self.process is not None and self.process.poll() is None

    def reload_config(self) -> bool:
        """热重载配置"""
        try:
            if not self._prepare_runtime_config():
                return False
            url = f"{self.api_url}/configs"
            res = requests.put(url, json={"path": self.runtime_config_path}, timeout=5)
            return res.ok
        except Exception:
            return False

    def get_proxies(self) -> Dict:
        """获取所有代理节点"""
        try:
            url = f"{self.api_url}/proxies"
            res = requests.get(url, timeout=5)
            return res.json().get("proxies", {})
        except Exception:
            return {}

    def test_latency(self, proxy_name: str, timeout: int = 5000) -> int:
        """测试节点延迟 (ms)，失败返回 -1"""
        try:
            encoded = urllib.parse.quote(proxy_name)
            url = f"{self.api_url}/proxies/{encoded}/delay?timeout={timeout}&url=http://www.gstatic.com/generate_204"
            res = requests.get(url, timeout=6)
            if res.status_code == 200:
                return res.json().get("delay", -1)
            return -1
        except Exception:
            return -1

    def select_proxy(self, proxy_name: str, group_name: str = "GLOBAL") -> bool:
        """切换到指定节点"""
        try:
            encoded = urllib.parse.quote(group_name)
            url = f"{self.api_url}/proxies/{encoded}"
            res = requests.put(url, json={"name": proxy_name}, timeout=5)
            return res.ok
        except Exception:
            return False

    def find_healthy_node(self) -> Optional[str]:
        """查找健康节点（延迟测试）"""
        proxies = self.get_proxies()
        if not proxies:
            return None

        skip_types = {"Selector", "URLTest", "Fallback", "LoadBalance", "Direct", "Reject"}
        nodes = [name for name, info in proxies.items() if info.get("type") not in skip_types]

        for node in nodes:
            delay = self.test_latency(node)
            if delay > 0 and delay < 3000:
                return node

        return None

    def get_runtime_config(self) -> str:
        """获取运行时配置内容"""
        try:
            with open(self.runtime_config_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    def get_proxy_ip(self) -> Optional[str]:
        """获取当前代理IP"""
        try:
            proxies = {"http": f"http://127.0.0.1:{self.mixed_port}", "https": f"http://127.0.0.1:{self.mixed_port}"}
            res = requests.get("https://api.ipify.org?format=json", proxies=proxies, timeout=5)
            return res.json().get("ip")
        except Exception:
            return None

    def _prepare_runtime_config(self) -> bool:
        """准备运行时配置（设置端口和完整配置）"""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}

            cfg["mixed-port"] = self.mixed_port
            cfg["external-controller"] = f"127.0.0.1:{self.api_port}"
            cfg.setdefault("mode", "global")
            cfg.setdefault("log-level", "silent")

            # 确保有 proxies 和 proxy-groups
            cfg.setdefault("proxies", [])
            cfg.setdefault("proxy-groups", [{"name": "GLOBAL", "type": "select", "proxies": ["DIRECT"]}])
            cfg.setdefault("rules", ["MATCH,GLOBAL"])

            with open(self.runtime_config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(cfg, f, allow_unicode=True)
            return True
        except Exception:
            return False

    def _find_available_port(self, port: int) -> int:
        """查找可用端口"""
        for p in range(port, port + 100):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("127.0.0.1", p))
                    return p
            except OSError:
                continue
        return port

    def _log(self, level: str, message: str) -> None:
        """日志输出"""
        if self.log_callback:
            try:
                self.log_callback(level, message)
            except Exception:
                pass
