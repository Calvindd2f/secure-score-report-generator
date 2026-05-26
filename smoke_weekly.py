import sys
sys.path.insert(0, '.')
from secure_score_graph import render_weekly_html_report

json_data = {
    'tenant_name': 'Contoso Ltd',
    'report_type': 'weekly',
    'result': [
        {
            'createdDateTime': '2024-01-14T00:00:00Z',
            'currentScore': 312, 'maxScore': 445,
            'averageComparativeScores': [{'basis': 'AllTenants', 'averageScore': 62.3}],
            'azureTenantId': 'aaaabbbb-1234-5678-abcd-111122223333',
        },
        {
            'createdDateTime': '2024-01-21T00:00:00Z',
            'currentScore': 305, 'maxScore': 445,
            'averageComparativeScores': [{'basis': 'AllTenants', 'averageScore': 62.3}],
            'azureTenantId': 'aaaabbbb-1234-5678-abcd-111122223333',
        },
    ],
    'control_profiles': [
        {
            'rank': 1, 'title': 'Require MFA for all users', 'controlCategory': 'Identity',
            'maxScore': 9, 'implementationCost': 'low', 'userImpact': 'moderate',
            'threats': ['accountBreach', 'phishingOrWhaling'],
            'remediation': 'Create a Conditional Access policy requiring MFA for all users.',
            'remediationImpact': 'Users will be prompted for MFA on first sign-in.',
            'actionUrl': 'https://portal.azure.com/#blade/Microsoft_AAD_IAM/ConditionalAccessBlade',
        },
        {
            'rank': 2, 'title': 'Block legacy authentication protocols', 'controlCategory': 'Identity',
            'maxScore': 8, 'implementationCost': 'low', 'userImpact': 'low',
            'threats': ['accountBreach'],
            'remediation': 'Create a Conditional Access policy to block all legacy auth protocols.',
            'remediationImpact': 'Clients without modern auth support will be blocked.',
            'actionUrl': 'https://portal.azure.com/#blade/Microsoft_AAD_IAM/ConditionalAccessBlade',
        },
        {
            'rank': 3, 'title': 'Enable Self-Service Password Reset', 'controlCategory': 'Identity',
            'maxScore': 4, 'implementationCost': 'low', 'userImpact': 'low',
            'threats': ['accountBreach'],
            'remediation': 'Enable SSPR for all users in Azure AD Password Reset settings.',
            'remediationImpact': 'Users can reset passwords without contacting the helpdesk.',
            'actionUrl': 'https://portal.azure.com/#blade/Microsoft_AAD_IAM/PasswordResetMenuBlade',
        },
    ],
}

html = render_weekly_html_report(json_data, json_data['control_profiles'])
with open('smoke_weekly.html', 'w', encoding='utf-8') as f:
    f.write(html)
print('OK — written smoke_weekly.html')
assert 'Weekly Secure Score Update' in html
assert 'Score Regression This Week' in html  # 305 < 312
assert 'Require MFA' in html
print('Assertions passed.')
