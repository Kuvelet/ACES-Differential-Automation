"""Local browser contract test; every HTTP request is fulfilled from a fixture."""
import importlib.util
import unittest
import test_pim_automation
import detector as d
from pim_automation import PIMSubmitter, RecoveringBrowser


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'Install requirements.txt to run browser integration test')
class BrowserWorkflowTest(unittest.TestCase):
    def test_closed_browser_restarts_before_next_pair(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            session = RecoveringBrowser(playwright.chromium)
            try:
                first = session.new_page()
                first.set_content('<p>First pair</p>')
                session.browser.close()  # Reproduce the browser-disconnected condition.
                second = session.new_page()
                second.set_content('<p>Next pair</p>')
                self.assertTrue(session.browser.is_connected())
                self.assertEqual(second.locator('p').inner_text(), 'Next pair')
                session.restart_required = True
                third = session.new_page()
                self.assertFalse(third.is_closed())
                self.assertTrue(second.is_closed())
            finally:
                session.close()

    def test_upload_selection_blur_and_exact_confirmation(self):
        from playwright.sync_api import sync_playwright
        fixture = (d.ROOT / 'tests' / 'pim_fixture.html').read_text(encoding='utf-8')
        setup = test_pim_automation.SubmissionTests()
        setup.setUp()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, timeout=15000)
                try:
                    page = browser.new_page()
                    page.route('**/*', lambda route: route.fulfill(content_type='text/html', body=fixture))
                    runner = PIMSubmitter(page, {'login_url': 'http://pim-test.invalid/Login',
                                                'action_delay_seconds': 0, 'upload_delay_seconds': 0}, 'test-user', 'test-password')
                    boundaries = []
                    result = runner.submit(setup.rows[1], lambda: boundaries.append(True))
                    self.assertEqual(result, 'Task — Added BrandGamma-ACES_Diff_2026-09-20_to_2026-10-18')
                    self.assertEqual(boundaries, [True])
                    self.assertEqual(page.evaluate('window.compareCount'), 1)
                    self.assertIn('2026-09-20', page.locator('#DropDownListACESCompareReference').input_value())
                    self.assertIn('2026-10-18', page.locator('#DropDownListACESCompareSource').input_value())
                finally:
                    browser.close()
        finally:
            setup.doCleanups()


if __name__ == '__main__':
    unittest.main()
