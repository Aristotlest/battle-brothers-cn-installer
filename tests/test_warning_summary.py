"""Conservative summaries: no unknown converter output is discarded."""
import unittest
import bb_cn_installer as app


class WarningSummaryTests(unittest.TestCase):
    def test_verified_repair_is_distinguished_from_preserved_upstream_failure(self):
        notice = '阵亡记录修复已校验: getObituaryInfo 拼接顺序。上游 onDeath 失败日志原样保留，详见 obituary-repair.json。'
        raw = '[*] 兼容处理失败, 将无法正常展示被击杀的历史'
        summary = app.conversion_warning_summary([notice, raw])
        self.assertTrue(summary.startswith('阵亡记录修复已校验'))
        self.assertNotIn('补丁兼容性存在不确定性', summary)
        self.assertIn(raw, summary)
        self.assertIn('第三方 MOD', summary)

    def test_only_exact_observed_progress_forms_are_grouped_unknown_lines_stay(self):
        warnings = [
            '正在翻译 scripts/entity/tactical/player.cnut',
            '[setup pyenv]Cost Time: 4[ms]',
            '[init python]Cost Time: 12[ms]',
            '挂载文件:  C:\\stage\\data\\data_001.dat',
            'modify failed for sourcecode: scripts/entity/tactical/player, func: onDeath',
            '[*] 兼容处理失败, 将无法正常展示被击杀的历史',
            '正在翻译 scripts/error.cnut ERROR unexpected',
            '[setup pyenv]Cost Time: unknown',
            'unfamiliar output: do not hide me',
        ]
        helper = getattr(app, 'conversion_warning_summary', None)
        self.assertTrue(callable(helper), 'Missing conservative warning summary')
        summary = helper(warnings)
        self.assertIn('9', summary)
        self.assertIn('4', summary)
        self.assertIn('player / onDeath', summary)
        self.assertIn('不能保证', summary)
        self.assertIn('第三方 MOD', summary)
        for line in warnings[4:]:
            self.assertIn(line, summary)
        self.assertNotIn(warnings[0], summary)
        self.assertEqual(warnings[-1], 'unfamiliar output: do not hide me')


if __name__ == '__main__':
    unittest.main()
