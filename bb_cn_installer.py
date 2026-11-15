"""Chinese Tk GUI and conservative CLI. Run without arguments for GUI."""
import argparse
import json
import os
from pathlib import Path
import queue
import re
import sys
import threading
import traceback
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from installer_core import Engine, Prepared, SafetyError, WindowsPlatform, PINS, discover_games, launch_option, no_links, prepare, sha256
from installer_paths import detect_paths


def steam_operation(engine, action, restart=False):
    """Use one explicitly selected backend; never retry writes on another route."""
    methods = {'set': ('set_steam_options', 'backend'),
               'restore': ('restore_steam_options', 'backend'),
               'uninstall': ('uninstall', 'steam_backend')}
    if action not in methods:
        raise SafetyError('未知 Steam 操作')
    name, keyword = methods[action]
    if not restart:
        return getattr(engine, name)()
    from steam_debug_session import debug_session
    with debug_session(consent=True) as backend:
        result = getattr(engine, name)(**{keyword: backend})
    if isinstance(result, dict) and result.get('status') in ('applied', 'restored'):
        result = dict(result)
        result['note'] = ('已通过 Steam 原生 AppID 接口设置并重新订阅读回；原值备份已保留。'
                          if result['status'] == 'applied' else '已通过 Steam 原生 AppID 接口恢复并重新订阅读回原值。')
        result['note'] += '临时调试会话已结束，Steam 已恢复普通启动。'
    return result


def default_cache():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'BB-CN-Installer'


def load_prepared(path):
    path = Path(path).absolute()
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value.get('warnings'), list) or not all(isinstance(w, str) for w in value['warnings']):
        raise SafetyError('准备清单无效')
    package = path.parent / 'zdata_cn.zip'
    if sha256(package) != value['sha256']: raise SafetyError('准备清单与 ZIP 不匹配')
    return Prepared(package, value['sha256'], value['warnings'])


def conversion_warning_summary(warnings):
    """Group only narrow progress forms observed in pinned converter evidence."""
    progress = (
        r'正在翻译 scripts/[A-Za-z0-9_/-]+\.cnut',
        r'\[(?:setup pyenv|init python)\]Cost Time: [0-9]+\[ms\]',
        r'挂载文件:  [^\r\n]+[\\/]data[\\/]data_[0-9]+\.dat',
    )
    retained = []
    grouped = 0
    for warning in warnings:
        # A multi-line or unfamiliar message is never classified by prefix alone.
        if any(re.fullmatch(pattern, warning) for pattern in progress):
            grouped += 1
        else:
            retained.append(warning)
    headline = ('重要：player / onDeath 补丁兼容性存在不确定性；不能保证该功能汉化完整。\n'
                '转换器若报告兼容处理失败，可能无法正常展示被击杀的历史；请核对下方实际输出。\n'
                '第三方 MOD 兼容性未验证；不合并 MOD。\n\n')
    return (headline + f'原始输出共 {len(warnings)} 条；已分组的已知进度 / 耗时行 {grouped} 条。\n'
            f'其余 {len(retained)} 条逐条保留（未判定为无害）：\n'
            + '\n'.join(retained)
            + '\n\n"显示全部原始输出"可查看未经删改的全部内容。')


def confirm_conversion_warnings(parent, warnings, log_path):
    """Show all conversion warnings in a bounded Tk modal; call on the Tk thread."""
    accepted = False
    window = tk.Toplevel(parent)
    window.title('明确接受转换警告？')
    window.geometry('760x520')
    window.resizable(False, False)
    window.transient(parent)
    frame = ttk.Frame(window, padding=12)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='请阅读实际转换警告；仅明确接受后才会继续安装。').pack(anchor='w')
    buttons = ttk.Frame(frame)
    buttons.pack(side='bottom', fill='x', pady=(12, 0))
    notebook = ttk.Notebook(frame)
    notebook.pack(fill='both', expand=True, pady=(8, 0))
    suffix = ('\n\n完整日志：' + str(log_path)
              + '\n\n接受以上实际转换警告并继续安装？')
    for title, content in (
            ('警告摘要', conversion_warning_summary(warnings)),
            ('显示全部原始输出', '\n'.join(warnings))):
        body = ttk.Frame(notebook)
        notebook.add(body, text=title)
        if title == '警告摘要':
            notebook.select(body)
        text = tk.Text(body, wrap='word')
        scroll = ttk.Scrollbar(body, orient='vertical', command=text.yview)
        scroll.pack(side='right', fill='y')
        text.pack(side='left', fill='both', expand=True)
        text.configure(yscrollcommand=scroll.set)
        text.insert('1.0', content + suffix)
        text.configure(state='disabled')

    def accept():
        nonlocal accepted
        accepted = True
        window.destroy()

    ttk.Button(buttons, text='接受警告并继续安装', command=accept,
               default='disabled').pack(side='left')
    cancel = ttk.Button(buttons, text='取消', command=window.destroy, default='active')
    cancel.pack(side='right')
    window.protocol('WM_DELETE_WINDOW', window.destroy)
    window.bind('<Escape>', lambda _event: window.destroy())
    window.wait_visibility()
    window.grab_set()
    cancel.focus_set()
    parent.wait_window(window)
    return accepted


