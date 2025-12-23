#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Let Me Go - 健康提醒助手
定时提醒用户站起来走走的Windows桌面应用
"""

import tkinter as tk
from tkinter import messagebox
import threading
import time
import datetime
import json
import os
import sys
import winreg
import pystray
from PIL import Image, ImageDraw


# =============== 常量定义 ===============
def get_app_dir():
    """获取程序所在目录"""
    if getattr(sys, 'frozen', False):
        # 如果是打包后的exe
        return os.path.dirname(sys.executable)
    else:
        # 如果是Python脚本
        return os.path.dirname(os.path.abspath(__file__))

APP_DIR = get_app_dir()
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
APP_NAME = "LetMeGo"
REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


# =============== 配置管理 ===============
class ConfigManager:
    """配置文件管理器"""
    
    def __init__(self, config_file=CONFIG_FILE):
        self.config_file = config_file
        self.default_config = {
            "work_periods": [
                {"start": "09:00", "end": "18:00"}
            ],
            "block_periods": [
                {"start": "12:00", "end": "13:30"}
            ],
            "interval_minutes": 60,
            "auto_start": False,
            "workdays": [1, 2, 3, 4, 5],  # 周一到周五 (1=周一, 7=周日)
            "off_work_time": "18:00",  # 下班时间
            "off_work_reminder_enabled": True  # 是否启用下班提醒
        }
    
    def load_config(self):
        """加载配置文件"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    # 兼容旧版本配置格式
                    if "start_time" in config and "work_periods" not in config:
                        # 旧格式，转换为新格式
                        config = {
                            "work_periods": [
                                {"start": config.get("start_time", "09:00"), 
                                 "end": config.get("end_time", "18:00")}
                            ],
                            "block_periods": [
                                {"start": config.get("block_start", "12:00"), 
                                 "end": config.get("block_end", "13:30")}
                            ],
                            "interval_minutes": config.get("interval_minutes", 60),
                            "auto_start": config.get("auto_start", False)
                        }
                    # 合并默认配置，确保所有键都存在
                    for key in self.default_config:
                        if key not in config:
                            config[key] = self.default_config[key]
                    # 确保列表不为空
                    if not config.get("work_periods"):
                        config["work_periods"] = self.default_config["work_periods"]
                    if not config.get("block_periods"):
                        config["block_periods"] = self.default_config["block_periods"]
                    return config
            except Exception as e:
                print(f"加载配置文件失败: {e}")
                return self.default_config.copy()
        return self.default_config.copy()
    
    def save_config(self, config):
        """保存配置文件"""
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
            return True
        except Exception as e:
            print(f"保存配置文件失败: {e}")
            return False


# =============== 开机自启动管理 ===============
class AutoStartManager:
    """开机自启动管理器"""
    
    @staticmethod
    def get_exe_path():
        """获取程序可执行文件路径"""
        if getattr(sys, 'frozen', False):
            # 如果是打包后的exe
            return sys.executable
        else:
            # 如果是Python脚本
            return os.path.abspath(sys.argv[0])
    
    @staticmethod
    def is_auto_start_enabled():
        """检查是否已设置开机自启动"""
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY)
            try:
                value, _ = winreg.QueryValueEx(key, APP_NAME)
                return value == AutoStartManager.get_exe_path()
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(key)
        except Exception as e:
            print(f"检查自启动状态失败: {e}")
            return False
    
    @staticmethod
    def set_auto_start(enabled):
        """设置或取消开机自启动"""
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY, 0, winreg.KEY_SET_VALUE)
            if enabled:
                exe_path = AutoStartManager.get_exe_path()
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, exe_path)
                print(f"已设置开机自启动: {exe_path}")
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                    print("已取消开机自启动")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
            return True
        except Exception as e:
            print(f"设置自启动失败: {e}")
            messagebox.showerror("错误", f"设置开机自启动失败: {e}")
            return False


# =============== 工具函数 ===============
def parse_time(time_str):
    """解析时间字符串为datetime.time对象"""
    try:
        hour, minute = map(int, time_str.split(":"))
        return datetime.time(hour, minute)
    except (ValueError, AttributeError):
        return None


