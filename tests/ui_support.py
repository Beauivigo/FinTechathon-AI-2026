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
    birthday=flow.gateway.clock().date()+timedelta(days=32)
    page.date_input(key='birthday_date').set_value(birthday)
    page.date_input(key='birthday_budget_day').set_value(birthday.replace(day=1))
    page.selectbox(key='merchant_address').select(flow.resources['addresses'][0])
    page.multiselect(key='birthday_items').set_value([flow.resources['items'][0],flow.resources['items'][2]])
    page.text_input(key='birthday_budget_text').set_value('1000.00')
    page.button(key='birthday_review').click().run()
    return page
