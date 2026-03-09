"""节点统计追踪器"""
import json
import os
from typing import Dict, Literal


class NodeStatsTracker:
    """追踪节点的成功/风控/其他失败统计"""

    def __init__(self, stats_file: str = "data/node_stats.json"):
        self.stats_file = stats_file
        os.makedirs(os.path.dirname(stats_file), exist_ok=True)

    def record(self, node_name: str, result: Literal["success", "risk_control", "other"], sync_node_db: bool = True) -> None:
        """记录节点结果"""
        stats = self._load_stats()
        if node_name not in stats:
            stats[node_name] = {"success": 0, "risk_control": 0, "other": 0}
        stats[node_name][result] = stats[node_name].get(result, 0) + 1
        self._save_stats(stats)

        if not sync_node_db:
            return

        # 同步更新节点数据库
        from core import node_manager
        nodes = node_manager.load_all_nodes()
        for node in nodes:
            if node.get("name") == node_name:
                if result == "success":
                    node["success"] = node.get("success", 0) + 1
                    node["consecutive_failures"] = 0
                else:
                    node["fail"] = node.get("fail", 0) + 1
                    node["consecutive_failures"] = node.get("consecutive_failures", 0) + 1
                node_manager.save_all_nodes(nodes)
                break

    def get_stats(self) -> Dict[str, Dict[str, int]]:
        """获取统计数据"""
        return self._load_stats()

    def get_chart_data(self) -> Dict:
        """返回 ECharts 格式数据"""
        stats = self._load_stats()
        labels = list(stats.keys())
        return {
            "labels": labels,
            "datasets": [
                {"label": "成功", "data": [stats[n].get("success", 0) for n in labels]},
                {"label": "风控", "data": [stats[n].get("risk_control", 0) for n in labels]},
                {"label": "其他", "data": [stats[n].get("other", 0) for n in labels]},
            ],
        }

    def _load_stats(self) -> Dict:
        """加载统计数据"""
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_stats(self, stats: Dict) -> None:
        """保存统计数据"""
        try:
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
