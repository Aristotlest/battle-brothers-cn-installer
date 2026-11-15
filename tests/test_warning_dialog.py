"""Warning modal contract without opening any live windows or touching Steam."""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import bb_cn_installer as app


class WarningDialogTests(unittest.TestCase):
    def test_summary_is_default_and_full_raw_text_has_separate_scrollable_tab(self):
        warnings = ['正在翻译 scripts/root_state.cnut',
                    '[*] 兼容处理失败, 将无法正常展示被击杀的历史',
                    'modify failed for sourcecode: scripts/entity/tactical/player, func: onDeath']
        parent, window, notebook = Mock(), Mock(), Mock()
        summary, raw = Mock(), Mock()
        with patch.object(app.tk, 'Toplevel', return_value=window), \
                patch.object(app.tk, 'Text', side_effect=[summary, raw]), \
                patch.object(app.ttk, 'Notebook', return_value=notebook), \
                patch.object(app.ttk, 'Frame'), patch.object(app.ttk, 'Label'), \
                patch.object(app.ttk, 'Scrollbar'), patch.object(app.ttk, 'Button'):
            self.assertFalse(app.confirm_conversion_warnings(parent, warnings, Path('cache/translator.log')))
        self.assertEqual(notebook.add.call_count, 2)
        self.assertIn('摘要', notebook.add.call_args_list[0].kwargs['text'])
        self.assertIn('全部原始输出', notebook.add.call_args_list[1].kwargs['text'])
        notebook.select.assert_called_once_with(notebook.add.call_args_list[0].args[0])
        summary_text = summary.insert.call_args.args[1]
        self.assertNotIn(warnings[0], summary_text)
        self.assertIn(warnings[1], summary_text)
        self.assertIn('不能保证', summary_text)
        self.assertIn('\n'.join(warnings), raw.insert.call_args.args[1])
        for text in (summary, raw):
            self.assertIn('cache', text.insert.call_args.args[1])
            text.configure.assert_any_call(state='disabled')
            self.assertTrue(any('yscrollcommand' in call.kwargs for call in text.configure.call_args_list))

    def test_large_warning_text_is_complete_in_fixed_scrollable_modal(self):
        warnings = [f'{i:04d} 实际转换警告 ' + 'x' * 45 for i in range(2377)]
        log_path = Path('cached/translator.log')
        parent = Mock()
        window = Mock()
        text = Mock()
        scroll = Mock()
        buttons = []
        def button(master, **kwargs):
            widget = Mock()
            buttons.append((widget, kwargs))
            return widget
        def accept(_window):
            next(options['command'] for _, options in buttons if '接受' in options['text'])()
        parent.wait_window.side_effect = accept
        with patch.object(app.tk, 'Toplevel', return_value=window), \
                patch.object(app.tk, 'Text', return_value=text) as make_text, \
                patch.object(app.ttk, 'Frame'), patch.object(app.ttk, 'Notebook'), patch.object(app.ttk, 'Label'), \
                patch.object(app.ttk, 'Scrollbar', return_value=scroll) as make_scroll, \
                patch.object(app.ttk, 'Button', side_effect=button):
            helper = getattr(app, 'confirm_conversion_warnings', None)
            self.assertTrue(callable(helper), 'Missing reusable conversion warning modal')
            self.assertTrue(helper(parent, warnings, log_path))
        inserted = ''.join(call.args[1] for call in text.insert.call_args_list)
        self.assertIn('\n'.join(warnings), inserted)
        self.assertIn(str(log_path), inserted)
        self.assertIn('继续安装', inserted)
        self.assertGreater(len(inserted), 135000)
        self.assertEqual(make_text.call_args.kwargs['wrap'], 'word')
        text.configure.assert_any_call(state='disabled')
        self.assertEqual(make_scroll.call_args.kwargs['command'], text.yview)
        text.configure.assert_any_call(yscrollcommand=scroll.set)
        window.geometry.assert_called_once_with('760x520')
        window.resizable.assert_called_once_with(False, False)
        window.transient.assert_called_once_with(parent)
        window.wait_visibility.assert_called_once_with()
        window.grab_set.assert_called_once_with()
        parent.wait_window.assert_called_once_with(window)
        window.destroy.assert_called_once_with()

    def test_cancel_is_default_and_close_escape_or_cancel_never_accept(self):
        helper = getattr(app, 'confirm_conversion_warnings', None)
        self.assertTrue(callable(helper), 'Missing reusable conversion warning modal')
        for action in ('default', 'button', 'close', 'escape'):
            with self.subTest(action=action):
                parent, window = Mock(), Mock()
                buttons = []
                def button(master, **options):
                    widget = Mock()
                    buttons.append((widget, options))
                    return widget
                def dismiss(_window):
                    cancel = next((widget, options) for widget, options in buttons
                                  if options['text'] == '取消')
                    cancel[0].focus_set.assert_called_once_with()
                    self.assertEqual(cancel[1]['default'], 'active')
                    accept = next(options for _, options in buttons if '接受' in options['text'])
                    self.assertEqual(accept['default'], 'disabled')
                    if action == 'button':
                        cancel[1]['command']()
                    elif action == 'close':
                        callbacks = dict(call.args for call in window.protocol.call_args_list)
                        callbacks['WM_DELETE_WINDOW']()
                    elif action == 'escape':
                        callbacks = dict(call.args for call in window.bind.call_args_list)
                        callbacks['<Escape>'](None)
                parent.wait_window.side_effect = dismiss
                with patch.object(app.tk, 'Toplevel', return_value=window), \
                        patch.object(app.tk, 'Text'), patch.object(app.ttk, 'Frame'), \
                        patch.object(app.ttk, 'Notebook'), patch.object(app.ttk, 'Label'), patch.object(app.ttk, 'Scrollbar'), \
                        patch.object(app.ttk, 'Button', side_effect=button):
                    self.assertFalse(helper(parent, ['actual warning'], Path('translator.log')))
                if action != 'default':
                    window.destroy.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