def time_in_range(start_time, end_time, current_time):
    """判断当前时间是否在指定时间范围内"""
    if start_time <= end_time:
        return start_time <= current_time <= end_time
    else:
        # 跨天的情况
        return current_time >= start_time or current_time <= end_time


# =============== 主应用类 ===============
class LetMeGoApp:
    """主应用类"""
    
    def __init__(self):
        self.config_manager = ConfigManager()
        self.config = self.config_manager.load_config()
        self.running = False
        self.next_reminder = None
        self.icon = None
        self.tray_running = True
        self.last_reminder_time = None
        self.last_off_work_reminder_date = None  # 记录今天是否已发送下班提醒
        self.is_first_start = True  # 标记是否首次启动
        
        # 判断是否应该显示配置窗口
        should_show = self.should_show_config()
        
        # 如果不需要显示配置窗口且配置了自动启动，直接启动托盘
        if not should_show and self.config.get("auto_start", False):
            self.start_reminder_service()
        else:
            self.show_config_window()
    
    def should_show_config(self):
        """判断是否应该显示配置窗口（首次运行或参数中指定）"""
        # 如果命令行参数包含 --config 或 --setup，显示配置窗口
        if "--config" in sys.argv or "--setup" in sys.argv:
            return True
        
        # 如果配置文件不存在，显示配置窗口（首次运行）
        if not os.path.exists(CONFIG_FILE):
            return True
        
        return False
    
    def show_config_window(self):
        """显示配置窗口"""
        # 重新加载配置以确保显示最新值
        self.config = self.config_manager.load_config()
        
        root = tk.Tk()
        root.title("Let Me Go - 健康提醒设置")
        root.geometry("700x650")
        root.resizable(True, True)
        root.minsize(600, 550)
        
        # 居中显示窗口
        root.update_idletasks()
        width = 700
        height = 650
        x = (root.winfo_screenwidth() // 2) - (width // 2)
        y = (root.winfo_screenheight() // 2) - (height // 2)
        root.geometry(f"{width}x{height}+{x}+{y}")
        
        # 设置背景色（微信风格的浅灰色）
        root.configure(bg="#F5F5F5")
        
        # 创建主容器
        main_container = tk.Frame(root, bg="#F5F5F5")
        main_container.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
        
        # 创建可滚动的画布
        canvas = tk.Canvas(main_container, bg="#F5F5F5", highlightthickness=0)
        scrollbar = tk.Scrollbar(main_container, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg="#F5F5F5")
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        # 使用居中布局
        def on_canvas_configure(event):
            canvas_width = event.width
            scrollable_frame.update_idletasks()
            frame_width = scrollable_frame.winfo_width()
            if frame_width > 0:
                x = (canvas_width - frame_width) // 2
                canvas.coords(canvas.find_all()[0], x, 0)
            canvas.configure(scrollregion=canvas.bbox("all"))
        
        canvas.bind('<Configure>', on_canvas_configure)
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # 内容容器（限制最大宽度，居中显示）
        content_frame = tk.Frame(scrollable_frame, bg="#F5F5F5", width=600)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        # 标题区域（微信风格的顶部区域）
        header_frame = tk.Frame(content_frame, bg="#FFFFFF", relief=tk.FLAT, height=80)
        header_frame.pack(fill=tk.X, pady=(0, 15))
        header_frame.pack_propagate(False)
        
        title_label = tk.Label(header_frame, text="⏰ 健康提醒设置", 
                               font=("微软雅黑", 18, "bold"), bg="#FFFFFF", fg="#1A1A1A")
        title_label.pack(pady=25)
        
        # 工作时间段配置（微信风格白色卡片）
        work_frame = tk.Frame(content_frame, bg="#FFFFFF", relief=tk.FLAT, padx=20, pady=15)
        work_frame.pack(fill=tk.X, pady=(0, 10))
        
        tk.Label(work_frame, text="工作时间段", font=("微软雅黑", 13, "bold"), 
                bg="#FFFFFF", fg="#1A1A1A", anchor="w").pack(fill=tk.X, pady=(0, 10))
        tk.Label(work_frame, text="提醒生效的时间段", font=("微软雅黑", 10), 
                bg="#FFFFFF", fg="#888888", anchor="w").pack(fill=tk.X, pady=(0, 15))
        
        work_periods_widgets = []
        
        def add_work_period(period=None):
            """添加一个工作时间段"""
            item_frame = tk.Frame(work_frame)
            item_frame.pack(fill=tk.X, pady=5)
            
            tk.Label(item_frame, text="开始:", width=6, anchor="w").pack(side=tk.LEFT, padx=5)
            start_entry = tk.Entry(item_frame, font=("Consolas", 11), width=10)
            start_entry.pack(side=tk.LEFT, padx=5)
            
            tk.Label(item_frame, text="结束:", width=6, anchor="w").pack(side=tk.LEFT, padx=5)
            end_entry = tk.Entry(item_frame, font=("Consolas", 11), width=10)
            end_entry.pack(side=tk.LEFT, padx=5)
            
            if period:
                start_entry.insert(0, period.get("start", "09:00"))
                end_entry.insert(0, period.get("end", "18:00"))
            else:
                start_entry.insert(0, "09:00")
                end_entry.insert(0, "18:00")
            
            def remove_work():
                item_frame.destroy()
                if (start_entry, end_entry, item_frame) in work_periods_widgets:
                    work_periods_widgets.remove((start_entry, end_entry, item_frame))
                root.update_idletasks()
                on_canvas_configure(None)
            
            remove_btn = tk.Button(item_frame, text="删除", command=remove_work, 
                                   font=("微软雅黑", 9), width=8, bg="#FF4444", fg="white",
                                   relief=tk.FLAT, cursor="hand2")
            remove_btn.pack(side=tk.RIGHT, padx=5)
            
            work_periods_widgets.append((start_entry, end_entry, item_frame))
        
        def add_work_btn_click():
            add_work_period()
            root.update_idletasks()
            on_canvas_configure(None)
        
        # 加载已有的工作时间段
        work_periods = self.config.get("work_periods", [])
        if not work_periods and "start_time" in self.config:
            # 兼容旧格式
            work_periods = [{"start": self.config.get("start_time", "09:00"), 
                           "end": self.config.get("end_time", "18:00")}]
        
        for period in work_periods:
            add_work_period(period)
        
        # 如果没有时间段，添加一个默认的
        if not work_periods_widgets:
            add_work_period()
        
        add_work_btn = tk.Button(work_frame, text="+ 添加时间段", command=add_work_btn_click,
                                font=("微软雅黑", 10), bg="#07C160", fg="white",
                                relief=tk.FLAT, padx=15, pady=5, cursor="hand2")
        add_work_btn.pack(pady=(5, 0))
        
        # 屏蔽时间段配置（微信风格白色卡片）
        block_frame = tk.Frame(content_frame, bg="#FFFFFF", relief=tk.FLAT, padx=20, pady=15)
        block_frame.pack(fill=tk.X, pady=(0, 10))
        
        tk.Label(block_frame, text="屏蔽时间段", font=("微软雅黑", 13, "bold"), 
                bg="#FFFFFF", fg="#1A1A1A", anchor="w").pack(fill=tk.X, pady=(0, 10))
        tk.Label(block_frame, text="不提醒的时间段", font=("微软雅黑", 10), 
                bg="#FFFFFF", fg="#888888", anchor="w").pack(fill=tk.X, pady=(0, 15))
        
        block_periods_widgets = []
        
        def add_block_period(period=None):
            """添加一个屏蔽时间段"""
            item_frame = tk.Frame(block_frame)
            item_frame.pack(fill=tk.X, pady=5)
            
            tk.Label(item_frame, text="开始:", width=6, anchor="w").pack(side=tk.LEFT, padx=5)
            start_entry = tk.Entry(item_frame, font=("Consolas", 11), width=10)
            start_entry.pack(side=tk.LEFT, padx=5)
            
            tk.Label(item_frame, text="结束:", width=6, anchor="w").pack(side=tk.LEFT, padx=5)
            end_entry = tk.Entry(item_frame, font=("Consolas", 11), width=10)
            end_entry.pack(side=tk.LEFT, padx=5)
            
            if period:
                start_entry.insert(0, period.get("start", "12:00"))
                end_entry.insert(0, period.get("end", "13:30"))
            else:
                start_entry.insert(0, "12:00")
                end_entry.insert(0, "13:30")
            
            def remove_block():
                item_frame.destroy()
                if (start_entry, end_entry, item_frame) in block_periods_widgets:
                    block_periods_widgets.remove((start_entry, end_entry, item_frame))
                root.update_idletasks()
                on_canvas_configure(None)
            
            remove_btn = tk.Button(item_frame, text="删除", command=remove_block,
                                  font=("微软雅黑", 9), width=8, bg="#FF4444", fg="white",
                                  relief=tk.FLAT, cursor="hand2")
            remove_btn.pack(side=tk.RIGHT, padx=5)
            
            block_periods_widgets.append((start_entry, end_entry, item_frame))
        
        def add_block_btn_click():
            add_block_period()
            root.update_idletasks()
            on_canvas_configure(None)
        
        # 加载已有的屏蔽时间段
        block_periods = self.config.get("block_periods", [])
        if not block_periods and "block_start" in self.config:
            # 兼容旧格式
            block_periods = [{"start": self.config.get("block_start", "12:00"), 
                            "end": self.config.get("block_end", "13:30")}]
        
        for period in block_periods:
            add_block_period(period)
        
        add_block_btn = tk.Button(block_frame, text="+ 添加时间段", command=add_block_btn_click,
                                  font=("微软雅黑", 10), bg="#FF9500", fg="white",
                                  relief=tk.FLAT, padx=15, pady=5, cursor="hand2")
        add_block_btn.pack(pady=(5, 0))
        
        # 提醒间隔（微信风格白色卡片）
        interval_frame = tk.Frame(content_frame, bg="#FFFFFF", relief=tk.FLAT, padx=20, pady=15)
        interval_frame.pack(fill=tk.X, pady=(0, 10))
        
        tk.Label(interval_frame, text="提醒间隔", font=("微软雅黑", 13, "bold"), 
                bg="#FFFFFF", fg="#1A1A1A", anchor="w").pack(fill=tk.X, pady=(0, 10))
        interval_entry_frame = tk.Frame(interval_frame, bg="#FFFFFF")
        interval_entry_frame.pack(fill=tk.X)
        tk.Label(interval_entry_frame, text="分钟", font=("微软雅黑", 11), 
                bg="#FFFFFF", fg="#1A1A1A").pack(side=tk.LEFT, padx=(0, 10))
        interval_entry = tk.Entry(interval_entry_frame, font=("Consolas", 12), width=10,
                                  relief=tk.SOLID, borderwidth=1)
        interval_entry.insert(0, str(self.config.get("interval_minutes", 60)))
        interval_entry.pack(side=tk.LEFT)
        
        # 工作日设置（微信风格白色卡片）
        workdays_frame = tk.Frame(content_frame, bg="#FFFFFF", relief=tk.FLAT, padx=20, pady=15)
        workdays_frame.pack(fill=tk.X, pady=(0, 10))
        
        tk.Label(workdays_frame, text="工作日设置", font=("微软雅黑", 13, "bold"), 
                bg="#FFFFFF", fg="#1A1A1A", anchor="w").pack(fill=tk.X, pady=(0, 15))
        
        workdays_labels = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        workdays_vars = []
        workdays_config = self.config.get("workdays", [1, 2, 3, 4, 5])
        
        workdays_check_frame = tk.Frame(workdays_frame)
        workdays_check_frame.pack(anchor="w")
        
        for i in range(7):
            day_num = i + 1  # 1=周一, 7=周日
            var = tk.BooleanVar(value=day_num in workdays_config)
            workdays_vars.append(var)
            check = tk.Checkbutton(workdays_check_frame, text=workdays_labels[i], 
                                  variable=var, font=("微软雅黑", 10))
            check.pack(side=tk.LEFT, padx=10)
        
        # 下班时间和提醒设置（微信风格白色卡片）
        off_work_frame = tk.Frame(content_frame, bg="#FFFFFF", relief=tk.FLAT, padx=20, pady=15)
        off_work_frame.pack(fill=tk.X, pady=(0, 10))
        
        tk.Label(off_work_frame, text="下班提醒", font=("微软雅黑", 13, "bold"), 
                bg="#FFFFFF", fg="#1A1A1A", anchor="w").pack(fill=tk.X, pady=(0, 15))
        
        # 下班时间
        off_work_time_frame = tk.Frame(off_work_frame)
        off_work_time_frame.pack(fill=tk.X, pady=5)
        tk.Label(off_work_time_frame, text="下班时间", font=("微软雅黑", 11), 
                bg="#FFFFFF", fg="#1A1A1A").pack(side=tk.LEFT, padx=(0, 10))
        off_work_time_entry = tk.Entry(off_work_time_frame, font=("Consolas", 11), width=10,
                                       relief=tk.SOLID, borderwidth=1)
        off_work_time_entry.insert(0, self.config.get("off_work_time", "18:00"))
        off_work_time_entry.pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(off_work_time_frame, text="(距离下班10分钟时提醒)", 
                font=("微软雅黑", 9), bg="#FFFFFF", fg="#888888").pack(side=tk.LEFT)
        
        # 下班提醒开关
        off_work_reminder_enabled = self.config.get("off_work_reminder_enabled", True)
        off_work_reminder_var = tk.BooleanVar(value=off_work_reminder_enabled)
        off_work_reminder_check = tk.Checkbutton(
            off_work_frame, 
            text="启用下班提醒", 
            variable=off_work_reminder_var,
            font=("微软雅黑", 10)
        )
        off_work_reminder_check.pack(anchor="w", pady=5)
        
        # 开机自启动（微信风格白色卡片）
        auto_start_frame = tk.Frame(content_frame, bg="#FFFFFF", relief=tk.FLAT, padx=20, pady=15)
        auto_start_frame.pack(fill=tk.X, pady=(0, 20))
        
        auto_start_value = self.config.get("auto_start", AutoStartManager.is_auto_start_enabled())
        auto_start_var = tk.BooleanVar(value=auto_start_value)
        auto_start_check = tk.Checkbutton(
            auto_start_frame, 
            text="开机自动启动", 
            variable=auto_start_var,
            font=("微软雅黑", 12),
            bg="#FFFFFF",
            fg="#1A1A1A",
            selectcolor="#FFFFFF",
            activebackground="#FFFFFF",
            activeforeground="#1A1A1A"
        )
        auto_start_check.pack(anchor="w")
        
        def validate_and_start():
            """验证并启动"""
            try:
                # 验证工作时间段
                work_periods = []
                for start_entry, end_entry, _ in work_periods_widgets:
                    start_time = start_entry.get().strip()
                    end_time = end_entry.get().strip()
                    if not start_time or not end_time:
                        continue
                    if not parse_time(start_time) or not parse_time(end_time):
                        messagebox.showerror("错误", f"工作时间段格式错误: {start_time} - {end_time}\n请使用 HH:MM 格式（如 09:00）")
                        return
                    work_periods.append({"start": start_time, "end": end_time})
                
                if not work_periods:
                    messagebox.showerror("错误", "至少需要配置一个工作时间段！")
                    return
                
                # 验证屏蔽时间段
                block_periods = []
                for start_entry, end_entry, _ in block_periods_widgets:
                    start_time = start_entry.get().strip()
                    end_time = end_entry.get().strip()
                    if not start_time or not end_time:
                        continue
                    if not parse_time(start_time) or not parse_time(end_time):
                        messagebox.showerror("错误", f"屏蔽时间段格式错误: {start_time} - {end_time}\n请使用 HH:MM 格式（如 09:00）")
                        return
                    block_periods.append({"start": start_time, "end": end_time})
                
                # 验证间隔
                try:
                    interval = int(interval_entry.get().strip())
                    if interval <= 0:
                        raise ValueError
                except ValueError:
                    messagebox.showerror("错误", "提醒间隔必须是大于0的整数")
                    return
                
                # 获取工作日设置
                selected_workdays = []
                for i, var in enumerate(workdays_vars):
                    if var.get():
                        selected_workdays.append(i + 1)  # 1=周一, 7=周日
                
                if not selected_workdays:
                    messagebox.showerror("错误", "至少需要选择一个工作日！")
                    return
                
                # 验证下班时间
                off_work_time = off_work_time_entry.get().strip()
                if off_work_time and not parse_time(off_work_time):
                    messagebox.showerror("错误", f"下班时间格式错误: {off_work_time}\n请使用 HH:MM 格式（如 18:00）")
                    return
                
                # 保存配置
                self.config = {
                    "work_periods": work_periods,
                    "block_periods": block_periods,
                    "interval_minutes": interval,
                    "auto_start": auto_start_var.get(),
                    "workdays": selected_workdays,
                    "off_work_time": off_work_time if off_work_time else "18:00",
                    "off_work_reminder_enabled": off_work_reminder_var.get()
                }
                self.config_manager.save_config(self.config)
                
                # 设置开机自启动
                AutoStartManager.set_auto_start(auto_start_var.get())
                
                # 关闭配置窗口
                root.destroy()

                # 如果服务未运行，启动服务；如果已运行，配置会在下次循环时生效
                if not self.running:
                    self.start_reminder_service()
                else:
                    # 服务已在运行，重置提醒时间以立即应用新配置
                    self.last_reminder_time = None
                    messagebox.showinfo("提示", "配置已保存，新的设置将在下次提醒时生效！")
                
            except Exception as e:
                messagebox.showerror("错误", f"启动失败: {e}")
        
        # 启动按钮（微信风格绿色按钮）
        button_frame = tk.Frame(content_frame, bg="#F5F5F5")
        button_frame.pack(fill=tk.X, pady=(0, 20))
        
        start_button = tk.Button(
            button_frame, 
            text="保存并启动", 
            command=validate_and_start,
            bg="#07C160",
            fg="white",
            font=("微软雅黑", 14, "bold"),
            cursor="hand2",
            relief=tk.FLAT,
            padx=30,
            pady=12,
            borderwidth=0,
            activebackground="#06AD56",
            activeforeground="white"
        )
        start_button.pack(ipadx=50)
        
        # 提示信息
        tip_label = tk.Label(
            content_frame, 
            text="程序启动后将在系统托盘运行，右键托盘图标可进行设置",
            font=("微软雅黑", 10),
            bg="#F5F5F5",
            fg="#888888",
            wraplength=560
        )
        tip_label.pack(pady=(0, 10))
        
        # 配置滚动
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # 鼠标滚轮支持
        def on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        
        canvas.bind_all("<MouseWheel>", on_mousewheel)
        
        root.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))
        
        root.mainloop()
    
    def create_tray_icon_image(self, text=""):
        """创建托盘图标图像（闹钟图标）"""
        img = Image.new("RGB", (64, 64), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        
        # 绘制闹钟外圆
        draw.ellipse([8, 8, 56, 56], fill=(255, 193, 7), outline=(255, 152, 0), width=2)
        
        # 绘制闹钟内部圆
        draw.ellipse([16, 16, 48, 48], fill=(255, 255, 255), outline=(255, 152, 0), width=1)
        
        # 绘制12点位置
        draw.ellipse([31, 18, 33, 20], fill=(0, 0, 0))
        
        # 绘制6点位置
        draw.ellipse([31, 44, 33, 46], fill=(0, 0, 0))
        
        # 绘制3点位置
        draw.ellipse([44, 31, 46, 33], fill=(0, 0, 0))
        
        # 绘制9点位置
        draw.ellipse([18, 31, 20, 33], fill=(0, 0, 0))
        
        # 绘制时针和分针（指向12点）
        # 时针（较短）
        draw.line([32, 32, 32, 26], fill=(0, 0, 0), width=2)
        # 分针（较长）
        draw.line([32, 32, 32, 22], fill=(0, 0, 0), width=1)
        
        # 绘制中心点
        draw.ellipse([30, 30, 34, 34], fill=(0, 0, 0))
        
        # 如果提供了文字，在右下角显示（用于倒计时）
        if text:
            try:
                from PIL import ImageFont
                try:
                    font_path = "C:/Windows/Fonts/arial.ttf"
                    font = ImageFont.truetype(font_path, 12)
                except:
                    font = ImageFont.load_default()
                
                # 文字显示在右下角
                text_width = len(text) * 7
                x = 64 - text_width - 2
                y = 64 - 16
                # 绘制文字背景（半透明）
                draw.rectangle([x-2, y-2, 62, 62], fill=(0, 0, 0))
                draw.text((x, y), text, fill=(255, 255, 255), font=font)
            except:
                pass
        
        return img
    
    def update_tray_icon(self):
        """更新托盘图标（显示倒计时）"""
        if not self.icon or not self.next_reminder:
            return
        
        try:
            remaining = int((self.next_reminder - datetime.datetime.now()).total_seconds())
            if remaining < 0:
                remaining = 0
            
            mins = remaining // 60
            secs = remaining % 60
            
            if mins > 99:
                text = "99+"
            else:
                text = f"{mins:02d}"
            
            self.icon.icon = self.create_tray_icon_image(text)
        except Exception:
            pass
    
    def show_reminder_popup(self, message=None, title=None):
        """显示提醒弹窗"""
        def popup_thread():
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            root.update()
            
            msg = message if message else "该站起来走走了！\n\n已经坐了很长时间，起来活动一下吧！\n\n是否已完成活动？"
            title_text = title if title else "⏰ 健康提醒"
            
            if message and "下班" in message:
                # 下班提醒只需确认，不需要选择
                messagebox.showinfo(title_text, msg)
                root.destroy()
            else:
                result = messagebox.askyesno(title_text, msg, icon="question")
                root.destroy()
                
                if result:
                    self.last_reminder_time = datetime.datetime.now()
        
        threading.Thread(target=popup_thread, daemon=True).start()
    
    def reminder_service(self):
        """提醒服务主循环"""
        while self.running:
            try:
                # 每次循环重新加载配置，以支持运行时配置更新
                self.config = self.config_manager.load_config()
                
                now = datetime.datetime.now()
                current_time = now.time()
                today = now.date()
                weekday = now.weekday() + 1  # 转换为1-7 (1=周一, 7=周日)
                
                # 获取配置
                work_periods = self.config.get("work_periods", [])
                block_periods = self.config.get("block_periods", [])
                interval = self.config.get("interval_minutes", 60)
                workdays = self.config.get("workdays", [1, 2, 3, 4, 5])
                off_work_time_str = self.config.get("off_work_time", "18:00")
                off_work_reminder_enabled = self.config.get("off_work_reminder_enabled", True)
                
                if not work_periods:
                    time.sleep(60)
                    continue
                
                # 检查今天是否是工作日
                is_workday = weekday in workdays
                
                # 下班提醒检查（仅在工作日）
                if is_workday and off_work_reminder_enabled and off_work_time_str:
                    off_work_time = parse_time(off_work_time_str)
                    if off_work_time:
                        # 计算下班前10分钟的时间
                        off_work_dt = datetime.datetime.combine(today, off_work_time)
                        reminder_dt = off_work_dt - datetime.timedelta(minutes=10)
                        
                        # 检查是否到了下班提醒时间（在前后30秒内）
                        time_diff = abs((now - reminder_dt).total_seconds())
                        if time_diff <= 30:
                            # 检查今天是否已经提醒过
                            if self.last_off_work_reminder_date != today:
                                self.show_reminder_popup(
                                    "🎉 马上下班咯！\n\n还有10分钟就下班了，准备一下下班的事情吧！",
                                    "下班提醒"
                                )
                                self.last_off_work_reminder_date = today
                
                # 检查是否在任意一个工作时间段内（仅在工作日）
                in_work_period = False
                if is_workday:
                    for period in work_periods:
                        start_time = parse_time(period.get("start"))
                        end_time = parse_time(period.get("end"))
                        if start_time and end_time and time_in_range(start_time, end_time, current_time):
                            in_work_period = True
                            break
                
                # 检查是否在任意一个屏蔽时间段内
                in_block_period = False
                for period in block_periods:
                    block_start = parse_time(period.get("start"))
                    block_end = parse_time(period.get("end"))
                    if block_start and block_end and time_in_range(block_start, block_end, current_time):
                        in_block_period = True
                        break
                
                if in_work_period and not in_block_period:
                    # 首次启动时，设置初始时间，不立即提醒
                    if self.is_first_start:
                        self.last_reminder_time = now
                        self.is_first_start = False
                        # 计算下次提醒时间
                        self.next_reminder = now + datetime.timedelta(minutes=interval)
                    # 在工作时间段内且不在屏蔽时间段内，检查是否需要提醒
                    elif (self.last_reminder_time is None or 
                        (now - self.last_reminder_time).total_seconds() >= interval * 60):
                        self.show_reminder_popup()
                        self.last_reminder_time = now
                        
                        # 计算下次提醒时间
                        self.next_reminder = now + datetime.timedelta(minutes=interval)
                    else:
                        # 计算下次提醒时间
                        elapsed = (now - self.last_reminder_time).total_seconds() / 60
                        remaining = interval - elapsed
                        self.next_reminder = now + datetime.timedelta(minutes=remaining)
                else:
                    # 不在工作时间段内或在屏蔽时间段内，计算下次提醒时间
                    next_times = []
                    
                    # 计算所有工作时间段的开始时间（仅考虑工作日）
                    if is_workday:
                        for period in work_periods:
                            start_time = parse_time(period.get("start"))
                            if start_time:
                                start_dt = datetime.datetime.combine(today, start_time)
                                if start_dt <= now:
                                    start_dt += datetime.timedelta(days=1)
                                next_times.append(start_dt)
                    
                    # 如果是周末，计算下一个工作日的开始时间
                    if not is_workday and workdays:
                        days_ahead = 1
                        while (weekday + days_ahead - 1) % 7 + 1 not in workdays:
                            days_ahead += 1
                            if days_ahead > 7:
                                break
                        if days_ahead <= 7 and work_periods:
                            first_period = work_periods[0]
                            start_time = parse_time(first_period.get("start"))
                            if start_time:
                                next_workday = today + datetime.timedelta(days=days_ahead)
                                next_times.append(datetime.datetime.combine(next_workday, start_time))
                    
                    # 计算所有屏蔽时间段的结束时间
                    for period in block_periods:
                        block_end = parse_time(period.get("end"))
                        if block_end:
                            block_end_dt = datetime.datetime.combine(today, block_end)
                            if block_end_dt <= now:
                                block_end_dt += datetime.timedelta(days=1)
                            next_times.append(block_end_dt)
                    
                    if next_times:
                        self.next_reminder = min(next_times)
                    else:
                        # 如果没有时间段，设置一个默认的
                        self.next_reminder = now + datetime.timedelta(hours=1)
                
                # 每天重置下班提醒日期（跨天时）
                if self.last_off_work_reminder_date and self.last_off_work_reminder_date < today:
                    self.last_off_work_reminder_date = None
                
                # 每秒更新一次图标
                for _ in range(60):
                    if not self.running:
                        return
                    self.update_tray_icon()
                    time.sleep(1)
                    
            except Exception as e:
                print(f"提醒服务错误: {e}")
                time.sleep(60)
    
    def on_tray_show_config(self, icon, item):
        """托盘菜单：显示配置"""
        threading.Thread(target=self.show_config_window, daemon=True).start()
    
    def on_tray_manual_reminder(self, icon, item):
        """托盘菜单：手动提醒"""
        self.show_reminder_popup()
    
    def on_tray_exit(self, icon, item):
        """托盘菜单：退出"""
        self.running = False
        self.tray_running = False
        if self.icon:
            self.icon.stop()
    
    def tray_service(self):
        """系统托盘服务"""
        image = self.create_tray_icon_image()
        menu = pystray.Menu(
            pystray.MenuItem("⚙️ 设置", self.on_tray_show_config),
            pystray.MenuItem("🔔 立即提醒", self.on_tray_manual_reminder),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ 退出", self.on_tray_exit)
        )
        
        self.icon = pystray.Icon(
            "LetMeGo",
            image,
            "Let Me Go - 健康提醒助手\n右键可进行设置",
            menu
        )
        
        self.icon.run()
    
    def start_reminder_service(self):
        """启动提醒服务"""
        self.running = True
        
        # 启动提醒线程
        reminder_thread = threading.Thread(target=self.reminder_service, daemon=True)
        reminder_thread.start()
        
        # 启动托盘线程
        tray_thread = threading.Thread(target=self.tray_service, daemon=False)
        tray_thread.start()
        
        # 等待托盘退出
        tray_thread.join()
        
        # 托盘退出后停止提醒服务
        self.running = False


# =============== 主程序入口 ===============
def main():
    """主程序入口"""
    try:
        app = LetMeGoApp()
    except KeyboardInterrupt:
        print("程序已退出")
    except Exception as e:
        print(f"程序运行错误: {e}")
        messagebox.showerror("错误", f"程序运行出错: {e}")


if __name__ == "__main__":
    main()
