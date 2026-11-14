"""Chinese Tk GUI and conservative CLI. Run without arguments for GUI."""
import argparse
import json
import os
from pathlib import Path
import queue
import sys
import threading
import traceback
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from installer_core import Engine, Prepared, SafetyError, WindowsPlatform, PINS, discover_games, launch_option, prepare, sha256


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


class Application:
    def __init__(self, root):
        self.root = root
        self.busy = False
        self.prepared = None
        self.prepared_game = None
        self.events = queue.Queue()
        self.root.title('战场兄弟 Steam 汉化安装器')
        self.root.geometry('900x690')
        self.root.minsize(760, 580)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='战场兄弟 · Steam 中文安装器', font=('Microsoft YaHei UI', 17, 'bold')).pack(anchor='w')
        self.instructions = ttk.Label(frame, text='仅支持 1.5.2.3；需导入作者 Steam 启动器。可自动设置 / 恢复 Steam 启动选项；界面不可访问时需手动粘贴。\n不改原版 EXE / steam_api.dll / 存档，不承诺实际成就解锁；第三方 MOD 兼容性未验证。', wraplength=840)
        self.instructions.pack(anchor='w', pady=10)
        self.game = tk.StringVar()
        self.launcher = tk.StringVar()
        self.cache = tk.StringVar(value=str(default_cache()))
        self.controls = []
        for label, variable, chooser in (
            ('游戏根目录', self.game, lambda: self.choose_directory(self.game)),
            ('Steam 启动器 .exe', self.launcher, self.choose_launcher),
            ('缓存 / 准备目录', self.cache, lambda: self.choose_directory(self.cache)),
        ):
            row = ttk.Frame(frame)
            row.pack(fill='x', pady=4)
            ttk.Label(row, text=label, width=19).pack(side='left')
            entry = ttk.Entry(row, textvariable=variable)
            entry.pack(side='left', fill='x', expand=True)
            button = ttk.Button(row, text='选择…', command=chooser)
            button.pack(side='left', padx=(6, 0))
            self.controls.extend((entry, button))
        links = ttk.Frame(frame)
        links.pack(fill='x', pady=5)
        link = ttk.Button(links, text='打开作者下载页（Steam v1.2.2）', command=lambda: webbrowser.open(PINS['launcher']['url']))
        link.pack(side='left')
        self.replace = tk.BooleanVar(value=False)
        cb = ttk.Checkbutton(frame, text='明确允许备份并替换已有未知汉化 / 启动器文件（否则拒绝覆盖）', variable=self.replace)
        cb.pack(anchor='w', pady=6)
        self.controls.extend((link, cb))
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x', pady=8)
        self.actions = {}
        for key, text, callback in (
            ('discover', '发现 Steam 游戏', self.discover),
            ('prepare', '1. 准备汉化', self.prepare),
            ('install', '2. 安装', self.install),
            ('check', '检查 / 诊断', self.check),
            ('uninstall', '卸载还原', self.uninstall),
            ('recover', '恢复中断事务', self.recover),
            ('steam', '3. 自动设置 Steam 启动选项', self.steam),
            ('steam_restore', '恢复 Steam 原启动选项', self.steam_restore),
        ):
            button = ttk.Button(buttons, text=text, command=callback)
            index = len(self.actions)
            button.grid(row=index // 4, column=index % 4, sticky='ew', padx=(0, 5), pady=3)
            self.actions[key] = button
            self.controls.append(button)
        self.status = tk.StringVar(value='就绪。准备过程只写缓存，安装前再次确认。')
        ttk.Label(frame, textvariable=self.status, wraplength=840).pack(anchor='w')
        self.progress = ttk.Progressbar(frame, mode='indeterminate')
        self.progress.pack(fill='x', pady=8)
        self.log = tk.Text(frame, height=17, wrap='word', state='disabled')
        self.log.pack(fill='both', expand=True)
        ttk.Button(frame, text='保存本次日志…（分享前检查路径隐私）', command=self.save_log).pack(anchor='e', pady=(8, 0))
        self.append('请先退出游戏。首次使用从作者页面手动下载并选择 bb-launcher-steam.exe；本安装器不再分发它。')
        self.poll_id = root.after(100, self.poll)

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
        self.status.set(label)
        for c in self.controls: c.configure(state='disabled')
        self.progress.start(12)
        def worker():
            try: self.events.put((True, task(), done))
            except Exception as exc: self.events.put((False, str(exc) + '\n' + traceback.format_exc(), None))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            ok, value, done = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
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
        self.poll_id = self.root.after(100, self.poll)

    def discover(self):
        def found(games):
            if games:
                self.game.set(str(games[0]))
                self.append('发现目录：\n' + '\n'.join(str(g) for g in games) + '\n默认选择第一个；请确认。')
            else: self.append('没有找到 AppID 365360，可手动选择游戏根目录。')
        self.start('正在读取 Steam 库…', lambda: discover_games(WindowsPlatform().steam()), found)

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
        self.start('正在下载校验、隔离复制并转换；可能需要几分钟…', lambda: prepare(engine, launcher, cache), ready)

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
            accept = messagebox.askyesno('明确接受转换警告？', '\n'.join(prepared.warnings)[:5000] + '\n\n完整日志已列在窗口。接受这些警告并继续？', parent=self.root)
            if not accept: return
        if not messagebox.askyesno('写入前确认', '将仅安装以下文件，并在游戏目录保存事务 / 备份状态：\n' + str(engine.game) + '\n\ndata/zdata_cn.zip\nwin32/bb-launcher-steam.exe\n\n不会修改原版程序、存档或 Steam 配置。确认？', parent=self.root): return
        def installed(_):
            self.append('安装及文件校验完成。请点击“自动设置 Steam 启动选项”；将先备份原值，再通过 Steam 界面设置。')
        def task():
            engine.install(prepared, launcher, replace=replace, accept_warnings=accept)
            if not engine.check()['healthy']: raise SafetyError('安装后校验失败')
        self.start('正在事务安装…', task, installed)

    def check(self):
        try: engine = self.engine()
        except Exception as exc:
            messagebox.showerror('检查失败', str(exc), parent=self.root)
            return
        self.start('只读检查中…', lambda: json.dumps(engine.diagnose(), ensure_ascii=False, indent=2))

    def uninstall(self):
        try: engine = self.engine()
        except Exception as exc:
            messagebox.showerror('卸载被拒绝', str(exc), parent=self.root)
            return
        if not messagebox.askyesno('确认卸载并恢复启动选项', '先通过 Steam 界面恢复自动设置前的原值（原值为空则清空）。\n仅当前值仍为本安装器设置值时恢复；用户改动或自动化失败则停止卸载，保留备份。\n随后恢复本安装器管理的两个文件；不会删除 awesome-battle-brothers 目录。\n手动设置且没有自动记录的选项须自行恢复。\n\n目录：' + str(engine.game), parent=self.root): return
        def task():
            result = engine.uninstall()
            return '卸载完成。' + result['note']
        self.start('正在恢复 Steam 原选项并事务卸载…', task)

    def recover(self):
        try: engine = self.engine()
        except Exception as exc:
            messagebox.showerror('恢复被拒绝', str(exc), parent=self.root)
            return
        if not messagebox.askyesno('恢复中断事务', '将校验日志和备份，并回滚到事务开始前。文件已被外部修改则拒绝。\n目录：' + str(engine.game), parent=self.root): return
        self.start('正在恢复中断事务…', lambda: (engine.recover(), '恢复完成。')[1])

    def steam(self):
        try:
            engine = self.engine()
            if not engine.check()['healthy']: raise SafetyError('请先安装并通过文件校验')
            value = launch_option(engine.game)
        except Exception as exc:
            messagebox.showerror('设置被拒绝', str(exc), parent=self.root)
            return
        if not messagebox.askyesno('确认自动设置 Steam 启动选项', '保持 Steam 运行且登录当前账户；操作期间不要编辑 Steam 属性或切换账户。\n将读取并备份原值，然后精确替换为以下命令，不会拼接其他参数：\n\n' + value + '\n\n仅通过 Steam 游戏属性界面操作，不直接编辑配置。若界面不可访问，将提示手动方案。确认？', parent=self.root): return
        def task():
            try: return engine.set_steam_options()
            except SafetyError as exc: return {'status': 'manual', 'note': str(exc)}
        def done(result):
            self.append(result['note'])
            if result['status'] == 'manual':
                self.status.set('自动操作未完成；请核对提示，未确认导航或设置成功。')
                if messagebox.askyesno('自动设置未完成：手动方案', result['note'] + '\n\n备份（若已生成）：' + str(engine.store / 'launch-options.json') + '\n\n仅复制目标命令供手动粘贴？不会自动打开 Steam 属性窗口，避免导航到错误页面。', parent=self.root):
                    self.root.clipboard_clear()
                    self.root.clipboard_append(value)
                    self.root.update_idletasks()
                    self.append('仅复制了目标命令；请在 Steam 库中选择“战场兄弟 Battle Brothers”→管理/属性→通用→启动选项，再手动粘贴。未确认成功。')
        self.start('正在读取、备份并通过 Steam 界面设置…', task, done)

    def steam_restore(self):
        try: engine = self.engine()
        except Exception as exc:
            messagebox.showerror('恢复被拒绝', str(exc), parent=self.root)
            return
        if not messagebox.askyesno('确认恢复 Steam 原启动选项', '仅当当前值仍等于本安装器设置值时恢复备份原值；原值为空则清空。\n若检测到用户改动会拒绝；不会修改游戏文件。保持 Steam 运行，操作期间不要编辑 Steam 属性或切换账户。\n备份位置：' + str(engine.store / 'launch-options.json'), parent=self.root): return
        self.start('正在安全恢复 Steam 原启动选项…', lambda: engine.restore_steam_options()['note'])

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
                steam_result = engine.uninstall()
                result = {'uninstalled': True, 'steam': steam_result}
            elif action == 'steam-set':
                result = engine.set_steam_options()
            elif action == 'steam-restore':
                result = engine.restore_steam_options()
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
