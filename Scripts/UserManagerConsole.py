import sys
from typing import List, Dict, Any, Callable

from GlobalConfig import DEFAULT_USER_DB_PATH
from ServiceComponent.UserManager import UserManager


class UserManagerConsole:
    PAGE_SIZE = 5  # 每页显示数量

    def __init__(self, db_path: str):
        self.user_manager = UserManager(db_path)
        self.current_user = None
        self.menu_stack = []  # 菜单导航栈

    def run(self):
        """主程序入口"""
        # self._show_login_screen()
        self._show_main_menu()

    def _clear_screen(self):
        """清屏函数（跨平台）"""
        if sys.platform == 'win32':
            os.system('cls')
        else:
            os.system('clear')

    def _show_header(self, title: str):
        """显示页面标题"""
        self._clear_screen()
        print(f"\n{'=' * 50}")
        print(f"{title.center(50)}")
        print(f"{'=' * 50}\n")

    def _paginate_display(self, data: List[Dict[str, Any]],
                          headers: List[str],
                          format_func: Callable):
        """通用分页显示功能"""
        page = 0
        total_pages = (len(data) + self.PAGE_SIZE - 1) // self.PAGE_SIZE

        while True:
            start = page * self.PAGE_SIZE
            end = min(start + self.PAGE_SIZE, len(data))
            page_data = data[start:end]

            # 显示表头
            print(" | ".join(headers))
            print("-" * 80)

            # 显示数据
            for item in page_data:
                print(format_func(item))

            # 显示分页信息
            print(f"\n第 {page + 1}/{total_pages} 页 (共 {len(data)} 条)")
            print("操作: (N)下一页, (P)上一页, (Q)返回")

            # 用户操作
            action = input("> ").upper()
            if action == 'N' and page < total_pages - 1:
                page += 1
            elif action == 'P' and page > 0:
                page -= 1
            elif action == 'Q':
                return
            else:
                print("无效操作")

    # -------------------------------- 登录功能 --------------------------------
    def _show_login_screen(self):
        """登录界面"""
        self._show_header("用户管理系统 - 登录")
        username = input("用户名: ")
        password = input("密码: ")

        # 认证逻辑
        success, message = self.user_manager.authenticate(username, password, "127.0.0.1")
        if success:
            print(f"\n✅ 登录成功: {message}")
            self.current_user = username
            input("\n按回车键继续...")
            self._show_main_menu()
        else:
            print(f"\n❌ 登录失败: {message}")
            input("\n按回车键重试...")
            self._show_login_screen()

    # -------------------------------- 主菜单 --------------------------------
    def _show_main_menu(self):
        """主菜单"""
        self.menu_stack = []
        while True:
            self._show_header("主菜单")
            print("1. 用户管理")
            print("2. 角色管理")
            print("3. 权限管理")
            print("4. 登录日志查看")
            print("5. 密码验证")
            print("6. 退出系统")

            choice = input("\n请选择操作: ")

            if choice == '1':
                self._show_user_management_menu()
            elif choice == '2':
                self._show_role_management_menu()
            elif choice == '3':
                self._show_permissions_menu()
            elif choice == '4':
                self._show_login_logs()
            elif choice == '5':
                self._verify_password()
            elif choice == '6':
                sys.exit("系统已退出")
            else:
                print("无效选择，请重试")

    # -------------------------------- 用户管理 --------------------------------
    def _show_user_management_menu(self):
        """用户管理菜单"""
        self.menu_stack.append(self._show_user_management_menu)

        while True:
            self._show_header("用户管理")
            print("1. 查看所有用户")
            print("2. 添加用户")
            print("3. 修改用户")
            print("4. 删除用户")
            print("5. 管理用户角色")
            print("6. 返回主菜单")

            choice = input("\n请选择操作: ")

            if choice == '1':
                self._list_all_users()
            elif choice == '2':
                self._add_user()
            elif choice == '3':
                self._update_user()
            elif choice == '4':
                self._delete_user()
            elif choice == '5':
                self._manage_user_roles()
            elif choice == '6':
                self.menu_stack.pop()
                return
            else:
                print("无效选择，请重试")

    def _list_all_users(self):
        """分页查看所有用户"""
        users = self.user_manager.get_all_users()

        headers = ["ID", "用户名", "状态", "角色", "创建时间"]

        def format_user(user):
            return f"{user['id']} | {user['username']} | {'激活' if user['is_active'] else '禁用'} | " \
                   f"{', '.join(user['roles'])} | {user['created_at']}"

        self._paginate_display(users, headers, format_user)

    def _add_user(self):
        """添加新用户"""
        self._show_header("添加新用户")
        username = input("用户名: ")
        password = input("密码: ")
        confirm = input("确认密码: ")

        if password != confirm:
            print("密码不一致!")
            return

        # 获取角色
        roles_input = input("分配角色(逗号分隔): ")
        roles = [r.strip() for r in roles_input.split(",") if r.strip()]

        user_id, message = self.user_manager.create_user(username, password, roles)
        print(f"\n{message}")
        input("\n按回车键继续...")

    def _update_user(self):
        """修改用户信息"""
        users = self.user_manager.get_all_users()
        if not users:
            print("❌ 系统中暂无用户")
            input("\n按回车键返回...")
            return

        # 分页选择用户
        headers = ["ID", "用户名", "状态", "角色"]

        def format_user(user):
            return f"{user['id']} | {user['username']} | {'激活' if user['is_active'] else '禁用'} | {', '.join(user['roles'])}"

        self._paginate_display(users, headers, format_user)

        # 选择用户ID
        try:
            user_id = int(input("\n请输入要修改的用户ID: "))
            selected_user = next((u for u in users if u['id'] == user_id), None)
            if not selected_user:
                print("❌ 无效的用户ID")
                return
        except ValueError:
            print("❌ 请输入有效的数字ID")
            return

        # 修改选项
        self._show_header(f"修改用户: {selected_user['username']}")
        print("1. 修改用户名")
        print("2. 修改密码")
        print("3. 切换激活状态")
        print("4. 返回")

        choice = input("\n请选择操作: ")

        params = {}
        if choice == '1':
            new_name = input("新用户名: ")
            params['new_username'] = new_name
        elif choice == '2':
            new_password = input("新密码: ")
            confirm = input("确认密码: ")
            if new_password != confirm:
                print("❌ 密码不一致")
                return
            params['new_password'] = new_password
        elif choice == '3':
            new_status = not selected_user['is_active']
            print(f"将状态改为: {'激活' if new_status else '禁用'}")
            params['is_active'] = new_status
        else:
            return

        # 执行更新
        success, message = self.user_manager.update_user(user_id, **params)
        print(f"\n{'✅' if success else '❌'} {message}")
        input("\n按回车键继续...")

    def _delete_user(self):
        """删除用户"""
        users = self.user_manager.get_all_users()
        if not users:
            print("❌ 系统中暂无用户")
            input("\n按回车键返回...")
            return

        # 分页选择用户
        headers = ["ID", "用户名", "状态"]

        def format_user(user):
            return f"{user['id']} | {user['username']} | {'激活' if user['is_active'] else '禁用'}"

        self._paginate_display(users, headers, format_user)

        # 选择用户ID
        try:
            user_id = int(input("\n请输入要删除的用户ID: "))
            if not any(u['id'] == user_id for u in users):
                print("❌ 无效的用户ID")
                return
        except ValueError:
            print("❌ 请输入有效的数字ID")
            return

        # 确认删除
        confirm = input(f"确定删除用户ID {user_id}? (y/n): ").lower()
        if confirm != 'y':
            print("操作已取消")
            return

        # 执行删除
        success, message = self.user_manager.delete_user(user_id)
        print(f"\n{'✅' if success else '❌'} {message}")
        input("\n按回车键继续...")

    def _manage_user_roles(self):
        """管理用户角色"""
        users = self.user_manager.get_all_users()
        if not users:
            print("❌ 系统中暂无用户")
            input("\n按回车键返回...")
            return

        # 分页选择用户
        headers = ["ID", "用户名", "当前角色"]

        def format_user(user):
            return f"{user['id']} | {user['username']} | {', '.join(user['roles']) or '无'}"

        self._paginate_display(users, headers, format_user)

        # 选择用户ID
        try:
            user_id = int(input("\n请输入要管理的用户ID: "))
            selected_user = next((u for u in users if u['id'] == user_id), None)
            if not selected_user:
                print("❌ 无效的用户ID")
                return
        except ValueError:
            print("❌ 请输入有效的数字ID")
            return

        while True:
            self._show_header(f"管理角色 - 用户: {selected_user['username']}")
            print(f"当前角色: {', '.join(selected_user['roles']) or '无'}")
            print("\n1. 添加角色")
            print("2. 移除角色")
            print("3. 返回")

            choice = input("\n请选择操作: ")

            if choice == '1':
                self._add_role_to_user(user_id, selected_user['roles'])
            elif choice == '2':
                self._remove_role_from_user(user_id, selected_user['roles'])
            elif choice == '3':
                return
            else:
                print("无效选择")

            # 刷新用户数据
            users = self.user_manager.get_all_users()
            selected_user = next((u for u in users if u['id'] == user_id), None)

    def _add_role_to_user(self, user_id, current_roles):
        """为用户添加角色"""
        all_roles = self.user_manager.get_all_roles()
        available_roles = [r['name'] for r in all_roles if r['name'] not in current_roles]

        if not available_roles:
            print("❌ 所有角色均已分配")
            input("\n按回车键继续...")
            return

        # 分页显示可用角色
        headers = ["角色名称"]
        self._paginate_display(available_roles, headers, lambda x: x)

        role_name = input("\n输入要添加的角色名: ")
        if role_name not in available_roles:
            print("❌ 无效的角色名")
            return

        # 添加角色（覆盖式更新）
        new_roles = current_roles + [role_name]
        success, message = self.user_manager.assign_roles(user_id, new_roles)
        print(f"\n{'✅' if success else '❌'} {message}")
        input("\n按回车键继续...")

    def _remove_role_from_user(self, user_id, current_roles):
        """移除用户角色"""
        if not current_roles:
            print("❌ 用户暂无角色")
            return

        # 分页显示当前角色
        headers = ["角色名称"]
        self._paginate_display(current_roles, headers, lambda x: x)

        role_name = input("\n输入要移除的角色名: ")
        if role_name not in current_roles:
            print("❌ 用户未分配此角色")
            return

        # 移除角色（覆盖式更新）
        new_roles = [r for r in current_roles if r != role_name]
        success, message = self.user_manager.assign_roles(user_id, new_roles)
        print(f"\n{'✅' if success else '❌'} {message}")
        input("\n按回车键继续...")

    # -------------------------------- 角色管理 --------------------------------
    def _show_role_management_menu(self):
        """角色管理菜单"""
        self.menu_stack.append(self._show_role_management_menu)

        while True:
            self._show_header("角色管理")
            print("1. 查看所有角色")
            print("2. 添加角色")
            print("3. 修改角色")
            print("4. 删除角色")
            print("5. 返回主菜单")

            choice = input("\n请选择操作: ")

            if choice == '1':
                self._list_all_roles()
            elif choice == '2':
                self._add_role()
            elif choice == '3':
                self._update_role()
            elif choice == '4':
                self._delete_role()
            elif choice == '5':
                self.menu_stack.pop()
                return
            else:
                print("无效选择，请重试")

    def _list_all_roles(self):
        """分页查看所有角色"""
        roles = self.user_manager.get_all_roles()

        headers = ["ID", "角色名称", "权限"]

        def format_role(role):
            return f"{role['id']} | {role['name']} | {', '.join(role['permissions'])}"

        self._paginate_display(roles, headers, format_role)

    def _add_role(self):
        """添加新角色"""
        self._show_header("添加新角色")
        role_name = input("角色名称: ").strip()
        if not role_name:
            print("❌ 角色名称不能为空")
            input("\n按回车键继续...")
            return

        # 获取权限列表
        permissions_input = input("权限列表(逗号分隔): ").strip()
        permissions = [p.strip() for p in permissions_input.split(",") if p.strip()] if permissions_input else []

        # 调用UserManager添加角色
        success, message = self.user_manager.add_role(role_name, permissions)
        print(f"\n{'✅' if success else '❌'} {message}")
        input("\n按回车键继续...")

    def _update_role(self):
        """更新角色权限"""
        roles = self.user_manager.get_all_roles()
        if not roles:
            print("❌ 系统中暂无角色")
            input("\n按回车键返回...")
            return

        # 分页选择角色
        headers = ["ID", "角色名称", "权限"]

        def format_role(role):
            return f"{role['id']} | {role['name']} | {', '.join(role['permissions'])}"

        self._paginate_display(roles, headers, format_role)

        # 选择角色ID
        try:
            role_id = int(input("\n请输入要修改的角色ID: "))
            selected_role = next((r for r in roles if r['id'] == role_id), None)
            if not selected_role:
                print("❌ 无效的角色ID")
                return
        except ValueError:
            print("❌ 请输入有效的数字ID")
            return

        # 显示当前权限
        self._show_header(f"修改角色: {selected_role['name']}")
        print(f"当前权限: {', '.join(selected_role['permissions']) or '无'}")

        # 获取新权限
        new_perms_input = input("新权限列表(逗号分隔，留空保持不变): ").strip()
        new_permissions = [p.strip() for p in new_perms_input.split(",") if p.strip()] if new_perms_input else None

        # 更新角色（通过删除重建实现）
        if new_permissions is not None:
            # 先删除旧角色
            # TODO: Add delete role.
            del_success, del_msg = self.user_manager.delete_role(selected_role['name'])
            if not del_success:
                print(f"❌ 删除旧角色失败: {del_msg}")
                return

            # 创建新角色
            success, message = self.user_manager.add_role(selected_role['name'], new_permissions)
            print(f"\n{'✅' if success else '❌'} {message}")
        else:
            print("⚠️ 未修改权限")

        input("\n按回车键继续...")

    def _delete_role(self):
        """删除角色"""
        roles = self.user_manager.get_all_roles()
        if not roles:
            print("❌ 系统中暂无角色")
            input("\n按回车键返回...")
            return

        # 分页选择角色
        headers = ["ID", "角色名称", "权限"]

        def format_role(role):
            return f"{role['id']} | {role['name']} | {', '.join(role['permissions'])}"

        self._paginate_display(roles, headers, format_role)

        # 选择角色ID
        try:
            role_id = int(input("\n请输入要删除的角色ID: "))
            selected_role = next((r for r in roles if r['id'] == role_id), None)
            if not selected_role:
                print("❌ 无效的角色ID")
                return
        except ValueError:
            print("❌ 请输入有效的数字ID")
            return

        # 确认删除
        confirm = input(f"确定删除角色 '{selected_role['name']}'? (y/n): ").lower()
        if confirm != 'y':
            print("操作已取消")
            return

        # 执行删除
        success, message = self.user_manager.delete_role(selected_role['name'])
        print(f"\n{'✅' if success else '❌'} {message}")
        input("\n按回车键继续...")

    # -------------------------------- 权限管理 --------------------------------
    def _show_permissions_menu(self):
        """权限查看菜单"""
        self._show_header("系统权限列表")
        permissions = self.user_manager.get_all_permissions()

        headers = ["ID", "权限名称"]

        def format_perm(perm):
            return f"{perm['id']} | {perm['name']}"

        self._paginate_display(permissions, headers, format_perm)
        input("\n按回车键返回主菜单...")

    # -------------------------------- 登录日志 --------------------------------
    def _show_login_logs(self):
        """查看登录日志"""
        self._show_header("登录日志")
        logs = self.user_manager.get_login_logs()

        headers = ["ID", "用户ID", "用户名", "IP地址", "结果", "时间"]

        def format_log(log):
            return f"{log['id']} | {log['user_id'] or 'N/A'} | {log['username']} | " \
                   f"{log['client_ip']} | {log['result']} | {log['timestamp']}"

        self._paginate_display(logs, headers, format_log)

    # -------------------------------- 密码验证 --------------------------------
    def _verify_password(self):
        """密码验证功能"""
        self._show_header("密码验证")
        username = input("用户名: ")
        password = input("密码: ")

        # 直接使用UserManager的认证逻辑
        success, message = self.user_manager.authenticate(username, password, "127.0.0.1")
        result = "✅ 验证成功" if success else "❌ 验证失败"
        print(f"\n{result}: {message}")
        input("\n按回车键返回主菜单...")


# 程序启动
if __name__ == "__main__":
    import os

    console = UserManagerConsole(
        f"../{DEFAULT_USER_DB_PATH}" if not os.path.isabs(DEFAULT_USER_DB_PATH) else DEFAULT_USER_DB_PATH)
    console.run()
