import io
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

import collection_runner as runner


class RunnerTests(unittest.TestCase):
    def exercise(self, collection_result=0, sql_result=0, error=None):
        calls = []
        def collect(path):
            calls.append(('collect', path))
            if error:
                raise error
            return collection_result
        def sql(path):
            calls.append(('sql', path))
            return sql_result
        with patch.object(runner.collect_reports, 'run', side_effect=collect), \
             patch.object(runner.import_reports, 'run', side_effect=sql), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = runner.run(Path('fixture.json'))
        self.assertEqual(calls, [('collect', Path('fixture.json')), ('sql', Path('fixture.json'))])
        return code

    def test_order_and_success(self):
        self.assertEqual(self.exercise(), 0)

    def test_collection_report_failure_does_not_block_ready_sql_imports(self):
        self.assertEqual(self.exercise(collection_result=2), 2)

    def test_collection_exception_is_logged_and_sql_still_attempted(self):
        self.assertEqual(self.exercise(error=RuntimeError('busy tracker')), 1)

    def test_import_failure_is_reported(self):
        self.assertEqual(self.exercise(sql_result=2), 2)

    def test_log_tee(self):
        first, second = io.StringIO(), io.StringIO()
        stream = runner.Tee(first, second)
        stream.write('test\n')
        stream.flush()
        self.assertEqual(first.getvalue(), second.getvalue())


if __name__ == '__main__':
    unittest.main()
