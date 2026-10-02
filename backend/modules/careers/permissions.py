"""Four permissions. Applicants need none: they apply to open vacancies and
answer offers. Interviewers need none to record feedback on their own
panel. Students and alumni read the job board; alumni post to it.

``careers.view``    see vacancies, candidates, interviews and offers at a campus
``careers.manage``  set up vacancies, screen, schedule interviews
``careers.hire``    make and withdraw offers; decide a job form's last step
``careers.board``   approve, reject and close job-board postings; post
                    without approval
"""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions(
    [
        PermissionSpec("careers.view", "View vacancies, candidates, interviews and offers"),
        PermissionSpec("careers.manage", "Set up vacancies, screen candidates, schedule interviews"),
        PermissionSpec("careers.hire", "Make job offers and hire"),
        PermissionSpec("careers.board", "Moderate the job board"),
    ]
)

grant_to_system_role("campus-admin", ["careers.view", "careers.manage", "careers.hire", "careers.board"])
