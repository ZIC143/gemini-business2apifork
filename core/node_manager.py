"""
节点管理模块

每个节点代表一个代理地址（http:// 或 socks5://），
用于替代静态 proxy_for_auth / proxy_for_chat 配置。
节点按成功率自动排序，失败回退到下一个节点。
"""

import base64
import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx
import requests
import yaml

from core import storage

logger = logging.getLogger(__name__)

# Clash 集成
_clash_manager = None
_stats_tracker = None


@dataclass
class NodeAttemptResult:
    """单个节点预检结果"""
    node_id: str
    node_name: str
    proxy_url: str
    reason: str


@dataclass
class NodeSelectionResult:
    """节点预检与选择结果"""
    selected: bool
    node_id: Optional[str] = None
    node_name: Optional[str] = None
    proxy_url: str = ""
    attempts: list[NodeAttemptResult] = field(default_factory=list)
    final_error: str = ""

    @property
    def attempted_count(self) -> int:
        return len(self.attempts)


def init_clash(clash_manager, stats_tracker):
    """初始化 Clash 管理器和统计追踪器"""
    global _clash_manager, _stats_tracker
    _clash_manager = clash_manager
    _stats_tracker = stats_tracker

# ---------- 数据模型 ----------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_node(name: str, url: str,
             use_for_auth: bool = True,
             use_for_chat: bool = True,
             enabled: bool = True,
             proxy_config: dict = None) -> dict:
    """创建一个新节点 dict"""
    return {
        "id": str(uuid.uuid4()),
        "name": name.strip(),
        "url": url.strip(),
        "enabled": enabled,
        "use_for_auth": use_for_auth,
        "use_for_chat": use_for_chat,
        "success": 0,
        "fail": 0,
        "consecutive_failures": 0,
        "proxy_config": proxy_config or {},
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


def _success_rate(node: dict) -> float:
    s = node.get("success", 0)
    f = node.get("fail", 0)
    total = s + f
    if total == 0:
        return 1.0   # 未使用过的节点排最前
    return s / total


# ---------- 内存缓存 ----------

_nodes_cache: Optional[list] = None


def _invalidate_cache():
    global _nodes_cache
    _nodes_cache = None


def load_all_nodes() -> list:
    """从数据库加载所有节点（含缓存）"""
    global _nodes_cache
    if _nodes_cache is not None:
        return _nodes_cache
    data = storage.load_nodes_sync()
    if data is None:
        data = []
    _nodes_cache = data
    return _nodes_cache


def _sorted_candidate_nodes(use_for: str = "auth") -> list[dict]:
    """按用途与成功率返回候选节点。"""
    nodes = load_all_nodes()
    key = "use_for_auth" if use_for == "auth" else "use_for_chat"
    candidates = [
        n for n in nodes
        if n.get("enabled")
        and n.get(key)
        and (_clash_manager is not None or not n.get("proxy_config"))
    ]
    candidates.sort(
        key=lambda node: (
            -_success_rate(node),
            node.get("created_at") or "",
            node.get("name") or "",
        )
    )
    return candidates


def _current_clash_proxy_url() -> str:
    if _clash_manager:
        return f"http://127.0.0.1:{_clash_manager.mixed_port}"
    return ""


def _node_runtime_proxy_url(node: dict) -> str:
    """根据节点类型返回本次实际使用的代理地址。"""
    if node.get("proxy_config"):
        clash_url = _current_clash_proxy_url()
        if clash_url:
            return clash_url
    return (node.get("url") or "").strip()


def _verify_proxy_connectivity(proxy_url: str, timeout: float = 10.0) -> tuple[bool, str]:
    """验证代理连通性。"""
    if not proxy_url:
        return False, "节点代理地址为空"
    try:
        proxies = {"http://": proxy_url, "https://": proxy_url}
        with httpx.Client(proxy=proxy_url, timeout=timeout, follow_redirects=True) as http_client:
            response = http_client.get("https://www.google.com/generate_204")
            if response.status_code != 204:
                return False, f"代理验证失败: HTTP {response.status_code}"
        return True, ""
    except httpx.ConnectError as exc:
        return False, f"代理连接失败: {exc}"
    except httpx.TimeoutException as exc:
        return False, f"代理验证超时: {exc}"
    except Exception as exc:
        return False, f"代理验证异常: {exc}"


def _select_node_for_attempt(node: dict) -> tuple[bool, str]:
    """切换到目标节点，必要时更新 Clash 选择器。"""
    if node.get("proxy_config"):
        if not _clash_manager:
            return False, "Clash 未初始化，无法使用 YAML 节点"
        if not _clash_manager.select_proxy(node.get("name", ""), "GLOBAL"):
            return False, "Clash 切换节点失败"
    return True, ""


def _remember_current_node(node: Optional[dict]) -> None:
    global _current_node_id
    _current_node_id = node.get("id") if node else None


def select_working_node(
    use_for: str = "auth",
    max_failures: int = 5,
    log_cb=None,
) -> NodeSelectionResult:
    """依次测试节点，返回可用节点；最多尝试 `max_failures` 个失败节点。"""
    candidates = _sorted_candidate_nodes(use_for)
    if not candidates:
        _remember_current_node(None)
        return NodeSelectionResult(selected=False)

    attempts: list[NodeAttemptResult] = []
    max_attempts = min(max_failures, len(candidates))

    for node in candidates[:max_attempts]:
        node_name = str(node.get("name") or "")
        node_id = str(node.get("id") or "")
        proxy_url = _node_runtime_proxy_url(node)

        if log_cb:
            log_cb("info", f"🔍 测试节点: {node_name}")

        switched, switch_reason = _select_node_for_attempt(node)
        if not switched:
            record_node_fail(node_id)
            attempts.append(NodeAttemptResult(node_id=node_id, node_name=node_name, proxy_url=proxy_url, reason=switch_reason))
            if log_cb:
                log_cb("warning", f"⚠️ 节点不可用: {node_name} - {switch_reason}")
            continue

        ok, reason = _verify_proxy_connectivity(proxy_url)
        if ok:
            _remember_current_node(node)
            if log_cb:
                log_cb("info", f"✅ 节点预检成功: {node_name}")
                log_cb("info", f"📍 代理地址: {proxy_url}")
            return NodeSelectionResult(
                selected=True,
                node_id=node_id,
                node_name=node_name,
                proxy_url=proxy_url,
                attempts=attempts,
            )

        record_node_fail(node_id)
        attempts.append(NodeAttemptResult(node_id=node_id, node_name=node_name, proxy_url=proxy_url, reason=reason))
        if log_cb:
            log_cb("warning", f"⚠️ 节点不可用: {node_name} - {reason}")

    _remember_current_node(None)
    final_error = attempts[-1].reason if attempts else "未找到可用节点"
    return NodeSelectionResult(selected=False, attempts=attempts, final_error=final_error)


def save_all_nodes(nodes: list) -> bool:
    """保存节点列表到数据库"""
    ok = storage.save_nodes_sync(nodes)
    if ok:
        global _nodes_cache
        _nodes_cache = nodes
    return ok


# ---------- CRUD ----------

def get_node_by_id(node_id: str) -> Optional[dict]:
    nodes = load_all_nodes()
    for n in nodes:
        if n.get("id") == node_id:
            return dict(n)
    return None


def create_node(name: str, url: str,
                use_for_auth: bool = True,
                use_for_chat: bool = True,
                enabled: bool = True) -> dict:
    node = new_node(name, url, use_for_auth, use_for_chat, enabled)
    nodes = load_all_nodes()
    nodes.append(node)
    save_all_nodes(nodes)
    return node


def update_node(node_id: str, updates: dict) -> Optional[dict]:
    """更新节点字段（不允许修改 id/created_at/success/fail）"""
    nodes = load_all_nodes()
    for i, n in enumerate(nodes):
        if n.get("id") == node_id:
            allowed = {"name", "url", "enabled", "use_for_auth", "use_for_chat"}
            for k, v in updates.items():
                if k in allowed:
                    n[k] = v
            n["updated_at"] = _now_iso()
            nodes[i] = n
            save_all_nodes(nodes)
            return dict(n)
    return None


def delete_node(node_id: str) -> bool:
    nodes = load_all_nodes()
    new_nodes = [n for n in nodes if n.get("id") != node_id]
    if len(new_nodes) == len(nodes):
        return False
    save_all_nodes(new_nodes)
    return True


def reset_node_stats(node_id: str) -> Optional[dict]:
    """清零某节点成功/失败计数"""
    nodes = load_all_nodes()
    for i, n in enumerate(nodes):
        if n.get("id") == node_id:
            n["success"] = 0
            n["fail"] = 0
            n["consecutive_failures"] = 0
            n["updated_at"] = _now_iso()
            nodes[i] = n
            save_all_nodes(nodes)
            return dict(n)
    return None


# ---------- 统计更新 ----------

def record_node_success(node_id: str):
    """记录代理请求成功"""
    nodes = load_all_nodes()
    for i, n in enumerate(nodes):
        if n.get("id") == node_id:
            n["success"] = n.get("success", 0) + 1
            n["consecutive_failures"] = 0
            n["updated_at"] = _now_iso()
            nodes[i] = n
            save_all_nodes(nodes)
            return
    logger.warning(f"[NodeManager] record_node_success: node {node_id} not found")


def record_node_fail(node_id: str, delete_after_consecutive_failures: int = 0) -> Optional[dict]:
    """记录代理请求失败，可选按连续失败阈值删除节点"""
    nodes = load_all_nodes()
    for i, n in enumerate(nodes):
        if n.get("id") == node_id:
            n["fail"] = n.get("fail", 0) + 1
            n["consecutive_failures"] = n.get("consecutive_failures", 0) + 1
            n["updated_at"] = _now_iso()

            if delete_after_consecutive_failures > 0 and n["consecutive_failures"] >= delete_after_consecutive_failures:
                removed = dict(n)
                del nodes[i]
                save_all_nodes(nodes)
                return removed

            nodes[i] = n
            save_all_nodes(nodes)
            return dict(n)
    logger.warning(f"[NodeManager] record_node_fail: node {node_id} not found")
    return None


# ---------- 代理选择 ----------

def get_best_proxy(use_for: str = "auth") -> Optional[str]:
    """
    按成功率选择最佳代理 URL。
    use_for: 'auth' | 'chat'
    返回 proxy URL 字符串，如无可用节点则返回 None。
    """
    candidates = _sorted_candidate_nodes(use_for)
    if not candidates:
        return None
    return _node_runtime_proxy_url(candidates[0]) or None


def get_effective_proxy(use_for: str, fallback: str = "") -> str:
    """
    获取当前有效的代理地址：优先从节点池选择，若无节点则使用静态配置 fallback。
    use_for: 'auth' | 'chat'
    """
    best = get_best_proxy(use_for)
    if best:
        return best
    return fallback or ""


# ---------- 批量导入 ----------

def import_from_url_list(text: str,
                         use_for_auth: bool = True,
                         use_for_chat: bool = True) -> list:
    """
    从纯文本（每行一个 URL）批量导入节点。
    返回新创建的节点列表。
    """
    created = []
    existing_urls = {n.get("url") for n in load_all_nodes()}

    for line in text.splitlines():
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        if not url.startswith(("http://", "https://", "socks5://", "socks5h://")):
            continue
        if url in existing_urls:
            continue
        # 用 URL 的 host:port 部分作为名称
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            name = f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname or url
        except Exception:
            name = url
        node = create_node(name, url, use_for_auth, use_for_chat)
        existing_urls.add(url)
        created.append(node)

    return created


def import_from_clash_yaml(yaml_text: str,
                           local_proxy_port: int = None,
                           use_for_auth: bool = True,
                           use_for_chat: bool = True) -> list:
    """从 Clash proxies YAML 批量导入节点，保存完整代理配置"""
    try:
        import yaml as _yaml
    except ImportError:
        logger.error("[NodeManager] PyYAML not installed")
        return []

    try:
        data = _yaml.safe_load(yaml_text)
    except Exception as e:
        logger.error(f"[NodeManager] YAML parse error: {e}")
        return []

    if not isinstance(data, dict):
        return []

    proxies = data.get("proxies", [])
    if not proxies:
        return []

    # 从代理控制配置读取端口
    if local_proxy_port is None:
        proxy_cfg = storage.load_proxy_control_sync() or {}
        local_proxy_port = proxy_cfg.get("port", 7890)

    local_url = f"http://127.0.0.1:{local_proxy_port}"
    existing_names = {n.get("name") for n in load_all_nodes()}
    created = []
    nodes = load_all_nodes()

    for proxy in proxies:
        if not isinstance(proxy, dict):
            continue
        name = proxy.get("name", "").strip()
        if not name or name in existing_names:
            continue
        # 保存完整代理配置
        node = new_node(name, local_url, use_for_auth, use_for_chat, True, proxy)
        nodes.append(node)
        existing_names.add(name)
        created.append(node)

    save_all_nodes(nodes)
    return created

# ---------- Clash 集成方法 ----------

_current_node_index = 0
_current_node_id = None

def rotate_node() -> Optional[str]:
    """轮询切换到下一个健康节点"""
    global _current_node_index, _current_node_id

    enabled_nodes = _sorted_candidate_nodes("auth")

    if not enabled_nodes:
        return None

    # 轮询选择节点
    _current_node_index = (_current_node_index + 1) % len(enabled_nodes)
    selected = enabled_nodes[_current_node_index]
    _remember_current_node(selected)

    # 如果有 Clash 管理器，切换到该节点
    if _clash_manager and selected.get("proxy_config"):
        try:
            _clash_manager.select_proxy(selected["name"], "GLOBAL")
        except Exception as e:
            logger.warning(f"[NodeManager] 切换节点失败: {e}")

    return selected["name"]


def get_current_proxy() -> str:
    """获取当前节点的代理地址"""
    global _current_node_id

    if not _current_node_id:
        return ""

    nodes = load_all_nodes()
    for n in nodes:
        if n.get("id") == _current_node_id:
            return _node_runtime_proxy_url(n)

    return ""


def import_subscription(url: str) -> int:
    """导入订阅链接"""
    try:
        res = requests.get(url, timeout=10)
        content = res.text
        try:
            content = base64.b64decode(content).decode("utf-8")
        except Exception:
            pass
        return import_yaml(content)
    except Exception as e:
        logger.error(f"导入订阅失败: {e}")
        return 0


def import_yaml(content: str) -> int:
    """导入 YAML 配置并创建节点记录"""
    try:
        created = import_from_clash_yaml(content, use_for_auth=True, use_for_chat=True)
        # 更新 Clash 配置
        if created and _clash_manager:
            _update_clash_config()
        return len(created)
    except Exception as e:
        logger.error(f"导入 YAML 失败: {e}")
        return 0


def _update_clash_config():
    """更新 Clash 配置文件并重载"""
    if not _clash_manager:
        return
    try:
        import yaml
        nodes = load_all_nodes()
        enabled_nodes = [n for n in nodes if n.get("enabled", True) and n.get("proxy_config")]

        config_path = _clash_manager.config_path
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        # 使用完整代理配置
        cfg["proxies"] = [n["proxy_config"] for n in enabled_nodes]
        proxy_names = [n["name"] for n in enabled_nodes]
        cfg["proxy-groups"] = [
            {"name": "GLOBAL", "type": "select", "proxies": ["DIRECT"] + proxy_names}
        ]

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True)

        # 重载配置
        _clash_manager.reload_config()
        logger.info(f"[NodeManager] 已更新并重载 Clash 配置: {len(proxy_names)} 个节点")
    except Exception as e:
        logger.error(f"[NodeManager] 更新 Clash 配置失败: {e}")