class Application:
    def __init__(self, root):
        self.root = root
        self.busy = False
        self.prepared = None
        self.prepared_game = None
        self.events = queue.Queue()
        self.root.title('战场兄弟 Steam 汉化安装器')
        self.root.geometry('900x520')
        self.root.minsize(760, 480)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.content_canvas = tk.Canvas(root, highlightthickness=0)
        content_scroll = ttk.Scrollbar(root, orient='vertical', command=self.content_canvas.yview)
        content_scroll.pack(side='right', fill='y')
        self.content_canvas.pack(side='left', fill='both', expand=True)
        self.content_canvas.configure(yscrollcommand=content_scroll.set)
        frame = ttk.Frame(self.content_canvas, padding=16)
        content_id = self.content_canvas.create_window((0, 0), window=frame, anchor='nw')
        frame.bind('<Configure>', lambda _event: self.content_canvas.configure(scrollregion=self.content_canvas.bbox('all')))
        self.content_canvas.bind('<Configure>', lambda event: self.content_canvas.itemconfigure(content_id, width=event.width))
        ttk.Label(frame, text='战场兄弟 - Steam 中文安装器', font=('Microsoft YaHei UI', 17, 'bold')).pack(anchor='w')
        self.instructions = ttk.Label(frame, text='仅支持 1.5.2.3。内置汉化文本，首次需联网获取转换器、从作者页面导入启动器。\n不改原版 EXE / steam_api.dll / 存档；未授权或接口不可用时需手动粘贴 Steam 启动项。不承诺实际成就解锁；第三方 MOD 兼容性未验证。', wraplength=840)
        self.instructions.pack(anchor='w', pady=10)
        self.game = tk.StringVar()
        self.launcher = tk.StringVar()
        self.game_state = tk.StringVar(value='缺少 / 未检测')
        self.launcher_state = tk.StringVar(value='缺少 / 未检测')
        self.selection_revision = {'game': 0, 'launcher': 0}
        self.game.trace_add('write', lambda *_: self.path_changed('game'))
        self.launcher.trace_add('write', lambda *_: self.path_changed('launcher'))
        self.cache = tk.StringVar(value=str(default_cache()))
        self.controls = []
        self.actions = {}
        for name, label, variable, chooser in (
            ('game', '游戏目录', self.game, lambda: self.choose_directory(self.game)),
            ('launcher', 'Steam 启动器', self.launcher, self.choose_launcher),
        ):
            row = ttk.Frame(frame)
            row.pack(fill='x', pady=6)
            ttk.Label(row, text=label, width=15).pack(side='left')
            ttk.Label(row, textvariable=getattr(self, name + '_state'), width=19).pack(side='left')
            summary = ttk.Label(row, textvariable=variable, wraplength=450)
            summary.pack(side='left', fill='x', expand=True)
            setattr(self, name + '_summary', summary)
            button = ttk.Button(row, text='更改...', command=chooser)
            button.pack(side='right')
            self.controls.append(button)
        guidance = ttk.Frame(frame)
        guidance.pack(fill='x')
        self.component_help = ttk.Label(guidance, text='缺少启动器？从作者页下载 Steam v1.2.2，选择 EXE 后重新检测。', wraplength=510)
        self.component_help.pack(side='left', fill='x', expand=True)
        self.download_button = ttk.Button(guidance, text='作者下载页', command=lambda: webbrowser.open(PINS['launcher']['url']))
        self.download_button.pack(side='left', padx=4)
        self.redetect_button = ttk.Button(guidance, text='重新检测', command=self.discover)
        self.redetect_button.pack(side='right')
        self.controls.extend((self.download_button, self.redetect_button))
        self.restart_steam = tk.BooleanVar(value=False)
        self.restart_checkbox = ttk.Checkbutton(
            frame, text='允许临时重启 Steam 并自动设置 / 恢复启动项（请先退出全部游戏）\n默认关闭；关闭时一键安装不会修改 Steam，需手动粘贴启动项。',
            variable=self.restart_steam)
        self.restart_checkbox.pack(anchor='w', pady=8)
        self.controls.append(self.restart_checkbox)
        primary = ttk.Frame(frame)
        primary.pack(fill='x', pady=8)
        for key, text, callback in (
            ('install_all', '一键安装', lambda: self.install_all()),
            ('uninstall', '卸载还原', self.uninstall),
        ):
            button = ttk.Button(primary, text=text, command=callback)
            button.pack(side='left', padx=(0, 12), ipadx=20, ipady=6)
            self.actions[key] = button
            self.controls.append(button)
        self.file_result = tk.StringVar(value='游戏文件：当前目标未校验')
        self.steam_result = tk.StringVar(value='Steam 启动项：未操作 / 未确认')
        self.installed_target = None
        self.installed_revision = None
        results = ttk.Frame(frame)
        results.pack(fill='x')
        self.copy_button = ttk.Button(results, text='复制启动命令', command=self.copy_launch_command, state='disabled')
        self.copy_button.pack(side='right')
        ttk.Label(results, textvariable=self.file_result, wraplength=650).pack(anchor='w')
        ttk.Label(results, textvariable=self.steam_result, wraplength=650).pack(anchor='w')
        self.status = tk.StringVar(value='就绪。请确认目录和启动器；安装前会询问许可。')
        ttk.Label(frame, textvariable=self.status, wraplength=840).pack(anchor='w', pady=6)
        self.progress = ttk.Progressbar(frame, mode='indeterminate')
        self.progress.pack(fill='x', pady=8)
        ttk.Button(frame, text='高级选项 / 日志（展开 / 收起）', command=self.toggle_advanced).pack(anchor='w')
        self.advanced = ttk.Frame(frame)
        for label, variable, chooser in (
            ('游戏根目录', self.game, lambda: self.choose_directory(self.game)),
            ('Steam 启动器 .exe', self.launcher, self.choose_launcher),
            ('缓存 / 准备目录', self.cache, lambda: self.choose_directory(self.cache)),
        ):
            row = ttk.Frame(self.advanced)
            row.pack(fill='x', pady=4)
            ttk.Label(row, text=label, width=19).pack(side='left')
            entry = ttk.Entry(row, textvariable=variable)
            entry.pack(side='left', fill='x', expand=True)
            button = ttk.Button(row, text='选择...', command=chooser)
            button.pack(side='left', padx=(6, 0))
            self.controls.extend((entry, button))
        links = ttk.Frame(self.advanced)
        links.pack(fill='x', pady=5)
        link = ttk.Button(links, text='打开作者下载页（Steam v1.2.2）', command=lambda: webbrowser.open(PINS['launcher']['url']))
        link.pack(side='left')
        self.replace = tk.BooleanVar(value=False)
        cb = ttk.Checkbutton(self.advanced, text='明确允许备份并替换已有未知汉化 / 启动器文件（否则拒绝覆盖）', variable=self.replace)
        cb.pack(anchor='w', pady=6)
        self.controls.extend((link, cb))
        buttons = ttk.Frame(self.advanced)
        buttons.pack(fill='x', pady=8)
        for key, text, callback in (
            ('discover', '发现 Steam 游戏', self.discover),
            ('prepare', '1. 准备汉化', self.prepare),
            ('install', '2. 安装', self.install),
            ('check', '检查 / 诊断', self.check),
            ('recover', '恢复中断事务', self.recover),
            ('steam', '3. 自动设置 Steam 启动选项', self.steam),
            ('steam_restore', '恢复 Steam 原启动选项', self.steam_restore),
        ):
            button = ttk.Button(buttons, text=text, command=callback)
            index = len(self.actions) - 2
            button.grid(row=index // 4, column=index % 4, sticky='ew', padx=(0, 5), pady=3)
            self.actions[key] = button
            self.controls.append(button)
        self.log = tk.Text(self.advanced, height=9, wrap='word', state='disabled')
        self.log.pack(fill='both', expand=True)
        ttk.Button(self.advanced, text='保存本次日志...（分享前检查路径隐私）', command=self.save_log).pack(anchor='e', pady=(8, 0))
        self.append('请先退出游戏。首次使用从作者页面手动下载并选择 bb-launcher-steam.exe；本安装器不再分发它。')
        self.poll_id = root.after(100, self.poll)
        self.detect_id = root.after(250, self.discover)

    def toggle_advanced(self):
        if self.advanced.winfo_manager():
            self.advanced.pack_forget()
            self.root.geometry('900x520')
        else:
            self.advanced.pack(fill='both', expand=True)
            self.root.geometry(f'900x{min(850, max(480, self.root.winfo_screenheight() - 80))}')

    def append(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', str(text) + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def choose_directory(self, variable):
        value = filedialog.askdirectory(parent=self.root)
        if value: variable.set(value)

    def choose_launcher(self):
        value = filedialog.askopenfilename(parent=self.root, title='选择作者 Steam v1.2.2 的 bb-launcher-steam.exe', filetypes=[('Steam launcher', '*.exe')])
        if value: self.launcher.set(value)

    def engine(self):
        if not self.game.get().strip(): raise SafetyError('请先选择游戏根目录（包含 data 与 win32）')
        return Engine(self.game.get())

    def start(self, label, task, done=None):
        if self.busy: return
        self.busy = True
        self.copy_button.configure(state='disabled')
        self.status.set(label)
        for c in self.controls: c.configure(state='disabled')
        self.progress.start(12)
        def worker():
            try: self.events.put((True, task(), done))
            except Exception as exc: self.events.put((False, str(exc) + '\n' + traceback.format_exc(), None))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            try:
                ok, value, done = self.events.get_nowait()
            except queue.Empty:
                return
            self.busy = False
            self.progress.stop()
            for c in self.controls: c.configure(state='normal')
            self.status.set('完成' if ok else '操作失败 / 已拒绝')
            if ok:
                if done: done(value)
                else: self.append(value)
            else:
                self.append(value)
                messagebox.showerror('操作未完成', value[:3000], parent=self.root)
            self.copy_button.configure(state='normal' if not self.busy and self.installed_target is not None
                                       and self.installed_revision == self.selection_revision else 'disabled')
        except Exception as exc:
            # A completion callback may fail after writes. Never imply rollback or success.
            self.installed_target = None
            self.installed_revision = None
            self.copy_button.configure(state='disabled')
            self.status.set('操作未完成：结果处理失败；请重新检查文件与 Steam 当前状态')
            self.file_result.set('游戏文件：结果处理失败，当前状态未确认；请重新检查')
            self.steam_result.set('Steam 启动项：当前状态未确认；请核对当前值和备份')
            detail = str(exc) + '\n' + traceback.format_exc()
            self.append(detail)
            messagebox.showerror('操作未完成', detail[:3000], parent=self.root)
        finally:
            self.poll_id = self.root.after(100, self.poll)

    def path_changed(self, name):
        self.selection_revision[name] += 1
        getattr(self, name + '_state').set(
            '已选择，未校验' if getattr(self, name).get().strip() else '缺少 / 未检测')
        if hasattr(self, 'file_result'):
            self.installed_target = None
            self.installed_revision = None
            self.file_result.set('游戏文件：选择已变化，当前目标未校验')
            self.steam_result.set('Steam 启动项：选择已变化，未确认')
            self.copy_button.configure(state='disabled')

    def record_installed(self, target, revision):
        if revision != self.selection_revision or Path(self.game.get()).absolute() != Path(target).absolute():
            self.append('已完成的文件校验属于先前目标：' + str(target) + '；当前选择已变化。')
            return
        self.installed_target = Path(target)
        self.installed_revision = dict(revision)
        self.file_result.set('游戏文件：已安装并校验')
        self.copy_button.configure(state='normal' if not self.busy else 'disabled')

    def copy_launch_command(self):
        if self.busy or self.installed_target is None or self.installed_revision != self.selection_revision:
            return
        try:
            value = launch_option(self.installed_target)
            self.root.clipboard_clear()
            self.root.clipboard_append(value)
            self.root.update_idletasks()
        except (tk.TclError, SafetyError) as exc:
            messagebox.showerror('复制未完成', str(exc), parent=self.root)
            return
        self.status.set('仅复制了启动命令；请在 Steam 属性 > 通用 > 启动选项中手动粘贴，未确认设置成功。')
        self.append(self.status.get())

    def discover(self):
        original_game, original_launcher = self.game.get(), self.launcher.get()
        revision = dict(self.selection_revision)
        def detect():
            result = dict(detect_paths(game=original_game) if original_game.strip() else detect_paths())
            if original_launcher.strip():
                try:
                    path = Path(original_launcher).absolute()
                    no_links(path)
                    result['explicit_launcher_valid'] = path.is_file() and sha256(path) == PINS['launcher']['sha256']
                    target = Path(original_game).absolute() if original_game.strip() else result['game']
                    result['explicit_launcher_inside_game'] = target is not None and path.is_relative_to(target)
                    result['explicit_launcher_usable'] = (result['explicit_launcher_valid']
                                                          and result['game'] is not None
                                                          and not result['explicit_launcher_inside_game'])
                except (OSError, SafetyError, ValueError):
                    result['explicit_launcher_valid'] = False
                    result['explicit_launcher_usable'] = False
            return result
        def found(result):
            game_unchanged = self.selection_revision['game'] == revision['game']
            source_note = ''
            if game_unchanged:
                if original_game.strip():
                    self.game_state.set('已校验：1.5.2.3' if result['game'] is not None else '未通过校验')
                elif result['game'] is not None:
                    self.game.set(str(result['game']))
                    self.game_state.set('已校验：1.5.2.3')
            if self.selection_revision['launcher'] == revision['launcher']:
                if original_launcher.strip():
                    if not game_unchanged:
                        self.launcher_state.set('游戏已变化，安装源未校验')
                    elif not result['explicit_launcher_valid']:
                        self.launcher_state.set('未通过校验：SHA256 / 路径')
                    elif result['explicit_launcher_usable']:
                        self.launcher_state.set('已校验：SHA256')
                    elif result['explicit_launcher_inside_game']:
                        self.launcher_state.set('SHA256 匹配；不可作安装源')
                        source_note = '所选启动器位于游戏目录内，SHA256 匹配仅作组件证据；请另选游戏目录之外的已校验启动器作为安装源。'
                    else:
                        self.launcher_state.set('SHA256 匹配；安装源待校验')
                        source_note = '所选启动器 SHA256 匹配；请先选择并校验游戏目录，再确认外部安装源是否可用。'
                elif result['launcher'] is not None and game_unchanged:
                    self.launcher.set(str(result['launcher']))
                    self.launcher_state.set('已校验：SHA256')
            self.status.set('\n'.join(note for note in (result['note'], source_note) if note)
                            or '只读检测完成；请查看组件校验状态。')
            self.append(self.status.get())
        self.start('正在只读检测游戏和已校验启动器...', detect, found)

    def install_all(self):
        """Capture permissions on Tk, then chain worker phases via main-thread callbacks."""
        if self.busy:
            return
        try:
            engine = self.engine()
            launcher = Path(self.launcher.get())
            if not launcher.is_file():
                raise SafetyError('请先导入官方 Steam 启动器 EXE')
            cache = Path(self.cache.get())
            replace = self.replace.get()
            restart = self.restart_steam.get()
            command = launch_option(engine.game)  # Required even for manual Steam setup.
        except Exception as exc:
            messagebox.showerror('安装被拒绝', str(exc), parent=self.root)
            return
        if not messagebox.askyesno(
                '一键安装：写入前确认',
                '内置汉化文本将在缓存中转换；首次使用需联网获取官方转换器。\n'
                '将安装并校验以下文件；游戏目录会保存事务和备份：\n'
                + str(engine.game) + '\n\ndata/zdata_cn.zip\nwin32/bb-launcher-steam.exe\n\n'
                '不会修改原版 EXE、steam_api.dll 或存档。转换警告将另行询问；请先退出游戏。确认？',
                parent=self.root):
            return
        if restart and not messagebox.askyesno(
                '确认 Steam 重启和启动项权限',
                '安装校验通过后，将正常退出并重启 Steam、临时开放仅本机调试接口以设置启动项；'
                '结束后恢复普通启动。请先退出全部游戏，保持当前账户登录，期间不要编辑 Steam 属性或切换账户。\n\n'
                '将读取并备份原值，再精确替换为：\n' + command
                + '\n\n不会直接编辑配置；接口不可用会拒绝。确认？', parent=self.root):
            return
        revision = dict(self.selection_revision)
        self.installed_target = None
        self.installed_revision = None
        self.copy_button.configure(state='disabled')
        self.file_result.set('游戏文件：安装未完成，正在准备 / 等待许可')
        self.steam_result.set('Steam 启动项：未修改')
        self.prepared = None
        self.prepared_game = None
        def ready(item):
            self.prepared = item
            self.prepared_game = engine.game
            self.append('准备完成：' + str(item.path) + '\nSHA256: ' + item.digest)
            self.append('转换器完整日志：' + str(item.path.parent / 'translator.log'))
            accept = False
            if item.warnings:
                self.append('\n'.join(item.warnings))
                accept = confirm_conversion_warnings(
                    self.root, item.warnings, item.path.parent / 'translator.log')
                if not accept:
                    self.status.set('已取消安装：未接受转换警告；游戏文件与 Steam 未修改。')
                    return
            def task():
                engine.install(item, launcher, replace=replace, accept_warnings=accept)
                if not engine.check()['healthy']:
                    raise SafetyError('安装后校验失败；请查看高级选项中的日志并检查 / 诊断')
            def installed(_):
                self.record_installed(engine.game, revision)
                if revision != self.selection_revision:
                    self.status.set('先前目标的汉化已安装并校验；当前选择已变化，Steam 未继续操作。')
                    return
                self.steam_result.set('Steam 启动项：未修改，需手动粘贴')
                if restart:
                    def configure_steam():
                        try:
                            return steam_operation(engine, 'set', restart=True)
                        except Exception as exc:
                            return {'status': 'manual', 'note': str(exc)}
                    def configured(result):
                        if revision != self.selection_revision:
                            self.append('先前目标的 Steam 操作结果：' + str(result))
                            return
                        if result.get('status') == 'applied':
                            self.steam_result.set('Steam 启动项：已设置并读回')
                            self.status.set('汉化与 Steam 启动项设置完成。')
                        else:
                            self.steam_result.set('Steam 启动项：未完成 / 未确认；请核对当前值和备份')
                            self.status.set('汉化已安装并校验；Steam 启动项未完成，已保留汉化和备份。')
                            self.append('请检查 Steam 当前启动项与备份，不要假定设置成功。\n'
                                        '备份（若已生成）：' + str(engine.store / 'launch-options.json')
                                        + '\n目标命令（供核对后手动粘贴）：\n' + command)
                        self.append(result.get('note', 'Steam 操作未返回详细说明'))
                    self.start('汉化已安装；正在设置并读回 Steam 启动项...', configure_steam, configured)
                    return
                self.status.set('汉化文件安装并校验完成；Steam 未修改，需手动粘贴启动选项。')
                self.append(self.status.get() + '\n' + command)
            self.start('正在事务安装并校验...', task, installed)
        self.start('正在下载校验、隔离复制并转换；可能需要几分钟...',
                   lambda: prepare(engine, launcher, cache), ready)

    def prepare(self):
        try:
            engine = self.engine()
            launcher = Path(self.launcher.get())
            if not launcher.is_file(): raise SafetyError('请先导入官方 Steam 启动器 EXE')
            cache = Path(self.cache.get())
        except Exception as exc:
            messagebox.showerror('准备被拒绝', str(exc), parent=self.root)
            return
        self.prepared = None
        def ready(value):
            self.prepared = value
            self.prepared_game = engine.game
            self.append('准备完成：' + str(value.path) + '\nSHA256: ' + value.digest + '\n' + '\n'.join(value.warnings))
            self.append('转换器完整日志：' + str(value.path.parent / 'translator.log'))
        self.start('正在下载校验、隔离复制并转换；可能需要几分钟...', lambda: prepare(engine, launcher, cache), ready)

    def install(self):
        try:
            engine = self.engine()
            if self.prepared is None or engine.game != self.prepared_game: raise SafetyError('请先为当前目录准备汉化')
            prepared = self.prepared
            launcher = Path(self.launcher.get())
            replace = self.replace.get()
        except Exception as exc:
            messagebox.showerror('安装被拒绝', str(exc), parent=self.root)
            return
        accept = False
        if prepared.warnings:
            accept = confirm_conversion_warnings(
                self.root, prepared.warnings, prepared.path.parent / 'translator.log')
            if not accept: return
        if not messagebox.askyesno('写入前确认', '将仅安装以下文件，并在游戏目录保存事务 / 备份状态：\n' + str(engine.game) + '\n\ndata/zdata_cn.zip\nwin32/bb-launcher-steam.exe\n\n不会修改原版程序、存档或 Steam 配置。确认？', parent=self.root): return
        revision = dict(self.selection_revision)
        self.installed_target = None
        self.installed_revision = None
        self.copy_button.configure(state='disabled')
        self.file_result.set('游戏文件：安装未完成 / 等待校验')
        self.steam_result.set('Steam 启动项：未修改，需手动粘贴或另行授权设置')
        def installed(_):
            self.record_installed(engine.game, revision)
            self.append('安装及文件校验完成。请点击"自动设置 Steam 启动选项"；将先备份原值，再通过 Steam 界面设置。')
        def task():
            engine.install(prepared, launcher, replace=replace, accept_warnings=accept)
            if not engine.check()['healthy']: raise SafetyError('安装后校验失败')
        self.start('正在事务安装...', task, installed)

    def check(self):
        try: engine = self.engine()
        except Exception as exc:
            messagebox.showerror('检查失败', str(exc), parent=self.root)
            return
        self.start('只读检查中...', lambda: json.dumps(engine.diagnose(), ensure_ascii=False, indent=2))

    def uninstall(self):
        try: engine = self.engine()
        except Exception as exc:
            messagebox.showerror('卸载被拒绝', str(exc), parent=self.root)
            return
        if not messagebox.askyesno('确认卸载并恢复启动选项', '先通过 Steam 界面恢复自动设置前的原值（原值为空则清空）。\n仅当前值仍为本安装器设置值时恢复；用户改动或自动化失败则停止卸载，保留备份。\n随后恢复本安装器管理的两个文件；不会删除 awesome-battle-brothers 目录。\n手动设置且没有自动记录的选项须自行恢复。\n\n目录：' + str(engine.game), parent=self.root): return
        restart = self.restart_steam.get()
        revision = dict(self.selection_revision)
        self.installed_target = None
        self.installed_revision = None
        self.copy_button.configure(state='disabled')
        self.file_result.set('游戏文件：卸载未完成 / 等待恢复校验')
        self.steam_result.set('Steam 启动项：恢复未完成 / 未确认')
        def task():
            return steam_operation(engine, 'uninstall', restart=restart)
        def done(result):
            if revision != self.selection_revision:
                self.append('先前目标的卸载结果：' + str(result))
                return
            self.file_result.set('游戏文件：已卸载并还原')
            self.steam_result.set('Steam 启动项：' + ('已恢复并读回' if result.get('status') == 'restored'
                                                   else '无自动管理记录；手动设置须自行核对'))
            self.append('卸载完成。' + result['note'])
        self.start('正在恢复 Steam 原选项并事务卸载...', task, done)

    def recover(self):
        try: engine = self.engine()
        except Exception as exc:
            messagebox.showerror('恢复被拒绝', str(exc), parent=self.root)
            return
        if not messagebox.askyesno('恢复中断事务', '将校验日志和备份，并回滚到事务开始前。文件已被外部修改则拒绝。\n目录：' + str(engine.game), parent=self.root): return
        self.installed_target = None
        self.installed_revision = None
        self.copy_button.configure(state='disabled')
        self.file_result.set('游戏文件：事务恢复后须重新检查，尚未确认安装状态')
        self.start('正在恢复中断事务...', lambda: (engine.recover(), '恢复完成。')[1])

    def steam(self):
        try:
            engine = self.engine()
            value = launch_option(engine.game)
        except Exception as exc:
            messagebox.showerror('设置被拒绝', str(exc), parent=self.root)
            return
        if not messagebox.askyesno('确认自动设置 Steam 启动选项', '保持 Steam 登录当前账户；操作期间不要编辑 Steam 属性或切换账户。\n若勾选临时重启，将正常退出并重启 Steam、临时开放仅本机调试接口，结束后恢复普通启动；请先退出全部游戏。\n将读取并备份原值，然后精确替换为以下命令，不会拼接其他参数：\n\n' + value + '\n\n通过 Steam 原生接口或可识别的属性界面操作，不直接编辑配置。接口不可用会拒绝。确认？', parent=self.root): return
        restart = self.restart_steam.get()
        revision = dict(self.selection_revision)
        self.installed_target = None
        self.installed_revision = None
        self.copy_button.configure(state='disabled')
        self.file_result.set('游戏文件：等待重新校验，尚未确认')
        self.steam_result.set('Steam 启动项：设置未完成 / 未确认')
        def task():
            if not engine.check()['healthy']:
                raise SafetyError('请先安装并通过文件校验')
            try: return steam_operation(engine, 'set', restart=restart)
            except Exception as exc: return {'status': 'manual', 'note': str(exc)}
        def done(result):
            self.record_installed(engine.game, revision)
            self.append(result['note'])
            if revision != self.selection_revision:
                return
            if result['status'] == 'applied':
                self.steam_result.set('Steam 启动项：已设置并读回')
                self.status.set('Steam 启动项已设置并读回。')
            else:
                self.steam_result.set('Steam 启动项：未完成 / 未确认；请核对当前值和备份')
                self.status.set('自动操作未完成；请核对提示，未确认导航或设置成功。')
                self.append('备份（若已生成）：' + str(engine.store / 'launch-options.json')
                            + '\n可使用主界面的"复制启动命令"后核对并手动粘贴；复制不代表 Steam 设置成功。')
        self.start('正在读取、备份并通过 Steam 界面设置...', task, done)

    def steam_restore(self):
        try: engine = self.engine()
        except Exception as exc:
            messagebox.showerror('恢复被拒绝', str(exc), parent=self.root)
            return
        if not messagebox.askyesno('确认恢复 Steam 原启动选项', '仅当当前值仍等于本安装器设置值时恢复备份原值；原值为空则清空。\n若检测到用户改动会拒绝；不会修改游戏文件。保持 Steam 运行，操作期间不要编辑 Steam 属性或切换账户。\n备份位置：' + str(engine.store / 'launch-options.json'), parent=self.root): return
        restart = self.restart_steam.get()
        revision = dict(self.selection_revision)
        self.steam_result.set('Steam 启动项：恢复未完成 / 未确认')
        def done(result):
            if revision != self.selection_revision:
                self.append('先前目标的 Steam 恢复结果：' + str(result))
                return
            self.steam_result.set('Steam 启动项：' + ('已恢复并读回' if result.get('status') == 'restored'
                                                   else '未确认恢复；手动设置须自行核对'))
            self.append(result['note'])
        self.start('正在安全恢复 Steam 原启动选项...', lambda: steam_operation(engine, 'restore', restart=restart), done)

    def save_log(self):
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension='.txt', filetypes=[('日志', '*.txt')])
        if path:
            try: Path(path).write_text(self.log.get('1.0', 'end'), encoding='utf-8')
            except OSError as exc: messagebox.showerror('保存失败', str(exc), parent=self.root)

    def close(self):
        if self.busy:
            messagebox.showwarning('操作正在进行', '请等待操作完成；不要在写入时关闭安装器。', parent=self.root)
            return
        self.root.after_cancel(self.poll_id)
        self.root.after_cancel(self.detect_id)
        self.root.destroy()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        root = tk.Tk()
        Application(root)
        root.mainloop()
        return 0
    parser = argparse.ArgumentParser(description='战场兄弟 Steam 汉化安装器（无参数打开中文 GUI）')
    parser.add_argument('action', nargs='?', choices=('diagnose', 'prepare', 'check', 'install', 'uninstall', 'recover', 'steam-set', 'steam-restore'), default='diagnose')
    parser.add_argument('--diagnose', action='store_true', help='只读诊断；不写游戏、Steam 配置或缓存')
    parser.add_argument('--game-dir', type=Path, help='游戏根目录，包含 data 和 win32')
    parser.add_argument('--launcher', type=Path, help='手动下载的官方 bb-launcher-steam.exe')
    parser.add_argument('--cache-dir', type=Path, default=default_cache())
    parser.add_argument('--prepared', type=Path, help='prepare 生成的 prepared.json')
    parser.add_argument('--replace', action='store_true', help='明确允许备份替换未知目标文件')
    parser.add_argument('--accept-warnings', action='store_true', help='明确接受已阅读的转换警告')
    parser.add_argument('--yes', action='store_true', help='明确确认 install / uninstall / recover / steam-set / steam-restore 写入')
    parser.add_argument('--restart-steam', action='store_true', help='明确允许临时重启 Steam 并启用仅本机调试以自动设置 / 恢复启动项，结束恢复普通启动')
    args = parser.parse_args(argv)
    if sys.stdout is not None and hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    try:
        if args.game_dir is None:
            games = discover_games(WindowsPlatform().steam())
            if len(games) != 1: raise SafetyError('未唯一发现游戏，请指定 --game-dir')
            args.game_dir = games[0]
        engine = Engine(args.game_dir)
        action = 'diagnose' if args.diagnose else args.action
        if action == 'diagnose':
            result = engine.diagnose()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            failed = (result.get('error') or not result['supported'] or result['pending_recovery']
                      or (result['installed'] and not result['healthy']))
            return 2 if failed else 0
        if action == 'prepare':
            if not args.launcher: raise SafetyError('prepare 需要 --launcher')
            item = prepare(engine, args.launcher, args.cache_dir)
            result = {'prepared': str(item.path.parent / 'prepared.json'), 'zip': str(item.path), 'sha256': item.digest, 'warnings': item.warnings}
        elif action == 'check':
            result = engine.check()
        else:
            if not args.yes: raise SafetyError('写入操作需要显式 --yes')
            if action == 'install':
                if not args.launcher or not args.prepared: raise SafetyError('install 需要 --launcher 和 --prepared')
                engine.install(load_prepared(args.prepared), args.launcher, replace=args.replace, accept_warnings=args.accept_warnings)
                result = engine.check()
            elif action == 'uninstall':
                steam_result = steam_operation(engine, 'uninstall', restart=args.restart_steam)
                result = {'uninstalled': True, 'steam': steam_result}
            elif action == 'steam-set':
                result = steam_operation(engine, 'set', restart=args.restart_steam)
            elif action == 'steam-restore':
                result = steam_operation(engine, 'restore', restart=args.restart_steam)
            else:
                engine.recover()
                result = {'recovered': True}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get('healthy', True) else 2
    except Exception as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False, indent=2))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
