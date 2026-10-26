"""Normal application-exit summary and background shutdown.

This module only reads persisted business state and asks the background
supervisor to stop this application's own worker.  It never deletes the
database, cancels reservations/plans or approves a payment.
"""
import json

from .background import BackgroundController

PENDING = ('awaiting_confirmation', 'awaiting_verification', 'running', 'unknown')
PLAN_PENDING = ('pending', 'awaiting_bank')


def exit_summary(store, principal):
    with store.connection() as c:
        operations = []
        for row in c.execute('''SELECT action,idempotency_key,result_json FROM operations
            WHERE principal_id=? ORDER BY rowid DESC''', (principal,)):
            try:
                result = json.loads(row['result_json'])
                status = result.get('status', 'unknown') if isinstance(result, dict) else 'unknown'
            except (ValueError, TypeError):
                status = 'unknown'
            if status in PENDING:
                operations.append(dict(action=row['action'], idempotency_key=row['idempotency_key'],
                                       status=status))
        plans = []
        for row in c.execute('SELECT plan_id,snapshot_json FROM agent_plans WHERE principal_id=? ORDER BY updated_at DESC',
                             (principal,)):
            try:
                snapshot = json.loads(row['snapshot_json'])
            except (ValueError, TypeError):
                snapshot = {}
            if not isinstance(snapshot, dict):
                snapshot = {}
            tasks = snapshot.get('tasks', [])
            if not isinstance(tasks, list):
                tasks = []
            pending = [task for task in tasks
                       if isinstance(task, dict) and task.get('status') in PLAN_PENDING]
            if pending:
                plans.append(dict(plan_id=row['plan_id'], unfinished=len(pending),
                                  titles=[str(task.get('title', task.get('id', ''))) for task in pending[:5]]))
        unread = c.execute('SELECT COUNT(*) FROM notifications WHERE principal_id=? AND is_read=0',
                           (principal,)).fetchone()[0]
    return dict(pending_operations=operations, pending_plans=plans,
                unread_notifications=unread, background=BackgroundController(store).status())


def normal_exit(store, principal, *, timeout=6, controller=None):
    """Prepare a user-confirmed app exit; return whether background is confirmed stopped."""
    summary = exit_summary(store, principal)
    if controller is None:
        stop = BackgroundController(store).request_stop(wait=timeout, preserve_preference=True)
    else:
        stop = controller.request_stop(wait=timeout)
    summary['background'] = stop
    summary['app_exit_allowed'] = not stop.get('live')
    return summary
