    owner_email = os.environ.get('VENDACERTA_OWNER_EMAIL', '').strip().lower()
    owner_exempt = bool(owner_email and str(user.get('email', '')).strip().lower() == owner_email)
    return {
        'trial_active': active_trial,
        'trial_ends_at': trial_until,
        'plan_active': active_plan,
        'plan': user.get('access_plan'),
        'plan_status': user.get('plan_status'),
        'access_until': access_until,
        'payment_history_count': int(user.get('payment_history_count', 0) or 0),
        'last_payment_at': user.get('last_payment_at'),
        'locked': not (active_trial or active_plan or owner_exempt),
        'owner_exempt': owner_exempt,
    }
