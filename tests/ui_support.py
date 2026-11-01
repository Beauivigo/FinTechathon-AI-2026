"""Legacy business-page tests explicitly enter sample experience via its button.

Tests for first-run/login use native AppTest directly. This helper never sets
an auth flag, mocks the gate or bypasses an initialized/pending account.
"""
from streamlit.testing.v1 import AppTest as NativeAppTest


class AppTest(NativeAppTest):
    @classmethod
    def from_file(cls, script_path, *, default_timeout=3):
        from pathlib import Path
        path = Path(script_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return cls(path, default_timeout=default_timeout)

    def _run(self, widget_state=None, timeout=None):
        from pathlib import Path
        script = Path(self._script_path)
        # Dedicated acceptance entry points own their database bindings.
        if script.name != 'main.py' and script.parent.name != 'pages':
            return super()._run(widget_state, timeout=timeout)
        from unittest.mock import Mock, patch
        from bank.gateway import BankGateway
        from app import demo
        import bank.seed
        def fixture_path():
            if isinstance(demo.gateway, Mock) and isinstance(demo.gateway.return_value, BankGateway):
                return demo.gateway.return_value.store.path
            return bank.seed.demo_path()
        # The auth module's imported alias can outlive an earlier fixture patch.
        # Keep the real identity gate and business gateway on this test's database.
        with patch('app.auth_ui.demo_path', side_effect=fixture_path), patch('app.demo.demo_path', side_effect=fixture_path):
            return super()._run(widget_state, timeout=timeout)

    def run(self, *, timeout=None):
        super().run(timeout=timeout)
        if any(button.key == 'welcome_demo' for button in self.button):
            self.button(key='welcome_demo').click()
            super().run(timeout=timeout)
        return self


def fill_manual_birthday(page):
    """Explicitly fill newly required fields for the retained manual tools."""
    from datetime import timedelta
    flow=page.session_state['action_flow']
    birthday=(flow.gateway.clock().date()+timedelta(days=32)).replace(day=15)
    page.date_input(key='birthday_date').set_value(birthday)
    page.date_input(key='birthday_budget_day').set_value(birthday.replace(day=1))
    page.selectbox(key='merchant_address').select(flow.resources['addresses'][0])
    page.multiselect(key='birthday_items').set_value([flow.resources['items'][0],flow.resources['items'][2]])
    page.text_input(key='birthday_budget_text').set_value('1000.00')
    page.button(key='birthday_review').click().run()
    return page
