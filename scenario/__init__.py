"""只读生日采购计划；所有写动作由页面逐项经网关授权。"""
from datetime import date, timedelta
import re


def calendar_dates(birthday, today):
    order=birthday-timedelta(days=2);remind=birthday-timedelta(days=4)
    if remind<today or order<=today:
        raise ValueError('生日计划需要留出准备时间，请选择至少四天后的生日。')
    return dict(birthday=birthday.isoformat(),remind_on=remind.isoformat(),order_on=order.isoformat(),deliver_on=birthday.isoformat())


def plan(goal, today):
    match=re.fullmatch(r'\s*(?:生日\s*)?(\d{4}-\d{2}-\d{2})\s*(?:生日)?\s*',goal)
    if not match: raise ValueError('请明确生日日期，例如“生日 2026-11-20”。')
    dates=calendar_dates(date.fromisoformat(match[1]),today)
    return dict(steps=[
        dict(step_id='prepare',action='account.balance',depends_on=[],description=f"{dates['remind_on']} 查询账户可用余额并准备采购：确认模拟商品、地址与配送日；此计划不注册后台提醒。"),
        dict(step_id='reserve',action='budget.reserve',depends_on=['prepare'],description='逐项核对后，经黄色确认预留所选商品总额；此计划不扣款。'),
        dict(step_id='order',action='merchant.order',depends_on=['reserve'],description=f"建议{dates['order_on']}下单，计划{dates['deliver_on']}配送；每笔下单单独强验证，未知结果先查原键。"),
    ],summary=f"生日{dates['birthday']}的模拟采购计划已生成，尚未预留、下单或注册自动执行任务。")
