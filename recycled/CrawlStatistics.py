import threading
from collections import deque
from typing import Any, Dict, List, Tuple, Optional, Set, Union, Iterable


class CrawlStatistics:
    # Singleton implementation with double-checked locking pattern
    _instance: Optional['CrawlStatistics'] = None
    _singleton_lock = threading.Lock()

    def __new__(cls, *args, **kwargs) -> 'CrawlStatistics':
        # First check avoids locking overhead after initialization
        if cls._instance is None:
            with cls._singleton_lock:  # Ensure thread-safe creation
                # Second check prevents race condition during lock acquisition
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # Ensure initialization only happens once
        if hasattr(self, '_initialized'):
            return

        # Thread-safe data structures for statistics
        self._counter_log_lock = threading.Lock()
        self._counter_log_record: Dict[Tuple, Dict[str, int]] = {}

        self._sub_item_log_lock = threading.Lock()
        self._sub_item_log_record: Dict[Tuple, Tuple[deque, Dict[str, List[Any]]]] = {}
        self._max_sub_items = 100

        self._initialized = True  # Mark initialization complete

    def set_sub_items_limit(self, max_items: int) -> None:
        """Set global maximum number of sub-items allowed per namespace.

        Args:
            max_items: Maximum number of sub-items to keep per namespace
        """
        if max_items < 0:
            raise ValueError("max_items must be non-negative")
        self._max_sub_items = max_items

    def counter_log(self, leveled_names: List[str], counter_item_name: str, log_text: str = '') -> None:
        """Increment counter for a specific item in a hierarchical namespace.

        Args:
            leveled_names: Hierarchical namespace path (e.g., ['domain', 'page'])
            counter_item_name: Name of the counter to increment
            log_text: Placeholder for future logging functionality
        """
        key = tuple(leveled_names)  # Convert path to hashable tuple

        with self._counter_log_lock:  # Ensure thread safety
            # Get or create namespace dictionary
            namespace = self._counter_log_record.setdefault(key, {})
            # Increment counter
            namespace[counter_item_name] = namespace.get(counter_item_name, 0) + 1

    def get_classified_counter(self, leveled_names: List[str]) -> Dict[str, int]:
        """Retrieve counters for a hierarchical namespace.

        Args:
            leveled_names: Hierarchical namespace path

        Returns:
            Copy of counter dictionary to prevent external modification
        """
        key = tuple(leveled_names)

        with self._counter_log_lock:  # Ensure thread safety during read
            # Return copy to avoid external modification of internal data
            return self._counter_log_record.get(key, {}).copy()

    def sub_item_log(self, leveled_names: List[str], sub_item: Any, status: str) -> None:
        """Record sub-item status with automatic removal of oldest items when exceeding limit.

        Args:
            leveled_names: Hierarchical namespace path
            sub_item: Item to track
            status: Current status of the item
        """
        key = tuple(leveled_names)

        with self._sub_item_log_lock:
            # Get or create data structures for this namespace
            if key not in self._sub_item_log_record:
                data_queue = deque()
                status_dict = {}
                self._sub_item_log_record[key] = (data_queue, status_dict)
            else:
                data_queue, status_dict = self._sub_item_log_record[key]

            # Add new item to both data structures
            data_queue.append((status, sub_item))
            if status not in status_dict:
                status_dict[status] = []
            status_dict[status].append(sub_item)

            # Remove oldest items if exceeding limit
            while len(data_queue) > self._max_sub_items:
                old_status, old_item = data_queue.popleft()
                if old_status in status_dict:
                    try:
                        # Remove first occurrence of this item in status list
                        status_dict[old_status].remove(old_item)
                    except ValueError:
                        # Item not found (shouldn't happen normally)
                        pass

                    # Clean up empty status lists
                    if not status_dict[old_status]:
                        del status_dict[old_status]

    def get_sub_item_statistics(self, leveled_names: List[str]) -> Dict[str, List[Any]]:
        """Retrieve sub-item statuses with updated data structure handling.

        Args:
            leveled_names: Hierarchical namespace path

        Returns:
            Copy of status dictionary with list copies
        """
        key = tuple(leveled_names)

        with self._sub_item_log_lock:
            if key in self._sub_item_log_record:
                _, status_dict = self._sub_item_log_record[key]
                return {
                    status: items.copy()
                    for status, items in status_dict.items()
                }
            return {}

    def reset(self, leveled_names: Union[List[str], None] = None) -> None:
        """Reset statistics either completely or for specified namespace(s)

        Args:
            leveled_names:
                - None: Reset all statistics
                - List: Reset specific namespace and its children
        """
        if leveled_names is None:
            # Reset everything
            with self._counter_log_lock, self._sub_item_log_lock:
                self._counter_log_record.clear()
                self._sub_item_log_record.clear()
            return

        # Convert to tuple for matching
        target_key = tuple(leveled_names)

        with self._counter_log_lock:
            # Remove target namespace and any child namespaces
            keys_to_remove = [
                k for k in self._counter_log_record.keys()
                if k[:len(target_key)] == target_key
            ]
            for k in keys_to_remove:
                del self._counter_log_record[k]

        with self._sub_item_log_lock:
            # Repeat for sub-item log
            keys_to_remove = [
                k for k in self._sub_item_log_record.keys()
                if k[:len(target_key)] == target_key
            ]
            for k in keys_to_remove:
                del self._sub_item_log_record[k]

    # def dump(self, leveled_names: Union[List[str], None] = None) -> str:
    #     """Generate formatted statistics report either for all namespaces or specified one
    #
    #     Args:
    #         leveled_names:
    #             - None: Dump all namespaces
    #             - List: Dump specific namespace and its children
    #
    #     Returns:
    #         Formatted multi-line statistics report
    #     """
    #     # Handle all-namespace case
    #     if leveled_names is None:
    #         return self._dump_all_namespaces()
    #
    #     # Handle specific namespace case
    #     target_key = tuple(leveled_names)
    #     return self._dump_single_namespace(target_key, include_children=True)

    def _get_all_namespaces(self) -> Set[Tuple]:
        """Get combined keys from both log records"""
        with self._counter_log_lock:
            counter_keys = set(self._counter_log_record.keys())
        with self._sub_item_log_lock:
            subitem_keys = set(self._sub_item_log_record.keys())
        return counter_keys | subitem_keys

    # def _dump_all_namespaces(self) -> str:
    #     """Generate report for all namespaces"""
    #     all_keys = sorted(self._get_all_namespaces(), key=lambda x: (len(x), x))
    #     return "\n\n".join([self._dump_single_namespace(key) for key in all_keys])
    #
    # def _dump_single_namespace(
    #         self,
    #         namespace_key: Tuple,
    #         include_children: bool = False
    # ) -> str:
    #     """Generate report for a single namespace (including children if requested)"""
    #     # Collect matching keys
    #     matching_keys = []
    #     if include_children:
    #         all_keys = self._get_all_namespaces()
    #         matching_keys = sorted(
    #             [k for k in all_keys if k[:len(namespace_key)] == namespace_key],
    #             key=lambda x: (len(x), x)
    #         )
    #     else:
    #         matching_keys = [namespace_key]
    #
    #     # Generate report sections
    #     sections = []
    #     for key in matching_keys:
    #         counter_data = self.get_classified_counter(list(key))
    #         subitem_data = self.get_sub_item_statistics(list(key))
    #
    #         # Skip empty namespaces
    #         if not counter_data and not subitem_data:
    #             continue
    #
    #         section = [
    #             f"Namespace: {'.'.join(key)}",
    #             "\nCounter Statistics:"
    #         ]
    #
    #         # Format counters
    #         for name, count in counter_data.items():
    #             section.append(f"  {name}: {count}")
    #
    #         # Format subitems
    #         if subitem_data:
    #             section.append("\nSub-item Statistics:")
    #             for status, items in subitem_data.items():
    #                 section.append(f"  STATUS: {status} (Count: {len(items)})")
    #                 for i, item in enumerate(items, 1):
    #                     section.append(f"    {i}. {str(item)}")
    #
    #         sections.append("\n".join(section))
    #
    #     return "\n\n".join(sections) if sections else ""

    def get_child_namespaces(self, parent_namespace: List[str]) -> List[Tuple[str, ...]]:
        """获取指定父命名空间下的所有直接子命名空间

        Args:
            parent_namespace: 父命名空间路径

        Returns:
            子命名空间元组列表（不含父路径前缀）
        """
        parent_key = tuple(parent_namespace)
        all_keys = self._get_all_namespaces()

        # 使用集合避免重复
        children = set()
        for key in all_keys:
            # 跳过不是父命名空间直接子项的键
            if len(key) <= len(parent_key) or key[:len(parent_key)] != parent_key:
                continue

            # 获取直接子项
            direct_child = key[:len(parent_key) + 1]
            children.add(direct_child)

        # 按元组排序返回
        return sorted(children, key=lambda x: (len(x), x))

    def dump_counters(self,
                      leveled_names: Union[List[str], None] = None,
                      include_children: bool = True) -> str:
        """生成格式化计数器统计信息

        Args:
            leveled_names:
                - None: 所有命名空间
                - List: 指定命名空间
            include_children: 是否包含子命名空间

        Returns:
            格式化计数器统计信息
        """
        # 处理所有命名空间
        if leveled_names is None:
            all_keys = sorted(self._get_all_namespaces(), key=lambda x: (len(x), x))
            return "\n\n".join([self._dump_counter_namespace(key) for key in all_keys])

        # 处理单个命名空间（包括子空间）
        return self._dump_counter_namespace(
            tuple(leveled_names),
            include_children=include_children
        )

    def _dump_counter_namespace(self,
                                namespace_key: Tuple,
                                include_children: bool = True,
                                ident: int = 2) -> str:
        """生成指定命名空间的计数器转储"""
        if include_children:
            # 获取所有匹配的子命名空间
            matching_keys = self._get_child_namespaces_recursive(namespace_key)
        else:
            matching_keys = [namespace_key]

        sections = []
        for key in matching_keys:
            counter_data = self.get_classified_counter(list(key))
            if not counter_data:
                continue

            section = [f"Namespace: {'.'.join(key)}", f"{' ' * ident * 1}Counter Statistics:"]
            for name, count in counter_data.items():
                section.append(f"{' ' * ident * 2}{name}: {count}")

            sections.append("\n".join(section))

        return "\n\n".join(sections) if sections else f"No counter data for namespace: {'.'.join(namespace_key)}"

    def dump_sub_items(self,
                       leveled_names: Union[List[str], None] = None,
                       include_children: bool = True,
                       statuses: Optional[Iterable[str]] = None) -> str:
        """生成格式化子项统计信息

        Args:
            leveled_names:
                - None: 所有命名空间
                - List: 指定命名空间
            include_children: 是否包含子命名空间
            statuses: 要包含的状态列表（None表示所有状态）

        Returns:
            格式化子项统计信息
        """
        # 处理所有命名空间
        if leveled_names is None:
            all_keys = sorted(self._get_all_namespaces(), key=lambda x: (len(x), x))
            return "\n\n".join([
                self._dump_sub_items_namespace(key, include_children=False, statuses=statuses)
                for key in all_keys
            ])

        # 处理单个命名空间（包括子空间）
        return self._dump_sub_items_namespace(
            tuple(leveled_names),
            include_children=include_children,
            statuses=statuses
        )

    def _dump_sub_items_namespace(self,
                                  namespace_key: Tuple,
                                  include_children: bool = True,
                                  statuses: Optional[Iterable[str]] = None,
                                  ident: int = 2) -> str:
        """生成指定命名空间的子项转储（支持状态过滤）"""
        if include_children:
            # 获取所有匹配的子命名空间
            matching_keys = self._get_child_namespaces_recursive(namespace_key)
        else:
            matching_keys = [namespace_key]

        sections = []
        for key in matching_keys:
            subitem_data = self.get_sub_item_statistics(list(key))
            if not subitem_data:
                continue

            section = [f"Namespace: {'.'.join(key)}", f"{' ' * ident * 1}Sub-item Statistics:"]

            # 应用状态过滤
            if statuses is not None:
                statuses = set(statuses)
                filtered_data = {
                    status: items
                    for status, items in subitem_data.items()
                    if status in statuses
                }
            else:
                filtered_data = subitem_data

            for status, items in filtered_data.items():
                section.append(f"{' ' * ident * 2}{status} ({len(items)})")
                for i, item in enumerate(items, 1):
                    section.append(f"{' ' * ident * 3}{i}. {str(item)}")

            sections.append("\n".join(section))

        return "\n\n".join(sections) if sections else f"No sub-item data for namespace: {'.'.join(namespace_key)}"

    # 辅助方法: 递归获取所有子命名空间
    def _get_child_namespaces_recursive(self, parent_key: Tuple) -> List[Tuple]:
        """获取指定父键的所有子命名空间（包括子子空间）"""
        all_keys = self._get_all_namespaces()
        return sorted(
            [k for k in all_keys if k[:len(parent_key)] == parent_key],
            key=lambda x: (len(x), x)
        )
